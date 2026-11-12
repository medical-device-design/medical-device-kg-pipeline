#!/usr/bin/env python3
"""
denovo.py -- the CDRH De Novo database (cfPMN denovo.cfm) -> RDF.

LIST  GET https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/denovo.cfm
          ?start_search=1&decisiondatefrom=01/01/1997&decisiondateto=12/31/2026
          &pagenum=500&sortcolumn=dd
      "1 to 495 of 495 results" on October 12, 2026. Columns: Device Name (link to the
      detail page) | Requester | De Novo number | 510(k) number | Decision date.
      490 rows carry a DEN number, 3 a CBER "BR" number in the De Novo column, and 2
      (CBER, 2001 and 2007) only a "BK" number in the 510(k) column. The record key is
      the De Novo number, or the 510(k) number when the De Novo column is blank.
      Further pages (start_search=501, ...) are fetched while rows < the stated total.
DETAIL denovo.cfm?id=<id from the list link>, fields (two layouts):
      CDRH: Device Classification Name, De Novo Number, 510(K) Number, Device Name,
            Requester (firm + address), Contact, Regulation Number, Classification
            Product Code, Date Received, Decision Date, Decision ("granted (DENG)"),
            Classification / Review Advisory Committee, Classification Order (PDF),
            FDA Review (Decision Summary PDF), Type, Predetermined Change Control Plan
            Authorized
      CBER: Device Classification Name, De Novo or 510(K) Number, Device Name, Product
            Code, Device Class, Regulation Number, Submission Type, Requestor, Date
            Received, Decision Date, Decision, Expedited Review, Third Party Review,
            In Vitro Device?
      Combination Product is mapped too if FDA shows it.
NOT INGESTED: Contact (a person). Labels this parser does not know are counted and
printed, never written, so a new personal field cannot leak in unnoticed.

Modeling: ex:DeNovoRecord at iri.de_novo(number); ex:relatedK510Record <iri.k510(number)>
(the 510(k) file also lists DEN numbers) and <iri.k510(510k number)> when one is given;
ex:hasProductCode, ex:hasRegulationNumber (the predicate classification.py uses),
ex:hasApplicantFirm <iri.firm_name(normalize_org(requester))>.
Detail pages are cached in pages/<id>.html and never refetched once complete.
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
from identity import normalize_org   # noqa: E402

NAME = "denovo"
DATASET = "cdrh/denovo"
PAGE_SIZE = 500
_B = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/denovo.cfm"
LIST_URL = (_B + "?start_search={start}&decisiondatefrom=01/01/1997&decisiondateto=12/31/{year}"
            "&pagenum=" + str(PAGE_SIZE) + "&sortcolumn=dd")
DETAIL_URL = _B + "?id={}"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
END_MARK = "end database content"
BUSY_MARK = "Your request is being processed"

_ROW = re.compile(r"(?is)<tr bgcolor=[^>]*>(.*?)</tr>")
_CELL = re.compile(r"(?is)<td[^>]*>(.*?)</td>")
_ID = re.compile(r"denovo\.cfm\?id=([A-Za-z0-9]+)")
_TOTAL = re.compile(r"of\s+([\d,]+)\s+results", re.I)
_INNER_TD = re.compile(r"(?is)<td[^>]*>((?:(?!<td|<table|</td>).)*)</td>")
_HREF = re.compile(r'(?is)href="([^"]+)"')
_REG = re.compile(r"\b\d{3,4}\.\d+\b")
_DECISION = re.compile(r"^(.*?)\s*\(([A-Z0-9]+)\)\s*$")

TEXT = {"device classification name": "ex:deviceClassificationName",
        "device name": "ex:deviceName",
        "device class": "ex:deviceClass",
        "submission type": "ex:submissionType",
        "classification advisory committee": "ex:classificationAdvisoryCommittee",
        "review advisory committee": "ex:reviewAdvisoryCommittee",
        "type": "ex:deNovoType",
        "third party review": "ex:thirdPartyReview"}
DATES = {"date received": "ex:dateReceived", "decision date": "ex:decisionDate"}
BOOLS = {"predetermined change control plan authorized": "ex:pccpAuthorized",
         "expedited review": "ex:expeditedReview",
         "in vitro device": "ex:inVitroDevice",
         "combination product": "ex:combinationProduct"}
URLS = {"classification order": "ex:classificationOrderUrl", "fda review": "ex:decisionSummaryUrl"}
SPECIAL = {"de novo number", "510(k) number", "requester", "requestor", "regulation number",
           "classification product code", "product code", "decision"}
SKIP = {"contact"}                      # a person: never ingested


# ===========================================================================
#  Polite fetching (same rules as pma_memo.py)
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


def _complete(path):
    if not path.exists():
        return False
    t = path.read_text(encoding="utf-8", errors="replace")
    return END_MARK in t and BUSY_MARK not in t


# ===========================================================================
#  Parsing
# ===========================================================================
def _text(fragment):
    t = re.sub(r"(?i)<br\s*/?>", " ", fragment or "")
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return " ".join(htmllib.unescape(t).replace("\xa0", " ").split())


def parse_list(page):
    """-> (total_or_None, [dict(id, number, denovo, k510, device, requester, decision_date)])."""
    m = _TOTAL.search(_text(page[:400000]))
    total = int(m.group(1).replace(",", "")) if m else None
    rows = []
    for r in _ROW.findall(page):
        c = _CELL.findall(r)
        if len(c) != 5 or "<th" in r:
            continue
        link = _ID.search(c[0])
        if not link:
            continue
        dn, k = _text(c[2]), _text(c[3])
        number = dn or k
        if not number:
            continue
        rows.append({"id": link.group(1).upper(), "number": number.upper(), "denovo": dn or None,
                     "k510": k or None, "device": _text(c[0]), "requester": _text(c[1]),
                     "decision_date": _text(c[4])})
    return total, rows


def _label(th):
    return _text(th).rstrip("?").strip().lower()


def parse_detail(page):
    """-> dict label -> raw value HTML, for every <th> row of the detail table, plus
    'requester' -> (name, address) parsed from the nested table."""
    end = page.find(END_MARK)
    body = page[:end] if end > 0 else page
    if end > 0 and "<!--" in body[-100:]:
        body = body[:body.rfind("<!--")]          # the end marker sits inside an HTML comment
    first = body.find("<th")
    out = {}
    for chunk in body[first:].split("<th")[1:] if first >= 0 else []:
        th, _, val = chunk.partition("</th>")
        lab = _label(th[th.find(">") + 1:])
        if lab and lab not in out:
            out[lab] = val
    return out


def _requester(val):
    lines = [x for x in (_text(t) for t in _INNER_TD.findall(val)) if x]
    lines = [re.sub(r"\s+,", ",", x) for x in lines]
    return (lines[0] if lines else None), (", ".join(lines[1:]) or None)


def detail_pairs(d, unknown):
    """Turtle pairs from a parsed detail page (requester/firm handled by the caller)."""
    pairs, codes, regs = [], [], []
    for lab, val in d.items():
        txt = _text(val)
        if lab in SKIP:
            continue
        if lab in TEXT:
            pairs.append((TEXT[lab], common.lit(txt)))
        elif lab in DATES:
            pairs.append((DATES[lab], common.date_lit(txt)))
        elif lab in BOOLS:
            pairs.append((BOOLS[lab], common.bool_lit(txt) or common.lit(txt)))
        elif lab in URLS:
            h = _HREF.search(val)
            pairs.append((URLS[lab], common.lit(h.group(1)) if h else None))
        elif lab == "de novo number":
            pairs.append(("ex:deNovoNumber", common.lit(txt)))
        elif lab == "510(k) number":
            pairs.append(("ex:k510Number", common.lit(txt)))
        elif lab in ("classification product code", "product code"):
            codes += [c for c in (common.product_code(t) for t in re.findall(r"\b[A-Za-z]{3}\b", txt)) if c]
        elif lab == "regulation number":
            regs += _REG.findall(txt)
        elif lab == "decision":
            m = _DECISION.match(txt)
            pairs.append(("ex:decision", common.lit(m.group(1) if m else txt)))
            if m:
                pairs.append(("ex:decisionCode", common.lit(m.group(2))))
        elif lab in ("requester", "requestor"):
            pass
        else:
            unknown[lab] = unknown.get(lab, 0) + 1
    return pairs, list(dict.fromkeys(codes)), list(dict.fromkeys(regs))


# ===========================================================================
#  Contract
# ===========================================================================
def fetch(dest, delay=1.0, workers=3):
    from concurrent.futures import ThreadPoolExecutor
    dest = Path(dest)
    pages = dest / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    f = Fetcher(delay)
    year = time.gmtime().tm_year
    start, rows, files, total = 1, [], [], None
    while True:                                   # list pages: refetched every run
        url = LIST_URL.format(start=start, year=year)
        text = f.get(url)
        p = pages / f"list_{start}.html"
        _save(p, text)
        total, got = parse_list(text)
        rows += got
        files.append(base.FileRef(url=url, path=str(p.relative_to(dest)),
                                  sha256=base.sha256_file(p), bytes=p.stat().st_size))
        if not got or total is None or len(rows) >= total:
            break
        start += PAGE_SIZE
    for old in pages.glob("list_*.html"):         # drop list pages beyond the current last one
        if int(re.sub(r"\D", "", old.stem)) > start:
            old.unlink()
    ids = sorted({r["id"] for r in rows})
    todo = [i for i in ids if not _complete(pages / f"{i}.html")]
    counts = {"fetched": 0, "errors": 0}
    lock = threading.Lock()

    def one(i):
        try:
            _save(pages / f"{i}.html", f.get(DETAIL_URL.format(i)))
            (pages / f"{i}.error").unlink(missing_ok=True)
            with lock:
                counts["fetched"] += 1
        except Exception as e:                    # one bad page must not stop the run
            (pages / f"{i}.error").write_text(str(e), encoding="utf-8")
            with lock:
                counts["errors"] += 1

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, todo))
    (dest / "fetch_log.json").write_text(json.dumps(
        {"date": base.today(), "fda_total": total, "list_rows": len(rows), "detail_ids": len(ids),
         "fetched_now": counts["fetched"], "errors": counts["errors"]}), encoding="utf-8")
    print(f"  denovo: list {len(rows)} rows (FDA total {total}); {len(ids)} detail pages, "
          f"fetched {counts['fetched']} now, {counts['errors']} errors")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    rows, total = {}, None
    for p in sorted((src / "pages").glob("list_*.html"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
        t, got = parse_list(p.read_text(encoding="utf-8", errors="replace"))
        total = total or t
        for r in got:
            if r["number"] in rows:
                st.skipped += 1
            else:
                rows[r["number"]] = r
    firms, hub_codes, hub_regs, unknown, list_only = {}, set(), set(), {}, []
    with base.TurtleWriter(out) as w:
        for num, r in sorted(rows.items()):
            page = src / "pages" / f"{r['id']}.html"
            d = parse_detail(page.read_text(encoding="utf-8", errors="replace")) if _complete(page) else {}
            if not d:
                list_only.append(num)
            dp, codes, regs = detail_pairs(d, unknown)
            name_d, addr = _requester(d.get("requester") or d.get("requestor") or "")
            requester = r["requester"] or name_d          # the list keeps FDA's capitalization
            nf = normalize_org(requester)
            device = _text(d["device name"]) if "device name" in d else r["device"]
            pairs = [("a", "ex:DeNovoRecord"),
                     ("rdfs:label", common.label(num, device)),
                     ("ex:deNovoNumber", common.lit(r["denovo"])),
                     ("ex:k510Number", common.lit(r["k510"])),
                     ("ex:requester", common.lit(requester)),
                     ("ex:requesterAddress", common.lit(addr))]
            if not d:
                pairs += [("ex:deviceName", common.lit(r["device"])),
                          ("ex:decisionDate", common.date_lit(r["decision_date"]))]
            pairs += dp
            for pc in codes:
                pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
                hub_codes.add(pc)
            for reg in regs:
                pairs += [("ex:regulationNumber", common.lit(reg)),
                          ("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>")]
                hub_regs.add(reg)
            for k in (num, r["k510"]):
                if k:
                    pairs.append(("ex:relatedK510Record", f"<{iri.k510(k)}>"))
            if nf:
                pairs.append(("ex:hasApplicantFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, requester)
            pairs += common.provenance(DATASET, num, source_url=DETAIL_URL.format(r["id"]),
                                       retrieved_at=retrieved_at)
            w.block(iri.de_novo(num), list(dict.fromkeys(p for p in pairs if p[1] is not None)))
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hub_codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
        for reg in sorted(hub_regs):
            w.block(iri.regulation(reg), [("a", "ex:RegulationNumber"), ("ex:regulationNumber", common.lit(reg))])
    st.triples = w.triples
    Path(out).with_suffix(".report.json").write_text(json.dumps(
        {"fda_total": total, "records": st.records, "list_only": list_only, "unknown_labels": unknown},
        indent=1), encoding="utf-8")
    print(f"  denovo: {st.records} records (FDA total {total}); {len(list_only)} without detail page; "
          f"unknown labels {unknown or 'none'}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- fragments copied verbatim from denovo.cfm on October 12, 2026
# ===========================================================================
_LIST = """<td width="30%">
1 to 495 of 495 results
<br />
						<tr bgcolor="#ffffff">
					<td align="left" style="text-transform: capitalize">
							<a style="text-decoration:underline;" href="../cfpmn/denovo.cfm?id=DEN200001" title="details about Cell-Free DNA BCT">Cell-Free DNA BCT</a>
					</td>
					<td align="left" style="text-transform: capitalize">Streck, Inc.</td>
					<td align="middle"><a style="text-decoration:underline;" href="../cfpmn/denovo.cfm?id=DEN200001" title="details about DEN200001">DEN200001</a></td>
					<td align="middle"></td>
					<td align="center">08/07/2020</td>
					</tr>
<tr bgcolor="#f9f9f3"> <td align="left" style="text-transform: capitalize"> <a style="text-decoration:underline;" href="../cfpmn/denovo.cfm?id=BK060036" title="details about Sepax Cell Separation System and Single ">Sepax Cell Separation System and Single </a> </td> <td align="left" style="text-transform: capitalize">Biosafe SA</td> <td align="middle"><a style="text-decoration:underline;" href="../cfpmn/denovo.cfm?id=" title="details about "></a></td> <td align="middle">BK060036</td> <td align="center">01/03/2007</td> </tr>
<!-- ********  end database content ******** -->"""

_DETAIL = """<table align="center" style="text-transform: capitalize; table-layout:fixed; width:500px">
<tr><th align="left" style="font-family: arial; color: #23238e;">Device Classification Name</th>
<td align="left" class="wrapword">
<a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfpcd/classification.cfm?start_search=1&productcode=QMA" title="Products Classification Database Information for Blood Collection Device For Cell-Free Nucleic Acid">blood collection device for cell-free nucleic acid</a>
</td>
</tr>
<tr><th align="left">De Novo Number</th>
<td align="left">DEN200001</td></tr>
<tr valign="top"><th align="left">Device Name</th>
<td align="left" class="wrapword">Cell-Free DNA BCT</td></tr>
<tr valign="top">
<th align="left">Requester</th>
<td align="left">
<table border="0" cellpadding="0" cellspacing="0" style="text-transform: capitalize">
<tr valign="top"><td align="left" >streck, inc.</td></tr>
<tr><td align="left">7002 s 109th st.</td></tr>
<tr><td align="left">
la vista,&nbsp;
NE&nbsp;
68128
</td></tr>
</table>
</td></tr>
<tr>
<th align="left">Contact</th>
<td align="left">deborah  kipp</td>
</tr>
<tr><td align="left">
</td></tr>
<tr><th align="left">Regulation Number</th><td align="left"><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfcfr/cfrsearch.cfm?fr=862.1676" title="Code of Federal Regulations (CFR) database information for 862.1676">862.1676</a>
</td></tr>
<tr valign="top"><th align="left">Classification Product Code</th>
<td align="left">
<table border="0" cellpadding="0" cellspacing="0">
<tr>
<td align="left"><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfpcd/classification.cfm?start_search=1&productcode=QMA">QMA</a>&nbsp;&nbsp;</td>
</tr>
</table>
</td>
</tr>
<tr><th align="left">Date Received</th><td align="left">01/10/2020</td></tr>
<tr><th align="left">Decision Date</th><td align="left"> 08/07/2020</td></tr>
<tr><th align="left">Decision</th>
<td align="left">
granted
(DENG)</td></tr>
<tr><th align="left">Classification Advisory Committee</th>
<td align="left">
Clinical Chemistry
</td></tr>
<tr><th align="left">Review Advisory Committee</th>
<td align="left">
Clinical Chemistry
</td></tr>
<tr>
<th align="left">Classification Order</th>
<td align="left">
<A style="text-decoration:underline;" HREF="https://www.accessdata.fda.gov/cdrh_docs/pdf20/DEN200001.pdf" TITLE="PDF for DEN200001"  style="text-decoration:underline;">Classification Order</A>
</td>
</tr>
<tr><th align="left">FDA Review</th>
<td align="left"><a style="text-decoration:underline;" href="http://www.accessdata.fda.gov/cdrh_docs/reviews/DEN200001.pdf" title="pdf of 510(k) DEN200001">Decision Summary</a>
</td>
</tr>
<tr><th align="left">Type</th><td align="left">
Direct
</td></tr>
<tr><th align="left">Predetermined Change<br>Control Plan Authorized</th><td align="left">
No
</td></tr>
</table>
<!-- ********  end database content ******** -->"""

_DETAIL_CBER = """<tr><th align="left">510(K) Number</th>
<td align="left">BK060036</td></tr>
<tr valign="top"><th align="left">Device Name</th>
<td align="left">Sepax Cell Separation System and Single Use Kits</td></tr>
<tr><th align="left">Product Code</th><td align="left"><a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfpcd/classification.cfm?start_search=1&productcode=OAO" title="Products Classification Database Information for OAO">OAO</a>
</td></tr>
<tr><th align="left">Device Class</th><td align="left">II</td></tr>
<tr><th align="left">Decision</th><td align="left">DENOVOGRANT</td></tr>
<tr><th align="left">Third Party Review</th>
<td align="left">
Not Third Party Elligble
</td>
</tr>
<tr><th align="left">In Vitro Device?</th>
<td align="left">
No
</td>
</tr>
</table>
<!-- ********  end database content ******** -->"""


def _selftest():
    import tempfile
    total, rows = parse_list(_LIST)
    assert total == 495 and [r["number"] for r in rows] == ["DEN200001", "BK060036"], rows
    assert rows[1]["id"] == "BK060036" and rows[1]["denovo"] is None and rows[1]["k510"] == "BK060036"
    d = parse_detail(_DETAIL)
    assert _requester(d["requester"]) == ("streck, inc.", "7002 s 109th st., la vista, NE 68128"), _requester(d["requester"])
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        _save(t / "pages" / "list_1.html", _LIST)
        _save(t / "pages" / "DEN200001.html", _DETAIL)
        _save(t / "pages" / "BK060036.html", _DETAIL_CBER)
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-12", files=[]).write(t / "manifest.json")
        st = transform(t, t / "denovo.ttl")
        ttl = (t / "denovo.ttl").read_text(encoding="utf-8")
    assert st.records == 2, st
    s = f"<{iri.de_novo('DEN200001')}>"
    assert s in ttl and f"<{iri.de_novo('BK060036')}>" in ttl
    assert f"ex:relatedK510Record <{iri.k510('DEN200001')}>" in ttl
    assert f"ex:hasProductCode <{iri.product_code('QMA')}>" in ttl
    assert f"ex:hasRegulationNumber <{iri.regulation('862.1676')}>" in ttl
    assert f"ex:hasApplicantFirm <{iri.firm_name('STRECK')}>" in ttl
    assert 'ex:requester "Streck, Inc."' in ttl and 'ex:requesterAddress "7002 s 109th st., la vista, NE 68128"' in ttl
    assert 'ex:decision "granted"' in ttl and 'ex:decisionCode "DENG"' in ttl
    assert 'ex:decisionDate "2020-08-07"^^xsd:date' in ttl and 'ex:dateReceived "2020-01-10"^^xsd:date' in ttl
    assert 'ex:pccpAuthorized "false"^^xsd:boolean' in ttl and 'ex:deNovoType "Direct"' in ttl
    assert 'ex:classificationOrderUrl "https://www.accessdata.fda.gov/cdrh_docs/pdf20/DEN200001.pdf"' in ttl
    assert 'ex:decisionSummaryUrl "http://www.accessdata.fda.gov/cdrh_docs/reviews/DEN200001.pdf"' in ttl
    assert 'ex:inVitroDevice "false"^^xsd:boolean' in ttl and 'ex:thirdPartyReview "Not Third Party Elligble"' in ttl
    assert 'ex:deviceName "Sepax Cell Separation System and Single Use Kits"' in ttl   # full name, not the list's cut
    assert "kipp" not in ttl.lower()                                       # contact person excluded
    assert f"<{iri.product_code('OAO')}>\n    a ex:ProductCode" in ttl
    assert f"<{iri.regulation('862.1676')}>\n    a ex:RegulationNumber" in ttl
    print(f"denovo.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
