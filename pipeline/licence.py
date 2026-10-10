#!/usr/bin/env python3
"""
licence.py -- the per-source licence register that GATES ingest (action plan A2).

Rule: no triple is written for a source until licence_register.csv (repo root)
has a row for it. Rows with open_release=yes may go into the public graph;
open_release=no sources may only be built into a separate, non-public output
(run.py --restricted-out) and are never merged into the open release.

    python pipeline/licence.py            # self-test + print the register
"""
import csv
import os
from pathlib import Path

REGISTER = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "licence_register.csv"
REQUIRED = ("source", "dataset", "licence", "licence_url", "open_release")


class LicenceError(RuntimeError):
    pass


def load(path=REGISTER):
    path = Path(path)
    if not path.exists():
        raise LicenceError(f"licence register missing: {path}")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    reg = {}
    for i, r in enumerate(rows, start=2):
        missing = [k for k in REQUIRED if not (r.get(k) or "").strip()]
        if missing:
            raise LicenceError(f"{path.name} line {i}: missing {missing}")
        flag = r["open_release"].strip().lower()
        if flag not in ("yes", "no"):
            raise LicenceError(f"{path.name} line {i}: open_release must be yes/no")
        reg[r["source"].strip()] = r
    return reg


def check(source, register=None, allow_restricted=False):
    """Return the register row, or raise. allow_restricted lets a licensed
    overlay build into the non-public output only."""
    reg = register if register is not None else load()
    row = reg.get(source)
    if row is None:
        raise LicenceError(f"source '{source}' has no licence_register.csv row; "
                           "add one (with licence + URL) before ingesting")
    if row["open_release"].strip().lower() != "yes" and not allow_restricted:
        raise LicenceError(f"source '{source}' is not cleared for the open release "
                           f"({row['licence']}); build it with --restricted-out")
    return row


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "r.csv"
        p.write_text("source,dataset,licence,licence_url,open_release\n"
                     "k510,device/510k,openFDA,https://open.fda.gov/license/,yes\n"
                     "cpt,AMA,AMA,https://ama-assn.org,no\n", encoding="utf-8")
        reg = load(p)
        assert check("k510", reg)["dataset"] == "device/510k"
        for bad in ("nosuch", "cpt"):
            try:
                check(bad, reg)
            except LicenceError:
                pass
            else:
                raise AssertionError(bad)
        assert check("cpt", reg, allow_restricted=True)
    real = load()
    print(f"licence.py self-test PASSED. register: {len(real)} sources, "
          f"{sum(r['open_release'] == 'yes' for r in real.values())} open.")


if __name__ == "__main__":
    _selftest()
