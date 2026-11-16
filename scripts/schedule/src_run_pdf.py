#!/usr/bin/env python3
"""src_run_pdf.py -- fetch and/or transform the two PDF-reading sources (k510_predicates, hud_designations).

    python3 -I src_run_pdf.py <k510_predicates|hud_designations> <fetch|transform|all> <out dir>
                              [--sample N] [--limit N] [--workers N] [--numbers-file F]

The PDFs are a cache shared by every snapshot (they do not change once FDA posts them):
    $PDF_CACHE_ROOT/k510_summaries     (default work/downloads-2026-11-04/k510_summaries)
    $PDF_CACHE_ROOT/hud_designations
fetch reads the record lists from <out dir>/devices_fda_with_pma.ttl and <out dir>/hde.ttl and asks only for
documents not yet in the cache (1 request per second). transform writes <out dir>/<source>.ttl and its
.counts.json / .links.tsv / .rows.tsv / .ocr_backlog.tsv, resolving targets against the files in <out dir>.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

WT = os.environ.get("MDKG_WT", "/srv/medical-device-kg/work/v007")
sys.path[:0] = [WT + "/pipeline", WT + "/pipeline/sources"]
import base      # noqa: E402
import licence   # noqa: E402

ROOT = Path(os.environ.get("PDF_CACHE_ROOT", "/srv/medical-device-kg/work/downloads-2026-11-04"))
CACHE = {"k510_predicates": ROOT / "k510_summaries", "hud_designations": ROOT / "hud_designations"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=sorted(CACHE))
    ap.add_argument("stage", choices=["fetch", "transform", "all"])
    ap.add_argument("out")
    ap.add_argument("--sample", type=int)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--numbers-file")
    a = ap.parse_args()
    row = licence.check(a.name, licence.load(), allow_restricted=False)
    if row["open_release"].strip().lower() != "yes":
        print(f"[{a.name}] not an open-release source; stop")
        return 3
    mod = base.load_source(a.name)
    out_dir, dl = Path(a.out), CACHE[a.name]
    nums = [l.strip() for l in open(a.numbers_file) if l.strip()] if a.numbers_file else None
    t = time.time()
    if a.stage in ("fetch", "all"):
        if a.name == "k510_predicates":
            mod.fetch(dl, devices_ttl=out_dir / "devices_fda_with_pma.ttl", sample=a.sample, limit=a.limit, numbers=nums)
        else:
            mod.fetch(dl, hde_ttl=out_dir / "hde.ttl", numbers=nums)
    if a.stage in ("transform", "all"):
        out = out_dir / f"{a.name}.ttl"
        if a.name == "k510_predicates":
            st = mod.transform(dl, out, known_dir=out_dir, workers=a.workers, numbers=nums)
        else:
            st = mod.transform(dl, out, hde_ttl=out_dir / "hde.ttl")
        print(json.dumps({"records": st.records, "triples": st.triples, "out": str(out)}))
    print(f"[{a.name}] {a.stage} finished in {round(time.time() - t)} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
