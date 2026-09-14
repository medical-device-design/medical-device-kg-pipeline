#!/usr/bin/env python3
"""
run.py -- orchestrate the rebuild: fetch -> transform -> validate, per source.

Each source is pipeline/sources/<name>.py satisfying the base.py contract
(NAME, DATASET, fetch, transform, validate). See docs/rebuild-strategy.md.

Examples
--------
    python pipeline/run.py --selftest
    python pipeline/run.py --sources all --stage transform --out out
    python pipeline/run.py --sources k510,pma --stage all --shapes ontology/shapes

Exit code is non-zero if any requested validate() reports ok=False, so this is
safe to call from CI or the .83 cron before the publish step.
"""
import os
import sys
import argparse
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../pipeline
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from sources import base   # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="medical-device-kg rebuild orchestrator")
    ap.add_argument("--sources", default="all",
                    help='"all" or a comma list of source names (e.g. k510,pma)')
    ap.add_argument("--stage", default="all",
                    choices=["fetch", "transform", "validate", "all"])
    ap.add_argument("--downloads", default="downloads", help="raw bulk files root")
    ap.add_argument("--out", default="out", help="generated .ttl root")
    ap.add_argument("--shapes", default="ontology/shapes", help="SHACL shapes dir")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        base._selftest()
        print("run.py OK. discovered sources:", base.source_names() or "(none yet)")
        return 0

    names = (base.source_names() if a.sources == "all"
             else [s.strip() for s in a.sources.split(",") if s.strip()])
    if not names:
        print("No source modules under pipeline/sources/ yet. "
              "Add pipeline/sources/<name>.py per the base.py contract.")
        return 0

    downloads, out = Path(a.downloads), Path(a.out)
    failures = 0
    for name in names:
        mod = base.load_source(name)
        print(f"== {name}  ({mod.DATASET}) ==")
        dl = downloads / name
        if a.stage in ("fetch", "all"):
            man = mod.fetch(dl)
            man.write(dl / "manifest.json")
            print(f"  fetch: {len(man.files)} file(s), retrieved {man.retrieved_at}")
        if a.stage in ("transform", "all"):
            ttl = out / f"{name}.ttl"
            st = mod.transform(dl, ttl)
            print(f"  transform: {st.records} records -> {st.triples} triples "
                  f"({st.skipped} skipped) -> {ttl}")
        if a.stage in ("validate", "all"):
            ttl = out / f"{name}.ttl"
            rep = mod.validate(ttl)
            print(f"  validate: {'OK' if rep.ok else 'FAIL'}  ({rep.triples} triples)")
            for m in rep.messages:
                print("     " + m.replace("\n", "\n     "))
            failures += 0 if rep.ok else 1

    print(f"\nDONE. sources={len(names)}  failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
