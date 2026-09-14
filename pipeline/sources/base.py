#!/usr/bin/env python3
"""
base.py -- the source-module contract and shared plumbing for the rebuild pipeline.

Every FDA source lives in ``pipeline/sources/<name>.py`` and implements three
module-level functions with these EXACT signatures (see docs/rebuild-strategy.md):

    NAME    : str                              # short id, e.g. "k510"
    DATASET : str                              # openFDA dataset, e.g. "device/510k"
    fetch(dest: Path) -> Manifest             # BULK download only; record provenance
    transform(src: Path, out: Path) -> Stats  # rows -> RDF, deterministic
    validate(ttl: Path) -> Report             # riot + SHACL; ok=False on any violation

This module supplies the plumbing so each converter stays small:
  * a Manifest (what was downloaded, when, with checksums) for reproducibility,
  * a bulk downloader,
  * a deterministic Turtle writer that binds ``ex:`` to iri.NS and counts triples,
  * a default validator that shells out to Apache Jena (riot + shacl).

Division of labour (unchanged): iri.py mints every IRI, common.py formats every
literal + provenance, a source module decides only WHICH triples a record has.
"""

import os
import sys
import json
import time
import hashlib
import subprocess
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

# --- make iri.py / common.py importable whether run as a script or imported ---
_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../pipeline
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
import iri        # noqa: E402
import common     # noqa: E402


# ===========================================================================
#  Provenance / manifest types -- written by fetch(), read by transform()
# ===========================================================================
@dataclass
class FileRef:
    url: str
    path: str          # local path, relative to the manifest's directory
    sha256: str
    bytes: int
    etag: str = ""


@dataclass
class Manifest:
    """What a fetch produced. retrieved_at is the download date (YYYY-MM-DD) and
    is the ONLY time value allowed into the output -- passed to common.provenance
    so a rerun is reproducible."""
    source: str
    dataset: str
    retrieved_at: str
    files: list = field(default_factory=list)   # list[FileRef]

    def write(self, path):
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path):
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        d["files"] = [FileRef(**f) for f in d.get("files", [])]
        return cls(**d)


@dataclass
class Stats:
    source: str
    records: int = 0
    triples: int = 0
    skipped: int = 0

    def merge(self, other):
        self.records += other.records
        self.triples += other.triples
        self.skipped += other.skipped
        return self


@dataclass
class Report:
    ok: bool
    triples: int = 0
    messages: list = field(default_factory=list)


# ===========================================================================
#  Fetch helpers -- bulk only
# ===========================================================================
def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def bulk_download(url, dest_dir, filename=None, timeout=120):
    """Download one bulk file (e.g. an openFDA download.json partition) and return
    a FileRef with checksum + ETag. NEVER use this to page an API -- the whole
    point of the rebuild is that acquisition is bulk (closes D9/D10)."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = filename or url.rstrip("/").split("/")[-1] or "download"
    out_path = dest_dir / name
    req = urllib.request.Request(url, headers={"User-Agent": "medical-device-kg/rebuild"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(out_path, "wb") as f:
        etag = r.headers.get("ETag", "") or ""
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    return FileRef(url=url, path=str(out_path.relative_to(dest_dir)),
                   sha256=sha256_file(out_path), bytes=out_path.stat().st_size, etag=etag)


def today():
    """UTC download date, YYYY-MM-DD. Use at fetch time only."""
    return time.strftime("%Y-%m-%d", time.gmtime())


# ===========================================================================
#  Turtle writing -- deterministic, ex: bound to iri.NS
# ===========================================================================
def turtle_header(extra=None):
    """Standard prefixes plus ex: -> iri.NS (the vocabulary namespace)."""
    base = {"ex": iri.NS}
    if extra:
        base.update(extra)
    return common.prefixes(base) + "\n"


class TurtleWriter:
    """Context manager wrapping an output .ttl. Writes the header on open, counts
    triples as blocks are written, and guarantees the file is closed. Use:

        with TurtleWriter(out_path, extra_prefixes={...}) as w:
            w.block(iri.k510("K1"), [("a", "ex:K510Record"), ("ex:deviceName", common.lit(name))])
        stats.triples = w.triples
    """
    def __init__(self, path, extra=None):
        self.path = Path(path)
        self.extra = extra
        self.triples = 0
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w", encoding="utf-8")
        self._fh.write(turtle_header(self.extra))
        return self

    def block(self, subject, pairs):
        """subject is a full IRI string (from iri.py) or a prefixed name; it is
        written wrapped in <> if it looks like an absolute IRI."""
        subj = f"<{subject}>" if "://" in str(subject) else str(subject)
        self.triples += common.write_block(self._fh, subj, pairs)
        return self

    def raw(self, text):
        self._fh.write(text)

    def __exit__(self, *exc):
        if self._fh:
            self._fh.close()
        return False


# ===========================================================================
#  Default validator -- Apache Jena (riot + shacl), skipped gracefully if absent
# ===========================================================================
def _tool(name):
    from shutil import which
    return which(name)


def default_validate(ttl, shapes=None):
    """riot --validate, optional SHACL, and a triple count. If Jena is not on PATH
    (e.g. a laptop without it) the check is SKIPPED rather than failed -- the
    authoritative gate is scripts/validate.sh on the VM, which has Jena."""
    ttl = str(ttl)
    msgs = []
    riot = _tool("riot")
    if not riot:
        return Report(ok=True, triples=0, messages=["riot not on PATH; validation skipped"])
    v = subprocess.run([riot, "--validate", ttl], capture_output=True, text=True)
    if v.returncode != 0:
        return Report(ok=False, messages=["riot --validate failed:\n" + (v.stderr or v.stdout)])
    msgs.append("riot --validate OK")
    triples = 0
    c = subprocess.run([riot, "--count", ttl], capture_output=True, text=True)
    for line in (c.stdout or "").splitlines():
        if "Triples" in line:
            digits = "".join(ch for ch in line.split("=")[-1] if ch.isdigit())
            triples = int(digits) if digits else triples
    shacl = _tool("shacl")
    if shapes and shacl:
        for shp in sorted(Path(shapes).glob("*.ttl")):
            s = subprocess.run([shacl, "validate", "--shapes", str(shp), "--data", ttl],
                               capture_output=True, text=True)
            out = s.stdout or ""
            if "sh:conforms  true" not in out and "conforms: true" not in out.lower():
                return Report(ok=False, triples=triples,
                              messages=[f"SHACL {shp.name} reported violations:\n{out}"])
            msgs.append(f"SHACL {shp.name} conforms")
    elif shapes and not shacl:
        msgs.append("shacl not on PATH; SHACL skipped")
    return Report(ok=True, triples=triples, messages=msgs)


# ===========================================================================
#  Source discovery -- run.py uses these
# ===========================================================================
def source_names():
    """All source module names under pipeline/sources/, excluding base and _*."""
    here = Path(__file__).parent
    names = []
    for p in sorted(here.glob("*.py")):
        if p.stem == "base" or p.stem.startswith("_"):
            continue
        names.append(p.stem)
    return names


def load_source(name):
    """Import pipeline/sources/<name>.py and return the module. Raises with a clear
    message if it does not satisfy the contract."""
    import importlib
    if _PIPELINE_DIR not in sys.path:
        sys.path.insert(0, _PIPELINE_DIR)
    mod = importlib.import_module(f"sources.{name}") if _import_ok() else _load_by_path(name)
    for attr in ("NAME", "DATASET", "fetch", "transform", "validate"):
        if not hasattr(mod, attr):
            raise TypeError(f"source '{name}' is missing required '{attr}' (see base.py contract)")
    return mod


def _import_ok():
    return os.path.isfile(os.path.join(os.path.dirname(__file__), "__init__.py"))


def _load_by_path(name):
    import importlib.util
    path = os.path.join(os.path.dirname(__file__), f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_src_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
#  SELF-TEST -- `python base.py`. No network.
# ===========================================================================
def _selftest():
    import tempfile

    # Manifest round-trips
    m = Manifest(source="k510", dataset="device/510k", retrieved_at="2026-10-02",
                 files=[FileRef(url="http://x/a.json", path="a.json", sha256="ab", bytes=3)])
    with tempfile.TemporaryDirectory() as d:
        mp = Path(d) / "manifest.json"
        m.write(mp)
        m2 = Manifest.read(mp)
        assert m2.source == "k510" and m2.files[0].url == "http://x/a.json" and m2.files[0].bytes == 3

        # sha256_file
        f = Path(d) / "f.bin"; f.write_bytes(b"hello")
        assert sha256_file(f) == hashlib.sha256(b"hello").hexdigest()

        # TurtleWriter: header + counted blocks
        out = Path(d) / "t.ttl"
        with TurtleWriter(out, extra={"skos": "http://www.w3.org/2004/02/skos/core#"}) as w:
            w.block(iri.k510("K033394"),
                    [("a", "ex:K510Record"),
                     ("ex:deviceName", common.lit("Pump")),
                     ("ex:decisionDate", common.date_lit("20040630")),
                     ("ex:blank", common.lit(""))])       # None -> skipped
        text = out.read_text(encoding="utf-8")
        assert f"@prefix ex: <{iri.NS}>" in text
        assert "@prefix skos:" in text
        assert f"<{iri.k510('K033394')}>" in text
        assert '"2004-06-30"^^xsd:date' in text
        assert "ex:blank" not in text                     # None pair skipped
        assert w.triples == 3, w.triples                  # a, deviceName, decisionDate

    # header
    h = turtle_header()
    assert h.startswith("@prefix") and "xsd:" in h and f"ex: <{iri.NS}>" in h

    # Stats.merge
    s = Stats("k510", records=2, triples=10).merge(Stats("k510", records=3, triples=5, skipped=1))
    assert (s.records, s.triples, s.skipped) == (5, 15, 1)

    # discovery does not crash (0+ sources is fine at this stage)
    assert isinstance(source_names(), list)

    print("base.py self-test PASSED.")


if __name__ == "__main__":
    _selftest()
