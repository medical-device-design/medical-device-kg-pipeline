#!/usr/bin/env python3
"""compare_snapshots.py -- change detection between two dated fetches (scheduled weekly job).

    python3 -I compare_snapshots.py <new weekly dir> <previous weekly dir or snapshot out dir> <report.md> [--sources a,b,...]

For each source it compares
  * records / triples / skipped from the transform summary (<out>/.status/<source>.json, written by src_run.py);
  * the downloaded files: count, bytes and SHA-256 from <downloads>/<source>/manifest.json (files whose
    checksum changed, appeared or disappeared);
  * for openFDA endpoints, export_date and total_records from api.fda.gov/download.json (saved by the weekly
    job as openfda_index.json in each weekly dir).
A weekly dir has downloads/ and out/; a release snapshot dir (work/out-YYYY-MM-DD) is accepted as the baseline
(its .status/*.json; downloads looked up in work/downloads-YYYY-MM-DD when present).
Writes a markdown table and a JSON twin (<report>.json). Exit 0 always (it reports, it does not judge).
"""
import json
import sys
from pathlib import Path

OPENFDA = {"event": "event", "recalls": "enforcement", "recall_db": "recall", "k510": "510k", "pma": "pma",
           "registrationlisting": "registrationlisting", "udi": "udi", "classification": "classification"}


def dirs(root):
    root = Path(root)
    if (root / "out").is_dir():
        return root / "downloads", root / "out", root / "openfda_index.json"
    dl = root.parent / root.name.replace("out-", "downloads-")
    return dl, root, root / "openfda_index.json"


def status(out, s):
    p = out / ".status" / f"{s}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def manifest(dl, s):
    p = dl / s / "manifest.json"
    if s.startswith("dd_"):
        cands = list((dl / "fda_dashboard").glob("*/manifest.json"))
        p = next((c for c in cands if s.replace("dd_", "") in str(c).lower()), p)
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
        return {f["path"]: (f.get("sha256", ""), f.get("bytes", 0)) for f in m.get("files", [])}, m.get("retrieved_at")
    except Exception:
        return None, None


def openfda(idx, s):
    try:
        d = json.loads(Path(idx).read_text(encoding="utf-8"))
        e = d["results"]["device"][OPENFDA[s]]
        return e.get("export_date"), e.get("total_records")
    except Exception:
        return None, None


def main(a):
    new, old, rep = a[0], a[1], Path(a[2])
    srcs = None
    if len(a) > 4 and a[3] == "--sources":
        srcs = [x for x in a[4].split(",") if x]
    ndl, nout, nidx = dirs(new)
    odl, oout, oidx = dirs(old)
    if srcs is None:
        srcs = sorted(p.stem for p in (nout / ".status").glob("*.json"))
    rows, js = [], {}
    for s in srcs:
        ns, os_ = status(nout, s), status(oout, s)
        nm, nret = manifest(ndl, s)
        om, oret = manifest(odl, s)
        r = {"records_new": ns.get("records"), "records_old": os_.get("records"),
             "triples_new": ns.get("triples"), "triples_old": os_.get("triples"), "retrieved_new": nret, "retrieved_old": oret}
        if nm is not None and om is not None:
            r["files_changed"] = sorted(k for k in nm if k in om and nm[k][0] != om[k][0])
            r["files_added"] = sorted(set(nm) - set(om))
            r["files_removed"] = sorted(set(om) - set(nm))
            r["bytes_new"], r["bytes_old"] = sum(v[1] for v in nm.values()), sum(v[1] for v in om.values())
        if s in OPENFDA:
            r["openfda_new"], r["openfda_old"] = openfda(nidx, s), openfda(oidx, s)
        if r["records_new"] is not None and r["records_old"] is not None:
            r["records_delta"] = r["records_new"] - r["records_old"]
        changed = (r.get("records_delta") not in (None, 0) or r.get("files_changed") or r.get("files_added")
                   or r.get("files_removed") or (r.get("openfda_new") and r.get("openfda_new") != r.get("openfda_old")))
        r["changed"] = bool(changed)
        js[s] = r
        delta = r.get("records_delta")
        files = ("" if nm is None or om is None else
                 f"{len(r['files_changed'])} changed, {len(r['files_added'])} new, {len(r['files_removed'])} gone")
        ofda = (f"{r['openfda_old'][0]} -> {r['openfda_new'][0]} ({r['openfda_new'][1]} records)"
                if r.get("openfda_new") and r.get("openfda_old") else (str(r.get("openfda_new") or "")))
        rows.append(f"| {s} | {'yes' if changed else 'no'} | {r['records_old'] if r['records_old'] is not None else '-'} | "
                    f"{r['records_new'] if r['records_new'] is not None else '-'} | {'' if delta is None else f'{delta:+d}'} | "
                    f"{files} | {ofda} |")
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"## Changes: {Path(new).name} vs {Path(old).name}\n\n")
        f.write("| source | changed | records before | records now | delta | downloaded files (SHA-256) | openFDA export date |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        f.write("\n".join(rows) + "\n")
    Path(str(rep) + ".json").write_text(json.dumps(js, indent=1, default=str), encoding="utf-8")
    print(f"compared {len(srcs)} sources; changed: {sum(1 for v in js.values() if v['changed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
