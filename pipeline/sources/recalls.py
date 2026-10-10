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
from distribution_parser import distribution_pairs   # noqa: E402
from identity import normalize_org                    # noqa: E402

NAME = "recalls"
DATASET = "device/enforcement"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
CODE_INFO_CAP = None      # keep the full lot/serial text (PI decision 2026-10-09: no data loss)
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
    """Stream records one at a time (base.iter_openfda_results; ijson if installed)."""
    yield from base.iter_openfda_results(src)


def pending_key(r):
    """Stable key for a report FDA posted before assigning a recall number:
    'pending-<event id>-<8 hex of the product description>'. None if no event id."""
    import hashlib
    ev = str(r.get("event_id") or "").strip()
    if not ev:
        return None
    h = hashlib.sha1(str(r.get("product_description") or "").encode("utf-8")).hexdigest()[:8]
    return f"pending-{ev}-{h}"


def transform(src, out, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    hub = {}
    firms = {}          # normalized name -> first raw spelling seen
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            rn = str(r.get("recall_number") or "").strip()
            key = rn or pending_key(r)
            if not key:
                st.skipped += 1
                continue
            of = r.get("openfda") or {}

            pairs = [
                ("a", "ex:RecallRecord"),
                ("ex:recallNumber", common.lit(rn) if rn else None),
                # FDA posts some reports before assigning a recall number (2026-10-10:
                # one record, event 94520, "Not Yet Classified"). Keep it, flagged.
                ("ex:recallNumberStatus", None if rn else common.lit("not yet assigned by FDA")),
                ("ex:recallClassification", common.lit(r.get("classification"))),
                ("ex:recallStatus", common.lit(r.get("status"))),   # ontology term (was ex:status)
                ("ex:reasonForRecall", common.lit(r.get("reason_for_recall"))),
                ("ex:recallingFirm", common.lit(r.get("recalling_firm"))),
                ("ex:productDescription", common.lit(r.get("product_description"))),
                ("ex:recallInitiationDate", common.date_lit(r.get("recall_initiation_date"))),
                ("ex:reportDate", common.date_lit(r.get("report_date"))),
                ("ex:centerClassificationDate", common.date_lit(r.get("center_classification_date"))),
                ("ex:terminationDate", common.date_lit(r.get("termination_date"))),
                ("ex:voluntaryMandated", common.lit(r.get("voluntary_mandated"))),
                ("ex:initialFirmNotification", common.lit(r.get("initial_firm_notification"))),
                ("ex:productQuantity", common.lit(r.get("product_quantity"))),
                ("ex:eventId", common.lit(r.get("event_id"))),
                ("ex:distributionPattern", common.lit(r.get("distribution_pattern"))),
                ("ex:city", common.lit(r.get("city"))),
                ("ex:state", common.lit(r.get("state"))),
                ("ex:country", common.lit(r.get("country"))),
                ("ex:address", common.lit(", ".join(x for x in (r.get("address_1"), r.get("address_2")) if x))),
                ("ex:postalCode", common.lit(r.get("postal_code"))),
                ("ex:productType", common.lit(r.get("product_type"))),
            ]

            # code_info (lots/serials): kept in full unless CODE_INFO_CAP is set.
            # FDA moves the overflow of long lot lists into more_code_info; it is joined
            # back so one ex:codeInfo literal holds the complete published list.
            code = " ".join(str(x).strip() for x in (r.get("code_info"), r.get("more_code_info")) if x and str(x).strip())
            pairs += common.capped_pairs("ex:codeInfo", code or None, CODE_INFO_CAP)
            # barcode path: GS1 device identifiers FDA quotes in the lot text -> UDI identifier nodes
            for di in common.gs1_dis(code):
                pairs.append(("ex:quotedDeviceIdentifier", common.lit(di)))
                pairs.append(("ex:mentionsDeviceIdentifier", f"<{iri.udi_identifier('GS1', di)}>"))
            # structured geography from the free-text field (task: distribution parser)
            pairs += distribution_pairs(r.get("distribution_pattern"))

            # recall event (groups product-level records into one FDA event)
            if str(r.get("event_id") or "").strip():
                pairs.append(("ex:partOfRecallEvent", f"<{iri.recall_event(r['event_id'])}>"))

            # organization identity: name-keyed node; identity.py links it to
            # FEI/DUNS-keyed nodes with scored ex:IdentityMapping (never sameAs)
            nf = normalize_org(r.get("recalling_firm"))
            if nf:
                pairs.append(("ex:hasRecallingFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, r.get("recalling_firm"))

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

            for pred, obj in common.provenance(DATASET, key,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(iri.recall(key), pairs)
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

        for nf in sorted(firms):
            w.block(iri.firm_name(nf), [
                ("a", "ex:FirmName"),
                ("rdfs:label", common.lit(firms[nf])),
                ("ex:normalizedName", common.lit(nf)),
            ])

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
        {"reason_for_recall": "no number"},   # missing recall_number and event id -> skipped
        {"event_id": "94520", "classification": "Not Yet Classified", "recall_number": "",
         "product_description": "SYR 30ML L/L", "reason_for_recall": "Out of 510(k) range"},  # pending
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "recalls.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 2 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.recall('Z-0001-2025')}>" in text
    pk = pending_key({"event_id": "94520", "product_description": "SYR 30ML L/L"})
    assert f"<{iri.recall(pk)}>" in text and 'ex:recallNumberStatus "not yet assigned by FDA"' in text
    assert 'ex:recallClassification "Class II"' in text
    assert '"2025-01-15"^^xsd:date' in text
    assert f"<{iri.k510('K180179')}>" in text                 # cross-link to 510(k)
    assert f"<{iri.pma('P010001')}>" in text                  # cross-link to PMA
    assert f"<{iri.product_code('OYC')}>" in text and text.count("a ex:ProductCode") == 1
    assert "ex:sourceDataset \"device/enforcement\"" in text
    assert 'ex:recallStatus "Ongoing"' in text and "ex:status " not in text
    assert "ex:distributedNationwide" in text                 # parser folded in
    assert f"<{iri.firm_name('MINIMED')}>" in text and "a ex:FirmName" in text
    print(f"recalls.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
