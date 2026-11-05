#!/usr/bin/env python3
"""
foia_reading_room.py -- CDRH FOIA Electronic Reading Room (the listing of FOIA-released records) -> RDF.

LISTING (accessdata ColdFusion page, checked 2026-11-05)
  https://www.accessdata.fda.gov/scripts/cdrh/devicesatfda/readingroom.cfm
      ?start_search=1&q=&pagenum=<rows per page>
  one table (id="rr-results-table"): Document Number | FOIA Released Record | Size | Type | Date Posted
  ("1 to 5 of 177 records" on November 5, 2026). The page offers 5 to 500 rows per page; fetch() asks
  for 500 per page and follows start_search until every listed record is read (one request on
  November 5, 2026). The first column is either a document number linked to its 510(k), PMA or 522
  page (K..., P..., PS...) or a category word (Letter, Recall, Presentation, or blank); the second
  links the released document (accessdata cdrh_docs / CDRH510K PDFs, fda.gov /downloads/... or
  /media/<id>/download).

KEPT per row (listing metadata only): title (rdfs:label), document type (Type column, as published),
  reading-room category (the first column when it is not a number), file size (as published), date
  posted, the document URL (ex:url; a link only, the document is never downloaded or stored),
  510(k)/De Novo, PMA and 522 numbers in the first column or the title (ex:mentionsK510Record,
  ex:mentionsPMARecord; a 522 order number links to the study nodes of postmarket_522.ttl, which
  are keyed on FDA's study id, through their ex:orderNumber -> ex:mentionsPostmarket522Study, when
  that file is given to transform), and a firm named in the title of a letter
  ("<Firm> - <place> - 483 Issued", "<Firm> - <City>, <State>", "Letter to <Firm> Concerning ...",
  "... Letter to <Firm>") as ex:namesFirm -> iri.firm_name(normalized name). The listing shows no
  product codes or FEI numbers.
PERSONS: FDA staff presentations are titled "<Surname> - <topic>" (category Presentation, type PPT);
  the surname is dropped, and a "Dr./Mr./Mrs./Ms. <Name>" in any title is replaced by "[NAME
  WITHHELD]"; such rows carry an ex:dataQualityNote. No person is ingested.

Key: the document URL (FDA media id, else the PDF file name):
  https://www.fda.gov/media/93240/download -> media_93240;  .../UCM135899.pdf -> ucm135899;
  .../CDRH510K/K011251.pdf -> k011251.  Minter: iri.foia_reading_room_record(key)
"""
import html as htmllib
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
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
import _cfweb     # noqa: E402
from identity import normalize_org   # noqa: E402

NAME = "foia_reading_room"
DATASET = "cdrh/foia-electronic-reading-room"
SITE = "https://www.accessdata.fda.gov"
LIST_URL = SITE + "/scripts/cdrh/devicesatfda/readingroom.cfm"
PER_PAGE = 500

_TABLE = re.compile(r'(?is)<table\b[^>]*id="rr-results-table"[^>]*>(.*?)</table>')
_TR = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr>")
_TD = re.compile(r"(?is)<td\b[^>]*>(.*?)</td>")
_A = re.compile(r'(?is)<a\b[^>]*href="([^"]*)"[^>]*>(.*?)</a>')
_TOTAL = re.compile(r"(?i)\b(\d+)\s+to\s+(\d+)\s+of\s+(\d+)\s+records")
_NUM = re.compile(r"^(?:K\d{6}|DEN\d{6}|P\d{6}|N\d{4,5}|PS\d{6})$")
_K510 = re.compile(r"\b((?:K|DEN)\d{6})\b")
_PMA = re.compile(r"\b(P\d{6})\b")
_PS = re.compile(r"\b(PS\d{6})\b")
_HONORIFIC = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|Miss|Prof)\.?\s+[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?")
_PRESENTER = re.compile(r"^\s*([A-Z][A-Za-z'\-]+)\s+-\s+(.+)$")
_483 = re.compile(r"^\s*(.+?)\s+-\s+.+\s+-\s+483\s+Issued\s*$", re.I)
_FIRM_PLACE = re.compile(r"^\s*(.+?)\s+-\s+[A-Z][A-Za-z .'\-]+,\s+[A-Z][A-Za-z .'\-]+\s*$")
_LETTER_TO = re.compile(r"(?i)\bletter\s+to\s+(.+?)(?:\s+(?:concerning|regarding|re)\b.*)?$")
_ORG_WORD = re.compile(r"(?i)\b(inc|llc|ltd|corp|corporation|company|co|gmbh|plc|ag|sa|kk|group|medical|"
                       r"systems?|pharma|division|laboratories|optics)\b")
_TWO_NAMES = re.compile(r"^[A-Z][a-z'\-]+\s+(?:[A-Z]\.\s+)?[A-Z][a-z'\-]+$")      # "Jane Roe": could be a person


def _one(fragment):
    return _cfweb.one_line(fragment)


def _abs(href):
    h = htmllib.unescape(href or "").strip()
    return SITE + h if h.startswith("/") else h


def doc_key(url):
    """Stable key from the document URL (see module docstring)."""
    u = (url or "").split("?", 1)[0].rstrip("/")
    m = re.search(r"/media/(\d+)/download$", u)
    if m:
        return f"media_{m.group(1)}"
    stem = u.rsplit("/", 1)[-1]
    return re.sub(r"(?i)\.pdf$", "", stem).lower() or None


def parse_listing(page):
    """-> (total records or None, [row dict])."""
    m = _TOTAL.search(_one(page) or "")
    total = int(m.group(3)) if m else None
    t = _TABLE.search(page)
    rows = []
    for tr in _TR.findall(t.group(1) if t else ""):
        cells = _TD.findall(tr)
        if len(cells) < 5:
            continue                                   # header row (th cells)
        first = _one(cells[0]) or ""
        a = _A.search(cells[1])
        rows.append({
            "number": first if _NUM.match(first) else None,
            "category": None if (not first or _NUM.match(first)) else first,
            "title": _one(a.group(2)) if a else _one(cells[1]),
            "url": _abs(a.group(1)) if a else None,
            "size": _one(cells[2]),
            "type": _one(cells[3]),
            "posted": _one(cells[4]),
        })
    return total, rows


def withhold_persons(title, category, typ):
    """-> (title without person names, True if a name was dropped)."""
    t, dropped = title or "", False
    if (category or "").lower() == "presentation" or (typ or "").upper() == "PPT":
        m = _PRESENTER.match(t)
        if m and not _ORG_WORD.search(m.group(1)):
            t, dropped = m.group(2).strip(), True
    t2 = _HONORIFIC.sub("[NAME WITHHELD]", t)
    return t2, dropped or t2 != t


def firm_of(title, category):
    """A firm named in a letter's title (firm names only), else None."""
    if (category or "").lower() not in ("letter", "recall") and "483 issued" not in (title or "").lower():
        return None
    for rx in (_483, _LETTER_TO, _FIRM_PLACE):
        m = rx.search(title or "")
        if m:
            f = m.group(1).strip(" ,-")
            if _HONORIFIC.search(f) or (_TWO_NAMES.match(f) and not _ORG_WORD.search(f)):
                return None
            # a Form 483 heading names the inspected establishment; other titles need an organization word
            if _ORG_WORD.search(f) or rx is _483:
                return f
    return None


def numbers_of(row):
    text = " ".join(x for x in (row.get("number"), row.get("title")) if x)
    k = sorted(set(_K510.findall(text)))
    p = sorted(set(_PMA.findall(text)))
    ps = sorted(set(_PS.findall(text)))
    return k, p, ps


# ---------------------------------------------------------------------------
def _page_url(start):
    return f"{LIST_URL}?start_search={start}&q=&pagenum={PER_PAGE}"


def fetch(dest, delay=1.0, log=print, max_age_days=0.5):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    names, start, total, seen = [], 1, None, 0
    while True:
        nm = f"list_{start}"
        page = f.get(_page_url(start), nm, max_age_days=max_age_days)
        t, rows = parse_listing(page)
        total = t if t is not None else total
        names.append([_page_url(start), nm, len(rows)])
        seen += len(rows)
        if not rows or total is None or seen >= total:
            break
        start += PER_PAGE
    (dest / "lists.json").write_text(json.dumps({"date": base.today(), "total": total, "pages": names}, indent=1),
                                     encoding="utf-8")
    log(f"  foia_reading_room: {seen} rows of {total} listed; fetched {f.fetched}, cached {f.cached}")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    return m


def _rows(src):
    meta = json.loads((Path(src) / "lists.json").read_text(encoding="utf-8"))
    out = []
    for url, nm, _ in meta["pages"]:
        page = (Path(src) / "pages" / f"{_cfweb.safe_name(nm)}.html").read_text(encoding="utf-8", errors="replace")
        out += parse_listing(page)[1]
    return meta, out


def _pss_map(pss_ttl):
    """Section 522 order number -> [study node IRI] read from postmarket_522.ttl, whose studies are
    keyed on FDA's study id and carry ex:orderNumber "PS160001 / PSS001"."""
    out = {}
    if not pss_ttl or not Path(pss_ttl).exists():
        return out
    subj = None
    for ln in Path(pss_ttl).read_text(encoding="utf-8").splitlines():
        if ln.startswith("<"):
            subj = ln.split()[0][1:-1]
        m = re.search(r'ex:orderNumber "(PS\d{6})\b', ln)
        if m and subj:
            out.setdefault(m.group(1), [])
            if subj not in out[m.group(1)]:
                out[m.group(1)].append(subj)
    return out


def transform(src, out, retrieved_at=None, pss_ttl=None):
    """pss_ttl: postmarket_522.ttl, to link Section 522 order numbers to their study nodes."""
    src, out = Path(src), Path(out)
    pss = _pss_map(pss_ttl)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    meta, rows = _rows(src)
    st = base.Stats(source=NAME)
    counts = {"listed": meta.get("total"), "rows": len(rows), "with_k510": 0, "with_pma": 0, "with_522": 0,
              "with_firm": 0, "person_withheld": 0, "no_url": 0, "duplicate_keys": 0}
    firms, keys = {}, {}
    with base.TurtleWriter(out) as w:
        for r in rows:
            key = doc_key(r["url"])
            if not key:
                counts["no_url"] += 1
                st.skipped += 1
                continue
            keys[key] = keys.get(key, 0) + 1
            if keys[key] > 1:                        # the same document listed twice: keep each row
                counts["duplicate_keys"] += 1
                key = f"{key}_{keys[key]}"
            title, withheld = withhold_persons(r["title"], r["category"], r["type"])
            counts["person_withheld"] += withheld
            k, p, ps = numbers_of({"number": r["number"], "title": title})
            pairs = [("a", "ex:FOIAReadingRoomRecord"),
                     ("rdfs:label", common.lit(title)),
                     ("ex:documentNumber", common.lit(r["number"])),
                     ("ex:readingRoomCategory", common.lit(r["category"])),
                     ("ex:documentType", common.lit(r["type"])),
                     ("ex:fileSize", common.lit(r["size"])),
                     ("ex:datePosted", common.date_lit(r["posted"])),
                     ("ex:url", common.lit(r["url"]))]
            for n in k:
                pairs += [("ex:kNumber", common.lit(n)), ("ex:mentionsK510Record", f"<{iri.k510(n)}>")]
            for n in p:
                pairs += [("ex:pmaNumber", common.lit(n)), ("ex:mentionsPMARecord", f"<{iri.pma(n)}>")]
            for n in ps:
                pairs += [("ex:mentionsPostmarket522Study", f"<{s}>") for s in pss.get(n, [])]
            firm = None if withheld else firm_of(title, r["category"])
            nf = normalize_org(firm) if firm else None
            if nf:
                pairs.append(("ex:namesFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, firm)
            if withheld:
                pairs.append(("ex:dataQualityNote", common.lit(
                    "a person's name in FDA's listing title is not stored")))
            counts["with_k510"] += bool(k)
            counts["with_pma"] += bool(p)
            counts["with_522"] += bool(ps)
            counts["with_firm"] += bool(nf)
            pairs += common.provenance(DATASET, key, source_url=LIST_URL, retrieved_at=retrieved_at)
            w.block(iri.foia_reading_room_record(key), pairs)
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
    st.triples = w.triples
    out.with_suffix(".counts.json").write_text(json.dumps(dict(counts, records=st.records), indent=1), encoding="utf-8")
    print(f"  foia_reading_room: {st.records} records; {counts}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim rows read 2026-11-05 (tabs and blank lines as served; the Presentation row's title
# carries an FDA staff surname, which must not be stored), and the record-count line.
_ROWS = [
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="/scripts/cdrh/cfdocs/cfpma/pma.cfm?id=P020014"  target="_blank">P020014</a>\n\t\t\t\t\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.accessdata.fda.gov/cdrh_docs/presentations/EssureFOIReleasableMDRReports.pdf"  target="_blank">ESSURE Maude Reports</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t61MB\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\tPDF\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t02/09/2016\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?id=K011251"  target="_blank">K011251</a>\n\t\t\t\t\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.accessdata.fda.gov/CDRH510K/K011251.pdf"  target="_blank">K011251 - American Medical Systems, Inc SPARC Sling System</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t4.46MB\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t510K\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t08/12/2013\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\tPresentation\n\t\t\t\t\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.fda.gov/downloads/AboutFDA/CentersOffices/OfficeofMedicalProductsandTobacco/CDRH/CDRHFOIAElectronicReadingRoom/UCM279425.pdf"  target="_blank">Ghosh - Contact lens presentation at 15th CL symposium on 03-18-11</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t363KB\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\tPPT\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t06/11/2013\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\tPS160001\n\t\t\t\t\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.fda.gov/media/125016/download"  target="_blank">ESSURE 24 Month Interim Postmarket Surveillance Study Report - September 2018</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t23MB\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\tPSS\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t10/01/2014\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\tLetter\n\t\t\t\t\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.fda.gov/media/78742/download"  target="_blank">Letter to Pathway Genomics Corporation Concerning the Pathway Genomics Genetic Health Report</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t&nbsp;\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\tLetter\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t06/11/2013\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\tLetter\n\t\t\t\t\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.fda.gov/media/93240/download"  target="_blank">Aizu Olympus Co. Ltd - Aizuwakamatsu-City, Fukushima, Japan - 483 Issued</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t1.1MB\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\tPDF\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t04/17/2015\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
    '<tr>\n\t\t<td>\n\t\t\t\n\t\t\t\t&nbsp;\n\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t\n\t\t\t\t\t<a href="https://www.fda.gov/media/117046/download"  target="_blank">Opternative Warning Letter Response - Letter issued 09-21-</a>\n\t\t\t\t\t\t\t\t\n\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t38KB\n\t\t\t\t\t\n\t\t</td>\n\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\tLetter\n\t\t\t\t\t\n\t\t</td>\n\t\t\t\t\n\t\t<td>\n\t\t\t\n\t\t\t\t09/21/2018\n\t\t\t\t\t\n\t\t</td>\t\t\n\t\t</tr>',
]
_PAGE = ('<p>1 to 5 of 177 records</p><table id="rr-results-table" cellpadding="0" cellspacing="0">\n<tbody>\n\t<tr>\n'
         '\t\t<th scope="col" style="width:15%">\n\t\t\t<div style="float: left;">Document<br>Number</div></th></tr>'
         + "".join(_ROWS) + "</tbody></table>")


def _selftest():
    import tempfile
    total, rows = parse_listing(_PAGE)
    assert total == 177 and len(rows) == 7, (total, len(rows))
    assert rows[0] == {"number": "P020014", "category": None, "title": "ESSURE Maude Reports",
                       "url": "https://www.accessdata.fda.gov/cdrh_docs/presentations/EssureFOIReleasableMDRReports.pdf",
                       "size": "61MB", "type": "PDF", "posted": "02/09/2016"}, rows[0]
    assert rows[2]["category"] == "Presentation" and rows[6]["category"] is None and rows[4]["size"] is None
    assert doc_key(rows[0]["url"]) == "essurefoireleasablemdrreports" and doc_key(rows[1]["url"]) == "k011251"
    assert doc_key(rows[2]["url"]) == "ucm279425" and doc_key(rows[3]["url"]) == "media_125016"
    assert withhold_persons(rows[2]["title"], "Presentation", "PPT") == \
        ("Contact lens presentation at 15th CL symposium on 03-18-11", True)
    assert withhold_persons("Letter to Dr. Jane Roe re: device", "Letter", "Letter") == \
        ("Letter to [NAME WITHHELD] re: device", True)
    assert firm_of(rows[5]["title"], "Letter") == "Aizu Olympus Co. Ltd"
    assert firm_of(rows[4]["title"], "Letter") == "Pathway Genomics Corporation"
    assert firm_of("Custom Ultrasonics, Inc. - Ivyland, Pennsylvania", "Letter") == "Custom Ultrasonics, Inc."
    assert firm_of(rows[1]["title"], None) is None
    assert firm_of("Fujifilm - Ashigarakami Gun, Japan - 483 Issued", "Letter") == "Fujifilm"
    assert firm_of("Jane Roe - Springfield, Ohio - 483 Issued", "Letter") is None
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "list_1.html").write_text(_PAGE, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"date": "2026-11-05", "total": 177,
                                                  "pages": [[_page_url(1), "list_1", 7]]}), encoding="utf-8")
        # two study blocks in postmarket_522.ttl's layout (verbatim order-number value)
        (t / "pss.ttl").write_text(f"<{iri.postmarket_522('356')}>\n    a ex:Postmarket522Study ;\n"
                                   f'    ex:orderNumber "PS160001 / PSS001" .\n\n<{iri.postmarket_522("358")}>\n'
                                   '    ex:orderNumber "PS160003 / PSS001" .\n', encoding="utf-8")
        st = transform(t, t / "f.ttl", retrieved_at="2026-11-05", pss_ttl=t / "pss.ttl")
        ttl = (t / "f.ttl").read_text(encoding="utf-8")
    assert st.records == 7, st
    assert "Ghosh" not in ttl and "a person's name in FDA's listing title is not stored" in ttl
    assert f"ex:mentionsPMARecord <{iri.pma('P020014')}>" in ttl and f"ex:mentionsK510Record <{iri.k510('K011251')}>" in ttl
    assert ttl.count("ex:mentionsPostmarket522Study") == 1 and f"ex:mentionsPostmarket522Study <{iri.postmarket_522('356')}>" in ttl
    assert f"ex:namesFirm <{iri.firm_name(normalize_org('Aizu Olympus Co. Ltd'))}>" in ttl and "a ex:FirmName" in ttl
    assert 'ex:datePosted "2016-02-09"^^xsd:date' in ttl and 'ex:fileSize "61MB"' in ttl
    assert 'ex:readingRoomCategory "Letter"' in ttl and 'ex:documentType "PSS"' in ttl
    assert f"<{iri.foia_reading_room_record('media_93240')}>" in ttl
    assert ttl.count("a ex:FOIAReadingRoomRecord") == 7 and ttl.count("rdfs:label") == 7 + ttl.count("a ex:FirmName")
    print(f"foia_reading_room.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        # foia_reading_room.py transform <download dir> <out.ttl> [<postmarket_522.ttl>]
        s = transform(sys.argv[2], sys.argv[3], pss_ttl=sys.argv[4] if len(sys.argv) > 4 else None)
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
