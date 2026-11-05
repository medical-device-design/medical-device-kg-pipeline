#!/usr/bin/env python3
"""
dd_compliance_actions.py -- FDA Data Dashboard compliance actions (devices) -> RDF.

SOURCE
------
POST https://api-datadashboard.fda.gov/v1/compliance_actions (client and credential handling in _ddapi.py).
Field definitions: https://datadashboard.fda.gov/oii/api/api-definitions-compliance-actions.htm.
Scope filter: ProductType "Devices" (1,844 rows on 2026-11-04; Center "CDRH" gives the same 1,844).
ActionType values: Warning Letter, Injunction, Seizure.

ONE NODE PER (action type, Case/Injunction ID, FEI)
---------------------------------------------------
One case can name several firms (an injunction against a parent and its sites; a warning letter to
several Olympus establishments), so FDA lists one row per firm. Each (type, case, FEI) is one
ex:ComplianceAction node with the firm's FEI (-> iri.manufacturer(fei=...)), legal name and address.
Exact repeats are merged; a second firm name for the same FEI is kept as another ex:companyName.

WARNING-LETTER LINK
-------------------
For ActionType "Warning Letter" the Case/Injunction ID is the MARCS-CMS case number. The action is
linked (ex:relatedWarningLetter) to every ex:WarningLetter node in warning_letters.ttl whose
ex:marcsCmsNumber equals it, preferring the letters whose ex:letterIssueDate equals the action date
(if none has that date, all letters with the number are linked and the counts say so).
warning_letters.ttl is read from the transform's output directory unless wl_ttl is given.

Firms whose name reads as a person (_ddapi.is_person: degree or honorific), or whose FEI dd_inspections
judged to be an individual (_ddapi.person_context, e.g. a clinical investigator), keep no name, street address, city, ZIP, FEI or
profile URL; their node key uses "no-fei".

Minter (iri.py, aliased in _ddapi): fda_compliance_action(type, case, fei)
    -> .../id/fda-compliance-action/<slug(type)>/<case>/<fei | no-fei>
"""
import json
import os
import re
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

NAME = "dd_compliance_actions"
DATASET = "fda-data-dashboard/compliance_actions"
ENDPOINT = "compliance_actions"
QUERIES = [("devices", {"ProductType": ["Devices"]})]
SOURCE_URL = "https://datadashboard.fda.gov/oii/cd/complianceactions.htm"

_SUBJ = re.compile(r"^<(\S+/id/warning-letter/\S+)>\s*$")
_CMS = re.compile(r'^\s+ex:marcsCmsNumber "([^"]+)"')
_ISSUE = re.compile(r'^\s+ex:letterIssueDate "([^"]+)"')


def fetch(dest, log=print, client=None):
    return dd.fetch_endpoint(dest, ENDPOINT, QUERIES, NAME, DATASET, client=client, log=log)


def load_warning_letters(path):
    """{cms number: [(letter IRI, issue date or None), ...]} from warning_letters.ttl (streamed)."""
    out = {}
    p = Path(path) if path else None
    if not p or not p.exists():
        return out
    subj, cms, issue = None, [], None

    def flush():
        for c in cms:
            out.setdefault(c, []).append((subj, issue))
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            m = _SUBJ.match(line)
            if m:
                if subj:
                    flush()
                subj, cms, issue = m.group(1), [], None
                continue
            if subj is None:
                continue
            m = _CMS.match(line)
            if m:
                cms.append(m.group(1))
                continue
            m = _ISSUE.match(line)
            if m:
                issue = m.group(1)
    if subj:
        flush()
    return out


def transform(src, out, retrieved_at=None, wl_ttl=None):
    src, out = Path(src), Path(out)
    retrieved_at = dd.retrieved_at_of(src, retrieved_at)
    wl_ttl = wl_ttl or os.environ.get("DD_WARNING_LETTERS_TTL") or (out.parent / "warning_letters.ttl")
    wls = load_warning_letters(wl_ttl)
    st = base.Stats(source=NAME)
    nodes = {}
    counts = {"rows": 0, "person_withheld": 0, "warning_letter_actions": 0, "wl_linked_same_date": 0,
              "wl_linked_number_only": 0, "wl_not_found": 0, "warning_letter_nodes_read": sum(map(len, wls.values())),
              "wl_ttl": str(wl_ttl), "by_type": {}}
    _, person_feis, counts["person_context"] = dd.person_context(src)
    for _, r in dd.iter_rows(src):
        counts["rows"] += 1
        typ, case = dd.s(r.get("ActionType")), dd.s(r.get("CaseInjunctionID"))
        if not typ or not case:
            st.skipped += 1
            continue
        person, fei, firm = dd.firm_pairs(r, person_feis=person_feis)
        key = (typ, case, fei)
        n = nodes.setdefault(key, {"rows": [], "firm": [], "person": person})
        n["rows"].append(r)
        for p in firm:
            if p not in n["firm"]:
                n["firm"].append(p)
    with base.TurtleWriter(out) as w:
        for (typ, case, fei) in sorted(nodes, key=lambda k: (k[0], len(k[1]), k[1], k[2] or "")):
            n = nodes[(typ, case, fei)]
            rows = n["rows"]
            r0 = rows[0]
            counts["person_withheld"] += n["person"]
            counts["by_type"][typ] = counts["by_type"].get(typ, 0) + 1
            name = None if n["person"] else common.norm_text(r0.get("LegalName"))
            date = dd.s(r0.get("ActionTakenDate"))
            pairs = [("a", "ex:ComplianceAction"),
                     ("rdfs:label", common.label(f"{typ} {case}", name)),
                     ("ex:complianceActionType", common.lit(typ)),
                     ("ex:caseInjunctionId", common.lit(case))]
            for f, pred, fn in (("ActionTakenDate", "ex:actionTakenDate", common.date_lit),
                                ("FiscalYear", "ex:fiscalYear", common.int_lit),
                                ("ProductType", "ex:productType", common.lit),
                                ("Center", "ex:fdaCenter", common.lit),
                                ("Region", "ex:firmRegion", common.lit)):
                vals = []
                for r in rows:
                    v = dd.s(r.get(f))
                    if v and v not in vals:
                        vals.append(v)
                pairs += [(pred, fn(v)) for v in vals]
            pairs += n["firm"]
            if typ.lower() == "warning letter":
                counts["warning_letter_actions"] += 1
                cands = wls.get(case, [])
                same = [u for u, d in cands if d == date]
                links = same or [u for u, _ in cands]
                counts["wl_linked_same_date" if same else "wl_linked_number_only" if links else "wl_not_found"] += 1
                for u in sorted(set(links)):
                    pairs.append(("ex:relatedWarningLetter", f"<{u}>"))
            pairs += common.provenance(DATASET, f"{typ}/{case}" + (f"/{fei}" if fei else ""),
                                       source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(dd.fda_compliance_action(typ, case, fei), pairs)
            st.records += 1
    st.triples = w.triples
    counts.update(records=st.records, triples=st.triples)
    out.with_suffix(".counts.json").write_text(json.dumps(counts, indent=1), encoding="utf-8")
    print(f"  {NAME}: {counts}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim API rows (2026-11-04): two 2009 warning letters, two Olympus establishments under one case,
# a seizure, and a constructed letter to an individual in the same format.
_ROWS = [
    {"FEINumber": "1177786", "LegalName": "Centra Health Inc IRB", "AddressLine1": "3300 Rivermont Ave",
     "AddressLine2": None, "City": "Lynchburg", "StateCode": "VA", "State": "Virginia", "ZipCode": "24503-2030",
     "CountryCode": "US", "CountryName": "United States", "Region": "Domestic", "ProductType": "Devices",
     "Center": "CDRH", "ActionType": "Warning Letter", "ActionTakenDate": "2009-11-20", "FiscalYear": "2010",
     "CaseInjunctionID": "80585", "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIc=1177786"},
    {"FEINumber": "3002807798", "LegalName": "Olympus Winter & IBE Gmbh", "AddressLine1": "Kuehnstr. 61",
     "AddressLine2": None, "City": "Hamburg", "StateCode": None, "State": None, "ZipCode": None,
     "CountryCode": "DE", "CountryName": "Germany", "Region": "Foreign", "ProductType": "Devices",
     "Center": "CDRH", "ActionType": "Warning Letter", "ActionTakenDate": "2026-10-07", "FiscalYear": "2027",
     "CaseInjunctionID": "729208", "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIc=3002807798"},
    {"FEINumber": "2429304", "LegalName": "Olympus Corporation of the Americas", "AddressLine1": "3500 Corporate Pkwy",
     "AddressLine2": None, "City": "Center Valley", "StateCode": "PA", "State": "Pennsylvania", "ZipCode": "18034",
     "CountryCode": "US", "CountryName": "United States", "Region": "Domestic", "ProductType": "Devices",
     "Center": "CDRH", "ActionType": "Warning Letter", "ActionTakenDate": "2026-10-07", "FiscalYear": "2027",
     "CaseInjunctionID": "729208", "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIc=2429304"},
    {"FEINumber": "2429304", "LegalName": "Olympus Corporation of the Americas", "AddressLine1": "3500 Corporate Pkwy",
     "AddressLine2": None, "City": "Center Valley", "StateCode": "PA", "State": "Pennsylvania", "ZipCode": "18034",
     "CountryCode": "US", "CountryName": "United States", "Region": "Domestic", "ProductType": "Devices",
     "Center": "CDRH", "ActionType": "Warning Letter", "ActionTakenDate": "2026-10-07", "FiscalYear": "2027",
     "CaseInjunctionID": "729208", "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIc=2429304"},
    {"FEINumber": "3001234567", "LegalName": "Acme Surgical Supply LLC", "AddressLine1": "1 Main St",
     "AddressLine2": None, "City": "Anytown", "StateCode": "TX", "State": "Texas", "ZipCode": "75001",
     "CountryCode": "US", "CountryName": "United States", "Region": "Domestic", "ProductType": "Devices",
     "Center": "CDRH", "ActionType": "Seizure", "ActionTakenDate": "2016-08-22", "FiscalYear": "2016",
     "CaseInjunctionID": "471550", "FirmProfile": None},
    {"FEINumber": "3009999999", "LegalName": "Jane Q. Doe, M.D.", "AddressLine1": "12 Elm Street",
     "AddressLine2": None, "City": "Springfield", "StateCode": "IL", "State": "Illinois", "ZipCode": "62701",
     "CountryCode": "US", "CountryName": "United States", "Region": "Domestic", "ProductType": "Devices",
     "Center": "CDRH", "ActionType": "Warning Letter", "ActionTakenDate": "2025-04-01", "FiscalYear": "2025",
     "CaseInjunctionID": "700001", "FirmProfile": "https://datadashboard.fda.gov/oii/firmprofile.htm?FEIc=3009999999"},
]
_WL = """<{a}>
    a ex:WarningLetter ;
    ex:letterIssueDate "2026-10-07"^^xsd:date ;
    ex:marcsCmsNumber "729208" .

<{b}>
    a ex:WarningLetter ;
    ex:letterIssueDate "2026-10-01"^^xsd:date ;
    ex:marcsCmsNumber "729208" .

<{c}>
    a ex:WarningLetter ;
    ex:marcsCmsNumber "700001" .
"""


def _selftest():
    import tempfile
    import iri
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "devices_s0000001.json").write_text(json.dumps({"result": _ROWS}), encoding="utf-8")
        (t / "warning_letters.ttl").write_text(_WL.format(a=iri.warning_letter("729208-10072026"),
                                                          b=iri.warning_letter("729208-10012026"),
                                                          c=iri.warning_letter("700001-04012025")), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-04", files=[]).write(t / "manifest.json")
        st = transform(t, t / "dd_compliance_actions.ttl", wl_ttl=t / "warning_letters.ttl")   # not $DD_WARNING_LETTERS_TTL
        ttl = (t / "dd_compliance_actions.ttl").read_text(encoding="utf-8")
        counts = json.loads((t / "dd_compliance_actions.counts.json").read_text(encoding="utf-8"))
    assert st.records == 5, st                                   # Olympus duplicate merged
    ol = ttl.split(f"<{dd.fda_compliance_action('Warning Letter', '729208', '2429304')}>")[1].split("\n\n")[0]
    assert "a ex:ComplianceAction" in ol and ol.count("ex:companyName") == 1
    assert f"ex:relatedWarningLetter <{iri.warning_letter('729208-10072026')}>" in ol
    assert "729208_10012026" not in ol                           # same number, other date: not linked
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='2429304')}>" in ol
    assert 'rdfs:label "Warning Letter 729208: Olympus Corporation of the Americas"' in ol
    assert 'ex:actionTakenDate "2026-10-07"^^xsd:date' in ol and 'ex:fdaCenter "CDRH"' in ol
    sz = ttl.split(f"<{dd.fda_compliance_action('Seizure', '471550', '3001234567')}>")[1].split("\n\n")[0]
    assert 'ex:complianceActionType "Seizure"' in sz and "relatedWarningLetter" not in sz
    p = ttl.split(f"<{dd.fda_compliance_action('Warning Letter', '700001', None)}>")[1].split("\n\n")[0]
    assert "warning-letter/700001_04012025" in p and "individual" in p          # linked by number only
    for x in ("Doe", "Elm Street", "Springfield", "3009999999"):
        assert x not in ttl, x
    assert counts["wl_linked_same_date"] == 2 and counts["wl_linked_number_only"] == 1 and counts["wl_not_found"] == 1
    print(f"dd_compliance_actions.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    dd._selftest()
    _selftest()
