#!/usr/bin/env python3
"""
event.py -- MAUDE adverse-event reports (openFDA device/event) -> RDF, on the contract.

Each report -> ex:MAUDEReport with typed dates, event type, report flags, device
problems, patient problems/outcomes/treatments, the device as reported (brand,
generic name, model, catalog, lot, UDI-DI, manufacturer, operator) and links to the
ProductCode hub plus any 510(k)/PMA named in the report or its openfda block.
Acquisition is BULK -- the old paging import capped MAUDE at 0.06% coverage (D9).

TWO OUTPUT FILES (2026-10-09)
  <out>            structured facts (event.ttl)
  <out>_text.ttl   the FDA-published narratives (event_text.ttl), same report IRIs:
                   ex:eventDescription ("Description of Event or Problem") and
                   ex:manufacturerNarrative ("Additional Manufacturer Narrative").
  Narratives are public FDA records (already redacted by FDA) and are kept in full
  (no-data-loss decision). They sit in their own file so that releasing them in the
  open graph or only in a licensed tier stays a one-file decision.

DELIBERATELY NOT INGESTED: manufacturer contact person names, phone numbers and
street addresses of contact persons, and patient demographics (age, sex, weight,
race, ethnicity). The research value is low and they are personal data.

Dates FDA ships that are not real calendar dates are kept under <pred>Text so the
typed predicate always holds a valid xsd:date (SHACL D4) and nothing is dropped.

Note for downstream: MAUDE is passive surveillance. Report counts are not failure
rates and FDA does not verify causation.

PERFORMANCE: 26 M reports / 18 GB zipped. With workers > 1 each partition is
written to its own part file in a worker process, then parts are concatenated in
partition order (deterministic; repeated @prefix lines are valid Turtle).
"""
import os
import sys
import json
import shutil
import zipfile
import urllib.request
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
import scrub      # noqa: E402

NAME = "event"
DATASET = "device/event"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/event/"
_NULLS = {"", "*", "NI", "N/A", "NA", "UNK", "UNKNOWN", "NONE"}     # FDA "no information" fillers
_TEXT_PRED = {"description of event or problem": "ex:eventDescription",
              "additional manufacturer narrative": "ex:manufacturerNarrative"}


def local_name(url):
    """Unique local filename for a MAUDE partition.

    MAUDE partitions are split by quarter and REUSE file names across quarters:
        .../device/event/2024q1/device-event-0001-of-0009.json.zip
        .../device/event/2023q4/device-event-0001-of-0009.json.zip
    Saving by the last path segment alone silently overwrote 316 of 371 files on
    2026-10-09 (caught on VM .83 before any build used them). The quarter folder is
    therefore part of the name:  2024q1__device-event-0001-of-0009.json.zip"""
    parts = url.rstrip("/").split("/")
    return f"{parts[-2]}__{parts[-1]}" if len(parts) >= 2 else parts[-1]


def fetch(dest):
    """Bulk-download every device/event partition (no paging -- closes D9)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["event"]["partitions"]
    names = [local_name(p["file"]) for p in partitions]
    if len(set(names)) != len(names):
        raise RuntimeError("MAUDE partition names still collide; refusing to overwrite files")
    files = [base.bulk_download(p["file"], dest, filename=n) for p, n in zip(partitions, names)]
    return base.Manifest(source=NAME, dataset=DATASET,
                         retrieved_at=base.today(), files=files)


def _iter_records(src):
    """Stream records one at a time (base.iter_openfda_results; ijson if installed)."""
    yield from base.iter_openfda_results(src)


# ===========================================================================
#  Record -> triples
# ===========================================================================
def _v(value):
    """A meaningful string value, or None for blanks and FDA no-information fillers."""
    s = " ".join(str(value or "").split())
    return None if s.upper() in _NULLS else s


def _date_pair(pred, value):
    """(pred, xsd:date) for a real date; (predText, raw string) otherwise; [] if blank."""
    v = _v(value)
    if not v:
        return []
    d = common.date_lit(v)
    return [(pred, d)] if d.endswith("^^xsd:date") else [(pred + "Text", common.lit(v))]


def _flag(value):
    v = (_v(value) or "").upper()
    return common.bool_lit({"Y": "true", "N": "false"}.get(v, v)) if v else None


def _premarket(num):
    """510(k)/De Novo or PMA link for a premarket number, or None."""
    n = _v(num)
    if not n:
        return None
    u = n.upper()
    try:
        if u.startswith("K") or u.startswith("DEN"):
            return ("ex:relatedK510Record", f"<{iri.k510(n)}>")
        if u.startswith("P") or u.startswith("N") or u.startswith("H"):
            if u.startswith("H"):
                return ("ex:relatedHDE", f"<{iri.hde(n.split('/')[0])}>")
            return ("ex:relatedPMARecord", f"<{iri.pma(n.split('/')[0])}>")
    except ValueError:
        return None
    return None


def report_label(rn, event_type=None):
    """rdfs:label for a MAUDE report, e.g. "MAUDE 1234567-2020-00001 (Injury)".
    scripts/patch_event_ttl.py uses the same function on an already-built file."""
    return common.lit(f"MAUDE {rn}" + (f" ({event_type})" if event_type else ""))


def record_blocks(r, retrieved_at=None):
    """-> (subject, pairs, text_pairs, {pc: label}) or None if the report has no usable id."""
    rn = str(r.get("report_number") or r.get("mdr_report_key") or "").strip()
    if not rn:
        return None
    try:
        subject = iri.report(rn)
    except ValueError:
        return None

    pairs = [
        ("a", "ex:MAUDEReport"),
        ("rdfs:label", report_label(rn, _v(r.get("event_type")))),
        ("ex:reportNumber", common.lit(rn)),
        ("ex:mdrReportKey", common.lit(r.get("mdr_report_key"))),
        ("ex:eventType", common.lit(_v(r.get("event_type")))),
        ("ex:adverseEventFlag", _flag(r.get("adverse_event_flag"))),
        ("ex:productProblemFlag", _flag(r.get("product_problem_flag"))),
        ("ex:summaryReportFlag", _flag(r.get("summary_report_flag"))),
        ("ex:reportSourceCode", common.lit(_v(r.get("report_source_code")))),
        ("ex:reporterOccupation", common.lit(_v(r.get("reporter_occupation_code")))),
        ("ex:reporterCountry", common.lit(_v(r.get("reporter_country_code")))),
        ("ex:eventLocation", common.lit(_v(r.get("event_location")))),
        ("ex:removalCorrectionNumber", common.lit(_v(r.get("removal_correction_number")))),
        ("ex:manufacturerName", common.lit(_v(r.get("manufacturer_name")))),
    ]
    pairs += _date_pair("ex:dateReceived", r.get("date_received"))
    pairs += _date_pair("ex:dateOfEvent", r.get("date_of_event"))
    pairs += _date_pair("ex:reportDate", r.get("date_report"))
    pairs += _date_pair("ex:dateManufacturerReceived", r.get("date_manufacturer_received"))
    for st_ in common.as_list(r.get("source_type")):
        pairs.append(("ex:sourceType", common.lit(_v(st_))))
    for t in common.as_list(r.get("type_of_report")):
        pairs.append(("ex:typeOfReport", common.lit(_v(t))))
    for t in common.as_list(r.get("remedial_action")):
        pairs.append(("ex:remedialAction", common.lit(_v(t))))
    for prob in common.as_list(r.get("product_problems")):
        pairs.append(("ex:deviceProblem", common.lit(_v(prob))))
    for pt in (r.get("patient") or []):
        for o in common.as_list(pt.get("sequence_number_outcome")):
            # older reports use FDA's one-letter codes; map them to the words newer ones use
            pairs.append(("ex:patientOutcome", common.lit(common.outcome_label(_v(o)))))
        for pp in common.as_list(pt.get("patient_problems")):
            pairs.append(("ex:patientProblem", common.lit(_v(pp))))
        for tr in common.as_list(pt.get("sequence_number_treatment")):
            pairs.append(("ex:patientTreatment", common.lit(_v(tr))))
    link = _premarket(r.get("pma_pmn_number"))
    if link:
        pairs.append(link)

    hub = {}
    for dev in (r.get("device") or []):
        of = dev.get("openfda") or {}
        raw_pc = _v(dev.get("device_report_product_code")) or _v((common.as_list(of.get("product_code")) or [""])[0])
        pc = common.product_code(raw_pc)
        if pc:
            pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
            pairs.append(("ex:productCode", common.lit(pc)))
            hub.setdefault(pc, _v((common.as_list(of.get("device_name")) or [None])[0])
                           or _v(dev.get("generic_name")))
        elif raw_pc:
            # reporter-typed text that is not a 3-letter FDA code: kept, but no hub node
            pairs.append(("ex:reportedProductCodeText", common.lit(raw_pc)))
        pairs += [
            ("ex:deviceName", common.lit(_v(dev.get("brand_name")))),
            ("ex:genericName", common.lit(_v(dev.get("generic_name")))),
            ("ex:modelNumber", common.lit(_v(dev.get("model_number")))),
            ("ex:catalogNumber", common.lit(_v(dev.get("catalog_number")))),
            ("ex:lotNumber", common.lit(_v(dev.get("lot_number")))),
            ("ex:udiDI", common.lit(_v(dev.get("udi_di")))),
            ("ex:udiPublic", common.lit(_v(dev.get("udi_public")))),
            ("ex:deviceManufacturerName", common.lit(_v(dev.get("manufacturer_d_name")))),
            ("ex:deviceManufacturerCountry", common.lit(_v(dev.get("manufacturer_d_country")))),
            ("ex:deviceOperator", common.lit(_v(dev.get("device_operator")))),
            ("ex:deviceAvailability", common.lit(_v(dev.get("device_availability")))),
            ("ex:deviceEvaluatedByManufacturer", common.lit(_v(dev.get("device_evaluated_by_manufacturer")))),
            ("ex:deviceAgeText", common.lit(_v(dev.get("device_age_text")))),
        ]
        for kn in common.as_list(of.get("k_number")):
            pairs.append(("ex:relatedK510Record", f"<{iri.k510(kn)}>"))
        for pn in common.as_list(of.get("pma_number")):
            pairs.append(("ex:relatedPMARecord", f"<{iri.pma(pn)}>"))

    pairs += common.provenance(DATASET, rn, source_url=SOURCE_URL, retrieved_at=retrieved_at)
    # de-duplicate identical pairs (multi-device reports repeat values), keep order
    seen, uniq = set(), []
    for p in pairs:
        if p[1] is not None and p not in seen:
            seen.add(p)
            uniq.append(p)

    text_pairs = []
    for t in (r.get("mdr_text") or []):
        body = str(t.get("text") or "").strip()
        if not body:
            continue
        pred = _TEXT_PRED.get(str(t.get("text_type_code") or "").strip().lower(), "ex:mdrText")
        body, _ = scrub.scrub_text(body)          # residual DOBs, record numbers, phones, contact names
        text_pairs.append((pred, common.lit(body)))
    return subject, uniq, text_pairs, hub


def _write_partition(args):
    """Worker: one partition -> <parts>/<name>.ttl and <name>_text.ttl. Returns stats + hub."""
    path, parts_dir, retrieved_at = args
    stem = Path(path).name.replace(".json.zip", "").replace(".zip", "").replace(".json", "")
    out = Path(parts_dir) / f"{stem}.ttl"
    tout = Path(parts_dir) / f"{stem}_text.ttl"
    st = base.Stats(source=NAME)
    hub = {}
    texts = 0
    import tplc                                    # reuse its single-file streaming reader
    with base.TurtleWriter(out) as w, base.TurtleWriter(tout) as wt:
        for r in tplc._iter_file(path):
            got = record_blocks(r, retrieved_at)
            if not got:
                st.skipped += 1
                continue
            subject, pairs, text_pairs, h = got
            w.block(subject, pairs)
            if text_pairs:
                wt.block(subject, text_pairs)
            for k, v in h.items():
                hub.setdefault(k, v)
            st.records += 1
    st.triples = w.triples
    texts = wt.triples
    return str(out), str(tout), st, texts, hub


def _concat(paths, dest):
    """Append the part files in order, deleting each as soon as it is copied, so the
    disk never holds two full copies of a ~60 GB output."""
    with open(dest, "wb") as fo:
        for p in paths:
            with open(p, "rb") as fi:
                shutil.copyfileobj(fi, fo, 1 << 22)
            os.remove(p)


def transform(src, out, retrieved_at=None, workers=None):
    src, out = Path(src), Path(out)
    man = src / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    import tplc
    files = tplc._partition_files(src)
    workers = workers or int(os.environ.get("MDKG_WORKERS", "0")) or max(1, min(3, (os.cpu_count() or 2) - 1))
    parts_dir = out.parent / f"{out.stem}.parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)
    jobs = [(str(f), str(parts_dir), retrieved_at) for f in files]
    results = []
    if workers <= 1 or len(jobs) <= 1:
        results = [_write_partition(j) for j in jobs]
    else:
        import multiprocessing as mp
        with mp.get_context("fork" if hasattr(os, "fork") else "spawn").Pool(workers) as pool:
            for i, res in enumerate(pool.imap_unordered(_write_partition, jobs), 1):
                results.append(res)
                print(f"  [{i}/{len(jobs)}] {Path(res[0]).name}: {res[2].records:,} reports")
    results.sort(key=lambda x: x[0])                      # deterministic partition order
    st, texts, hub = base.Stats(source=NAME), 0, {}
    for _, _, s, t, h in results:
        st.merge(s)
        texts += t
        for k, v in h.items():
            hub.setdefault(k, v)
    hub_file = parts_dir / "zz_hub.ttl"
    with base.TurtleWriter(hub_file) as w:
        for pc in sorted(hub):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"),
                                           ("skos:altLabel", common.lit(hub[pc])),   # rdfs:label from classification
                                           ("ex:productCode", common.lit(pc))])
    st.triples += w.triples
    _concat([r[0] for r in results] + [str(hub_file)], out)
    text_out = out.with_name(f"{out.stem}_text.ttl")
    _concat([r[1] for r in results], text_out)
    shutil.rmtree(parts_dir)
    print(f"  narratives: {texts:,} triples -> {text_out.name} (not counted in the structured total)")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {"report_number": "1234567-2020-00001", "mdr_report_key": "9988776",
         "event_type": "Injury", "date_received": "20200310", "date_of_event": "20200228",
         "date_report": "20200305", "adverse_event_flag": "Y", "product_problem_flag": "N",
         "report_source_code": "Manufacturer report", "manufacturer_name": "Acme",
         "source_type": ["Health Professional"], "type_of_report": ["Initial submission"],
         "remedial_action": ["Recall"], "reporter_occupation_code": "PHYSICIAN",
         "pma_pmn_number": "K991234",
         "manufacturer_contact_f_name": "Jane", "manufacturer_contact_phone_number": "5551234",
         "product_problems": ["Battery Problem", "Overheating"],
         "patient": [{"sequence_number_outcome": ["Hospitalization"], "patient_problems": ["Burn"],
                      "sequence_number_treatment": ["Surgery"], "patient_age": "54 YR", "patient_sex": "Female"}],
         "mdr_text": [{"text_type_code": "Description of Event or Problem",
                       "text": "Device overheated.\nPatient burned."},
                      {"text_type_code": "Additional Manufacturer Narrative",
                       "text": "Battery replaced. PT DOB 3/4/1950. CONTACT JANE DOE AT 555-123-4567."}],
         "device": [{"device_report_product_code": "DZE", "brand_name": "CardioX", "generic_name": "AED",
                     "model_number": "CX-9", "lot_number": "*", "catalog_number": "C1",
                     "udi_di": "00812345678901", "manufacturer_d_name": "ACME MEDICAL",
                     "device_operator": "HEALTH PROFESSIONAL",
                     "openfda": {"device_name": ["Defibrillator"], "k_number": ["K123456"],
                                 "pma_number": ["P900001"]}}]},
        {"report_number": "1234567-2020-00002", "event_type": "Malfunction",
         "date_received": "20200311", "date_of_event": "20201399",            # not a real date
         "patient": [{"sequence_number_outcome": ["R"]}],                      # one-letter code
         "device": [{"device_report_product_code": "FRN"}, {"device_report_product_code": "269"}]},
        {"event_type": "Malfunction"},   # no report id -> skipped
    ]}
    for workers in (1, 2):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "dl").mkdir()
            (d / "dl" / "a.json").write_text(json.dumps({"results": sample["results"][:1]}), encoding="utf-8")
            (d / "dl" / "b.json").write_text(json.dumps({"results": sample["results"][1:]}), encoding="utf-8")
            base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                          files=[]).write(d / "dl" / "manifest.json")
            out = d / "event.ttl"
            st = transform(d / "dl", out, workers=workers)
            text = out.read_text(encoding="utf-8")
            narr = (d / "event_text.ttl").read_text(encoding="utf-8")
            if workers == 1:
                first = (text, narr)
            else:
                assert (text, narr) == first, "parallel output differs from serial"
            assert not (d / "event.parts").exists()

    assert st.records == 2 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.report('1234567-2020-00001')}>" in text and "ex:MAUDEReport" in text
    assert 'ex:eventType "Injury"' in text and '"2020-03-10"^^xsd:date' in text
    assert 'ex:adverseEventFlag "true"^^xsd:boolean' in text and 'ex:productProblemFlag "false"^^xsd:boolean' in text
    assert 'ex:deviceProblem "Battery Problem"' in text and 'ex:patientOutcome "Hospitalization"' in text
    assert 'ex:patientTreatment "Surgery"' in text and 'ex:remedialAction "Recall"' in text
    assert 'ex:modelNumber "CX-9"' in text and 'ex:udiDI "00812345678901"' in text
    assert "ex:lotNumber" not in text                                        # "*" filler dropped
    assert 'ex:dateOfEventText "20201399"' in text                            # bad date kept, untyped predicate
    assert f"<{iri.product_code('DZE')}>" in text and f"<{iri.product_code('FRN')}>" in text
    assert f"<{iri.k510('K123456')}>" in text and f"<{iri.pma('P900001')}>" in text
    assert f"<{iri.k510('K991234')}>" in text                                  # pma_pmn_number link
    assert "Jane" not in text and "5551234" not in text                       # contact persons excluded
    assert "54 YR" not in text and "Female" not in text                       # demographics excluded
    assert "Device overheated." not in text                                   # narratives in their own file
    assert 'ex:eventDescription "Device overheated.\\nPatient burned."' in narr
    assert ('ex:manufacturerNarrative "Battery replaced. PT DOB [DOB REMOVED]. '
            'CONTACT [NAME REMOVED] AT [PHONE REMOVED]."') in narr                # narratives scrubbed
    assert "3/4/1950" not in narr and "JANE DOE" not in narr
    assert "ex:sourceDataset \"device/event\"" in text
    assert 'rdfs:label "MAUDE 1234567-2020-00001 (Injury)"' in text           # reports labelled
    assert 'ex:patientOutcome "Required Intervention"' in text and 'ex:patientOutcome "R"' not in text
    assert "product-code/269" not in text and 'ex:reportedProductCodeText "269"' in text
    assert 'skos:altLabel "Defibrillator"' in text and 'rdfs:label "Defibrillator"' not in text
    a = local_name("https://download.open.fda.gov/device/event/2024q1/device-event-0001-of-0009.json.zip")
    b = local_name("https://download.open.fda.gov/device/event/2023q4/device-event-0001-of-0009.json.zip")
    assert a == "2024q1__device-event-0001-of-0009.json.zip" and a != b      # quarters never collide
    print(f"event.py self-test PASSED ({st.records} records, {st.triples} triples; parallel == serial).")


if __name__ == "__main__":
    _selftest()
