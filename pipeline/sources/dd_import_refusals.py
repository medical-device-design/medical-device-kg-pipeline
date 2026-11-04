#!/usr/bin/env python3
"""
dd_import_refusals.py -- FDA Data Dashboard import refusals (devices and radiation-emitting products) -> RDF.

SOURCE
------
POST https://api-datadashboard.fda.gov/v1/import_refusals (client and credential handling in _ddapi.py).
Field definitions: https://datadashboard.fda.gov/oii/api/api-definitions-refusals.htm.
Scope filter: ProductCategory "Devices" (35,912 rows on 2026-11-04) plus "Radiological Health" (1,579 rows;
FDA lists radiation-emitting electronic products as their own category), fetched as two queries.
Charge codes: FDA's public list https://datadashboard.fda.gov/oii/download/ACT_SECTION_CHARGES.CSV
(columns ASC_ID, CHRG_CODE, CHRG_STMNT_TEXT, SCTN_NAME; no credentials needed), cached with the pages.

NODES
-----
  ex:ImportRefusal        one per ShipmentID (FDA: "the full shipment ID line ... Entry_Num, Rfrnc_Doc_Id,
                          Line_Num and Line_Sfx_Id"): refusal date, FEI (-> iri.manufacturer(fei=...)), firm
                          name and address, FDA import product code (verbatim) and its description, industry
                          code, product category, import division, sample-analysis flags, the charge list as
                          published (ex:refusalChargeCodes "118,235,3280,341") and one ex:hasRefusalCharge per
                          code.
  ex:ImportRefusalCharge  one per ASC_ID in FDA's charge list (all 305 rows, every product area), labelled with
                          FDA's short charge code and carrying the charge statement and Act section.

DEVICE PRODUCT-CODE LINK (rule)
-------------------------------
An FDA import product code for a device is the 2-digit industry code followed by the 3-letter device
product code ("86HQY" = industry 86, ophthalmic devices + HQY). The refusal is linked with
ex:hasProductCode iri.product_code(XYZ) ONLY when the import code is exactly 2 digits + 3 letters AND
XYZ is an ex:productCode in classification.ttl (the CDRH product classification database). Any other
code (e.g. a 7-character food/drug-style code, or a radiological code whose letters are not a CDRH
device product code) keeps only the verbatim ex:importProductCode. classification.ttl is read from
the output directory unless classification_ttl / $DD_CLASSIFICATION_TTL is given.

Firms whose name reads as a person (_ddapi.is_person: a degree or honorific) or whose FEI dd_inspections
judged to be an individual keep no name, street address, city, ZIP, FEI or profile URL. The listed firm
is the foreign manufacturer or shipper; a plain personal name without those signals is not detectable
here (the strict name-shape test is reserved for Bioresearch Monitoring, see _ddapi.is_person).

Minters (iri.py, aliased in _ddapi): import_refusal(shipment_id) -> .../id/import-refusal/<slug(shipment id)>;
                              import_refusal_charge(asc_id) -> .../id/import-refusal-charge/<asc_id>
"""
import csv
import io
import json
import os
import re
import sys
import time
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
import _ddapi as dd   # noqa: E402

NAME = "dd_import_refusals"
DATASET = "fda-data-dashboard/import_refusals"
ENDPOINT = "import_refusals"
QUERIES = [("devices", {"ProductCategory": ["Devices"]}),
           ("radiological", {"ProductCategory": ["Radiological Health"]})]
SOURCE_URL = "https://datadashboard.fda.gov/oii/cd/imprefusals.htm"
CHARGES_URL = "https://datadashboard.fda.gov/oii/download/ACT_SECTION_CHARGES.CSV"
CHARGES_FILE = "ACT_SECTION_CHARGES.CSV"
_DEVICE_IMPORT_CODE = re.compile(r"^(\d{2})([A-Z]{3})$")
_PC_LINE = re.compile(r'^\s+ex:productCode "([A-Z]{3})"')


def fetch(dest, log=print, client=None):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / CHARGES_FILE
    if not p.exists() or (time.time() - p.stat().st_mtime) / 86400 >= dd.CACHE_DAYS:
        req = urllib.request.Request(CHARGES_URL, headers={"User-Agent": dd.UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            p.write_bytes(r.read())
        time.sleep(1)
        log(f"  dd {NAME}: charge list fetched ({p.stat().st_size} bytes)")
    ref = base.FileRef(url=CHARGES_URL, path=CHARGES_FILE, sha256=base.sha256_file(p), bytes=p.stat().st_size)
    return dd.fetch_endpoint(dest, ENDPOINT, QUERIES, NAME, DATASET, client=client, log=log, extra_files=[ref])


def load_charges(src):
    p = Path(src) / CHARGES_FILE
    if not p.exists():
        return {}
    text = p.read_bytes().decode("utf-8-sig", errors="replace")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        k = (row.get("ASC_ID") or "").strip()
        if k:
            out[k] = row
    return out


def load_product_codes(path):
    codes = set()
    p = Path(path) if path else None
    if p and p.exists():
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                m = _PC_LINE.match(line)
                if m:
                    codes.add(m.group(1))
    return codes


def device_code(import_code, known):
    """'86HQY' -> 'HQY' when HQY is a known CDRH product code; else None (see module docstring)."""
    m = _DEVICE_IMPORT_CODE.match(dd.s(import_code) or "")
    return m.group(2) if m and m.group(2) in known else None


def transform(src, out, retrieved_at=None, classification_ttl=None):
    src, out = Path(src), Path(out)
    retrieved_at = dd.retrieved_at_of(src, retrieved_at)
    classification_ttl = classification_ttl or os.environ.get("DD_CLASSIFICATION_TTL") or \
        (out.parent / "classification.ttl")
    known = load_product_codes(classification_ttl) if not isinstance(classification_ttl, set) else classification_ttl
    charges = load_charges(src)
    st = base.Stats(source=NAME)
    recs = {}
    counts = {"rows": 0, "duplicate_shipment_rows": 0, "person_withheld": 0, "with_fei": 0,
              "known_product_codes": len(known), "codes_5char_digit_letters": 0, "product_code_linked": 0,
              "product_code_shape_not_device": 0, "product_code_letters_not_in_classification": 0,
              "charge_links": 0, "charge_ids_not_in_list": 0, "charges_listed": len(charges), "by_category": {}}
    for _, r in dd.iter_rows(src):
        counts["rows"] += 1
        sid = dd.s(r.get("ShipmentID"))
        if not sid:
            st.skipped += 1
            continue
        if sid in recs:
            counts["duplicate_shipment_rows"] += 1
        recs.setdefault(sid, []).append(r)
    hub, missing_charges = set(), set()
    _, person_feis, counts["person_context"] = dd.person_context(src)
    with base.TurtleWriter(out) as w:
        for sid in sorted(recs):
            rows = recs[sid]
            r0 = rows[0]
            person, fei, firm = dd.firm_pairs(r0, name_key="FirmName", state_code_key=None, state_name_key="State",
                                              person_feis=person_feis)
            counts["person_withheld"] += person
            counts["with_fei"] += bool(fei)
            name = None if person else common.norm_text(r0.get("FirmName"))
            date = dd.s(r0.get("RefusalDate"))
            pairs = [("a", "ex:ImportRefusal"),
                     ("rdfs:label", common.label(f"Import refusal {sid}", name, date)),
                     ("ex:shipmentId", common.lit(sid)),
                     ("ex:refusalDate", common.date_lit(date))]
            seen = set()
            for r in rows:
                for f, pred, fn in (("ProductCode", "ex:importProductCode", common.lit),
                                    ("ProductCodeDescription", "ex:importProductCodeDescription", common.lit),
                                    ("IndustryCode", "ex:industryCode", common.lit),
                                    ("IndustryCodeDescription", "ex:industryCodeDescription", common.lit),
                                    ("ProductCategory", "ex:productCategory", common.lit),
                                    ("DistrictCode", "ex:importDivisionCode", common.lit),
                                    ("DistrictDescription", "ex:importDivision", common.lit),
                                    ("FDASampleAnalysis", "ex:fdaSampleAnalysis", lambda v: common.bool_lit(dd.yes_no(v))),
                                    ("PrivateLabAnalysis", "ex:privateLabAnalysis", lambda v: common.bool_lit(dd.yes_no(v))),
                                    ("RefusalCharges", "ex:refusalChargeCodes", common.lit)):
                    v = dd.s(r.get(f))
                    o = fn(v) if v else None
                    if o and (pred, o) not in seen:
                        seen.add((pred, o))
                        pairs.append((pred, o))
                cat = dd.s(r.get("ProductCategory")) or "?"
                code = dd.s(r.get("ProductCode"))
                if code and ("pc", code) not in seen:
                    seen.add(("pc", code))
                    counts["by_category"][cat] = counts["by_category"].get(cat, 0) + 1
                    m = _DEVICE_IMPORT_CODE.match(code)
                    if m:
                        counts["codes_5char_digit_letters"] += 1
                    pc = device_code(code, known)
                    if pc:
                        counts["product_code_linked"] += 1
                        pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                        hub.add(pc)
                    elif m:
                        counts["product_code_letters_not_in_classification"] += 1
                    else:
                        counts["product_code_shape_not_device"] += 1
                for c in re.split(r"[,;\s]+", dd.s(r.get("RefusalCharges")) or ""):
                    if c and ("ch", c) not in seen:
                        seen.add(("ch", c))
                        pairs.append(("ex:hasRefusalCharge", f"<{dd.import_refusal_charge(c)}>"))
                        counts["charge_links"] += 1
                        if c not in charges:
                            missing_charges.add(c)
            pairs += firm
            pairs += common.provenance(DATASET, sid, source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(dd.import_refusal(sid), pairs)
            st.records += 1
        for k in sorted(charges, key=lambda x: (len(x), x)):
            c = charges[k]
            w.block(dd.import_refusal_charge(k), [
                ("a", "ex:ImportRefusalCharge"),
                ("rdfs:label", common.label(f"Import refusal charge {k}", c.get("CHRG_CODE"))),
                ("ex:chargeId", common.lit(k)),
                ("ex:chargeCode", common.lit(common.norm_text(c.get("CHRG_CODE")))),
                ("ex:chargeStatement", common.lit(common.norm_text(c.get("CHRG_STMNT_TEXT")))),
                ("ex:actSectionName", common.lit(common.norm_text(c.get("SCTN_NAME")))),
            ] + common.provenance(DATASET + "/charges", k, source_url=CHARGES_URL, retrieved_at=retrieved_at))
        for k in sorted(missing_charges, key=lambda x: (len(x), x)):      # cited but not in FDA's list
            w.block(dd.import_refusal_charge(k), [
                ("a", "ex:ImportRefusalCharge"), ("rdfs:label", common.lit(f"Import refusal charge {k}")),
                ("ex:chargeId", common.lit(k)),
                ("ex:dataQualityNote", common.lit("charge id cited on refusals but absent from FDA's ACT_SECTION_CHARGES list"))])
        for pc in sorted(hub):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    counts["charge_ids_not_in_list"] = len(missing_charges)
    st.triples = w.triples
    counts.update(records=st.records, triples=st.triples, classification_ttl=str(classification_ttl)
                  if not isinstance(classification_ttl, set) else "selftest")
    out.with_suffix(".counts.json").write_text(json.dumps(counts, indent=1), encoding="utf-8")
    print(f"  {NAME}: {counts}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim API rows (2026-11-04): two device refusals and one radiological refusal, plus a constructed
# private importer (a person) with a 7-character code; verbatim charge-list header and two rows
# (the 118 row's text constructed in FDA's format).
_ROWS = [
    {"FEINumber": "3012800615", "FirmName": "MAVERICK & WOLF", "AddressLine1": "150 Chiswick High Road",
     "AddressLine2": None, "City": "London", "State": None, "ZipCode": None, "CountryCode": "GB",
     "CountryName": "United Kingdom", "DistrictCode": "CIN-DO", "DistrictDescription": "Cincinnati District Office",
     "IndustryCode": "86", "IndustryCodeDescription": "Ophthalmic Medical Devices", "ProductCategory": "Devices",
     "ProductCode": "86HQY", "ProductCodeDescription": "SUNGLASSES (NON-PRESCRIPTION INCLUDING PHOTOSENSITIVE)",
     "RefusalDate": "2016-10-03", "ShipmentID": "UPS-0443680-5/1/1", "FDASampleAnalysis": "No ",
     "PrivateLabAnalysis": "No ", "RefusalCharges": "118,235,3280,341",
     "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIu=3012800615"},
    {"FEINumber": "3008270875", "FirmName": "P&G", "AddressLine1": "Sulzbacher Str. 40-50", "AddressLine2": None,
     "City": "Schwalbach am Taunus", "State": "Hesse", "ZipCode": None, "CountryCode": "DE", "CountryName": "Germany",
     "DistrictCode": "DNEI", "DistrictDescription": "Division of Northeast Imports", "IndustryCode": "85",
     "IndustryCodeDescription": "Obstetrics and Gynecology Medical Devices", "ProductCategory": "Devices",
     "ProductCode": "85NRC", "ProductCodeDescription": "PADS, MENSTRUAL, SCENTED-DEODORIZED",
     "RefusalDate": "2016-10-03", "ShipmentID": "EB5-1537172-3/1/1", "FDASampleAnalysis": "No ",
     "PrivateLabAnalysis": "No ", "RefusalCharges": "341",
     "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIu=3008270875"},
    {"FEINumber": "3010942033", "FirmName": "Shenzhen Yada Cosmetics Co., Ltd.", "AddressLine1": "Xiangyuer Industrial Park",
     "AddressLine2": "Longcheng Street Ailong Road; Longgang", "City": "Shenzhen", "State": "Guangdong",
     "ZipCode": None, "CountryCode": "CN", "CountryName": "China", "DistrictCode": "DNEI",
     "DistrictDescription": "Division of Northeast Imports", "IndustryCode": "95", "IndustryCodeDescription": "Light",
     "ProductCategory": "Radiological Health", "ProductCode": "95RFF",
     "ProductCodeDescription": "LASER WELDER, MATERIALS PROCESSING LASER PRODUCTS", "RefusalDate": "2016-10-07",
     "ShipmentID": "045-0397050-3/4/1", "FDASampleAnalysis": "No ", "PrivateLabAnalysis": "No ",
     "RefusalCharges": "476", "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIu=3010942033"},
    {"FEINumber": "3009999999", "FirmName": "Dr. John Q Public", "AddressLine1": "12 Elm Street", "AddressLine2": None,
     "City": "Springfield", "State": "IL", "ZipCode": "62701", "CountryCode": "US", "CountryName": "United States",
     "DistrictCode": "CHI-DO", "DistrictDescription": "Chicago District Office", "IndustryCode": "80",
     "IndustryCodeDescription": "General Hospital", "ProductCategory": "Devices", "ProductCode": "80FMFA1",
     "ProductCodeDescription": "SYRINGE", "RefusalDate": "2020-01-02", "ShipmentID": "AAA-0000001-1/1/1",
     "FDASampleAnalysis": "Yes", "PrivateLabAnalysis": "No ", "RefusalCharges": "118",
     "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIu=3009999999"},
]
_CHARGES = ('﻿ASC_ID,CHRG_CODE,CHRG_STMNT_TEXT,SCTN_NAME\r\n'
            '118,NOT LISTED,"It appears the device is not included in a list required by Section 510(j), or a notice or '
            'other information respecting it was not provided as required by section 510 or 518(a).","502(o), 801(a)(3); '
            'MISBRANDING"\r\n'
            '324,NO ENGLISH,"The article is subject to refusal of admission pursuant to Section 801(a)(3) of the FD&C Act in '
            'that it appears to be misbranded within the meaning of Section 403(f) of the FD&C Act.","403(f), 801(a)(3); '
            'MISBRANDING"\r\n')


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "devices_s0000001.json").write_text(json.dumps({"result": [_ROWS[0], _ROWS[1], _ROWS[3]]}),
                                                           encoding="utf-8")
        (t / "pages" / "radiological_s0000001.json").write_text(json.dumps({"result": [_ROWS[2]]}), encoding="utf-8")
        (t / CHARGES_FILE).write_bytes(_CHARGES.encode("utf-8"))
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-04", files=[]).write(t / "manifest.json")
        st = transform(t, t / "dd_import_refusals.ttl", classification_ttl={"HQY", "FMF"})
        ttl = (t / "dd_import_refusals.ttl").read_text(encoding="utf-8")
        counts = json.loads((t / "dd_import_refusals.counts.json").read_text(encoding="utf-8"))
    assert st.records == 4, st
    assert device_code("86HQY", {"HQY"}) == "HQY" and device_code("85NRC", {"HQY"}) is None
    assert device_code("80FMFA1", {"FMF"}) is None and device_code("16AGT64", {"AGT"}) is None
    b = ttl.split(f"<{dd.import_refusal('UPS-0443680-5/1/1')}>")[1].split("\n\n")[0]
    assert "a ex:ImportRefusal" in b and 'ex:importProductCode "86HQY"' in b
    assert f"ex:hasProductCode <{iri.product_code('HQY')}>" in b and 'ex:refusalChargeCodes "118,235,3280,341"' in b
    assert all(f"<{dd.import_refusal_charge(c)}>" in b for c in ("118", "235", "3280", "341"))
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='3012800615')}>" in b and 'ex:refusalDate "2016-10-03"^^xsd:date' in b
    assert 'ex:fdaSampleAnalysis "false"^^xsd:boolean' in b and 'ex:importDivisionCode "CIN-DO"' in b
    assert 'rdfs:label "Import refusal UPS-0443680-5/1/1: MAVERICK & WOLF: 2016-10-03"' in b
    g = ttl.split(f"<{dd.import_refusal('EB5-1537172-3/1/1')}>")[1].split("\n\n")[0]
    assert "hasProductCode" not in g and 'ex:stateName "Hesse"' in g
    rad = ttl.split(f"<{dd.import_refusal('045-0397050-3/4/1')}>")[1].split("\n\n")[0]
    assert 'ex:productCategory "Radiological Health"' in rad and "hasProductCode" not in rad
    ch = ttl.split(f"<{dd.import_refusal_charge('118')}>\n")[1].split("\n\n")[0]
    assert "a ex:ImportRefusalCharge" in ch and 'rdfs:label "Import refusal charge 118: NOT LISTED"' in ch
    assert 'ex:actSectionName "502(o), 801(a)(3); MISBRANDING"' in ch
    assert f"<{dd.import_refusal_charge('3280')}>\n    a ex:ImportRefusalCharge" in ttl     # cited, not listed
    assert f"<{iri.product_code('HQY')}>\n    a ex:ProductCode" in ttl
    for x in ("Public", "Elm Street", "Springfield", "62701", "3009999999"):
        assert x not in ttl, x
    assert counts["product_code_linked"] == 1 and counts["product_code_letters_not_in_classification"] == 2
    assert counts["product_code_shape_not_device"] == 1 and counts["person_withheld"] == 1
    print(f"dd_import_refusals.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    dd._selftest()
    _selftest()
