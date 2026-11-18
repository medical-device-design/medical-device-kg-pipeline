#!/usr/bin/env python3
"""src_run.py -- fetch and/or transform ONE source into a dated snapshot (scheduled jobs, scripts/schedule/).

Same as the runner of the v0.0.7 snapshot refresh of 2026-11-04 (work/out-2026-11-04/src_run.py), with the
worktree taken from MDKG_WT (default work/v007). Environment: V7C_DL (downloads root), V7C_OUT (output dir), V7C_RIOT (0 = skip riot).

    V7C_DL=<downloads root> V7C_OUT=<out dir> python3 -I src_run.py <source> <fetch|transform|all>

Calls the module's own fetch() and transform() (the same contract run.py uses), with the few keyword
arguments some modules need so that every input comes from the same snapshot:
  fda_tplc.fetch(codes_ttl=<out>/classification.ttl)
  safety_comms.fetch(archive=False), shortages.fetch(archive=False)  -- archive parts are read from the
      seeded cache (archive.json + archive/), never refetched
  device_eua: fetch() then fetch_letters() (cached letters are not refetched)
  dd_import_refusals.transform(classification_ttl=<out>/classification.ttl)
  dd_compliance_actions.transform(wl_ttl=<out>/warning_letters.ttl)
  dd_*: downloads under <downloads root>/fda_dashboard/<ENDPOINT> (layout of the earlier runs)
After transform, riot --validate and riot --count run on the output and its _text.ttl (JVM 2 GB, no
credentials in the environment), unless V7C_RIOT=0 (MAUDE / UDI: checked by the refresh script instead).
A one-line JSON summary goes to <out>/.status/<source>.json.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WT = os.environ.get("MDKG_WT", "/srv/medical-device-kg/work/v007")
sys.path[:0] = [WT + "/pipeline", WT + "/pipeline/sources"]
import base      # noqa: E402
import licence   # noqa: E402

RIOT = "/srv/medical-device-kg/tools/apache-jena-5.2.0/bin/riot"
name, stage = sys.argv[1], sys.argv[2]
DL, OUT = Path(os.environ["V7C_DL"]), Path(os.environ["V7C_OUT"])
row = licence.check(name, licence.load(), allow_restricted=False)
if row["open_release"].strip().lower() != "yes":
    print(f"[{name}] not an open-release source; stop")
    sys.exit(3)
mod = base.load_source(name)
dl = DL / "fda_dashboard" / mod.ENDPOINT if name.startswith("dd_") else DL / name
out = OUT / f"{name}.ttl"
FETCH_KW = {"fda_tplc": {"codes_ttl": str(OUT / "classification.ttl")},
            "safety_comms": {"archive": False},
            "shortages": {"archive": False}}
TRANSFORM_KW = {"dd_import_refusals": {"classification_ttl": str(OUT / "classification.ttl")},
                "dd_compliance_actions": {"wl_ttl": str(OUT / "warning_letters.ttl")}}
summary = {"source": name, "dataset": mod.DATASET, "licence": row["licence"], "downloads": str(dl)}
print(f"== {name}  ({mod.DATASET})  licence: {row['licence']}  stage {stage}", flush=True)

if stage in ("fetch", "all"):
    dl.mkdir(parents=True, exist_ok=True)
    t = time.time()
    man = mod.fetch(dl, **FETCH_KW.get(name, {}))
    if name == "device_eua":
        mod.fetch_letters(dl)
    if man is not None:
        man.write(dl / "manifest.json")
        summary["files"], summary["retrieved_at"] = len(man.files), man.retrieved_at
    summary["fetch_s"] = round(time.time() - t)
    print(f"[{name}] fetch done in {summary['fetch_s']} s", flush=True)

if stage in ("transform", "all"):
    t = time.time()
    st = mod.transform(dl, out, **TRANSFORM_KW.get(name, {}))
    summary.update(records=st.records, triples=st.triples, skipped=st.skipped, out=str(out),
                   transform_s=round(time.time() - t))
    print(f"[{name}] transform: {st.records} records -> {st.triples} triples ({st.skipped} skipped) -> {out}",
          flush=True)
    if os.environ.get("V7C_RIOT", "1") == "1":
        env = {k: v for k, v in os.environ.items() if not k.startswith("FDA_DD_")}
        env["JVM_ARGS"] = "-Xmx2G"
        for f in (out, out.with_name(out.stem + "_text.ttl")):
            if not f.exists():
                continue
            r = subprocess.run([RIOT, "--validate", str(f)], capture_output=True, text=True, env=env)
            c = subprocess.run([RIOT, "--count", str(f)], capture_output=True, text=True, env=env)
            msg = (r.stderr or r.stdout).strip()
            summary.setdefault("riot", {})[f.name] = {"rc": r.returncode, "messages": len(msg.splitlines()),
                                                      "count": (c.stderr + c.stdout).strip()[-120:]}
            print(f"[{name}] riot --validate {f.name}: rc={r.returncode} {msg[:1500]}", flush=True)
            print(f"[{name}] {(c.stderr + c.stdout).strip()[-200:]}", flush=True)
            if r.returncode != 0:
                (OUT / ".status").mkdir(exist_ok=True)
                (OUT / ".status" / f"{name}.json").write_text(json.dumps(summary), encoding="utf-8")
                sys.exit(4)

(OUT / ".status").mkdir(exist_ok=True)
(OUT / ".status" / f"{name}.json").write_text(json.dumps(summary), encoding="utf-8")
print(f"[{name}] DONE {json.dumps(summary)[:600]}", flush=True)
