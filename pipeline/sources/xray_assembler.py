#!/usr/bin/env python3
"""
xray_assembler.py -- FDA X-ray assembler reports (premarket/ftparea/assem.zip) -> RDF.

Each line of assem.txt is one report (FDA Form 2579) that an assembler filed when it
installed certified diagnostic X-ray components at a facility. FDA froze the file in
2010 (file date 2010-04-20 inside the zip); it is rebuilt from the bulk file, every
column kept, so nothing depends on the legacy scrape.

    ID|name|address|city|state|zip|phone|installed|room|use1|use2|use3|use4|typ|i|s|b|h|f|l|k|m|o

  name/address/city/state/zip/phone  the FACILITY where the equipment was installed
                                     (a business address and phone, not a person)
  installed                          installation date (typed xsd:date)
  room                               room / location label
  use1..use4                         FDA equipment-use codes as published
  typ                                equipment type code as published
  i s b h f l k m o                  component flags ("1" = that component type was
                                     installed); kept as the letter codes FDA uses,
                                     because the file carries no legend
"""
import os
import sys
import zipfile
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "xray_assembler"
DATASET = "cdrh/assem"
BULK_URL = "https://www.accessdata.fda.gov/premarket/ftparea/assem.zip"
COLUMNS = ["ID", "name", "address", "city", "state", "zip", "phone", "installed", "room",
           "use1", "use2", "use3", "use4", "typ", "i", "s", "b", "h", "f", "l", "k", "m", "o"]
FLAGS = ["i", "s", "b", "h", "f", "l", "k", "m", "o"]


def fetch(dest):
    f = base.bulk_download(BULK_URL, dest)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[f])


def _rows(src):
    src = Path(src)
    for p in sorted(src.glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.lower().endswith(".txt"):
                    yield from _lines(z.read(n).decode("latin-1"))
    for p in sorted(src.glob("*.txt")):
        yield from _lines(p.read_text(encoding="latin-1"))


def _lines(text):
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split("|")]
    if header[:len(COLUMNS)] != COLUMNS:
        raise RuntimeError(f"assem.txt header changed: {header}")      # stop, never guess columns
    for ln in lines[1:]:
        if ln.strip():
            cells = ln.split("|")
            yield {c: (cells[i].strip() if i < len(cells) else "") for i, c in enumerate(COLUMNS)}


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    seen = set()
    with base.TurtleWriter(out) as w:
        for r in _rows(src):
            rid = r["ID"]
            if not rid or rid in seen:
                st.skipped += 1
                continue
            seen.add(rid)
            pairs = [
                ("a", "ex:XrayAssemblerRecord"),
                ("rdfs:label", common.lit(r["name"])),
                ("ex:assemblerReportId", common.lit(rid)),
                ("ex:facilityName", common.lit(r["name"])),
                ("ex:address", common.lit(r["address"])),
                ("ex:city", common.lit(r["city"])),
                ("ex:state", common.lit(r["state"])),
                ("ex:postalCode", common.lit(r["zip"])),
                ("ex:phone", common.lit(r["phone"])),
                ("ex:installationDate", common.date_lit(r["installed"])),
                ("ex:room", common.lit(r["room"])),
                ("ex:equipmentType", common.lit(r["typ"])),
            ]
            for k in ("use1", "use2", "use3", "use4"):
                if r[k] not in ("", "0"):
                    pairs.append(("ex:equipmentUseCode", common.lit(r[k])))
            for k in FLAGS:
                if r[k] == "1":
                    pairs.append(("ex:installedComponentCode", common.lit(k)))
            pairs += common.provenance(DATASET, rid, source_url=BULK_URL, retrieved_at=retrieved_at)
            w.block(iri.xray_assembler(rid), pairs)
            st.records += 1
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import tempfile
    txt = ("|".join(COLUMNS) + "\n"
           "1|MEDICAL GROUP/SGSAR|330 KIRKPATRICK AVENUE EAST|MAXWELL AFB|AL|36612|(334)953-0393|1997-08-20|X-RAY|8|0|0|0|S|||1|1|1||||\n"
           "2|B SHARED SERVICES|333 W THOMAS SUITE A|PHOENIX|AZ|85013|(602)274-5459|1995-07-14|X-RAY|1|0|0|0|S|||1|1|1|1|||\n"
           "2|DUPLICATE ID|x|y|AZ|85013||1995-07-14|X|0|0|0|0|S|||||||||\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "assem.txt").write_text(txt, encoding="latin-1")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-10", files=[]).write(d / "manifest.json")
        st = transform(d, d / "x.ttl")
        ttl = (d / "x.ttl").read_text(encoding="utf-8")
        bad = d / "bad"; bad.mkdir(); (bad / "assem.txt").write_text("ID|name|other\n1|a|b\n", encoding="latin-1")
        try:
            transform(bad, bad / "x.ttl"); raise AssertionError("header change not detected")
        except RuntimeError:
            pass
    assert st.records == 2 and st.skipped == 1, st
    assert f"<{iri.xray_assembler('1')}>" in ttl and "a ex:XrayAssemblerRecord" in ttl
    assert '"1997-08-20"^^xsd:date' in ttl and 'ex:equipmentUseCode "8"' in ttl
    assert ttl.count('ex:installedComponentCode') == 7                    # b h f  +  b h f l
    assert 'ex:postalCode "36612"' in ttl and 'ex:sourceDataset "cdrh/assem"' in ttl
    print(f"xray_assembler.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
