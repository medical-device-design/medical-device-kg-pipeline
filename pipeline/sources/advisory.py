#!/usr/bin/env python3
"""
advisory.py -- CDRH Advisory Committee / Panel Meeting Materials Archive (to 2008) -> RDF.

Search: https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfAdvisory/search.cfm
(form GET to results.cfm). Form values (checked 2026-10-12): panel = numeric committee id
(8-29, "" = all), searchtype 0 = upcoming, 1 = past, 2 = "meetings during" month/year
(month 0 = any month, years 2000-2009). A search over all panels is refused ("returned
more than 250 meetings"), so the archive is enumerated PER PANEL with searchtype=1, paging
10 rows at a time exactly as the result page's own links do:
    results.cfm?start_search=11&panel=24&month=0&year=0&searchtype=1&maxrows=10&nocancelled=0&lf=0
(maxrows above 10 is ignored by the server). A panel that is itself over 250 falls back to
one search per year. Each row gives committee, date, and either a details.cfm?mtg=<id> link
(the meeting id; multi-day meetings repeat the id on each day) or a status icon such as
"Proposed Date is Not Needed". Detail pages give date/time sessions, location, agenda,
procedure, and the related documents (agenda, brief summary, briefing, minutes, questions,
roster, transcript ...) as links, most of them to archived fda.gov copies.

Key: the FDA meeting id (iri.advisory_meeting('694')); a row without a detail page is keyed
"p<panel>-<date>" (iri.advisory_meeting('p24-2006-11-07')) and carries ex:meetingStatus.

NOT INGESTED: the "Contact Information" block (FDA staff names and phone numbers).
Agenda / procedure / other free text goes to advisory_text.ttl after scrub.scrub_text.

Proposed minter (used locally via iri._instance, not added to iri.py):
    advisory_meeting_document(key, n) -> .../id/advisory-meeting/<key>/document/<n>
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
import scrub      # noqa: E402
import _cfweb     # noqa: E402

NAME = "advisory"
DATASET = "cdrh/cfAdvisory"
ROOT = _cfweb.BASE + "cfAdvisory/"
SEARCH_URL = ROOT + "search.cfm"
LIST_URL = ROOT + ("results.cfm?start_search={start}&panel={panel}&month=0&year={year}&searchtype={st}"
                   "&maxrows=10&nocancelled=0&lf=0")
DETAIL_URL = ROOT + "details.cfm?mtg={}"
PAGE_ROWS = 10
TOO_MANY = "returned more than 250 meetings"

_PANEL_SEL = re.compile(r'(?is)<select name="panel".*?</select>')
_OPT = re.compile(r'(?i)<option value="(\d+)">([^<\n]*)')
_YEAR_SEL = re.compile(r'(?is)<select name="year".*?</select>')
_LROW = re.compile(r"(?is)<tr[^>]*>\s*<td>([^<]*)</td>\s*<td>([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})</td>\s*<td>(.*?)</td>\s*</tr>")
_MTG = re.compile(r'details\.cfm\?mtg=(\d+)')
_ALT = re.compile(r'(?i)alt="([^"]*)"')
_DROW = re.compile(r'(?is)<tr><td[^>]*>\s*(?:<b>(.*?)</b>|&nbsp;)\s*</td>\s*<td>(.*?)</td></tr>')
_DOC = re.compile(r'(?is)<td[^>]*>\s*<b>([^<]*?):?\s*</b>\s*</td>\s*<td[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>')
_SESSION_DATE = re.compile(r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})")
TEXT_FIELDS = {"Agenda": "ex:agenda", "Procedure": "ex:procedure", "Conduct": "ex:conductNote"}
DROP_FIELDS = {"Contact Information"}


def _iso(s):
    try:
        return datetime.strptime(" ".join(s.split()), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def doc_iri(key, n):
    return iri._instance("advisory-meeting", iri.slug(key), "document", str(int(n)))


# ---------------------------------------------------------------------------
def parse_search(html):
    m = _PANEL_SEL.search(html)
    if not m:
        raise ValueError("cfAdvisory search form changed: no panel <select>")
    panels = [(v, " ".join(n.split())) for v, n in _OPT.findall(m.group(0))]
    y = _YEAR_SEL.search(html)
    years = [v for v, _ in _OPT.findall(y.group(0))] if y else []
    return panels, years


def parse_list(html):
    """-> (rows [(committee, iso date, mtg id or None, status or None)], too_many?, has_next_from)"""
    rows = []
    for com, dt, cell in _LROW.findall(html):
        m = _MTG.search(cell)
        alt = _ALT.search(cell)
        rows.append((" ".join(com.split()), _iso(dt), m.group(1) if m else None,
                     None if m else (alt.group(1) if alt else _cfweb.one_line(cell))))
    nxt = sorted({int(x) for x in re.findall(r"results\.cfm\?start_search=(\d+)", html)})
    return rows, TOO_MANY in html, nxt


def parse_detail(html):
    """-> dict(fields {label: html}, docs [(type, url, title)])"""
    a = html.find("Name of Committee")
    if a < 0:
        return None
    a = html.rfind("<tr", 0, a)
    b = html.find("Related Documents", a)
    top = html[a:b if b > 0 else html.find("end database content", a)]
    fields, extra = {}, []
    for lab, val in _DROW.findall(top):
        lab = _cfweb.one_line(lab) if lab else ""
        if lab:
            fields[lab] = val
        else:
            extra.append(val)
    docs = []
    if b > 0:
        e = html.find("end database content", b)
        for typ, url, title in _DOC.findall(html[b:e if e > 0 else len(html)]):
            docs.append((typ.strip(), url.strip(), _cfweb.one_line(title)))
    return {"fields": fields, "extra": extra, "docs": docs}


# ---------------------------------------------------------------------------
def fetch(dest, delay=1.0, log=print):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    panels, years = parse_search(f.get(SEARCH_URL, "search"))
    lists, failed, overflow = [], [], []

    def walk(panel, year, st):
        start = 1
        while True:
            nm = f"list_p{panel}_y{year}_t{st}_s{start}"
            try:
                body = f.get(LIST_URL.format(start=start, panel=panel, year=year, st=st), nm)
            except _cfweb.Unavailable as e:
                failed.append([nm, str(e)])
                return False
            rows, too_many, nxt = parse_list(body)
            if too_many:
                return None
            lists.append(nm)
            if not any(n > start for n in nxt) or not rows:
                return True
            start += PAGE_ROWS

    for pid, pname in panels:
        r = walk(pid, 0, 1)
        if r is None:                                   # over 250 past meetings: one search per year
            overflow.append(pid)
            for y in years:
                if walk(pid, y, 2) is None:
                    failed.append([f"panel {pid} year {y}", "still more than 250"])
        log(f"  advisory: panel {pid} {pname}: {len(lists)} list pages so far")
    (dest / "lists.json").write_text(json.dumps({"panels": panels, "lists": lists}), encoding="utf-8")
    mtgs = sorted({m for nm in lists for _, _, m, _ in
                   parse_list((dest / "pages" / f"{nm}.html").read_text(encoding="utf-8", errors="replace"))[0] if m},
                  key=int)
    dfail = 0
    for m in mtgs:
        try:
            f.get(DETAIL_URL.format(m), f"mtg_{m}")
        except _cfweb.Unavailable:
            dfail += 1
    (dest / "fetch_log.json").write_text(json.dumps({
        "date": base.today(), "panels": len(panels), "list_pages": len(lists), "meetings_with_detail": len(mtgs),
        "fetched_now": f.fetched, "from_cache": f.cached, "detail_failed": dfail,
        "panels_over_250": overflow, "failed": failed}, indent=1), encoding="utf-8")
    log(f"  advisory: {len(mtgs)} detail pages; fetched {f.fetched}, cached {f.cached}, failed {dfail + len(failed)}")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    return m


def _meetings(src):
    src = Path(src)
    meta = json.loads((src / "lists.json").read_text(encoding="utf-8"))
    pname = {p: n for p, n in meta["panels"]}
    out = {}
    for nm in meta["lists"]:
        pid = re.match(r"list_p(\d+)_", nm).group(1)
        rows = parse_list((src / "pages" / f"{nm}.html").read_text(encoding="utf-8", errors="replace"))[0]
        for com, dt, mtg, status in rows:
            key = mtg or f"p{pid}-{dt}"
            rec = out.setdefault(key, {"mtg": mtg, "committees": set(), "panel_ids": set(), "dates": set(),
                                       "status": set()})
            rec["committees"].add(com or pname.get(pid))
            rec["panel_ids"].add(pid)
            if dt:
                rec["dates"].add(dt)
            if status:
                rec["status"].add(status)
    return out


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    text_out = out.with_name(f"{out.stem}_text.ttl")
    meetings = _meetings(src)
    st = base.Stats(source=NAME)
    hits = {}
    with base.TurtleWriter(out) as w, base.TurtleWriter(text_out) as wt:
        for key in sorted(meetings, key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k)):
            rec = meetings[key]
            det = None
            if rec["mtg"]:
                p = src / "pages" / f"mtg_{rec['mtg']}.html"
                det = parse_detail(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else None
            fl = det["fields"] if det else {}
            committees = sorted(c for c in rec["committees"] if c)
            dates = sorted(rec["dates"])
            sessions = (_cfweb.text(fl.get("Date and Time")) or "").split("\n")
            sessions = [s for s in sessions if _SESSION_DATE.search(s)]
            for s in sessions:
                d = _iso(_SESSION_DATE.search(s).group(1))
                if d and d not in dates:
                    dates.append(d)
            dates.sort()
            loc = _cfweb.text(fl.get("Location"))
            url = DETAIL_URL.format(rec["mtg"]) if rec["mtg"] else None
            pairs = [("a", "ex:AdvisoryMeeting"),
                     ("rdfs:label", common.label(" / ".join(committees), dates[0] if dates else None, sep=", ")),
                     ("ex:meetingId", common.lit(rec["mtg"]))]
            pairs += [("ex:advisoryCommittee", common.lit(c)) for c in committees]
            pairs += [("ex:advisoryPanelId", common.lit(pid)) for pid in sorted(rec["panel_ids"], key=int)]
            pairs += [("ex:meetingDate", common.date_lit(d)) for d in dates]
            pairs += [("ex:meetingSession", common.lit(s)) for s in sessions]
            pairs += [("ex:meetingStatus", common.lit(s)) for s in sorted(rec["status"])]
            pairs.append(("ex:location", common.lit(", ".join(x.strip(" ,") for x in loc.split("\n")) if loc else None)))
            if det:
                committee_detail = _cfweb.one_line(fl.get("Name of Committee"))
                if committee_detail and committee_detail not in committees:
                    pairs.append(("ex:advisoryCommittee", common.lit(committee_detail)))
                for n, _ in enumerate(det["docs"], 1):
                    pairs.append(("ex:hasDocument", f"<{doc_iri(key, n)}>"))
            pairs += common.provenance(DATASET, key, source_url=url or SEARCH_URL, retrieved_at=retrieved_at)
            w.block(iri.advisory_meeting(key), pairs)
            if det:
                for n, (typ, durl, title) in enumerate(det["docs"], 1):
                    w.block(doc_iri(key, n), [("a", "ex:AdvisoryMeetingDocument"),
                                              ("rdfs:label", common.lit(title or f"{typ} document")),
                                              ("ex:documentType", common.lit(typ)),
                                              ("ex:url", common.lit(durl)),
                                              ("ex:documentOf", f"<{iri.advisory_meeting(key)}>")])
                tpairs = []
                for lab, val in fl.items():
                    if lab in DROP_FIELDS or lab in ("Name of Committee", "Date and Time", "Location"):
                        continue
                    body = _cfweb.one_line(val)
                    if not body:
                        continue
                    body, h = scrub.scrub_text(body)
                    for k, v in h.items():
                        hits[k] = hits.get(k, 0) + v
                    pred = TEXT_FIELDS.get(lab)
                    tpairs.append((pred, common.lit(body)) if pred else ("ex:otherDetail", common.lit(f"{lab}: {body}")))
                for val in det["extra"]:
                    body = _cfweb.one_line(val)
                    if body:
                        body, h = scrub.scrub_text(body)
                        for k, v in h.items():
                            hits[k] = hits.get(k, 0) + v
                        tpairs.append(("ex:otherDetail", common.lit(body)))
                if tpairs:
                    wt.block(iri.advisory_meeting(key), tpairs)
            st.records += 1
    st.triples = w.triples + wt.triples
    print(f"  advisory: {st.records} meetings; scrub hits {hits}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim fragments of search.cfm, results.cfm?panel=24&searchtype=1 and details.cfm?mtg=694,
# fetched 2026-10-12 (the detail sample is shortened: two of its documents, procedure cut).
_SEARCH = """<select name="panel" id="panel" size=1>
<option value="">(All Panels / Committees)
<option value="9">Circulatory System Devices Panel
<option value="24">Radiological Devices Panel
</select>
<select name="year" id="year" size="1"> <option value="">(select year) <option value="2000">2000 <option value="2001">2001 </select>"""
_LIST = """<a href="results.cfm?start_search=11&panel=24&month=0&year=0&searchtype=1&maxrows=10&nocancelled=0&lf=0">2</a>
<tr bgcolor="#ffffff">
<td>Radiological Devices Panel</td><td>Mar 5, 2008</td>

<td>
      <a href="details.cfm?mtg=694"><img src="linker-details.gif" alt="Details" border=0></a>
    </td></tr>
<tr BGCOLOR="#f9f9f3">
<td>Radiological Devices Panel</td><td>Mar 4, 2008</td>

<td>
      <a href="details.cfm?mtg=694"><img src="linker-details.gif" alt="Details" border=0></a>
    </td></tr>
<tr bgcolor="#ffffff">
<td>Radiological Devices Panel</td><td>Nov 7, 2006</td>

<td>
      <img src="linker-propcanc.gif" alt="Proposed Date is Not Needed">

    </td></tr>
"""
_DETAIL = """<tr><td valign="top" align="right" width="15%"><b>Name of Committee</b></td><td>Radiological Devices Panel</td></tr>
	<tr><td valign="top" align="right" width="15%"><b>Date and Time</b></td><td>The meeting was held on the following dates:<br>
		Mar 4, 2008 8:00 AM -  5:30 PM<br>
		Mar 5, 2008 8:00 AM -  5:00 PM<br>
	</td></tr>
		<tr><td valign="top" align="right" width="15%"><b>Location</b></td><td>Hilton Washington DC North/Gaithersburg, Salons A, B and C<br>620 Perry Parkway<br>
		Gaithersburg
		, MD</td></tr>
	<tr><td valign="top" align="right" width="15%"><b>Contact Information</b></td><td>Nancy Wersto, Center for Devices and Radiological Health, (HFZ-470), Food and Drug Administration, 9200 Corporate Blvd., Rockville, MD, 20850, (240) 276-3666, or FDA Advisory Committee Information Line, 1-800-741-8138 (301-443-0572 in the Washington, DC area), code 3014512526.  Please call the Information Line for up-to-date information on this meeting.</td></tr>
	<tr><td valign="top" align="right" width="15%"><b>Agenda</b></td><td>On March 4 and 5, 2008, the committee intends to discuss and make recommendations about computer aided detection and diagnosis (CAD) devices for radiological images, e.g., mammograms, chest x-rays, and computed tomography (CT) images of the lungs or colon.</td></tr>
	<tr><td valign="top" align="right" width="15%"><b>Procedure</b></td><td>On March 4, 2008, from 8 a.m. to 5:30 p,m., and on March 5, 2008, from 8:30 a.m. to 5 p.m., the meeting is open to the public.</td></tr>
<tr><td valign="top" align="right" width="15%">&nbsp;</td><td>Persons attending FDA�s advisory committee meetings are advised that the agency is not responsible for providing access to electrical outlets.</td></tr>
<tr><td valign="top" align="right" width="15%"><b>Conduct</b></td><td>FDA is committed to the orderly conduct of its advisory committee meetings.  Please visit our Web site at <a href="http://www.fda.gov/oc/advisory/">http://www.fda.gov/oc/advisory/</a> for procedures on public conduct during advisory committee meetings.</td></tr>
		<tr><td valign="top" align="right" width="15%"><b>Related Documents</b></td><td><table border=0>
		<tr><td valign="top"><b>Brief Summary:</b></td><td valign="top"><a href="http://web.archive.org/web/20090712040445/http://www.fda.gov/cdrh/panel/summary/rad-030408.html">Radiological Devices Panel Meeting - March 4 and 5, 2008</a></td><td valign="top"><a href="http://web.archive.org/web/20090712040445/http://www.fda.gov/cdrh/panel/summary/rad-030408.html"><img src="linker1.gif" border=0 alt="Text"></a></td></tr>
		<tr><td valign="top"><b>Transcript:</b></td><td valign="top"><a href="http://web.archive.org/web/20090712040445/http://www.fda.gov/ohrms/dockets/ac/08/transcripts/2008-4349t1-01.pdf">Transcript for March 4 2008</a></td><td valign="top"><a href="http://web.archive.org/web/20090712040445/http://www.fda.gov/ohrms/dockets/ac/08/transcripts/2008-4349t1-01.pdf"><img src="linker2.gif" border=0 alt="PDF"></a></td></tr>
</table></td></tr>
<!-- ********  end database content"""


def _selftest():
    import tempfile
    panels, years = parse_search(_SEARCH)
    assert panels == [("9", "Circulatory System Devices Panel"), ("24", "Radiological Devices Panel")], panels
    assert years == ["2000", "2001"]
    rows, too_many, nxt = parse_list(_LIST)
    assert rows[0] == ("Radiological Devices Panel", "2008-03-05", "694", None), rows
    assert rows[2] == ("Radiological Devices Panel", "2006-11-07", None, "Proposed Date is Not Needed"), rows
    assert not too_many and nxt == [11]
    assert parse_list("Sorry, but your search returned more than 250 meetings.")[1]
    d = parse_detail(_DETAIL)
    assert len(d["docs"]) == 2 and d["docs"][1][0] == "Transcript", d["docs"]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "list_p24_y0_t1_s1.html").write_text(_LIST, encoding="utf-8")
        (t / "pages" / "mtg_694.html").write_text(_DETAIL, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"panels": panels, "lists": ["list_p24_y0_t1_s1"]}), encoding="utf-8")
        st = transform(t, t / "advisory.ttl", retrieved_at="2026-10-12")
        ttl = (t / "advisory.ttl").read_text(encoding="utf-8")
        txt = (t / "advisory_text.ttl").read_text(encoding="utf-8")
    assert st.records == 2, st
    assert f"<{iri.advisory_meeting('694')}>" in ttl and f"<{iri.advisory_meeting('p24-2006-11-07')}>" in ttl
    assert 'ex:meetingDate "2008-03-04"^^xsd:date' in ttl and 'ex:meetingDate "2008-03-05"^^xsd:date' in ttl
    assert 'ex:meetingStatus "Proposed Date is Not Needed"' in ttl
    assert 'ex:documentType "Transcript"' in ttl and f"ex:hasDocument <{doc_iri('694', 2)}>" in ttl
    assert 'ex:location "Hilton Washington DC North/Gaithersburg, Salons A, B and C, 620 Perry Parkway, Gaithersburg, MD"' in ttl
    assert 'rdfs:label "Radiological Devices Panel, 2008-03-04"' in ttl
    for s in ("Wersto", "276-3666", "741-8138"):
        assert s not in ttl and s not in txt, s                # contact block never ingested
    assert "ex:agenda" in txt and "computer aided detection" in txt and "ex:agenda" not in ttl
    assert "electrical outlets" in txt and "ex:conductNote" in txt
    print(f"advisory.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
