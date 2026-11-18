#!/usr/bin/env python3
"""
tplc.py -- Total Product Life Cycle aggregates, REBUILT from the full MAUDE bulk
(openFDA device/event) instead of scraped from the FDA TPLC web pages.

WHY
---
FDA publishes no bulk TPLC file. The legacy graph scraped the TPLC pages
(7,034 TPLCRecord, 52,823 DeviceProblem, 46,041 PatientOutcome, 12,904
MDRStatistic nodes), which cannot be refreshed reproducibly. TPLC's adverse-event
panels are tallies of MDR reports per product code, so they can be recomputed
exactly from MAUDE, all years, every product code, on every release.

WHAT IT EMITS (per product code that appears in MAUDE)
------------------------------------------------------
  ex:TPLCRecord        iri.tplc(pc)        report totals by event type, first/last
                                           report date, count basis, provenance
  ex:DeviceProblem     iri.tplc_device_problem(pc, problem)   + ex:reportCount
  ex:PatientOutcome    iri.tplc_patient_problem(pc, problem)  ex:outcomeType "patient problem"
                       iri.tplc_outcome(pc, outcome)          ex:outcomeType "outcome"
  ex:MDRStatistic      iri.tplc_year(pc, year)  reports received that year, by event type
  ex:ProductCode       ex:relatedTPLC -> the TPLC node (hub link)

Recalls are NOT duplicated here: they already reach the same ProductCode hub
from recalls.py / recall_db.py, so "TPLC + recalls" is one hop through the hub.

COUNTING RULES (stated on every TPLC node as ex:countBasis)
  * the unit is one MDR report as published in openFDA device/event;
  * a report is counted once per distinct product code it names (a report naming
    two devices with the same code counts once; with two codes, once for each);
  * a problem/outcome is counted once per report;
  * the year is the year FDA RECEIVED the report (date_received).
  MAUDE is passive surveillance: counts are not rates and do not establish cause.

PERFORMANCE
  26 M reports / ~18 GB zipped. Partitions are aggregated in parallel worker
  processes (one record in memory at a time per worker, ijson), then merged.
  Only the tallies are held (a few hundred thousand keys), never the reports.
"""
import os
import sys
import json
import shutil
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "tplc"
DATASET = "device/event"          # aggregated; ex:sourceDataset says so explicitly
OUT_DATASET = "device/event (aggregated to TPLC)"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/event/"
COUNT_BASIS = ("MDR reports received (openFDA device/event, all years), counted once per "
               "report per product code; problems and outcomes once per report. Passive "
               "surveillance: not a failure rate, causation not established.")
_EVENT_TYPES = {"death": "death", "injury": "injury", "malfunction": "malfunction"}


# ===========================================================================
def fetch(dest):
    """MAUDE bulk partitions. If the event source was already fetched next to us
    (downloads/event), link to those files instead of downloading 18 GB twice."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    sib = dest.parent / "event"
    if (sib / "manifest.json").exists():
        man = base.Manifest.read(sib / "manifest.json")
        for f in man.files:
            link = dest / Path(f.path).name
            if not link.exists():
                try:
                    os.symlink((sib / f.path).resolve(), link)
                except OSError:
                    shutil.copy2(sib / f.path, link)
        return base.Manifest(source=NAME, dataset=DATASET,
                             retrieved_at=man.retrieved_at, files=man.files)
    import event                     # same partitions, same collision-safe file names
    man = event.fetch(dest)
    man.source = NAME
    return man


# ===========================================================================
#  Aggregation (runs inside worker processes; must be importable/picklable)
# ===========================================================================
def _iter_file(path):
    """Records of ONE openFDA partition (.json or .json.zip), streamed."""
    import zipfile
    try:
        import ijson
    except ImportError:
        ijson = None

    def _from(fh):
        if ijson is not None:
            yield from ijson.items(fh, "results.item", use_float=True)
        else:
            yield from json.load(fh).get("results", [])

    p = Path(path)
    if p.suffix == ".zip":
        with zipfile.ZipFile(p) as z:
            for n in sorted(z.namelist()):
                if n.endswith(".json"):
                    with z.open(n) as fh:
                        yield from _from(fh)
    else:
        with open(p, "rb") as fh:
            yield from _from(fh)


def _clean(v):
    s = " ".join(str(v or "").split())
    return s or None


def _new_tally():
    return {
        "reports": Counter(),        # pc -> n
        "etype": Counter(),          # (pc, etype) -> n
        "devprob": Counter(),        # (pc, problem) -> n
        "patprob": Counter(),        # (pc, problem) -> n
        "outcome": Counter(),        # (pc, outcome) -> n
        "year": Counter(),           # (pc, yyyy) -> n
        "year_etype": Counter(),     # (pc, yyyy, etype) -> n
        "first": {}, "last": {},     # pc -> yyyymmdd
        "label": {},                 # pc -> a device name seen in MAUDE (fallback label)
        "n": 0, "no_pc": 0,
    }


def aggregate_records(records, t=None):
    """Fold an iterable of MAUDE records into a tally dict."""
    t = t or _new_tally()
    for r in records:
        t["n"] += 1
        pcs = {}
        for dev in (r.get("device") or []):
            of = dev.get("openfda") or {}
            # only real three-letter FDA codes; reporter-typed noise ("269", "N/A") is
            # not minted as a product code (query test 2026-10-11 found 48 such nodes)
            pc = common.product_code(dev.get("device_report_product_code"))
            if not pc:
                pc = common.product_code((common.as_list(of.get("product_code")) or [None])[0])
            if not pc:
                continue
            pcs.setdefault(pc, _clean((common.as_list(of.get("device_name")) or [None])[0])
                           or _clean(dev.get("generic_name")))
        if not pcs:
            t["no_pc"] += 1
            continue
        et = _EVENT_TYPES.get((_clean(r.get("event_type")) or "").lower(), "other")
        dr = "".join(ch for ch in str(r.get("date_received") or "") if ch.isdigit())[:8]
        yr = dr[:4] if len(dr) == 8 else None
        dprobs = {p for p in (_clean(x) for x in common.as_list(r.get("product_problems"))) if p}
        pprobs, outs = set(), set()
        for pt in (r.get("patient") or []):
            pprobs |= {p for p in (_clean(x) for x in common.as_list(pt.get("patient_problems"))) if p}
            # older reports carry one-letter outcome codes; map them to FDA's words
            outs |= {o for o in (common.outcome_label(_clean(x)) for x in common.as_list(pt.get("sequence_number_outcome"))) if o}
        for pc, name in pcs.items():
            t["reports"][pc] += 1
            t["etype"][(pc, et)] += 1
            if name and pc not in t["label"]:
                t["label"][pc] = name
            if yr:
                t["year"][(pc, yr)] += 1
                t["year_etype"][(pc, yr, et)] += 1
                if dr < t["first"].get(pc, "99999999"):
                    t["first"][pc] = dr
                if dr > t["last"].get(pc, "00000000"):
                    t["last"][pc] = dr
            for p in dprobs:
                t["devprob"][(pc, p)] += 1
            for p in pprobs:
                t["patprob"][(pc, p)] += 1
            for o in outs:
                t["outcome"][(pc, o)] += 1
    return t


def _aggregate_file(path):
    return str(path), aggregate_records(_iter_file(path))


def merge(a, b):
    for k in ("reports", "etype", "devprob", "patprob", "outcome", "year", "year_etype"):
        a[k].update(b[k])
    for pc, d in b["first"].items():
        if d < a["first"].get(pc, "99999999"):
            a["first"][pc] = d
    for pc, d in b["last"].items():
        if d > a["last"].get(pc, "00000000"):
            a["last"][pc] = d
    for pc, nm in b["label"].items():
        a["label"].setdefault(pc, nm)
    a["n"] += b["n"]
    a["no_pc"] += b["no_pc"]
    return a


def _partition_files(src):
    src = Path(src)
    return sorted(p for p in set(src.glob("*.json")) | set(src.glob("*.zip"))
                  if p.name != "manifest.json")


def aggregate_dir(src, workers=None, log=print):
    files = _partition_files(src)
    workers = workers or int(os.environ.get("MDKG_WORKERS", "0")) or max(1, min(3, (os.cpu_count() or 2) - 1))
    total = _new_tally()
    if workers <= 1 or len(files) <= 1:
        for i, f in enumerate(files, 1):
            merge(total, _aggregate_file(f)[1])
            log(f"  [{i}/{len(files)}] {f.name}  reports so far {total['n']:,}")
        return total
    import multiprocessing as mp
    with mp.get_context("fork" if hasattr(os, "fork") else "spawn").Pool(workers) as pool:
        for i, (name, t) in enumerate(pool.imap_unordered(_aggregate_file, files), 1):
            merge(total, t)
            log(f"  [{i}/{len(files)}] {Path(name).name}  reports so far {total['n']:,}")
    return total


# ===========================================================================
#  Writing
# ===========================================================================
def _group_by_iri(counter, minter):
    """{(pc, value): n} -> {iri: [pc, first raw value, n]}, merging values whose
    slug collides (e.g. case or punctuation variants) so no IRI is written twice."""
    out = {}
    for (pc, val), n in counter.items():
        try:
            node = minter(pc, val)
        except ValueError:
            continue
        if node in out:
            out[node][2] += n
            out[node][1] = min(out[node][1], val)     # deterministic label
        else:
            out[node] = [pc, val, n]
    return out


def write(t, out, retrieved_at=None):
    st = base.Stats(source=NAME, records=len(t["reports"]), skipped=t["no_pc"])
    by_pc = defaultdict(lambda: {"dp": [], "pp": [], "oc": [], "yr": []})
    dps = _group_by_iri(t["devprob"], iri.tplc_device_problem)
    pps = _group_by_iri(t["patprob"], iri.tplc_patient_problem)
    ocs = _group_by_iri(t["outcome"], iri.tplc_outcome)
    for node, (pc, _, _) in dps.items():
        by_pc[pc]["dp"].append(node)
    for node, (pc, _, _) in pps.items():
        by_pc[pc]["pp"].append(node)
    for node, (pc, _, _) in ocs.items():
        by_pc[pc]["oc"].append(node)
    for (pc, yr) in t["year"]:
        by_pc[pc]["yr"].append(yr)

    with base.TurtleWriter(out) as w:
        for pc in sorted(t["reports"]):
            node = iri.tplc(pc)
            g = by_pc[pc]
            pairs = [
                ("a", "ex:TPLCRecord"),
                ("rdfs:label", common.lit(f"TPLC {pc}")),
                ("ex:productCode", common.lit(pc)),
                ("ex:hasProductCode", f"<{iri.product_code(pc)}>"),
                ("ex:reportCount", common.int_lit(t["reports"][pc])),
                ("ex:deathReportCount", common.int_lit(t["etype"].get((pc, "death"), 0))),
                ("ex:injuryReportCount", common.int_lit(t["etype"].get((pc, "injury"), 0))),
                ("ex:malfunctionReportCount", common.int_lit(t["etype"].get((pc, "malfunction"), 0))),
                ("ex:otherReportCount", common.int_lit(t["etype"].get((pc, "other"), 0))),
                ("ex:firstReportDate", common.date_lit(t["first"].get(pc))),
                ("ex:lastReportDate", common.date_lit(t["last"].get(pc))),
                ("ex:countBasis", common.lit(COUNT_BASIS)),
            ]
            pairs += [("ex:hasDeviceProblem", f"<{n}>") for n in sorted(g["dp"])]
            pairs += [("ex:hasPatientOutcome", f"<{n}>") for n in sorted(g["pp"] + g["oc"])]
            pairs += [("ex:hasMDRStatistic", f"<{iri.tplc_year(pc, y)}>") for y in sorted(g["yr"])]
            pairs += common.provenance(OUT_DATASET, pc, source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(node, pairs)
            w.block(iri.product_code(pc), [
                ("a", "ex:ProductCode"),
                ("ex:productCode", common.lit(pc)),
                ("skos:altLabel", common.lit(t["label"].get(pc))),   # rdfs:label comes from classification
                ("ex:relatedTPLC", f"<{node}>"),
            ])

        for node in sorted(dps):
            pc, val, n = dps[node]
            w.block(node, [("a", "ex:DeviceProblem"), ("rdfs:label", common.lit(val)),
                           ("ex:deviceProblem", common.lit(val)),
                           ("ex:reportCount", common.int_lit(n)),
                           ("ex:relatedTPLC", f"<{iri.tplc(pc)}>")])
        for node in sorted(pps):
            pc, val, n = pps[node]
            w.block(node, [("a", "ex:PatientOutcome"), ("rdfs:label", common.lit(val)),
                           ("ex:patientProblem", common.lit(val)),
                           ("ex:outcomeType", common.lit("patient problem")),
                           ("ex:reportCount", common.int_lit(n)),
                           ("ex:relatedTPLC", f"<{iri.tplc(pc)}>")])
        for node in sorted(ocs):
            pc, val, n = ocs[node]
            w.block(node, [("a", "ex:PatientOutcome"), ("rdfs:label", common.lit(val)),
                           ("ex:patientOutcome", common.lit(val)),
                           ("ex:outcomeType", common.lit("outcome")),
                           ("ex:reportCount", common.int_lit(n)),
                           ("ex:relatedTPLC", f"<{iri.tplc(pc)}>")])
        for (pc, yr) in sorted(t["year"]):
            w.block(iri.tplc_year(pc, yr), [
                ("a", "ex:MDRStatistic"),
                ("rdfs:label", common.lit(f"{pc} MDR reports {yr}")),
                ("ex:year", common.gyear_lit(yr)),
                ("ex:reportCount", common.int_lit(t["year"][(pc, yr)])),
                ("ex:deathReportCount", common.int_lit(t["year_etype"].get((pc, yr, "death"), 0))),
                ("ex:injuryReportCount", common.int_lit(t["year_etype"].get((pc, yr, "injury"), 0))),
                ("ex:malfunctionReportCount", common.int_lit(t["year_etype"].get((pc, yr, "malfunction"), 0))),
                ("ex:otherReportCount", common.int_lit(t["year_etype"].get((pc, yr, "other"), 0))),
                ("ex:relatedTPLC", f"<{iri.tplc(pc)}>"),
            ])
    st.triples = w.triples
    return st


def transform(src, out, retrieved_at=None, workers=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    t = aggregate_dir(src, workers=workers)
    print(f"  aggregated {t['n']:,} MDR reports; {len(t['reports']):,} product codes; "
          f"{t['no_pc']:,} reports with no product code (not tallied)")
    # keep the raw tallies next to the TTL: cheap to re-write, expensive to recompute
    try:
        import pickle
        with open(Path(out).with_suffix(".tally.pkl"), "wb") as fh:
            pickle.dump({k: (dict(v) if isinstance(v, Counter) else v) for k, v in t.items()}, fh)
    except Exception as e:      # noqa: BLE001
        print(f"  (tally cache not written: {e})")
    return write(t, out, retrieved_at=retrieved_at)


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    recs = [
        {"report_number": "1-2020-1", "event_type": "Injury", "date_received": "20200310",
         "product_problems": ["Battery Problem", "Overheating"],
         "patient": [{"sequence_number_outcome": ["Hospitalization"], "patient_problems": ["Burn"]}],
         "device": [{"device_report_product_code": "DZE", "openfda": {"device_name": ["Defibrillator"]}},
                    {"device_report_product_code": "DZE"}]},                      # same code twice -> once
        {"report_number": "1-2021-2", "event_type": "Malfunction", "date_received": "20210105",
         "product_problems": ["Battery Problem"],
         "device": [{"device_report_product_code": "DZE"}, {"device_report_product_code": "frn"}]},
        {"report_number": "1-2021-3", "event_type": "Death", "date_received": "20211231",
         "product_problems": ["battery problem"],                                 # slug-collides -> merged
         "patient": [{"sequence_number_outcome": ["Death"]}, {"sequence_number_outcome": ["H"]}],  # letter code
         "device": [{"openfda": {"product_code": ["DZE"]}}]},
        {"report_number": "1-2022-4", "event_type": "Malfunction", "date_received": "20220101",
         "device": [{"generic_name": "no code"}, {"device_report_product_code": "269"}]},  # no valid code
    ]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "a.json").write_text(json.dumps({"results": recs[:2]}), encoding="utf-8")
        (d / "b.json").write_text(json.dumps({"results": recs[2:]}), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-09", files=[]).write(d / "manifest.json")
        out = d / "tplc.ttl"
        st = transform(d, out, workers=1)
        text = out.read_text(encoding="utf-8")
        st2 = transform(d, d / "tplc2.ttl", workers=2)                           # parallel == serial
        assert (d / "tplc2.ttl").read_text(encoding="utf-8") == text, "parallel output differs"

    def block(subj):
        """The Turtle block whose SUBJECT is subj (blocks are 'subject\\n  pairs\\n\\n')."""
        head = f"\n<{subj}>\n"
        assert head in text, f"no block for {subj}"
        return text.split(head, 1)[1].split("\n\n", 1)[0]

    assert st.records == 2 and st.skipped == 1, st                               # DZE, FRN; one no-code
    assert "a ex:TPLCRecord" in text
    blk = block(iri.tplc("DZE"))
    assert 'ex:reportCount "3"^^xsd:integer' in blk, blk                       # 3 reports name DZE
    assert 'ex:deathReportCount "1"^^xsd:integer' in blk and 'ex:injuryReportCount "1"^^xsd:integer' in blk
    assert '"2020-03-10"^^xsd:date' in blk and '"2021-12-31"^^xsd:date' in blk
    bp_blk = block(iri.tplc_device_problem("DZE", "Battery Problem"))
    assert 'ex:reportCount "3"^^xsd:integer' in bp_blk, bp_blk                  # collided variants merged
    assert 'ex:reportCount "1"^^xsd:integer' in block(iri.tplc("FRN"))
    assert f"<{iri.tplc_outcome('DZE', 'Death')}>" in text and 'ex:outcomeType "outcome"' in text
    assert f"<{iri.tplc_patient_problem('DZE', 'Burn')}>" in text
    assert f"<{iri.tplc_year('DZE', 2021)}>" in text and '"2021"^^xsd:gYear' in text
    assert f"ex:relatedTPLC <{iri.tplc('FRN')}>" in text                        # hub link
    assert 'ex:sourceDataset "device/event (aggregated to TPLC)"' in text
    assert "medicaldevice.com" not in text
    assert "product-code/269" not in text                                       # junk code not minted
    hosp = block(iri.tplc_outcome("DZE", "Hospitalization"))
    assert 'ex:reportCount "2"^^xsd:integer' in hosp, hosp                       # "H" merged with the word
    assert 'rdfs:label "Defibrillator"' not in text and 'skos:altLabel "Defibrillator"' in text
    print(f"tplc.py self-test PASSED ({st.records} product codes, {st.triples} triples; parallel == serial).")


if __name__ == "__main__":
    _selftest()
