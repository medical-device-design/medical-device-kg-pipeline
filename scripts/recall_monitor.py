#!/usr/bin/env python3
"""
recall_monitor.py -- weekly completeness and change report for the recall layer.

Reads the three recall TTL files (recalls.ttl, recall_db.ttl, recall_class.ttl) and
FDA's live openFDA totals, then writes into <monitor_dir>/<date>/:

    report.md        human-readable summary (what changed, what is missing)
    report.json      the same numbers, machine-readable
    recalls.tsv      one row per recall: number, class, class source, initiated, status
    new_recalls.tsv  recalls that were not in the previous run
    class_changes.tsv recalls whose class changed since the previous run

and appends one line to <monitor_dir>/history.csv. Nothing is published.

Checks (each becomes a WARN line in the report):
  * built counts lower than FDA's openFDA totals
  * recalls still without a class
  * implausible initiation dates (before 1976, when device recall authority began,
    or after the run date) -- FDA data contains at least one year-0010 typo
  * riot/SHACL results passed in by the shell wrapper (--validation file)

    python scripts/recall_monitor.py --out <ttl dir> --monitor <dir> [--validation v.txt]
"""
import os
import re
import csv
import sys
import json
import argparse
import datetime as dt
import urllib.request
from pathlib import Path

SUBJ = re.compile(r"^<([^>]+)>\s*$")
PRED = re.compile(r'^\s+(ex:[A-Za-z]+)\s+"((?:[^"\\]|\\.)*)"')


def read_blocks(ttl, wanted):
    """{subject: {pred: [values]}} for the predicates in `wanted`, streaming the file."""
    out, cur = {}, None
    if not Path(ttl).exists():
        return out
    with open(ttl, encoding="utf-8") as fh:
        for line in fh:
            m = SUBJ.match(line)
            if m:
                cur = m.group(1)
                continue
            if cur is None:
                continue
            p = PRED.match(line)
            if p and p.group(1) in wanted:
                out.setdefault(cur, {}).setdefault(p.group(1), []).append(p.group(2))
    return out


def fda_totals():
    t = {}
    for ds in ("recall", "enforcement"):
        try:
            m = json.loads(urllib.request.urlopen(
                f"https://api.fda.gov/device/{ds}.json?limit=1", timeout=60).read())["meta"]
            t[ds] = {"total": m["results"]["total"], "last_updated": m["last_updated"]}
        except Exception as e:                              # report, never crash the monitor
            t[ds] = {"error": str(e)}
    return t


def build_table(out_dir):
    want = {"ex:recallNumber", "ex:recallClassification", "ex:recallClassificationSource",
            "ex:recallInitiationDate", "ex:recallStatus", "ex:cfresStatus", "ex:sourceDataset"}
    rows = {}
    for name in ("recall_db.ttl", "recalls.ttl", "recall_class.ttl"):
        for subj, preds in read_blocks(Path(out_dir) / name, want).items():
            if "/id/recall/" not in subj:
                continue
            r = rows.setdefault(subj, {"number": None, "class": None, "class_source": None,
                                       "initiated": None, "status": None, "sources": set()})
            # reports FDA posted before numbering have no recallNumber: key on the IRI tail
            r["number"] = r["number"] or (preds.get("ex:recallNumber") or [None])[0] or subj.rsplit("/", 1)[-1]
            if preds.get("ex:recallClassification"):
                src = (preds.get("ex:recallClassificationSource") or ["enforcement report"])[0]
                if r["class"] is None or src == "enforcement report":   # enforcement wins on conflict
                    r["class"], r["class_source"] = preds["ex:recallClassification"][0], src
            r["initiated"] = r["initiated"] or (preds.get("ex:recallInitiationDate") or [None])[0]
            r["status"] = r["status"] or (preds.get("ex:recallStatus") or preds.get("ex:cfresStatus") or [None])[0]
            r["sources"].add(name.replace(".ttl", ""))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="directory with recalls.ttl, recall_db.ttl, recall_class.ttl")
    ap.add_argument("--monitor", required=True, help="monitor root (dated run folders + history.csv)")
    ap.add_argument("--validation", help="text file with riot/SHACL results from the wrapper")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args(argv)

    root = Path(a.monitor)
    run = root / a.date
    run.mkdir(parents=True, exist_ok=True)
    rows = build_table(a.out)
    fda = fda_totals()

    by_number = {r["number"]: r for r in rows.values() if r["number"]}
    n_db = sum(1 for r in by_number.values() if "recall_db" in r["sources"])
    n_enf = sum(1 for r in by_number.values() if "recalls" in r["sources"])
    classified = {k: r for k, r in by_number.items() if r["class"]}
    by_class, by_source = {}, {}
    for r in classified.values():
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
        by_source[r["class_source"]] = by_source.get(r["class_source"], 0) + 1
    unclassified = sorted(k for k, r in by_number.items() if not r["class"])
    today = dt.date.fromisoformat(a.date)
    bad_dates = sorted((k, r["initiated"]) for k, r in by_number.items() if r["initiated"]
                       and re.match(r"^\d{4}-", r["initiated"])
                       and not (1976 <= int(r["initiated"][:4]) <= today.year))

    # compare with the previous run
    prev_runs = sorted(p for p in root.iterdir() if p.is_dir() and p.name < a.date and (p / "recalls.tsv").exists())
    prev = {}
    if prev_runs:
        with open(prev_runs[-1] / "recalls.tsv", encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                prev[row["recall_number"]] = row
    new = sorted(k for k in by_number if prev and k not in prev)
    changed = sorted(k for k in by_number if k in prev and (prev[k]["class"] or "") != (by_number[k]["class"] or ""))

    with open(run / "recalls.tsv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["recall_number", "class", "class_source", "initiated", "status", "sources"])
        for k in sorted(by_number):
            r = by_number[k]
            w.writerow([k, r["class"] or "", r["class_source"] or "", r["initiated"] or "",
                        r["status"] or "", "+".join(sorted(r["sources"]))])
    with open(run / "new_recalls.tsv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["recall_number", "class", "initiated", "status"])
        for k in new:
            r = by_number[k]
            w.writerow([k, r["class"] or "", r["initiated"] or "", r["status"] or ""])
    with open(run / "class_changes.tsv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["recall_number", "previous_class", "class"])
        for k in changed:
            w.writerow([k, prev[k]["class"], by_number[k]["class"] or ""])

    warns = []
    if "total" in fda.get("recall", {}) and n_db < fda["recall"]["total"]:
        warns.append(f"recall database: built {n_db:,} < FDA {fda['recall']['total']:,}")
    if "total" in fda.get("enforcement", {}) and n_enf < fda["enforcement"]["total"]:
        warns.append(f"enforcement reports: built {n_enf:,} < FDA {fda['enforcement']['total']:,}")
    if unclassified:
        warns.append(f"{len(unclassified):,} recalls still have no class (FDA may not have classified them yet)")
    if bad_dates:
        warns.append(f"{len(bad_dates)} implausible initiation dates: "
                     + ", ".join(f"{k} {d}" for k, d in bad_dates[:5]))
    for k in fda:
        if "error" in fda[k]:
            warns.append(f"openFDA {k} total unavailable: {fda[k]['error']}")
    validation = Path(a.validation).read_text(encoding="utf-8") if a.validation and Path(a.validation).exists() else ""
    if re.search(r"FAIL|violations=[1-9]", validation):
        warns.append("validation failures -- see the Validation section")

    rep = {"date": a.date, "fda": fda, "recalls_in_graph": len(by_number),
           "in_recall_database": n_db, "in_enforcement_reports": n_enf,
           "classified": len(classified), "unclassified": len(unclassified),
           "by_class": by_class, "class_source": by_source,
           "new_since_previous_run": len(new), "class_changes": len(changed),
           "previous_run": prev_runs[-1].name if prev_runs else None,
           "implausible_dates": bad_dates, "warnings": warns}
    (run / "report.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")

    md = [f"# Recall monitor, {a.date}", "",
          f"Status: **{'OK' if not warns else 'ATTENTION'}**", "",
          "| | FDA (openFDA) | In the graph |", "|---|---|---|",
          f"| Medical Device Recall database | {fda.get('recall', {}).get('total', 'n/a'):,} (updated {fda.get('recall', {}).get('last_updated', '?')}) | {n_db:,} |"
          if "total" in fda.get("recall", {}) else f"| Medical Device Recall database | n/a | {n_db:,} |",
          f"| Enforcement reports | {fda.get('enforcement', {}).get('total', 'n/a'):,} (updated {fda.get('enforcement', {}).get('last_updated', '?')}) | {n_enf:,} |"
          if "total" in fda.get("enforcement", {}) else f"| Enforcement reports | n/a | {n_enf:,} |",
          f"| **Distinct recalls** | | **{len(by_number):,}** |", "",
          f"Recall class known for {len(classified):,} ({100 * len(classified) / max(1, len(by_number)):.1f}%): "
          + ", ".join(f"{c} {n:,}" for c, n in sorted(by_class.items())) + ".",
          "Source of the class: " + ", ".join(f"{s} {n:,}" for s, n in sorted(by_source.items())) + ".", "",
          f"Since the previous run ({rep['previous_run'] or 'none'}): **{len(new):,} new recalls**, "
          f"{len(changed):,} class changes (lists in new_recalls.tsv and class_changes.tsv).", ""]
    md += ["## Warnings", ""] + ([f"- {x}" for x in warns] or ["- none"]) + [""]
    if validation:
        md += ["## Validation", "", "```", validation.strip(), "```", ""]
    (run / "report.md").write_text("\n".join(md), encoding="utf-8")

    hist = root / "history.csv"
    first = not hist.exists()
    with open(hist, "a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if first:
            w.writerow(["date", "fda_recall_total", "fda_enforcement_total", "recalls_in_graph",
                        "classified", "unclassified", "new", "class_changes", "warnings"])
        w.writerow([a.date, fda.get("recall", {}).get("total", ""), fda.get("enforcement", {}).get("total", ""),
                    len(by_number), len(classified), len(unclassified), len(new), len(changed), len(warns)])
    print((run / "report.md").read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
