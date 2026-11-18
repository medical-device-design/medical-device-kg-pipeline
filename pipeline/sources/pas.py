#!/usr/bin/env python3
"""
pas.py -- CDRH Post-Approval Studies (PAS) database -> RDF.

The PAS database (pma_pas.cfm) has no bulk file, but its "export to Excel" action
returns a CSV of every study (358 on October 12, 2026; 28 columns):
    POST https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMA/pma_pas_Excel.cfm
FDA writes semicolons where the text had commas. The first column is
"<application> / <PAS number>", e.g. "P160030  / PAS001"; applications are PMAs (P...)
or HDEs (H...), which link to the existing PMA/HDE nodes.
"""
import os
import re
import sys
import csv
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
from identity import normalize_org   # noqa: E402

NAME = "pas"
DATASET = "cdrh/pas"
EXPORT_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMA/pma_pas_Excel.cfm"
SOURCE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMA/pma_pas.cfm"
UA = "medical-device-kg/rebuild (Hofstra University)"
_APP = re.compile(r"^\s*([A-Z]\d{6})\s*(?:/\s*(\S+))?")
COLS = {  # CSV column -> predicate (text unless typed below)
    "Device Name": "ex:deviceName", "Medical Specialty": "ex:medicalSpecialty",
    "Study Name": "ex:studyName", "Study Status": "ex:studyStatus", "Study Design": "ex:studyDesign",
    "Study involve follow-up of premarket cohort (Y/N)": "ex:followsPremarketCohort",
    "Data Source": "ex:dataSource", "Comparison Group": "ex:comparisonGroup", "Analysis Type": "ex:analysisType",
    "Study Population": "ex:studyPopulation", "Study Design Description": "ex:studyDesignDescription",
    "Study Population Description": "ex:studyPopulationDescription", "Sample Size": "ex:sampleSize",
    "Data Collection": "ex:dataCollection", "Followup Visits and Length of Followup": "ex:followUp",
    "Actual Number of Patients Enrolled": "ex:patientsEnrolled", "Actual Number of Sites Enrolled": "ex:sitesEnrolled",
    "Patient Followup Rate": "ex:patientFollowupRate", "Final Safety Findings": "ex:finalSafetyFindings",
    "Final Effectiveness Findings": "ex:finalEffectivenessFindings",
    "Study Strengths and Weaknesses": "ex:studyStrengthsWeaknesses",
    "Recommendations for Labeling Changes": "ex:labelingRecommendations",
    "Interim Safety Information": "ex:interimSafetyInformation",
}
DATES = {"Date PMA Approved": "ex:applicationApprovalDate",
         "Date Original Protocol Accepted": "ex:originalProtocolAcceptedDate",
         "Date Current Protocol Accepted": "ex:currentProtocolAcceptedDate"}
INTS = {"ex:patientsEnrolled", "ex:sitesEnrolled"}


def fetch(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "pas.csv"
    req = urllib.request.Request(EXPORT_URL, data=b"", headers={"User-Agent": UA})
    out.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    f = base.FileRef(url=EXPORT_URL, path=out.name, sha256=base.sha256_file(out), bytes=out.stat().st_size)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[f])


def transform(src, out, retrieved_at=None):
    src = Path(src)
    man = src / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    firms, seen = {}, {}
    with open(src / "pas.csv", encoding="latin-1", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if rows and "Application Number" not in rows[0]:
        raise ValueError("PAS export header changed; 'Application Number' missing")
    with base.TurtleWriter(out) as w:
        for r in rows:
            m = _APP.match(r.get("Application Number") or "")
            if not m:
                st.skipped += 1
                continue
            app, pas_no = m.group(1), m.group(2) or "PAS"
            seen[(app, pas_no)] = seen.get((app, pas_no), 0) + 1
            if seen[(app, pas_no)] > 1:
                pas_no = f"{pas_no}-{seen[(app, pas_no)]}"
            nf = normalize_org(r.get("Applicant Name"))
            pairs = [("a", "ex:PostApprovalStudy"),
                     ("rdfs:label", common.label(f"{app} {pas_no}", r.get("Study Name") or r.get("Device Name"))),
                     ("ex:applicationNumber", common.lit(app)),
                     ("ex:studyNumber", common.lit(pas_no)),
                     ("ex:applicant", common.lit(common.norm_text(r.get("Applicant Name"))))]
            for col, pred in COLS.items():
                v = common.norm_text(r.get(col))
                if v:
                    pairs.append((pred, (common.int_lit(v) or common.lit(v)) if pred in INTS else common.lit(v)))
            for col, pred in DATES.items():
                pairs.append((pred, common.date_lit(r.get(col))))
            if app.startswith("P"):
                pairs.append(("ex:relatedPMARecord", f"<{iri.pma(app)}>"))
            elif app.startswith("H"):
                pairs.append(("ex:relatedHDE", f"<{iri.hde(app)}>"))
            if nf:
                pairs.append(("ex:hasApplicantFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, r.get("Applicant Name"))
            pairs += common.provenance(DATASET, f"{app}/{pas_no}", source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(iri.post_approval_study(app, pas_no), pairs)
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import tempfile
    hdr = ",".join(['Application Number', 'Applicant Name', 'Device Name', 'Medical Specialty', 'Date PMA Approved',
                    'Study Name', 'Date Original Protocol Accepted', 'Date Current Protocol Accepted', 'Study Status',
                    'Actual Number of Patients Enrolled'])
    rows = ["P160030  / PAS001,Abbott Diabetes Care; Inc.,Freestyle Libre,Clinical Chemistry,09/27/2017,"
            "FreeStyle Libre PAS,12/08/2017,04/05/2024,Delayed,184",
            "H120005 / PAS002,Kaneka,Liposorber,Gastro,10/10/2013,LDL PAS,,,Completed,n/a"]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "pas.csv").write_text("\n".join([hdr] + rows) + "\n", encoding="latin-1")
        out = d / "pas.ttl"
        st = transform(d, out, retrieved_at="2026-10-12")
        ttl = out.read_text(encoding="utf-8")
        assert st.records == 2
        assert "/id/post-approval-study/p160030/pas001>" in ttl
        assert "/id/pma/p160030>" in ttl and "/id/hde/h120005>" in ttl
        assert 'ex:patientsEnrolled "184"^^xsd:integer' in ttl and 'ex:patientsEnrolled "n/a"' in ttl
        assert 'ex:applicationApprovalDate "2017-09-27"^^xsd:date' in ttl
    print("pas.py self-test PASSED")


if __name__ == "__main__":
    _selftest()
