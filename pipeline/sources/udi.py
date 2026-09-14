#!/usr/bin/env python3
"""
udi.py -- UDI / AccessGUDID device records (openFDA device/udi) -> RDF, on the contract.

This is the physical-device layer: the only FDA source with a genuine device
identifier (the DI) and the only one that simultaneously carries GMDN terms, FDA
product codes, premarket submission numbers and a DUNS number -- so it is the
bridge from the regulatory records to the marketed device. Ported from the
standalone pipeline/udi/udi_to_rdf.py onto the source-module contract (base.py):
fetch() records a Manifest, transform() threads retrieved_at through provenance,
validate() shells out to the shapes gate. The record-to-triples mapping is
unchanged, so the nodes still join the ones the other converters mint.

Acquisition is BULK -- ~5.18M records across ~52 partitions; the old paging API
would have been ~52,000 requests against the 25k skip ceiling (D9/D10).

Every IRI is minted by iri.py; every literal by common.py. This module only
decides WHICH triples a record has.
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

NAME = "udi"
DATASET = "device/udi"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL_BASE = "https://accessgudid.nlm.nih.gov/devices/"


def _ref(iri_string):
    """Wrap a full IRI (from iri.py) as a Turtle IRI reference."""
    return f"<{iri_string}>"


def fetch(dest):
    """Bulk-download every device/udi partition (no paging -- closes D9/D10)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["udi"]["partitions"]
    files = [base.bulk_download(p["file"], dest) for p in partitions]
    return base.Manifest(source=NAME, dataset=DATASET,
                         retrieved_at=base.today(), files=files)


def _iter_records(src):
    """Yield records from every .json / .zip / .json.zip partition in src."""
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
    seen = {"gmdn": set(), "labeler": set(), "ident": set()}
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            key = str(r.get("public_device_record_key") or "").strip()
            if not key:
                st.skipped += 1
                continue
            dev = iri.udi_device(key)

            pairs = [
                ("a", "ex:UDIDeviceRecord"),
                ("rdfs:label", common.lit(r.get("brand_name"))),
                ("ex:brandName", common.lit(r.get("brand_name"))),
                ("ex:companyName", common.lit(r.get("company_name"))),
                ("ex:versionModelNumber", common.lit(r.get("version_or_model_number"))),
                ("ex:catalogNumber", common.lit(r.get("catalog_number"))),
                ("ex:deviceDescription", common.lit(r.get("device_description"))),
                ("ex:publicDeviceRecordKey", common.lit(key)),
                ("ex:devicePublishDate", common.date_lit(r.get("publish_date"))),
                ("ex:publicVersionDate", common.date_lit(r.get("public_version_date"))),
                ("ex:recordStatus", common.lit(r.get("record_status"))),
                ("ex:commercialDistributionStatus",
                 common.lit(r.get("commercial_distribution_status"))),
                ("ex:deviceCountInBasePackage",
                 common.int_lit(r.get("device_count_in_base_package"))),

                # design-relevant attributes -- the reason this source matters
                ("ex:isRx", common.bool_lit(r.get("is_rx"))),
                ("ex:isOTC", common.bool_lit(r.get("is_otc"))),
                ("ex:isSingleUse", common.bool_lit(r.get("is_single_use"))),
                ("ex:isKit", common.bool_lit(r.get("is_kit"))),
                ("ex:isCombinationProduct", common.bool_lit(r.get("is_combination_product"))),
                ("ex:isHCTP", common.bool_lit(r.get("is_hct_p"))),
                ("ex:isLabeledAsNRL", common.bool_lit(r.get("is_labeled_as_nrl"))),
                ("ex:isLabeledAsNoNRL", common.bool_lit(r.get("is_labeled_as_no_nrl"))),
                ("ex:isDirectMarkingExempt", common.bool_lit(r.get("is_direct_marking_exempt"))),
                ("ex:isPremarketExempt", common.bool_lit(r.get("is_pm_exempt"))),
                ("ex:mriSafetyStatus", common.lit(r.get("mri_safety"))),
                ("ex:hasLotOrBatchNumber", common.bool_lit(r.get("has_lot_or_batch_number"))),
                ("ex:hasSerialNumber", common.bool_lit(r.get("has_serial_number"))),
                ("ex:hasExpirationDate", common.bool_lit(r.get("has_expiration_date"))),
                ("ex:hasManufacturingDate", common.bool_lit(r.get("has_manufacturing_date"))),
                ("ex:hasDonationIdNumber", common.bool_lit(r.get("has_donation_id_number"))),
            ]

            # sterilisation is a NESTED object, not top-level flags
            ster = r.get("sterilization") or {}
            pairs.append(("ex:isSterile", common.bool_lit(ster.get("is_sterile"))))
            pairs.append(("ex:requiresSterilizationPriorToUse",
                          common.bool_lit(ster.get("is_sterilization_prior_use"))))
            for method in common.as_list(ster.get("sterilization_methods")):
                pairs.append(("ex:sterilizationMethod", common.lit(method)))

            # labeler (DUNS) -- a different key space from FEI-based Manufacturer;
            # kept separate on purpose (see docs/udi-ingest.md).
            duns = str(r.get("labeler_duns_number") or "").strip()
            if duns:
                pairs.append(("ex:hasLabeler", _ref(iri.labeler(duns))))

            for pc in r.get("product_codes") or []:
                code = str(pc.get("code") or "").strip()
                if code:
                    pairs.append(("ex:hasProductCode", _ref(iri.product_code(code))))

            for g in r.get("gmdn_terms") or []:
                code = str(g.get("code") or "").strip()
                if code:
                    pairs.append(("ex:hasGMDNTerm", _ref(iri.gmdn(code))))

            for sub in r.get("premarket_submissions") or []:
                num = str(sub.get("submission_number") or "").strip()
                if not num:
                    continue
                u = num.upper()
                # the join that ties a physical device to its clearance record
                if u.startswith("K") or u.startswith("DEN"):
                    pairs.append(("ex:relatedK510Record", _ref(iri.k510(num))))
                elif u.startswith("P") or u.startswith("N"):
                    pairs.append(("ex:relatedPMARecord", _ref(iri.pma(num))))
                else:
                    pairs.append(("ex:premarketSubmissionNumber", common.lit(num)))

            for ident in r.get("identifiers") or []:
                iid = str(ident.get("id") or "").strip()
                agency = str(ident.get("issuing_agency") or "unknown").strip()
                if iid:
                    pairs.append(("ex:hasIdentifier", _ref(iri.udi_identifier(agency, iid))))

            for pred, obj in common.provenance(DATASET, key,
                                               source_url=f"{SOURCE_URL_BASE}{key}",
                                               retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(dev, pairs)
            st.records += 1

            # ---- UDI identifier nodes (deduped) ----
            for ident in r.get("identifiers") or []:
                iid = str(ident.get("id") or "").strip()
                if not iid:
                    continue
                agency = str(ident.get("issuing_agency") or "unknown").strip()
                node = iri.udi_identifier(agency, iid)
                if node in seen["ident"]:
                    continue
                seen["ident"].add(node)
                w.block(node, [
                    ("a", "ex:UDIIdentifier"),
                    ("rdfs:label", common.lit(iid)),
                    ("ex:identifierId", common.lit(iid)),
                    ("ex:identifierType", common.lit(ident.get("type"))),
                    ("ex:issuingAgency", common.lit(agency)),
                    ("ex:packageType", common.lit(ident.get("package_type"))),
                    ("ex:quantityPerUnitOfUse", common.int_lit(ident.get("quantity_per_package"))),
                    ("ex:unitOfUseId", common.lit(ident.get("unit_of_use_id"))),
                    ("ex:belongsToDevice", _ref(dev)),
                ])

            # ---- GMDN terms (shared nodes, emitted once) ----
            for g in r.get("gmdn_terms") or []:
                code = str(g.get("code") or "").strip()
                if not code:
                    continue
                node = iri.gmdn(code)
                if node in seen["gmdn"]:
                    continue
                seen["gmdn"].add(node)
                w.block(node, [
                    ("a", "ex:GMDNTerm"),
                    ("rdfs:label", common.lit(g.get("name"))),
                    ("ex:gmdnTermCode", common.lit(code)),
                    ("ex:gmdnTermName", common.lit(g.get("name"))),
                    ("ex:gmdnTermDefinition", common.lit(g.get("definition"))),
                    ("ex:gmdnImplantable", common.bool_lit(g.get("implantable"))),
                    ("ex:gmdnCodeStatus", common.lit(g.get("code_status"))),
                ])

            # ---- labeler node (deduped) ----
            if duns:
                node = iri.labeler(duns)
                if node not in seen["labeler"]:
                    seen["labeler"].add(node)
                    w.block(node, [
                        ("a", "ex:Labeler"),
                        ("rdfs:label", common.lit(r.get("company_name"))),
                        ("ex:companyName", common.lit(r.get("company_name"))),
                        ("ex:dunsNumber", common.lit(duns)),
                    ])

            # ---- storage conditions (per-device, indexed) ----
            for i, sc in enumerate(r.get("storage") or []):
                node = iri.storage_condition(key, i)
                high = sc.get("high") or {}
                low = sc.get("low") or {}
                w.block(node, [
                    ("a", "ex:StorageCondition"),
                    ("ex:storageType", common.lit(sc.get("type"))),
                    ("ex:storageHighValue", common.lit(high.get("value"))),
                    ("ex:storageHighUnit", common.lit(high.get("unit"))),
                    ("ex:storageLowValue", common.lit(low.get("value"))),
                    ("ex:storageLowUnit", common.lit(low.get("unit"))),
                    ("ex:specialConditions", common.lit(sc.get("special_conditions"))),
                    ("ex:appliesToDevice", _ref(dev)),
                ])
                w.block(dev, [("ex:hasStorageCondition", _ref(node))])

    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=None)


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {"public_device_record_key": "abc-123", "brand_name": "CardioX",
         "company_name": "Acme Medical", "version_or_model_number": "CX-9",
         "publish_date": "2016-09-12", "public_version_date": "20200101",
         "record_status": "Published", "commercial_distribution_status": "In Commercial Distribution",
         "device_count_in_base_package": "1",
         "is_rx": "true", "is_single_use": "false", "is_kit": "false",
         "is_labeled_as_nrl": "false", "mri_safety": "MR Conditional",
         "sterilization": {"is_sterile": "true", "is_sterilization_prior_use": "false",
                           "sterilization_methods": ["Ethylene Oxide"]},
         "labeler_duns_number": "123456789",
         "product_codes": [{"code": "DZE"}],
         "gmdn_terms": [{"code": "35204", "name": "Defibrillator", "implantable": "true"}],
         "premarket_submissions": [{"submission_number": "K123456"},
                                   {"submission_number": "P900001"},
                                   {"submission_number": "DEN140010"}],
         "identifiers": [{"id": "00812345678901", "issuing_agency": "GS1",
                          "type": "Primary", "package_type": "Box",
                          "quantity_per_package": "10"}],
         "storage": [{"type": "Storage Environment Temperature",
                      "high": {"value": "25", "unit": "Degrees Celsius"},
                      "low": {"value": "2", "unit": "Degrees Celsius"}}]},
        {"brand_name": "NoKey"},   # no record key -> skipped
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "udi.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 1 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.udi_device('abc-123')}>" in text and "ex:UDIDeviceRecord" in text
    assert '"2016-09-12"^^xsd:date' in text and '"2020-01-01"^^xsd:date' in text
    assert 'ex:isSterile "true"^^xsd:boolean' in text
    assert 'ex:sterilizationMethod "Ethylene Oxide"' in text
    assert f"<{iri.product_code('DZE')}>" in text
    assert f"<{iri.gmdn('35204')}>" in text and "ex:GMDNTerm" in text
    assert f"<{iri.k510('K123456')}>" in text and f"<{iri.k510('DEN140010')}>" in text
    assert f"<{iri.pma('P900001')}>" in text
    assert f"<{iri.labeler('123456789')}>" in text and "ex:Labeler" in text
    assert "ex:StorageCondition" in text and "ex:hasStorageCondition" in text
    assert 'ex:sourceDataset "device/udi"' in text
    assert '"2026-10-02"^^xsd:date' in text            # retrieved_at threaded through
    print(f"udi.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
