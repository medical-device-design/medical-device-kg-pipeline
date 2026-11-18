#!/usr/bin/env python3
"""
classification_detail.py -- fields of FDA's product classification web page that the
openFDA classification bulk file does not carry -> extra triples on the classification node.

PAGE  https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?id=<CODE>
      one per product code (about 7,000). Rows are <th>label</th><td>value</td>; the
      lists are <strong>/<b> headed blocks. Fields taken (checked October 12, 2026):
        Review Panel (full name; the bulk file has only the 2-letter code)
                                         -> ex:reviewPanelName
        Regulation Description           -> ex:regulationDescription
        Premarket Review (office, division lines)
                                         -> ex:premarketReviewOffice / ex:premarketReviewDivision
        Physical State / Technical Method / Target Area (newer codes only)
                                         -> ex:physicalState / ex:technicalMethod / ex:targetArea
        Total Product Life Cycle (TPLC) link -> ex:tplcReportUrl
        Summary Malfunction Reporting    -> ex:summaryMalfunctionReportingEligible (Eligible/Ineligible)
        Implanted Device?                -> ex:implantedDevice
        Life-Sustain/Support Device?     -> ex:lifeSustainingDevice
        Recognized Consensus Standards   -> ex:hasConsensusStandard <iri.consensus_standard("1-147")>
        Guidance Document(s)             -> ex:guidanceDocumentTitle + ex:guidanceDocumentUrl
        Third Party Review               -> ex:thirdPartyReview (FDA's text) + ex:thirdPartyReviewEligible
        Accredited Persons (organizations) -> ex:accreditedPerson
      Fields the bulk file already has (device name, class, regulation number, submission
      type, definition, GMP exempt, specialty) are not repeated. Unknown labels are counted
      in the report, never written.

The triples go on the EXISTING node iri.classification(code); classification.py owns its
rdf:type and rdfs:label, so none are written here. Standards referenced get a minimal
typed stub (standards.py owns the full node). Pages are cached in pages/<CODE>.html and
not refetched unless older than refresh_days. FDA sometimes answers with a "Your request
is being processed" queue page; that page is never cached, and all requests pause with
backoff before retrying.
"""
import os
import re
import sys
import json
import html as htmllib
import threading
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

NAME = "classification_detail"
DATASET = "cdrh/pcd-detail"
PAGE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm?id={}"
SITE = "https://www.accessdata.fda.gov"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
END_MARK = "end database content"
BUSY_MARK = "Your request is being processed"

TEXT = {"review panel": "ex:reviewPanelName", "regulation description": "ex:regulationDescription",
        "physical state": "ex:physicalState", "technical method": "ex:technicalMethod",
        "target area": "ex:targetArea"}
BOOLS = {"implanted device": "ex:implantedDevice", "life-sustain/support device": "ex:lifeSustainingDevice"}
IN_BULK = {"device", "regulation medical specialty", "product code", "submission type",
           "regulation number", "device class", "definition", "gmp exempt", "unclassified reason"}
_SECTION = re.compile(r"(?is)<(strong|b)>\s*([^<]+?)\s*</\1>")
_LI = re.compile(r"(?is)<li[^>]*>(.*?)(?=<li|</ul>|$)")
_HREF = re.compile(r'(?is)href="([^"]+)"')
_REC = re.compile(r"^\s*(\d+-\d+)\b")


# ===========================================================================
#  Polite fetching (same rules as pma_memo.py / denovo.py)
# ===========================================================================
class Fetcher:
    def __init__(self, delay=1.0):
        self.delay, self.lock, self.next = delay, threading.Lock(), time.monotonic()

    def _slot(self):
        with self.lock:
            wait = self.next - time.monotonic()
            self.next = max(self.next, time.monotonic()) + self.delay
        if wait > 0:
            time.sleep(wait)

    def pause(self, secs):
        with self.lock:
            self.next = max(self.next, time.monotonic() + secs)

    def get(self, url, tries=5):
        err = None
        for i in range(tries):
            self._slot()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                text = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", errors="replace")
                if END_MARK in text and BUSY_MARK not in text:
                    return text
                err = "FDA queue page" if BUSY_MARK in text else "incomplete page"
                self.pause(30 * 2 ** i)
            except Exception as e:
                err = str(e)
                self.pause(5 * 2 ** i)
        raise RuntimeError(f"{url}: {err}")


def _save(path, text):
    tmp = path.with_suffix(".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _complete(path, refresh_days=None):
    if not path.exists():
        return False
    if refresh_days is not None and time.time() - path.stat().st_mtime > refresh_days * 86400:
        return False
    t = path.read_text(encoding="utf-8", errors="replace")
    return END_MARK in t and BUSY_MARK not in t


# ===========================================================================
#  Which product codes
# ===========================================================================
def targets(dest):
    """Product codes to fetch: dest/codes.txt (one per line) if present, else the codes in
    the classification Turtle named by $MDKG_CLASSIFICATION_TTL, else the openFDA
    classification download next to dest."""
    dest = Path(dest)
    codes = set()
    if (dest / "codes.txt").exists():
        codes = {l.strip() for l in (dest / "codes.txt").read_text(encoding="utf-8").splitlines()}
    elif os.environ.get("MDKG_CLASSIFICATION_TTL"):
        with open(os.environ["MDKG_CLASSIFICATION_TTL"], encoding="utf-8") as fh:
            for line in fh:
                codes.update(re.findall(r'ex:productCode "([A-Za-z]{3})"', line))
    else:
        codes = {str(r.get("product_code") or "") for r in base.iter_openfda_results(dest.parent / "classification")}
    return sorted(c for c in (common.product_code(x) for x in codes) if c)


# ===========================================================================
#  Parsing
# ===========================================================================
def _text(fragment):
    t = re.sub(r"(?i)<br\s*/?>", " ", fragment or "")
    t = re.sub(r"(?s)<!--.*?(-->|$)", " ", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return " ".join(htmllib.unescape(t).replace("\xa0", " ").split())


def _abs(url):
    return SITE + url if url.startswith("/") else url


def _known_section(t):
    return (t.startswith("recognized consensus standard") or t.startswith("guidance document")
            or t in ("third party review", "accredited persons"))


def _boilerplate(t):
    """FDA's generic exemption notes ("Note: FDA has exempted almost all class I devices",
    "Class II devices the Food and Drug Administration has also published ...")."""
    return t.startswith("note") or t.startswith("class ")


def parse_page(page):
    """-> dict with 'rows' (label -> value HTML) and 'sections' (title -> HTML)."""
    end = page.find(END_MARK)
    body = page[:end] if end > 0 else page
    rows = {}
    first = body.find("<th")
    for chunk in (body[first:].split("<th")[1:] if first >= 0 else []):
        th, _, val = chunk.partition("</th>")
        lab = _text(th[th.find(">") + 1:]).rstrip("?").strip().lower()
        if lab and lab not in rows:
            rows[lab] = val.split("</tr>", 1)[0]
    sections, marks, other = {}, [], []
    for m in _SECTION.finditer(body):
        t = m.group(2).strip().lower()
        (marks if _known_section(t) or _boilerplate(t) else other).append(m)
    for m in other:                                  # bold text inside a block is not a boundary
        sections.setdefault("bold: " + m.group(2).strip().lower(), "")
    for i, m in enumerate(marks):
        stop = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        sections.setdefault(m.group(2).strip().lower(), body[m.end():stop])
    return {"rows": rows, "sections": sections}


def page_pairs(code, d, unknown):
    """(pairs, standard numbers) for one product code page."""
    rows, secs = d["rows"], d["sections"]
    pairs, stds = [], []
    for lab, val in rows.items():
        txt = _text(val)
        if lab in TEXT:
            pairs.append((TEXT[lab], common.lit(txt)))
        elif lab in BOOLS:
            pairs.append((BOOLS[lab], common.bool_lit(txt)))
        elif lab == "premarket review":
            lines = [_text(x) for x in re.split(r"(?i)<br\s*/?>", val)]
            lines = [x for x in lines if x]
            if lines:
                pairs.append(("ex:premarketReviewOffice", common.lit(lines[0])))
            pairs += [("ex:premarketReviewDivision", common.lit(x)) for x in lines[1:]]
        elif lab.startswith("total product life cycle"):
            h = _HREF.search(val)
            pairs.append(("ex:tplcReportUrl", common.lit(_abs(h.group(1))) if h else None))
        elif lab == "summary malfunction reporting":
            low = txt.lower()
            b = "false" if low.startswith("ineligible") else "true" if low.startswith("eligible") else None
            pairs.append(("ex:summaryMalfunctionReportingEligible", f'"{b}"^^xsd:boolean' if b else None))
            if b is None and txt:
                unknown[f"summary malfunction reporting = {txt}"] = unknown.get(f"summary malfunction reporting = {txt}", 0) + 1
        elif lab in IN_BULK:
            pass
        else:
            unknown[lab] = unknown.get(lab, 0) + 1
    for title, val in secs.items():
        items = [x for x in _LI.findall(val)]
        if _boilerplate(title):
            continue
        if title.startswith("recognized consensus standard"):
            for li in items:
                m = _REC.match(_text(li))
                if m:
                    stds.append(m.group(1))
                    pairs.append(("ex:hasConsensusStandard", f"<{iri.consensus_standard(m.group(1))}>"))
        elif title.startswith("guidance document"):
            for li in items:
                h = _HREF.search(li)
                pairs.append(("ex:guidanceDocumentTitle", common.lit(_text(li))))
                pairs.append(("ex:guidanceDocumentUrl", common.lit(_abs(h.group(1))) if h else None))
        elif title == "third party review":
            txt = _text(" ".join(items)) if items else _text(val)
            pairs.append(("ex:thirdPartyReview", common.lit(txt)))
            low = txt.lower()
            b = "false" if low.startswith("not") else "true" if low.startswith("eligible") else None
            pairs.append(("ex:thirdPartyReviewEligible", f'"{b}"^^xsd:boolean' if b else None))
        elif title == "accredited persons":
            pairs += [("ex:accreditedPerson", common.lit(_text(li))) for li in items]
        else:
            unknown["section: " + title] = unknown.get("section: " + title, 0) + 1
    return list(dict.fromkeys(p for p in pairs if p[1] is not None)), stds


def page_code(d):
    v = d["rows"].get("product code")
    return common.product_code(_text(v)) if v else None


# ===========================================================================
#  Contract
# ===========================================================================
def fetch(dest, delay=1.0, workers=3, limit=None, refresh_days=None):
    from concurrent.futures import ThreadPoolExecutor
    dest = Path(dest)
    pages = dest / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    codes = targets(dest)
    (dest / "targets.json").write_text(json.dumps(codes), encoding="utf-8")
    todo = [c for c in (codes[:limit] if limit else codes) if not _complete(pages / f"{c}.html", refresh_days)]
    print(f"  classification_detail: {len(codes)} product codes, {len(todo)} pages to fetch", flush=True)
    f, lock, counts = Fetcher(delay), threading.Lock(), {"fetched": 0, "errors": 0}

    def one(c):
        try:
            _save(pages / f"{c}.html", f.get(PAGE_URL.format(c)))
            (pages / f"{c}.error").unlink(missing_ok=True)
            with lock:
                counts["fetched"] += 1
                n = counts["fetched"]
            if n % 250 == 0:
                print(f"  ... {n} fetched, {counts['errors']} errors", flush=True)
        except Exception as e:
            (pages / f"{c}.error").write_text(str(e), encoding="utf-8")
            with lock:
                counts["errors"] += 1

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, todo))
    (dest / "fetch_log.json").write_text(json.dumps(
        {"date": base.today(), "targets": len(codes), "fetched_now": counts["fetched"],
         "errors": counts["errors"]}), encoding="utf-8")
    print(f"  classification_detail: fetched {counts['fetched']} now; {counts['errors']} errors", flush=True)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    codes = json.loads((src / "targets.json").read_text(encoding="utf-8")) if (src / "targets.json").exists() \
        else sorted(p.stem for p in (src / "pages").glob("*.html"))
    st = base.Stats(source=NAME)
    unknown, stds, missing, mismatch = {}, set(), [], []
    with base.TurtleWriter(out) as w:
        for c in codes:
            p = src / "pages" / f"{c}.html"
            if not _complete(p):
                missing.append(c)
                st.skipped += 1
                continue
            d = parse_page(p.read_text(encoding="utf-8", errors="replace"))
            if page_code(d) != c:
                mismatch.append(c)                  # no record for this code on FDA's page
                st.skipped += 1
                continue
            pairs, s = page_pairs(c, d, unknown)
            stds.update(s)
            pairs += common.provenance(DATASET, c, source_url=PAGE_URL.format(c), retrieved_at=retrieved_at)
            w.block(iri.classification(c), pairs)
            st.records += 1
        for n in sorted(stds):                       # typed stubs; standards.py owns the full node
            w.block(iri.consensus_standard(n), [("a", "ex:RecognizedConsensusStandard"),
                                                ("ex:recognitionNumber", common.lit(n))])
    st.triples = w.triples
    Path(out).with_suffix(".report.json").write_text(json.dumps(
        {"targets": len(codes), "records": st.records, "missing_page": missing,
         "no_matching_record": mismatch, "unknown_labels": unknown}, indent=1), encoding="utf-8")
    print(f"  classification_detail: {st.records} codes written; {len(missing)} without a page; "
          f"{len(mismatch)} pages without that code; unknown labels {unknown or 'none'}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- fragments copied verbatim from classification.cfm?id=FRN and ?id=DXN
#  (third party block) on October 12, 2026; one standard and the guidance trimmed.
# ===========================================================================
_PAGE = """<table border="0" cellpadding="0" cellspacing=5 width="600">
<tbody>
<tr>
<tr style="font-family: arial; color: #23238e;">
<th align="left" style="width:30%">Device</th>
<td align="left" style="text-transform: capitalize; width:70%">pump, infusion</td>
</tr>
<tr>
<th align="left" nowrap>Regulation Description</th>
<td align="left">Infusion pump.</td>
</tr>
<tr><th align="left">Review Panel</th>
<td align="left"> General Hospital                    </td>
</tr>
<tr><th align="left" nowrap>Product Code</th><td align="left">FRN</td></tr>
<tr valign="top">
<th align="left" nowrap>Premarket Review</th>
<td align="left">
<A style="text-decoration:underline;"  href="https://www.fda.gov/about-fda/cdrh-offices/cdrh-management-directory-organization#OHT3">Office of Gastrorenal, ObGyn, General Hospital, and Urology Devices</A>
(OHT3)<br>
Drug Delivery and General Hospital Devices, and Human Factors
(DHT3C)<br>
</td>
</tr>
<tr>
<th align="left" nowrap>Total Product Life Cycle (TPLC)</th>
<td align="left">
<A style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfTPLC/tplc.cfm?id=FRN">TPLC Product Code Report</A>
</td>
</tr>
<tr>
<th align="left" nowrap>GMP Exempt?</th>
<td align="left">No
</td>
</tr>
<tr>
<th align="left" nowrap>Summary Malfunction<br>Reporting</th>
<td align="left">Ineligible</td>
</tr>
<tr><th align="left" nowrap>Implanted Device? </th><td align="left">
No
</td></tr>
<tr><th align="left" nowrap>Life-Sustain/Support Device? </th><td align="left">
No
</td></tr>
<tr valign="top">
<td valign="top" align="left" colspan="2"><strong>Recognized Consensus Standards</strong>
<table class="hardbreak" cellpadding="0" cellspacing="0" border="0" align="left" style="margin:0 10px; width:100%">
<tr><td><ul><li>1-147&nbsp;ISO&nbsp;26825 Second edition 2020-10 <br><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfStandards/detail.cfm?standard__identification_no=41849">Anaesthetic and respiratory equipment  - User-applied labels for syringes containing drugs used during anaesthesia - Colours, design and performance</a></li><li>6-482&nbsp;AAMI&nbsp;TIR101:2021<br><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfStandards/detail.cfm?standard__identification_no=43452">Fluid delivery performance testing for infusion pumps</a></li></ul></td></tr></table>
</td></tr>
<tr valign="top">
<td valign="top" align="left" colspan="2"><strong>Guidance Document</strong>
<table class="hardbreak" cellpadding="0" cellspacing="0" border="0" align="left" style="margin:0 10px; width:100%">
<tr><td><ul><li><A style="text-decoration:underline;" href="https://www.fda.gov/regulatory-information/search-fda-guidance-documents/infusion-pumps-total-product-life-cycle">Infusion Pumps Total Product Life Cycle - Guidance for Industry and FDA Staff</li></ul></td></tr>
</table>
</td>
</tr>
<tr>
<td valign="top" align="left"><b>Third Party Review</b></td>
<td valign="top" align="left">Not Third Party Eligible</td>
</tr>
</tbody>
</table>
<!-- ********  end database content ******** -->"""

_TP = """<tr><th align="left" nowrap>Product Code</th><td align="left">DXN</td></tr>
<tr><th align="left" nowrap>Technical Method</th>
<td align="left" class="hardbreak">The device provides triage or notification that is informed by machine learning, artificial intelligence or other image analysis algorithms.</td>
</tr>
<tr><td class="hardbreak" colspan="2">&nbsp;&nbsp;&nbsp;&nbsp;<b>Note:</b>&nbsp;<b>Class II devices</b> the Food and Drug Administration (FDA) has also published a <a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfpcd/315.cfm">list of class II (special controls) devices</a> subject to certain limitations</td></tr>
<tr><th align="left" nowrap>Implanted Device? </th><td align="left">
No
</td></tr>
<tr valign="top">
<td valign="top" align="left" colspan="2"><strong>Recognized Consensus Standard</strong>
<table class="hardbreak" cellpadding="0" cellspacing="0" border="0" align="left" style="margin:0 10px; width:100%">
<tr><td><ul><li>1-62&nbsp;ISO&nbsp;5356-1 Third edition 2004-05-15<br><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfStandards/detail.cfm?standard__identification_no=36929">Anaesthetic and respiratory equipment - Conical connectors: Part 1: Cones and sockets</a></li></ul></td></tr></table>
</td></tr>
	<td valign="top" align="left" colspan="2">
<b>Third Party Review</b>
<table width="100%" cellpadding="0" cellspacing="0" border="0" align="left" style="margin:0 10px; width:100%">
<tr><td><ul><li>Eligible for <i><a style="text-decoration:underline;" href="https://www.fda.gov/medical-devices/premarket-submissions/510k-third-party-review-program" title="Accredited Persons and Third Party information">510(k) Third Party Review Program</a></i></li></ul></td></tr>
</table>
</td>
</tr>
<tr>
<td valign="top" align="left" colspan="2">
<b>Accredited Persons</b>
<br>
<table cellpadding="0" cellspacing="0" border="0" align="left" style="margin:0 10px; width:100%">
<tr><td><ul>
<li style="text-transform: capitalize"><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfthirdparty/accredit.cfm?party_key=3#top">center for measurement standards of industrial</a></li>
</ul>
</td></tr></table>
</td>
</tr>
<!-- ********  end database content ******** -->"""


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        _save(t / "pages" / "FRN.html", _PAGE)
        _save(t / "pages" / "DXN.html", _TP)
        _save(t / "pages" / "LLZ.html", "<div>Your request is being processed. Please be patient</div>")
        (t / "targets.json").write_text(json.dumps(["DXN", "FRN", "LLZ"]), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-12", files=[]).write(t / "manifest.json")
        st = transform(t, t / "classification_detail.ttl")
        ttl = (t / "classification_detail.ttl").read_text(encoding="utf-8")
        assert not _complete(t / "pages" / "LLZ.html")          # queue page is not a result
    assert st.records == 2 and st.skipped == 1, st
    frn = ttl[ttl.find(f"<{iri.classification('FRN')}>"):]
    frn = frn[:frn.find(" .\n")]
    assert 'ex:reviewPanelName "General Hospital"' in frn
    assert 'ex:regulationDescription "Infusion pump."' in frn
    assert 'ex:premarketReviewOffice "Office of Gastrorenal, ObGyn, General Hospital, and Urology Devices (OHT3)"' in frn
    assert 'ex:premarketReviewDivision "Drug Delivery and General Hospital Devices, and Human Factors (DHT3C)"' in frn
    assert 'ex:tplcReportUrl "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfTPLC/tplc.cfm?id=FRN"' in frn
    assert 'ex:summaryMalfunctionReportingEligible "false"^^xsd:boolean' in frn
    assert 'ex:implantedDevice "false"^^xsd:boolean' in frn and 'ex:lifeSustainingDevice "false"^^xsd:boolean' in frn
    assert f"ex:hasConsensusStandard <{iri.consensus_standard('1-147')}>" in frn
    assert f"ex:hasConsensusStandard <{iri.consensus_standard('6-482')}>" in frn
    assert 'ex:guidanceDocumentTitle "Infusion Pumps Total Product Life Cycle - Guidance for Industry and FDA Staff"' in frn
    assert 'ex:guidanceDocumentUrl "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/infusion-pumps-total-product-life-cycle"' in frn
    assert 'ex:thirdPartyReview "Not Third Party Eligible"' in frn and 'ex:thirdPartyReviewEligible "false"^^xsd:boolean' in frn
    assert 'ex:thirdPartyReview "Eligible for 510(k) Third Party Review Program"' in ttl
    assert 'ex:thirdPartyReviewEligible "true"^^xsd:boolean' in ttl
    assert 'ex:accreditedPerson "center for measurement standards of industrial"' in ttl
    assert 'ex:technicalMethod "The device provides triage' in ttl
    assert f"ex:hasConsensusStandard <{iri.consensus_standard('1-62')}>" in ttl     # singular heading
    assert "list of class II" not in ttl and "Note" not in ttl                       # boilerplate skipped
    assert "rdfs:label" not in ttl and f"<{iri.classification('FRN')}>\n    a " not in ttl   # node owned elsewhere
    assert f"<{iri.consensus_standard('1-147')}>\n    a ex:RecognizedConsensusStandard" in ttl
    assert "pump, infusion" not in ttl                       # bulk-file field not repeated
    print(f"classification_detail.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
