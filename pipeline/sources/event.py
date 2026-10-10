#!/usr/bin/env python3
"""
event.py -- MAUDE adverse-event reports (openFDA device/event) -> RDF, on the contract.

Each report -> ex:MAUDEReport with typed dates, event type, device problems and
patient outcomes, and links to the device's ProductCode plus any 510(k)/PMA the
openfda block supplies. Acquisition is BULK -- this is the source whose old paging
import capped MAUDE at 0.06% coverage (D9); bulk partitions remove that ceiling.

Note for downstream: MAUDE is passive surveillance. Report counts are not failure
rates and FDA does not verify causation -- that caveat belongs in any answer, not
in the data. This module only records what FDA published.
"""
import os
import sys
import json
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

NAME = "event"
DATASET = "device/event"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/event/"


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


def transform(src, out, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    hub = {}
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            rn = str(r.get("report_number") or r.get("mdr_report_key") or "").strip()
            if not rn:
                st.skipped += 1
                continue
            try:
                subject = iri.report(rn)
            except ValueError:
                st.skipped += 1
                continue

            pairs = [
                ("a", "ex:MAUDEReport"),
                ("ex:reportNumber", common.lit(rn)),
                ("ex:mdrReportKey", common.lit(r.get("mdr_report_key"))),
                ("ex:eventType", common.lit(r.get("event_type"))),
                ("ex:dateReceived", common.date_lit(r.get("date_received"))),
                ("ex:dateOfEvent", common.date_lit(r.get("date_of_event"))),
                ("ex:reportSourceCode", common.lit(r.get("report_source_code"))),
                ("ex:manufacturerName", common.lit(r.get("manufacturer_name"))),
            ]
            for st_ in common.as_list(r.get("source_type")):
                pairs.append(("ex:sourceType", common.lit(st_)))
            for prob in common.as_list(r.get("product_problems")):
                pairs.append(("ex:deviceProblem", common.lit(prob)))
            for pt in (r.get("patient") or []):
                for out_ in common.as_list(pt.get("sequence_number_outcome")):
                    pairs.append(("ex:patientOutcome", common.lit(out_)))
                for pp in common.as_list(pt.get("patient_problems")):
                    pairs.append(("ex:patientProblem", common.lit(pp)))

            for dev in (r.get("device") or []):
                pc = str(dev.get("device_report_product_code") or "").strip()
                of = dev.get("openfda") or {}
                if not pc:
                    pc = str((common.as_list(of.get("product_code")) or [""])[0]).strip()
                if pc:
                    pairs.append(("ex:productCode", common.lit(pc)))
                    pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                    hub.setdefault(pc, (common.as_list(of.get("device_name")) or [dev.get("generic_name")])[0])
                if dev.get("brand_name"):
                    pairs.append(("ex:deviceName", common.lit(dev.get("brand_name"))))
                for kn in common.as_list(of.get("k_number")):
                    pairs.append(("ex:relatedK510Record", f"<{iri.k510(kn)}>"))
                for pn in common.as_list(of.get("pma_number")):
                    pairs.append(("ex:relatedPMARecord", f"<{iri.pma(pn)}>"))

            for pred, obj in common.provenance(DATASET, rn,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(subject, pairs)
            st.records += 1

        for pc in sorted(hub):
            w.block(iri.product_code(pc), [
                ("a", "ex:ProductCode"),
                ("rdfs:label", common.lit(hub[pc])),
                ("ex:productCode", common.lit(pc)),
            ])

    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=None)


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {"report_number": "1234567-2020-00001", "mdr_report_key": "9988776",
         "event_type": "Injury", "date_received": "20200310", "date_of_event": "20200228",
         "report_source_code": "Manufacturer report", "manufacturer_name": "Acme",
         "source_type": ["Health Professional"],
         "product_problems": ["Battery Problem", "Overheating"],
         "patient": [{"sequence_number_outcome": ["Hospitalization"],
                      "patient_problems": ["Burn"]}],
         "device": [{"device_report_product_code": "DZE", "brand_name": "CardioX",
                     "openfda": {"device_name": ["Defibrillator"], "k_number": ["K123456"],
                                 "pma_number": ["P900001"]}}]},
        {"event_type": "Malfunction"},   # no report id -> skipped
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "event.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 1 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.report('1234567-2020-00001')}>" in text and "ex:MAUDEReport" in text
    assert 'ex:eventType "Injury"' in text and '"2020-03-10"^^xsd:date' in text
    assert 'ex:deviceProblem "Battery Problem"' in text and 'ex:patientOutcome "Hospitalization"' in text
    assert f"<{iri.product_code('DZE')}>" in text
    assert f"<{iri.k510('K123456')}>" in text and f"<{iri.pma('P900001')}>" in text
    assert "ex:sourceDataset \"device/event\"" in text
    a = local_name("https://download.open.fda.gov/device/event/2024q1/device-event-0001-of-0009.json.zip")
    b = local_name("https://download.open.fda.gov/device/event/2023q4/device-event-0001-of-0009.json.zip")
    assert a == "2024q1__device-event-0001-of-0009.json.zip" and a != b      # quarters never collide
    print(f"event.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
