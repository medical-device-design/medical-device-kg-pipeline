#!/usr/bin/env python3
"""
live_check.py -- full fetch -> transform -> parse -> SHACL for one or more sources,
on a laptop or the VM, WITHOUT publishing anything. Pure-Python SHACL (pyshacl),
so it works where Jena is not installed.

    python scripts/live_check.py hde standards
    python scripts/live_check.py hde --keep out_check      # keep the TTL

Prints record/triple counts, class counts, and SHACL conformance per shape file.
Exit code 1 if any source fails to parse or any shape reports violations.
"""
import sys
import argparse
import tempfile
import collections
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "pipeline"), str(REPO / "pipeline" / "sources")]
import base      # noqa: E402
import licence   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--keep", default=None, help="directory to keep downloads and TTL")
    a = ap.parse_args(argv)
    import rdflib
    try:
        import pyshacl
    except ImportError:
        pyshacl = None
    shapes = {p.name: rdflib.Graph().parse(p, format="turtle")
              for p in sorted((REPO / "ontology" / "shapes").glob("*.ttl"))}
    failures = 0
    work = Path(a.keep) if a.keep else Path(tempfile.mkdtemp(prefix="mdkg_live_"))
    for name in a.sources:
        licence.check(name)
        mod = base.load_source(name)
        dl, ttl = work / "downloads" / name, work / "out" / f"{name}.ttl"
        man = mod.fetch(dl)
        man.write(dl / "manifest.json")
        st = mod.transform(dl, ttl)
        g = rdflib.Graph().parse(ttl, format="turtle")
        types = collections.Counter(str(o).split("/")[-1] for o in g.objects(None, rdflib.RDF.type))
        print(f"== {name}: {st.records} records, {st.skipped} skipped, {st.triples} written, "
              f"{len(g)} parsed (retrieved {man.retrieved_at})")
        print("   classes:", dict(types.most_common(8)))
        if pyshacl is None:
            print("   pyshacl not installed; SHACL skipped (pip install pyshacl)")
            continue
        for sname, sg in shapes.items():
            # warnings (e.g. a ProductCode stub awaiting its label from classification)
            # do not fail the gate; violations do
            ok, _, txt = pyshacl.validate(g, shacl_graph=sg, allow_warnings=True)
            print(f"   SHACL {sname}: {'conforms' if ok else 'VIOLATIONS'}")
            if not ok:
                failures += 1
                print("     " + txt[:1200].replace("\n", "\n     "))
    print(f"\nwork dir: {work}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
