#!/usr/bin/env python3
"""
recalls.py -- device recall enforcement reports (openFDA device/enforcement) -> RDF.

On the base.py contract. Each report -> ex:RecallRecord with typed dates, the
recall classification, and -- where openFDA supplies them -- real cross-links to
the clearance/approval records that the recalled device came through
(ex:relatedK510Record / ex:relatedPMARecord), plus the ProductCode hub. Those
cross-source joins are the whole point of the graph.
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

NAME = "recalls"
DATASET = "device/enforcement"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/enforcement/"


def fetch(dest):
    """Bulk-download every device/enforcement partition (no paging)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["enforcement"]["partitions"]
    files = [base.bulk_download(p["file"], dest) for p in partitions]
    return base.Manifest(source=NAME, dataset=DATASET,
                         retrieved_at=base.today(), files=files)


def _iter_records(src):
    src = Path(src)
    paths = sorted(set(src.glob("*.json")) | set(src.glob("*.zip")) | set(src.glob("*.json.zip")))
    for p in paths:
        if p.name.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith(".json"):
                        for rec in json.loads(z.read(n)).get("results", []):
                            yield rec
        else:
            for rec in json.loads(p.read_text(encoding="utf-8")).get("results", []):
                yield rec


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
            rn = str(r.get("recall_number") or "").strip()
            if not rn:
                st.skipped += 1
                continue
            of = r.get("openfda") or {}

            pairs = [
                ("a", "ex:RecallRecord"),
                ("ex:recallNumber", common.lit(rn)),
                ("ex:recallClassification", common.lit(r.get("classification"))),
                ("ex:status", common.lit(r.get("status"))),
                ("ex:reasonForRecall", common.lit(r.get("reason_for_recall"))),
                ("ex:recallingFirm", common.lit(r.get("recalling_firm"))),
                ("ex:productDescription", common.lit(r.get("product_description"))),
                ("ex:recallInitiationDate", common.date_lit(r.get("recall_initiation_date"))),
                ("ex:reportDate", common.date_lit(r.get("report_date"))),
                ("ex:voluntaryMandated", common.lit(r.get("voluntary_mandated"))),
                ("ex:initialFirmNotification", common.lit(r.get("initial_firm_notification"))),
                ("ex:codeInfo", common.lit(r.get("code_info"))),
                ("ex:productQuantity", common.lit(r.get("product_quantity"))),
                ("ex:eventId", common.lit(r.get("event_id"))),
                ("ex:distributionPattern", common.lit(r.get("distribution_pattern"))),
                ("ex:city", common.lit(r.get("city"))),
                ("ex:state", common.lit(r.get("state"))),
                ("ex:country", common.lit(r.get("country"))),
            ]

            # cross-source links (the joins no single FDA DB provides)
            for kn in common.as_list(of.get("k_number")):
                pairs.append(("ex:relatedK510Record", f"<{iri.k510(kn)}>"))
            for pn in common.as_list(of.get("pma_number")):
                pairs.append(("ex:relatedPMARecord", f"<{iri.pma(pn)}>"))

            for pc in common.as_list(of.get("product_code")):
                pairs.append(("ex:productCode", common.lit(pc)))
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                hub.setdefault(pc, {
                    "label": (common.as_list(of.get("device_name")) or [None])[0],
                    "device_class": (common.as_list(of.get("device_class")) or [None])[0],
                    "regulation_number": (common.as_list(of.get("regulation_number")) or [None])[0],
                    "specialty": (common.as_list(of.get("medical_specialty_description")) or [None])[0],
                })

            for pred, obj in common.provenance(DATASET, rn,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(iri.recall(rn), pairs)
            st.records += 1

        for pc in sorted(hub):
            d = hub[pc]
            block = [
                ("a", "ex:ProductCode"),
                ("rdfs:label", common.lit(d.get("label"))),
                ("ex:productCode", common.lit(pc)),
                ("ex:deviceClass", common.lit(d.get("device_class"))),
                ("ex:medicalSpecialtyDescription", common.lit(d.get("specialty"))),
            ]
            if d.get("regulation_number"):
                block.append(("ex:hasRegulationNumber", f"<{iri.regulation(d['regulation_number'])}>"))
            w.block(iri.product_code(pc), block)

    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=None)


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {"recall_number": "Z-0001-2025", "classification": "Class II",
         "status": "Ongoing", "reason_for_recall": "Software error",
         "recalling_firm": "MiniMed", "product_description": "Insulin pump",
         "recall_initiation_date": "20250115", "report_date": "20250201",
         "voluntary_mandated": "Voluntary: Firm initiated", "state": "CA",
         "distribution_pattern": "Nationwide and Canada",
         "openfda": {"product_code": ["OYC"], "k_number": ["K180179"],
                     "pma_number": ["P010001"], "device_class": ["2"],
                     "device_name": ["Pump, infusion, insulin"]}},
        {"reason_for_recall": "no number"},   # missing recall_number -> skipped
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "recalls.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 1 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.recall('Z-0001-2025')}>" in text
    assert 'ex:recallClassification "Class II"' in text
    assert '"2025-01-15"^^xsd:date' in text
    assert f"<{iri.k510('K180179')}>" in text                 # cross-link to 510(k)
    assert f"<{iri.pma('P010001')}>" in text                  # cross-link to PMA
    assert f"<{iri.product_code('OYC')}>" in text and text.count("a ex:ProductCode") == 1
    assert "ex:sourceDataset \"device/enforcement\"" in text
    print(f"recalls.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
