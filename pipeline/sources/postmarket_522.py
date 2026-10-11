#!/usr/bin/env python3
"""
postmarket_522.py -- FDA Section 522 postmarket surveillance studies (cfPMA/pss.cfm) -> RDF.

FDA publishes no bulk file. Every study (active or closed) has a detail page,
pss.cfm?t_id=N, laid out as label/value table rows ("Study Status", "522 Number /
Requirement Number", "Study Design", ... "Recommendations for Labeling Changes").
fetch() walks t_id 1, 2, 3 ... at 1 request per second, saves each page that carries a
522 number, and stops after a long run of empty ids past the last known study.

The Excel export (pss_Excel.cfm, POST ID=<t_ids>) returns ONLY studies that are still
open, but it adds three fields the detail page lacks: manufacturer, medical specialty
and the date of the 522 order. fetch() requests it once for all found ids and saves it;
transform() joins it to the pages on the 522 number. The export replaces commas inside
fields with ";", so the detail page is always the source for free text.
"""
import datetime as dt
import html as htmllib
import os
import re
import sys
import time
import urllib.error
import urllib.parse
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

NAME = "postmarket_522"
DATASET = "cdrh/pss"
DETAIL_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMA/pss.cfm?t_id={}"
EXPORT_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMA/pss_Excel.cfm"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"

# detail-page label -> predicate (the second "Study Population" row is the description)
LABELS = {
    "Study Status": "ex:studyStatus", "522 Number / Requirement Number": "ex:orderNumber",
    "Study Name": "ex:studyName", "Device Name": "ex:deviceName",
    "Root Document Number": "ex:rootDocumentNumber", "Study Design": "ex:studyDesign",
    "Data Source": "ex:dataSource", "Comparison Group": "ex:comparisonGroup",
    "Analysis Type": "ex:analysisType", "Study Population": "ex:studyPopulation",
    "Study Objectives": "ex:studyObjectives", "Sample Size": "ex:sampleSize",
    "Key Study Endpoints": "ex:keyStudyEndpoints",
    "Follow-up Visits and Length of Follow-up": "ex:followUp",
    "Interim Safety Information": "ex:interimSafetyInformation",
    "Actual Number of Patients Enrolled": "ex:patientsEnrolled",
    "Actual Number of Sites Enrolled": "ex:sitesEnrolled", "Patient Followup Rate": "ex:patientFollowupRate",
    "Final Safety Findings": "ex:finalSafetyFindings", "Final Effectiveness Findings": "ex:finalEffectivenessFindings",
    "Study Strengths and Weaknesses": "ex:studyStrengthsWeaknesses",
    "Recommendations for Labeling Changes": "ex:labelingRecommendations",
}
DATE_LABELS = {"Date Original Plan Accepted": "ex:originalPlanAcceptedDate",
               "Date Current Plan Accepted": "ex:currentPlanAcceptedDate"}
SECTION_HEADS = {"General", "General Study Protocol Parameters", "Detailed Study Protocol Parameters",
                 "Interim or Final Data Summary"}
EXPORT_EXTRA = {"Manufacturer": "ex:manufacturerName", "Medical Specialty": "ex:medicalSpecialtyDescription"}


def cells(page):
    out = []
    for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", page, re.S | re.I):
        t = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
        if t:
            out.append(t)
    return out


def parse_detail(page):
    """-> dict label -> value (+ 'Study Population Description'); {} if not a study page."""
    cs = cells(page)
    if "522 Number / Requirement Number" not in cs:
        return {}
    known = set(LABELS) | set(DATE_LABELS) | SECTION_HEADS
    rec, i = {}, cs.index("Study Status") if "Study Status" in cs else 0
    while i < len(cs):
        lab = cs[i]
        if lab in known and lab not in SECTION_HEADS:
            val = cs[i + 1] if i + 1 < len(cs) and cs[i + 1] not in known else ""
            key = "Study Population Description" if lab == "Study Population" and lab in rec else lab
            rec[key] = val
            i += 2 if val else 1
        else:
            i += 1
    return rec


def parse_export(text):
    """Header ends with <br>; one study per following line. -> {522 number: row dict}."""
    text = text.replace("\r", "")
    head, _, rest = text.partition("<br>")
    hdr = [h.strip() for h in head.split(",")]
    if "522 Order Number" not in hdr:
        return {}
    out = {}
    for ln in rest.replace("<br>", "\n").split("\n"):
        cells_ = [c.strip() for c in ln.split(",")]
        while len(cells_) > len(hdr) and cells_[-1] == "":
            cells_.pop()
        if len(cells_) == len(hdr):
            r = dict(zip(hdr, cells_))
            out[r["522 Order Number"]] = r
    return out


def _get(url, delay, data=None, tries=6, backoff=30, timeout=120):
    """GET/POST politely; on 429/5xx back off backoff, 2x, 4x ... seconds."""
    for n in range(tries):
        time.sleep(delay)
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
        try:
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if n == tries - 1:
                raise
        time.sleep(backoff * 2 ** n)


def fetch(dest, delay=1.0, min_last=450, stop_after=80):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    found, failed, empty, tid = [], [], 0, 1

    def try_page(t, timeout):
        # FDA answers unused ids with a 503 after a ~60 s wait, while real pages come
        # back in under a second: give up after `timeout`, record the id, move on;
        # errored ids inside the found range get one slower retry at the end
        try:
            page = _get(DETAIL_URL.format(t), delay, tries=1, timeout=timeout)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            return None
        if parse_detail(page):
            (dest / f"pss_{t}.html").write_text(page, encoding="utf-8")
            return True
        return False

    while tid <= min_last or empty < stop_after:
        if (dest / f"pss_{tid}.html").exists():     # resumable: keep pages already saved
            found.append(tid)
            empty = 0
        else:
            ok = try_page(tid, 15)
            if ok:
                found.append(tid)
                empty = 0
            else:
                empty += 1
                if ok is None:
                    failed.append(tid)
        tid += 1
    still = []
    top = max(found) if found else 0
    for t in [x for x in failed if x < top]:        # one slower pass over errored ids in range
        ok = try_page(t, 45)
        if ok:
            found.append(t)
        elif ok is None:
            still.append(t)
    found.sort()
    (dest / "pss_unreachable_ids.txt").write_text("\n".join(map(str, still)) + "\n", encoding="utf-8")
    body = _get(EXPORT_URL, delay, urllib.parse.urlencode(
        {"ID": ",".join(map(str, found)), "sortcolumn": "pdn", "s": "a"}).encode())
    (dest / "pss_export_open_studies.csv").write_text(body, encoding="utf-8")
    print(f"postmarket_522: {len(found)} study pages (last id tried {tid - 1}); "
          f"{len(parse_export(body))} open studies in the export; {len(still)} ids FDA would not serve")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(),
                      files=[{"url": DETAIL_URL.format("N"), "path": "pss_<t_id>.html", "count": len(found)}])
    m.write(dest / "manifest.json")
    return m


def _date(v):
    v = (v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _root_links(docs):
    out = []
    for doc in re.findall(r"\b(?:K\d{6}|DEN\d{6}|P\d{6}|H\d{6})(?:/S\d+)?", (docs or "").upper()):
        base_no = doc.split("/")[0]
        if base_no.startswith("P"):
            out.append(("ex:relatedPMARecord", f"<{iri.pma(base_no)}>"))
        elif base_no.startswith("H"):
            out.append(("ex:relatedHDE", f"<{iri.hde(base_no)}>"))
        else:
            out.append(("ex:relatedK510Record", f"<{iri.k510(base_no)}>"))
    return out


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    export = {}
    ep = src / "pss_export_open_studies.csv"
    if ep.exists():
        export = parse_export(ep.read_text(encoding="utf-8", errors="replace"))
    st = base.Stats(source=NAME)
    pages = sorted(src.glob("pss_*.html"), key=lambda p: int(p.stem.split("_")[1]))
    with base.TurtleWriter(out) as w:
        for p in pages:
            tid = p.stem.split("_")[1]
            r = parse_detail(p.read_text(encoding="utf-8", errors="replace"))
            if not r:
                st.skipped += 1
                continue
            pairs = [("a", "ex:Postmarket522Study"),
                     ("rdfs:label", common.lit(r.get("Study Name") or r.get("Device Name"))),
                     ("ex:studyId", common.lit(tid))]
            for lab, pred in LABELS.items():
                pairs.append((pred, common.lit(r.get(lab))))
            pairs.append(("ex:studyPopulationDescription", common.lit(r.get("Study Population Description"))))
            for lab, pred in DATE_LABELS.items():
                iso = _date(r.get(lab))
                if iso:
                    pairs.append((pred, common.date_lit(iso)))
                elif r.get(lab):
                    pairs.append(("ex:dateText", common.lit(f"{lab}: {r[lab]}")))
            ex_row = export.get(r.get("522 Number / Requirement Number", ""))
            if ex_row:
                for col, pred in EXPORT_EXTRA.items():
                    pairs.append((pred, common.lit(ex_row.get(col))))
                iso = _date(ex_row.get("Date 522 Order"))
                if iso:
                    pairs.append(("ex:orderDate", common.date_lit(iso)))
            pairs += _root_links(r.get("Root Document Number"))
            pairs += common.provenance(DATASET, tid, source_url=DETAIL_URL.format(tid), retrieved_at=retrieved_at)
            w.block(iri.postmarket_522(tid), pairs)
            st.records += 1
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import tempfile

    def row(lab, val):
        return f'<TR><TD style="x">{lab}</TD>\n<TD>\n {val}\n</TD></tr>'
    page = ("<table><tr><td>To search for Manufacturer beginning with a specific letter</td><td>A</td></tr></table>"
            "<TABLE>" + row("General", "") +
            row("Study Status", "Completed") + row("522 Number / Requirement Number", "PS120001 / PSS001") +
            row("Date Original Plan Accepted", "09/08/2013") + row("Date Current Plan Accepted", "") +
            row("Study Name", "POP AE and Effectiveness rates, registry") +
            row("Device Name", "Acell matristem pelvic floor matrix") + row("Root Document Number", "K041140 K141084") +
            row("Study Population", "Adult: At least 22 yrs") + row("Study Objectives", "Design: multicenter, non-randomized") +
            row("Study Population", "Qualifying consecutive adult female subjects") +
            row("Actual Number of Sites Enrolled", "13") + row("Recommendations for Labeling Changes", "n/a") + "</TABLE>")
    rec = parse_detail(page)
    assert rec["Study Status"] == "Completed" and rec["Date Current Plan Accepted"] == ""
    assert rec["Study Population"].startswith("Adult") and rec["Study Population Description"].startswith("Qualifying")
    assert parse_detail("<td>No records</td>") == {}
    export = ("522 Order Number,Manufacturer,Medical Specialty,Date 522 Order<br>\n"
              "PS120001 / PSS001,Acell; Inc.,Obstetrics/Gynecology,01/05/2012,\n\n")
    assert parse_export(export)["PS120001 / PSS001"]["Manufacturer"] == "Acell; Inc."
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "pss_187.html").write_text(page, encoding="utf-8")
        (d / "pss_export_open_studies.csv").write_text(export, encoding="utf-8")
        st = transform(d, d / "p.ttl", retrieved_at="2026-10-10")
        ttl = (d / "p.ttl").read_text(encoding="utf-8")
    assert st.records == 1, st
    assert f"<{iri.postmarket_522('187')}>" in ttl and "a ex:Postmarket522Study" in ttl
    assert '"2013-09-08"^^xsd:date' in ttl and '"2012-01-05"^^xsd:date' in ttl
    assert 'ex:orderNumber "PS120001 / PSS001"' in ttl and 'ex:manufacturerName "Acell; Inc."' in ttl
    assert f"<{iri.k510('K041140')}>" in ttl and f"<{iri.k510('K141084')}>" in ttl
    assert "non-randomized" in ttl and 'ex:sitesEnrolled "13"' in ttl and "pss.cfm?t_id=187" in ttl
    print(f"postmarket_522.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
