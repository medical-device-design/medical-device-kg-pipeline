#!/usr/bin/env python3
"""
dd_inspections.py -- FDA Data Dashboard inspection classifications (devices) -> RDF.

SOURCE
------
POST https://api-datadashboard.fda.gov/v1/inspections_classifications (client and credential handling in
_ddapi.py). Field definitions: https://datadashboard.fda.gov/oii/api/api-definitions-inspections.htm.
Scope filter: ProductType "Devices" (FDA's list of values is Biologics, Devices, Drugs, Food/Cosmetics,
Tobacco, Veterinary; radiation-emitting product inspections by CDRH carry ProductType "Devices", there is
no separate value). On 2026-11-04 the filter matched 50,601 rows.

ONE ROW PER (inspection, project area)
--------------------------------------
FDA lists an inspection once per project area covered (e.g. "Postmarket Assurance: Devices" and
"Compliance: Devices" for the same InspectionID). The rows are merged into ONE ex:FDAInspection node per
InspectionID; every project area is kept (ex:projectArea, repeated). If FDA gives different
classifications for one inspection, all are kept and ex:dataQualityNote says so.

KEPT: FEI (-> iri.manufacturer(fei=...), the same establishment node registration/listing and the recall
database use), legal name, firm business address, inspection ID, end date, fiscal year, classification
(text and NAI/VAI/OAI code), project areas, product type, posted-citations flag, FMD-145 date, additional
details (main file only if scrub.scrub_text finds no personal-data pattern, else scrubbed in
dd_inspections_text.ttl), firm-profile URL. NEVER: inspector names (the API does not publish them).
Individuals (_ddapi.is_person / person_context): a firm name with a degree suffix or honorific, or, for
Bioresearch Monitoring inspections (clinical investigators), any name shaped like a personal name with no
organization word; every inspection of an FEI so judged is treated the same. Those keep no name, street
address, city, ZIP, FEI or profile URL (ex:dataQualityNote says so).

Minter (iri.fda_inspection, aliased as _ddapi.fda_inspection): .../id/fda-inspection/<InspectionID>
"""
import json
import os
import sys
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import common     # noqa: E402
import base       # noqa: E402
import _ddapi as dd   # noqa: E402

NAME = "dd_inspections"
DATASET = "fda-data-dashboard/inspections_classifications"
ENDPOINT = "inspections_classifications"
QUERIES = [("devices", {"ProductType": ["Devices"]})]
SOURCE_URL = "https://datadashboard.fda.gov/oii/cd/inspections.htm"


def fetch(dest, log=print, client=None):
    return dd.fetch_endpoint(dest, ENDPOINT, QUERIES, NAME, DATASET, client=client, log=log)


def _group(src):
    groups = {}
    for _, r in dd.iter_rows(src):
        iid = dd.s(r.get("InspectionID"))
        if not iid:
            groups.setdefault(None, []).append(r)
            continue
        groups.setdefault(iid, []).append(r)
    return groups


def _uniq(rows, key):
    out = []
    for r in rows:
        v = dd.s(r.get(key))
        if v and v not in out:
            out.append(v)
    return out


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    retrieved_at = dd.retrieved_at_of(src, retrieved_at)
    st = base.Stats(source=NAME)
    groups = _group(src)
    st.skipped = len(groups.pop(None, []))
    bimo, person_feis, _ = dd.person_context(src, root=src)
    counts = {"rows": sum(len(v) for v in groups.values()) + st.skipped, "person_withheld": 0,
              "bimo_inspections": len(bimo), "person_feis": len(person_feis),
              "multi_classification": 0, "details_main": 0, "details_text": 0, "with_fei": 0}
    text_out = out.with_name(out.stem + "_text.ttl")
    with base.TurtleWriter(out) as w, base.TurtleWriter(text_out) as wt:
        for iid in sorted(groups, key=lambda k: (len(k), k)):
            rows = groups[iid]
            r0 = rows[0]
            person, fei, firm = dd.firm_pairs(r0, strict=iid in bimo, person_feis=person_feis)
            counts["person_withheld"] += person
            counts["with_fei"] += bool(fei)
            classes, codes = _uniq(rows, "Classification"), _uniq(rows, "ClassificationCode")
            end = dd.s(r0.get("InspectionEndDate"))
            name = None if person else common.norm_text(r0.get("LegalName"))
            pairs = [("a", "ex:FDAInspection"),
                     ("rdfs:label", common.label(f"Inspection {iid}", name, end) if name else
                      common.label(f"Inspection {iid}", end)),
                     ("ex:inspectionId", common.lit(iid)),
                     ("ex:inspectionEndDate", common.date_lit(end)),
                     ("ex:fiscalYear", common.int_lit(dd.s(r0.get("FiscalYear"))))]
            pairs += [("ex:inspectionClassification", common.lit(c)) for c in classes]
            pairs += [("ex:inspectionClassificationCode", common.lit(c)) for c in codes]
            pairs += [("ex:projectArea", common.lit(p)) for p in _uniq(rows, "ProjectArea")]
            pairs += [("ex:productType", common.lit(p)) for p in _uniq(rows, "ProductType")]
            flags = {dd.yes_no(r.get("PostedCitations")) for r in rows} - {None}
            pairs += [("ex:postedCitations", f'"{f}"^^xsd:boolean') for f in sorted(flags)]
            pairs += [("ex:fmd145Date", common.date_lit(d)) for d in _uniq(rows, "FMD145Date")]
            if len(codes) > 1 or len(classes) > 1:
                counts["multi_classification"] += 1
                pairs.append(("ex:dataQualityNote", common.lit(
                    "FDA lists more than one classification for this inspection (one per project area)")))
            for det in _uniq(rows, "AdditionalDetails"):
                main, scrubbed, _ = dd.split_text(det)
                if main:
                    pairs.append(("ex:inspectionAdditionalDetails", common.lit(main)))
                    counts["details_main"] += 1
                elif scrubbed:
                    wt.block(dd.fda_inspection(iid), [("ex:inspectionAdditionalDetails", common.lit(scrubbed))])
                    counts["details_text"] += 1
            pairs += firm
            pairs += common.implausible_year_pairs(end, "inspection end date", lo=1900)
            pairs += common.provenance(DATASET, iid, source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(dd.fda_inspection(iid), pairs)
            st.records += 1
    st.triples = w.triples
    counts.update(records=st.records, triples=st.triples, text_triples=wt.triples)
    out.with_suffix(".counts.json").write_text(json.dumps(counts, indent=1), encoding="utf-8")
    print(f"  {NAME}: {counts}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim API rows (2026-11-04): the two rows of inspection 1277550 (two project areas), and one row
# of a constructed clinical-investigator inspection in the same format (a person: name must not appear).
_ROWS = [
    {"FEINumber": "3010402691", "LegalName": "Foremount Enterprise Co., Ltd.",
     "AddressLine1": "Shengang District, No. 257 Changping Rd., Sec. 5; Shen Gang Qushi", "AddressLine2": None,
     "City": "Tai Zhong Shi", "StateCode": None, "State": None, "ZipCode": None, "CountryCode": "TW",
     "CountryName": "Taiwan", "InspectionID": "1277550", "InspectionEndDate": "2025-06-19", "FiscalYear": "2025",
     "PostedCitations": "No ", "Classification": "Official Action Indicated (OAI)", "ClassificationCode": "OAI",
     "ProjectArea": "Postmarket Assurance: Devices", "ProductType": "Devices", "AdditionalDetails": None,
     "FMD145Date": None, "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIi=3010402691"},
    {"FEINumber": "3010402691", "LegalName": "Foremount Enterprise Co., Ltd.",
     "AddressLine1": "Shengang District, No. 257 Changping Rd., Sec. 5; Shen Gang Qushi", "AddressLine2": None,
     "City": "Tai Zhong Shi", "StateCode": None, "State": None, "ZipCode": None, "CountryCode": "TW",
     "CountryName": "Taiwan", "InspectionID": "1277550", "InspectionEndDate": "2025-06-19", "FiscalYear": "2025",
     "PostedCitations": "No ", "Classification": "Official Action Indicated (OAI)", "ClassificationCode": "OAI",
     "ProjectArea": "Compliance: Devices", "ProductType": "Devices", "AdditionalDetails": None,
     "FMD145Date": None, "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIi=3010402691"},
    {"FEINumber": "3009999999", "LegalName": "Jane Q. Doe, M.D.", "AddressLine1": "12 Elm Street",
     "AddressLine2": None, "City": "Springfield", "StateCode": "IL", "State": "Illinois", "ZipCode": "62701",
     "CountryCode": "US", "CountryName": "United States", "InspectionID": "1100001",
     "InspectionEndDate": "2019-03-01", "FiscalYear": "2019", "PostedCitations": "Yes",
     "Classification": "Voluntary Action Indicated (VAI)", "ClassificationCode": "VAI",
     "ProjectArea": "Bioresearch Monitoring", "ProductType": "Devices",
     "AdditionalDetails": "CALL JANE DOE AT 555-123-4567.", "FMD145Date": "2019-04-10",
     "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIi=3009999999"},
    # constructed: a clinical investigator without a degree suffix (Bioresearch Monitoring -> strict test),
    # and a later inspection of the first individual's FEI under another name form (withheld by FEI)
    {"FEINumber": "3008888888", "LegalName": "Richard Roe", "AddressLine1": "9 Oak Ave", "City": "Dayton",
     "CountryCode": "US", "CountryName": "United States", "InspectionID": "1100002",
     "InspectionEndDate": "2020-02-02", "FiscalYear": "2020", "PostedCitations": "No ",
     "Classification": "No Action Indicated (NAI)", "ClassificationCode": "NAI",
     "ProjectArea": "Bioresearch Monitoring", "ProductType": "Devices"},
    {"FEINumber": "3009999999", "LegalName": "Jane Q. Doe", "AddressLine1": "12 Elm Street", "City": "Springfield",
     "CountryCode": "US", "CountryName": "United States", "InspectionID": "1100003",
     "InspectionEndDate": "2021-02-02", "FiscalYear": "2021", "PostedCitations": "No ",
     "Classification": "No Action Indicated (NAI)", "ClassificationCode": "NAI",
     "ProjectArea": "Compliance: Devices", "ProductType": "Devices"},
]


def _selftest():
    import tempfile
    import iri
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "devices_s0000001.json").write_text(json.dumps(
            {"endpoint": ENDPOINT, "statuscode": 400, "resultcount": len(_ROWS), "result": _ROWS}), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-04", files=[]).write(t / "manifest.json")
        st = transform(t, t / "dd_inspections.ttl")
        ttl = (t / "dd_inspections.ttl").read_text(encoding="utf-8")
        txt = (t / "dd_inspections_text.ttl").read_text(encoding="utf-8")
    assert st.records == 4, st
    for x in ("Roe", "Oak Ave", "Dayton", "3008888888"):
        assert x not in ttl, x
    blk = ttl.split(f"<{dd.fda_inspection('1277550')}>")[1].split("\n\n")[0]
    assert "a ex:FDAInspection" in blk and 'ex:inspectionClassificationCode "OAI"' in blk
    assert 'ex:projectArea "Postmarket Assurance: Devices"' in blk and 'ex:projectArea "Compliance: Devices"' in blk
    assert 'ex:inspectionEndDate "2025-06-19"^^xsd:date' in blk and 'ex:fiscalYear "2025"^^xsd:integer' in blk
    assert 'ex:postedCitations "false"^^xsd:boolean' in blk and 'ex:countryCode "TW"' in blk
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='3010402691')}>" in blk
    assert 'rdfs:label "Inspection 1277550: Foremount Enterprise Co., Ltd.: 2025-06-19"' in blk
    assert blk.count("rdfs:label") == 1 and "dataQualityNote" not in blk
    p = ttl.split(f"<{dd.fda_inspection('1100001')}>")[1].split("\n\n")[0]
    for x in ("Doe", "DOE", "555-123", "Elm Street", "Springfield", "62701", "3009999999"):
        assert x not in ttl and x not in txt, x
    assert "firm name reads as an individual" in p and 'ex:fmd145Date "2019-04-10"^^xsd:date' in p
    assert "[PHONE REMOVED]" in txt and "inspectionAdditionalDetails" not in p
    print(f"dd_inspections.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    dd._selftest()
    _selftest()
