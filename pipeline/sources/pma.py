#!/usr/bin/env python3
"""
pma.py -- PMA approvals + supplements (openFDA device/pma) -> RDF, on the contract.

Mirrors k510.py. PMAs and their supplements arrive as separate records; a
supplement nests under its base PMA via iri.pma(number, supplement) and links
back with ex:supplementOf. ProductCode is the shared hub (rebuild-strategy 1b).
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

NAME = "pma"
DATASET = "device/pma"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/pma/"


def fetch(dest):
    """Bulk-download every device/pma partition (no paging)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["pma"]["partitions"]
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


def _first(v):
    if isinstance(v, list):
        return next((x for x in v if str(x).strip()), None)
    return v


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
            pma_no = str(r.get("pma_number") or "").strip()
            if not pma_no:
                st.skipped += 1
                continue
            supp = str(r.get("supplement_number") or "").strip()
            of = r.get("openfda") or {}
            pc = str(r.get("product_code") or _first(of.get("product_code")) or "").strip()
            reg = _first(of.get("regulation_number"))
            subject = iri.pma(pma_no, supp) if supp else iri.pma(pma_no)
            rid = f"{pma_no}-{supp}" if supp else pma_no

            pairs = [
                ("a", "ex:PMARecord"),
                ("ex:pmaNumber", common.lit(pma_no)),
                ("ex:supplementNumber", common.lit(supp) if supp else None),
                ("ex:applicant", common.lit(r.get("applicant"))),
                ("ex:tradeName", common.lit(r.get("trade_name"))),
                ("ex:deviceName", common.lit(r.get("trade_name"))),
                ("ex:genericName", common.lit(r.get("generic_name"))),
                ("ex:decisionCode", common.lit(r.get("decision_code"))),
                ("ex:decisionDate", common.date_lit(r.get("decision_date"))),
                ("ex:dateReceived", common.date_lit(r.get("date_received"))),
                ("ex:supplementType", common.lit(r.get("supplement_type"))),
                ("ex:supplementReason", common.lit(r.get("supplement_reason"))),
                ("ex:advisoryCommittee",
                 common.lit(r.get("advisory_committee_description") or r.get("advisory_committee"))),
                ("ex:expeditedReviewFlag", common.bool_lit(r.get("expedited_review_flag"))),
                ("ex:aoStatement", common.lit(r.get("ao_statement"))),
                ("ex:docketNumber", common.lit(r.get("docket_number"))),
                ("ex:city", common.lit(r.get("city"))),
                ("ex:state", common.lit(r.get("state"))),
            ]
            if supp:
                pairs.append(("ex:supplementOf", f"<{iri.pma(pma_no)}>"))
            if pc:
                pairs.append(("ex:productCode", common.lit(pc)))
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                hub.setdefault(pc, {
                    "label": of.get("device_name") or r.get("generic_name") or r.get("trade_name"),
                    "device_class": of.get("device_class"),
                    "regulation_number": reg,
                    "specialty": of.get("medical_specialty_description"),
                })
            if reg:
                pairs.append(("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>"))
            for pred, obj in common.provenance(DATASET, rid,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(subject, pairs)
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
        {"pma_number": "P010001", "applicant": "ACME", "trade_name": "HeartValve X",
         "generic_name": "Valve, Heart", "decision_code": "APPR",
         "decision_date": "20030415", "date_received": "20020101",
         "product_code": "LWR",
         "openfda": {"device_class": "3", "regulation_number": "870.3925",
                     "medical_specialty_description": "Cardiovascular",
                     "device_name": "Valve, Replacement Heart"}},
        {"pma_number": "P010001", "supplement_number": "S001",
         "applicant": "ACME", "trade_name": "HeartValve X",
         "supplement_type": "Panel Track", "supplement_reason": "Design change",
         "decision_date": "20050620", "product_code": "LWR",
         "openfda": {"device_class": "3"}},
        {"applicant": "NO KEY"},   # missing pma_number -> skipped
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "pma.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 2 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.pma('P010001')}>" in text
    assert f"<{iri.pma('P010001', 'S001')}>" in text          # supplement nested
    assert "ex:supplementOf" in text
    assert '"2003-04-15"^^xsd:date' in text
    assert f"<{iri.product_code('LWR')}>" in text
    assert text.count("a ex:ProductCode") == 1                # hub deduped
    assert 'ex:supplementNumber "S001"' in text
    assert "ex:sourceDataset \"device/pma\"" in text
    print(f"pma.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
