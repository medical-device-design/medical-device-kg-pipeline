#!/usr/bin/env python3
"""
k510.py -- 510(k) clearances (openFDA device/510k) -> RDF, on the base.py contract.

This is the reference source module: every other source copies its shape.
  * IRIs come only from iri.py (k510 records + the ProductCode hub + regulations).
  * Literals/provenance come only from common.py (typed dates, provenance block).
  * Acquisition is BULK (device/510k download partitions), never the paging API.

Model notes (docs/rebuild-strategy.md):
  * Record class ex:K510Record is preserved -- FDA publishes regulatory events.
  * ex:ProductCode is made the explicit hub (1b): each record links to it via
    ex:hasProductCode, and a deduped ProductCode block carries rdfs:label,
    ex:deviceClass, ex:medicalSpecialtyDescription, ex:hasRegulationNumber.
  * Dates are typed via common.date_lit (D4); booleans via common.bool_lit.
"""
import os
import sys
import json
import zipfile
import urllib.request
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))       # .../pipeline/sources
_PIPELINE = os.path.dirname(_SRC_DIR)                         # .../pipeline
_REPO = os.path.dirname(_PIPELINE)                            # repo root
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "k510"
DATASET = "device/510k"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/510k/"


# ---------------------------------------------------------------------------
def fetch(dest):
    """Bulk-download every device/510k partition listed in openFDA's download
    index. No paging -- see docs/rebuild-strategy.md (closes D9/D10)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["510k"]["partitions"]
    files = [base.bulk_download(p["file"], dest) for p in partitions]
    return base.Manifest(source=NAME, dataset=DATASET,
                         retrieved_at=base.today(), files=files)


def _iter_records(src):
    """Yield each 510(k) result dict from *.json / *.json.zip / *.zip under src.
    openFDA partitions are zipped JSON with a top-level {meta, results:[...]}."""
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
    """openFDA sometimes wraps a scalar in a list; take the first non-empty."""
    if isinstance(v, list):
        return next((x for x in v if str(x).strip()), None)
    return v


def transform(src, out, retrieved_at=None):
    """Rows -> RDF. Deterministic (ProductCode hub emitted once, sorted). The
    download date for provenance comes from the fetch manifest, never now()."""
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    hub = {}   # product_code -> dict(label, device_class, regulation_number, specialty)
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            kn = str(r.get("k_number") or "").strip()
            if not kn:
                st.skipped += 1
                continue
            of = r.get("openfda") or {}
            pc = str(r.get("product_code") or _first(of.get("product_code")) or "").strip()
            reg = _first(of.get("regulation_number"))

            pairs = [
                ("a", "ex:K510Record"),
                ("ex:kNumber", common.lit(kn)),
                ("ex:applicant", common.lit(r.get("applicant"))),
                ("ex:deviceName", common.lit(r.get("device_name"))),
                ("ex:decisionCode", common.lit(r.get("decision_code"))),
                ("ex:decisionDescription", common.lit(r.get("decision_description"))),
                ("ex:decisionDate", common.date_lit(r.get("decision_date"))),
                ("ex:dateReceived", common.date_lit(r.get("date_received"))),
                ("ex:clearanceType", common.lit(r.get("clearance_type"))),
                ("ex:reviewAdvisoryCommittee",
                 common.lit(r.get("advisory_committee_description") or r.get("advisory_committee"))),
                ("ex:thirdPartyFlag", common.bool_lit(r.get("third_party_flag"))),
                ("ex:expeditedReviewFlag", common.bool_lit(r.get("expedited_review_flag"))),
                ("ex:city", common.lit(r.get("city"))),
                ("ex:state", common.lit(r.get("state"))),
                ("ex:postalCode", common.lit(r.get("postal_code") or r.get("zip_code"))),
            ]
            if pc:
                pairs.append(("ex:productCode", common.lit(pc)))
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                hub.setdefault(pc, {
                    "label": of.get("device_name") or r.get("device_name"),
                    "device_class": of.get("device_class"),
                    "regulation_number": reg,
                    "specialty": of.get("medical_specialty_description"),
                })
            if reg:
                pairs.append(("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>"))
            for pred, obj in common.provenance(DATASET, kn,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(iri.k510(kn), pairs)
            st.records += 1

        # ProductCode hub, deduped + sorted for determinism
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
    """riot syntax check (SHACL added when a k510 shape lands). Skips if Jena
    is not installed; scripts/validate.sh on the VM is the authoritative gate."""
    return base.default_validate(ttl, shapes=None)


# ===========================================================================
#  SELF-TEST -- `python k510.py`. No network: transforms a tiny inline sample.
# ===========================================================================
def _selftest():
    import tempfile
    sample = {"meta": {"results": {"total": 2}}, "results": [
        {"k_number": "K033394", "applicant": "MEDTRONIC INC",
         "device_name": "Infusion Pump", "decision_code": "SESE",
         "decision_description": "Substantially Equivalent",
         "decision_date": "20040630", "date_received": "20031015",
         "product_code": "FRN", "third_party_flag": "N", "state": "MN",
         "openfda": {"device_class": "2", "regulation_number": "880.5725",
                     "medical_specialty_description": "General Hospital",
                     "device_name": "Pump, Infusion"}},
        {"k_number": "K999999", "applicant": "ACME",
         "device_name": "Widget", "decision_date": "20169999",  # invalid -> string
         "product_code": "FRN", "openfda": {"device_class": "2"}},
        {"applicant": "NO KEY"},   # missing k_number -> skipped
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "k510.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 2 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.k510('K033394')}>" in text
    assert "ex:K510Record" in text and "ex:kNumber \"K033394\"" in text
    assert '"2004-06-30"^^xsd:date' in text                 # D4 typed date
    assert '"20169999"' in text                             # invalid date kept as string
    assert f"<{iri.product_code('FRN')}>" in text
    assert "ex:ProductCode" in text and 'rdfs:label "Pump, Infusion"' in text
    assert text.count("a ex:ProductCode") == 1              # hub deduped
    assert f"<{iri.regulation('880.5725')}>" in text
    assert "ex:sourceDataset \"device/510k\"" in text and '"2026-10-02"^^xsd:date' in text
    print(f"k510.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
