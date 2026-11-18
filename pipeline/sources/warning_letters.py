#!/usr/bin/env python3
"""
warning_letters.py -- FDA Warning Letters about medical devices -> RDF (metadata and cited identifiers only).

LISTING
-------
https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/compliance-actions-and-activities/warning-letters
is a Drupal "datatables" view. The page fills its table from the JSON endpoint named in its own
drupal-settings (view warning_letter_solr_index / warning_letter_solr_block, checked 2026-10-28):
    https://www.fda.gov/datatables/views/ajax?draw=1&start=<n>&length=<k>&view_name=...&view_display_id=...
It answers {"recordsTotal": N, "data": [[posted, issued, company link, office, subject, response,
closeout, excerpt], ...]} with HTML fragments in each cell. The listing is paged 100 rows at a time.
(The page's own "Export Excel" link returns only the first 1,000 rows and no letter URLs, so it is
not used.) On 2026-10-28 the index held 3,779 letters (all FDA centers).

DEVICE FILTER (listing level, documented so it can be audited)
  keep a letter when its Issuing Office matches   DEVICE_OFFICE  (CDRH, "Medical Device", "Radiological Health")
  or its Subject matches                         DEVICE_SUBJECT (device, 510(k), PMA, QSR, mammography, ...)
The letter page's own "Product:" field (e.g. "Medical Devices") is kept as ex:letterProductType so a
consumer can tighten the filter further.

LETTER PAGES
------------
Each kept letter page is fetched once (1 request/second, cached under pages/). From the page we keep
ONLY: the MARCS-CMS number, the "Product:" field, the firm's postal address (structured address spans
of the recipient block), FEI number(s), 510(k)/De Novo and PMA numbers (except numbers FDA cannot
have issued, such as firm document numbers shaped like a PMA number; see possible_number), three-letter product codes
introduced by "product code", and the 21 CFR sections/parts cited. The letter body, the recipient name
and title, signatories, phone numbers and e-mail addresses are NEVER stored.

Firm link: ex:hasManufacturer iri.manufacturer(fei=...) when the letter shows an FEI, otherwise
ex:hasRecipientFirm iri.firm_name(normalize_org(company)).

LETTERS TO INDIVIDUALS: some letters (clinical investigators, sole individuals) carry a PERSON's name in
the "Company Name" column. is_individual() flags them (degree suffix such as "M.D.", a company name equal
to the page's recipient name, or a "Clinical Investigator" subject without an organization word); for
those the name, address, FEI, firm link and FDA URLs (which embed the name) are dropped, provenance points
at the listing page, and ex:dataQualityNote says why.

LETTERS BEFORE 2021 (fetch_archive, PI approval of 2026-11-03)
---------------------------------------------------------------
FDA's index above starts in 2021. Earlier letters were listed on the old site as one page per year,
    https://www.fda.gov/ICECI/EnforcementActions/WarningLetters/<year>/default.htm (?Page=N),
columns Letter Issue Date | Company Name | Issuing Office | Subject | Close Out Date, which survive
only in FDA's official web archive on Archive-It (collection 7993, "FDA.gov Web Archive", linked from
https://www.fda.gov/about-fda/about-website/fdagov-archive). fetch_archive() asks the collection's
CDX index for each year page, reads the newest readable capture of page 1 and of every page it links
(?Page=N), applies the same device filter to the rows, and reads each device letter page from the same
archive (nearest capture), all cached under archive/. Readable listings exist for 2005-2018 and for
January-April 2019 (2019/default.htm, last captured 2019-04-24); the 1996-2004 year pages are archived
only as redirect records, and from mid-2019 the listing is the JavaScript table above, whose data the
archive did not capture. The letter page is parsed exactly as above (identifiers only; never the body,
recipient or signatory); old pages also write "CMS Case: NNNNNN" and end at "Page Last Updated".
Old pages have no structured recipient fields, so is_individual() also compares the salutation
("Dear Dr. Smith:") with the Company Name column, used only for that check and never stored.
Archive-derived letters keep the IRI rule (the URL slug, e.g. ucm534145.htm, as old URLs carry no
CMS number), ex:url/ex:sourceUrl = the archive URL, ex:archivedFrom = the original fda.gov URL, and an
ex:dataQualityNote saying the letter was read from FDA's archive copy. Letters to individuals are
withheld exactly as above (no name, address, FEI, firm link or URLs; provenance = the archived listing).

LETTERS OF MAY 2019 - DECEMBER 2020 (fetch_archive_newsite, added 2026-11-11)
----------------------------------------------------------------------------
From mid-2019 FDA's listing is the JavaScript table above; Archive-It 7993 holds the page that draws it,
but not the table data (the collection's only datatables/views/ajax captures are 404 pages), and the
current index starts in 2021. The letter pages of that period ARE in the collection (captures
September 2019 - December 2020) under
    https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/<slug>-<CMS>-<MMDDYYYY>
fetch_archive_newsite() asks the collection's CDX index for that URL prefix (status 200), keeps the
letter URLs whose date part lies in NEWSITE_FROM..NEWSITE_TO and reads each page (all FDA centers,
because only the page says which office issued it): from fda.gov itself when fda.gov still serves it
(it does for the letters checked on 2026-11-11, although no FDA listing links them; cached under pages/
like current letters, 1 request per second), otherwise the newest archive capture. The device letters
are kept by the same filter, applied to the page's "Issuing Office" and "Product" fields (these pages
show no Subject, so these records have no ex:letterSubject). A letter read from fda.gov is written like
a current letter (ex:url = the fda.gov page) with an ex:dataQualityNote saying how it was found. The
page is parsed exactly as a current letter (parse_letter: identifiers only, never the body, recipient
name/title, signatory or e-mail addresses). Company Name comes from the page title
("<company> - <CMS> - <MM/DD/YYYY> | FDA"). A letter already in the current index or in the 2019
old-site listing (same key, or same issue date and company) is not written twice; the MARCS-CMS number
alone is not a letter id (FDA reuses a case number for later letters of the same case).

LETTERS BEFORE 2005 (fetch_archive_foi, added 2026-11-11)
---------------------------------------------------------
Before the ICECI year pages (2005-2018), FDA's Electronic FOI Reading Room listed warning letters by
month (www.fda.gov/foi/warning.htm -> http://www.accessdata.fda.gov/scripts/wlcfm/date.cfm?MONTH=m&YEAR=y
and date_archive.cfm, November 1996 - December 2004), columns Company Name | Date Issued | Issuing
Office | Subject | File (PDF; HTML too for letters posted after June 25, 2001). Archive-It 7993 holds
none of these (its CDX has no capture of /foi/warning_letters/ or of the 1996-2004 year pages except
redirects); web.archive.org does, and fda.gov links its archive.org copy of www.fda.gov from
https://www.fda.gov/about-fda/about-website/fdagov-archive. fetch_archive_foi() reads web.archive.org's
CDX index for the two index scripts, the newest readable capture of every month page from FOI_FIRST
to FOI_LAST, applies the same device filter to the rows, and reads each device letter's HTML page
(www.fda.gov/foi/warning_letters/<file>.htm) from web.archive.org (nearest capture); PDF-only letters
keep the listing metadata and a note that the letter was not read. NOTE: the month index pages are on
the host www.accessdata.fda.gov (linked from the archived www.fda.gov/foi/warning.htm), not www.fda.gov;
the letters themselves are www.fda.gov pages. Old listings write company names as "Last, First" for
people; such rows (no organization word) are treated as letters to individuals (see above).

Minter: iri.warning_letter(key) -> .../id/warning-letter/<slug(key)>
        key = "<MARCS-CMS number>-<MMDDYYYY>" taken from the letter URL ("...-727888-08262026"),
        else the whole URL slug.   iri.warning_letter('727888-08262026') -> .../id/warning-letter/727888_08262026
"""
import html as htmllib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
import _cfweb     # noqa: E402
import _archive   # noqa: E402
from identity import normalize_org   # noqa: E402

NAME = "warning_letters"
DATASET = "fda/warning-letters"
SITE = "https://www.fda.gov"
LIST_PAGE = SITE + "/inspections-compliance-enforcement-and-criminal-investigations/compliance-actions-and-activities/warning-letters"
AJAX = SITE + "/datatables/views/ajax"
VIEW = {"view_name": "warning_letter_solr_index", "view_display_id": "warning_letter_solr_block",
        "view_args": "", "view_path": "/node/360089",
        "view_base_path": "inspections-compliance-enforcement-and-criminal-investigations/"
                          "compliance-actions-and-activities/warning-letters/datatables-data",
        "pager_element": "0"}
PAGE_ROWS = 100

DEVICE_OFFICE = re.compile(r"(?i)devices\s+and\s+radiological\s+health|\bCDRH\b|medical\s+device|radiological\s+health")
DEVICE_SUBJECT = re.compile(
    r"(?i)medical\s+devices?|\bdevices?\b|510\s*\(k\)|\bPMA\b|premarket\s+(?:approval|notification)|"
    r"quality\s+system|\bQSR\b|\bQMSR\b|mammography|radiation[- ]emitting|\bMDR\b|\bUDI\b|investigational\s+device")

_TIME = re.compile(r'<time[^>]*>\s*([^<]+?)\s*</time>')
_HREF = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_KEY = re.compile(r"-(\d{4,8})-(\d{8})$")
_CMS = re.compile(r"\bCMS\s*(?:#|No\.?|Number)?\s*:?\s*(\d{4,8})\b")
_FEI = re.compile(r"\bFEI(?:\s*(?:#|No\.?|Number|Numbers|Nos\.?))?\s*[:#]?\s*((?:\d{7,10})(?:\s*(?:,|and|&|/)\s*\d{7,10})*)")
_K510 = re.compile(r"\b((?:K|DEN)\d{6})\b")
_PMA = re.compile(r"\b(P\d{6})(?:\s*/\s*(S\d{3,4}))?\b")
_PC = re.compile(r"(?<![Uu]niversal )(?i:product\s+codes?)\s*(?:\(s\))?\s*[:#]?\s*\(?((?:[A-Z]{3})(?:\s*(?:,|;|/|&|\band\b|\bor\b)\s*[A-Z]{3}\b)*)\b")
_CFR = re.compile(r"21\s*C\.?\s*F\.?\s*R\.?\s*(?:§+\s*|[Pp]arts?\s+|[Ss]ections?\s+|[Ss]ec\.\s*)?"
                  r"((?:\d{1,4}(?:\.\d{1,4})?(?:\s*\([a-zA-Z0-9]{1,4}\))*)"
                  r"(?:\s*(?:,|;|\band\b|\bor\b|,\s*and|,\s*or|through|-)\s*(?:§+\s*)?"
                  # a following "21 CFR ..." or "21 U.S.C. ..." starts a new citation, not another section;
                  # "90 Fed. Reg. 25889" / "53 FR 23872" is a Federal Register volume, never a CFR part
                  r"(?:\d{1,4}(?:\.\d{1,4})?(?!\d|\s*(?:C\.?\s*F\.?\s*R|U\.?\s*S\.?\s*C|Fed\.?\s*Reg|F\.?\s*R\b))"
                  r"(?:\s*\([a-zA-Z0-9]{1,4}\))*"
                  r"|\([a-zA-Z0-9]{1,4}\)))*)")
_CFR_TOKEN = re.compile(r"(?<![\w.(])(\d{1,4})(?:\.(\d{1,4}))?")


def _one(fragment):
    return _cfweb.one_line(fragment)


def _dates_links(cell):
    """[(iso-able date text, absolute url or None)] from a listing cell."""
    out = []
    for href, inner in _HREF.findall(cell or ""):
        t = _TIME.search(inner)
        out.append((t.group(1).strip() if t else _one(inner), _abs(href)))
    if not out:
        for t in _TIME.findall(cell or ""):
            out.append((t.strip(), None))
    return [(d, u) for d, u in out if d]


def _unquote(s):
    if s and len(s) >= 2 and s[0] == s[-1] == '"':
        s = s[1:-1].strip()
    return s or None


def _abs(href):
    href = htmllib.unescape(href or "").strip()
    return SITE + href if href.startswith("/") else href or None


def parse_list(payload):
    """JSON text of one datatables page -> (total, [row dict])."""
    d = json.loads(payload)
    rows = []
    for c in d.get("data", []):
        c = list(c) + [""] * (8 - len(c))
        m = _HREF.search(c[2] or "")
        url = _abs(m.group(1)) if m else None
        company = _one(m.group(2)) if m else _one(c[2])
        posted = _TIME.search(c[0] or "")
        issued = _TIME.search(c[1] or "")
        rows.append({
            "url": url,
            "company": company,
            "posted": posted.group(1).strip() if posted else _one(c[0]),
            "issued": issued.group(1).strip() if issued else _one(c[1]),
            "office": _unquote(_one(c[3])),            # FDA wraps a few office names in literal quotes
            "subject": _unquote(_one(c[4])),
            "response": _dates_links(c[5]),
            "closeout": _dates_links(c[6]),
        })
    return int(d.get("recordsTotal") or 0), rows


def is_device(row):
    return bool(DEVICE_OFFICE.search(row.get("office") or "") or DEVICE_SUBJECT.search(row.get("subject") or ""))


_DEGREE = re.compile(r"(?i),?\s*\b(M\.?\s?D|D\.?\s?O|Ph\.?\s?D|R\.?\s?N|D\.?D\.?S|D\.?P\.?M|Pharm\.?\s?D|M\.?B\.?B\.?S)\.?\s*$")
_ORG_WORD = re.compile(r"(?i)\b(inc|llc|ltd|corp|corporation|company|co|gmbh|plc|hospital|university|center|centre|"
                       r"clinic|institute|foundation|laborator(?:y|ies)|group|associates|health|medical|pharma)\b")


def _squash(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def is_individual(company, subject, recipient_names=()):
    """True when the letter is addressed to a person (e.g. a clinical investigator), whose name the
    listing shows in the Company Name column. Such names are never ingested."""
    if not company:
        return False
    if _DEGREE.search(company):
        return True
    if any(_squash(n) and _squash(n) == _squash(company) for n in recipient_names):
        return True
    return bool(re.search(r"(?i)clinical\s+investigator", subject or "")) and not _ORG_WORD.search(company)


def letter_key(url):
    s = (url or "").rstrip("/").rsplit("/", 1)[-1]
    m = _KEY.search(s)
    return f"{m.group(1)}-{m.group(2)}" if m else s


def _cfr_refs(text):
    """-> (sorted sections like '820.30', sorted parts like '820') cited as 21 CFR ..."""
    secs, parts = set(), set()
    for m in _CFR.finditer(text):
        for pm in _CFR_TOKEN.finditer(m.group(1)):
            part, sec = pm.group(1), pm.group(2)
            if sec:
                secs.add(f"{int(part)}.{sec}")
                parts.add(str(int(part)))
            elif int(part) >= 1:
                parts.add(str(int(part)))
    key = lambda s: tuple(int(x) for x in s.split("."))   # noqa: E731
    return sorted(secs, key=key), sorted(parts, key=int)


_FIRST_YEAR = 76     # the 510(k) and PMA programs began in 1976 (Medical Device Amendments)


def possible_number(num, this_year=None):
    """False only for a number FDA cannot have issued: a year part after the current year (and
    before 1976), or a PMA sequence of 1000 or more (FDA numbers PMAs P<yy><nnnn> per year and has
    never reached 100 in a year; firm document numbers such as "P017333; Rev. B" look like PMA
    numbers). A number that is merely missing from FDA data is kept; reference_fallback.py names it."""
    m = re.match(r"^(K|DEN|P)(\d{2})(\d{4})$", num or "")
    if not m:
        return False
    yy = int(m.group(2))
    cur = (this_year if this_year is not None else date.today().year) % 100
    if cur < yy < _FIRST_YEAR:
        return False
    return not (m.group(1) == "P" and int(m.group(3)) >= 1000)


_CMS_OLD = re.compile(r"\bCMS\s*(?:Case|Ref(?:erence)?)?\s*(?:#|No\.?|Number)?\s*:?\s*#?\s*(\d{4,8})\b")
_SALUTATION = re.compile(r"\bDear\s+(?:Dr|Mr|Mrs|Ms|Miss|Mx)\.?\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)\s*[:,]")


def parse_letter(page, archived=False):
    """Letter page HTML -> dict of the few fields we keep (never the body text).
    archived=True: an old-site page read from FDA's archive (see LETTERS BEFORE 2021)."""
    a = page.find('role="main"')
    main = page[a:] if a >= 0 else page
    e = main.find("Content current as of")
    if e < 0 and archived:
        e = main.find("Page Last Updated")
    if e > 0:
        main = main[:e]
    out = {"product": None, "cms": None, "address": {}, "fei": [], "k510": [], "pma": [],
           "product_codes": [], "cfr_sections": [], "cfr_parts": []}
    m = re.search(r"(?s)<dt[^>]*>\s*Product:\s*</dt>\s*<dd[^>]*>(.*?)</dd>", main)
    if m:
        out["product"] = _one(m.group(1))
    # firm address: the structured address spans of the RECIPIENT block (never the name/title fields)
    rb = re.search(r"(?s)<dt>\s*Recipient:\s*</dt>(.*?)</dl>", main)
    if rb:
        am = re.search(r'(?s)<p class="address"[^>]*>(.*?)</p>', rb.group(1))
        if am:
            for cls, key in (("address-line1", "line1"), ("address-line2", "line2"), ("locality", "city"),
                             ("administrative-area", "state"), ("postal-code", "postal"), ("country", "country")):
                v = re.search(rf'<span class="{cls}">(.*?)</span>', am.group(1), re.S)
                if v and _one(v.group(1)):
                    out["address"][key] = _one(v.group(1))
        # used ONLY to detect letters addressed to an individual; never written out
        out["_recipient_names"] = [_one(x) for x in re.findall(
            r'(?s)field--name-field-recipient-name.*?field--item">(.*?)</div>', rb.group(1)) if _one(x)]
    text = _cfweb.text(main) or ""
    flat = " ".join(text.split())
    c = _CMS.search(flat) or (_CMS_OLD.search(flat) if archived else None)
    out["cms"] = c.group(1) if c else None
    if archived:      # used ONLY to detect letters addressed to an individual; never written out
        out["_salutation"] = [m.group(1) for m in _SALUTATION.finditer(flat)][:3]
    fei = []
    for m in _FEI.finditer(flat):
        fei += [x for x in re.findall(r"\d{7,10}", m.group(1)) if x not in fei]
    out["fei"] = fei
    k510 = set(_K510.findall(flat))
    pma = {p for p, _ in _PMA.findall(flat)}
    out["k510"] = sorted(k for k in k510 if possible_number(k))
    out["pma"] = sorted(p for p in pma if possible_number(p))
    out["impossible_numbers"] = sorted((k510 | pma) - set(out["k510"]) - set(out["pma"]))
    pcs = set()
    for m in _PC.finditer(flat):
        for code in re.findall(r"\b[A-Z]{3}\b", m.group(1)):
            pc = common.product_code(code)
            if pc:
                pcs.add(pc)
    out["product_codes"] = sorted(pcs)
    out["cfr_sections"], out["cfr_parts"] = _cfr_refs(flat)
    return out


# ---------------------------------------------------------------------------
def list_url(start, length=PAGE_ROWS):
    q = {"draw": "1", "start": str(start), "length": str(length)}
    q.update(VIEW)
    return AJAX + "?" + urlencode(q)


def _page_name(url):
    return "letter_" + _cfweb.safe_name(url.rstrip("/").rsplit("/", 1)[-1])


def fetch(dest, delay=1.0, log=print, list_max_age_days=0.5, lists_only=False):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    lists, start, total, failed = [], 0, None, []
    while total is None or start < total:
        nm = f"list_s{start:05d}"
        try:
            body = f.get(list_url(start), nm, max_age_days=list_max_age_days)
            t, rows = parse_list(body)
        except (_cfweb.Unavailable, ValueError) as e:
            failed.append([nm, str(e)])
            break
        total = t if total is None else total
        lists.append(nm)
        if not rows:
            break
        start += PAGE_ROWS
    (dest / "lists.json").write_text(json.dumps({"total": total, "lists": lists}), encoding="utf-8")
    rows = _rows(dest)
    keep = [r for r in rows if is_device(r) and r["url"]]
    log(f"  warning_letters: {len(rows)} listed letters, {len(keep)} device letters")
    if lists_only:
        return None
    dfail = 0
    for i, r in enumerate(keep, 1):
        try:
            f.get(r["url"], _page_name(r["url"]))
        except _cfweb.Unavailable as e:
            dfail += 1
            failed.append([r["url"], str(e)])
        if i % 100 == 0:
            log(f"  warning_letters: {i}/{len(keep)} letter pages (fetched {f.fetched}, cached {f.cached})")
    (dest / "fetch_log.json").write_text(json.dumps({
        "date": base.today(), "listed_total": total, "list_pages": len(lists), "device_letters": len(keep),
        "fetched_now": f.fetched, "from_cache": f.cached, "detail_failed": dfail, "failed": failed}, indent=1),
        encoding="utf-8")
    log(f"  warning_letters: fetched {f.fetched}, cached {f.cached}, failed {len(failed)}")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    return m


def _rows(src):
    src = Path(src)
    meta = json.loads((src / "lists.json").read_text(encoding="utf-8"))
    seen, out = set(), []
    for nm in meta["lists"]:
        _, rows = parse_list((src / "pages" / f"{nm}.html").read_text(encoding="utf-8", errors="replace"))
        for r in rows:
            k = r["url"] or (r["company"], r["issued"])
            if k in seen:
                continue
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
#  Letters before 2021: FDA's old-site year listings in its Archive-It collection (fetch_archive)
# ---------------------------------------------------------------------------
OLD_LIST = SITE + "/ICECI/EnforcementActions/WarningLetters/{}/default.htm"
ARCHIVE_YEARS = range(1996, 2021)
_LONG = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
                   r"\s+(\d{1,2}),\s*(\d{4})\b")
_MON = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
        "november", "december"]
_ANY_A = re.compile(r"""(?is)<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>(.*?)</a>""")


def _us_date(text):
    """'December 29, 2005' / '12/29/2017' / '12/29/17' -> '12/29/2005' (None if neither)."""
    t = " ".join((text or "").split())
    m = _LONG.search(t)
    if m:
        return f"{_MON.index(m.group(1).lower()) + 1:02d}/{int(m.group(2)):02d}/{m.group(3)}"
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b", t)
    if m:
        y = int(m.group(3))
        if y < 100:                      # '3/02/98' (FOI index, 1996-2004) and '12/29/17' (ICECI pages)
            y += 1900 if y >= 70 else 2000
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{y}"
    return None


def _a_links(cell):
    return [(next(g for g in m.groups()[:3] if g is not None), m.group(4)) for m in _ANY_A.finditer(cell or "")]


def parse_archived_list(page):
    """Old-site year listing -> (rows like parse_list's, sorted page numbers linked as ?Page=N)."""
    rows = []
    for t in re.findall(r"(?is)<table\b.*?</table>", page):
        trs = re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", t)
        if not trs:
            continue
        hdr = [(_one(c) or "").lower() for c in re.findall(r"(?is)<th\b[^>]*>(.*?)</th>", trs[0])]
        if not any("issue date" in h for h in hdr) or not any("company" in h for h in hdr):
            continue
        col = {k: next((i for i, h in enumerate(hdr) if w in h), None) for k, w in
               (("issued", "issue date"), ("company", "company"), ("office", "issuing office"),
                ("subject", "subject"), ("closeout", "close"), ("response", "response"), ("posted", "posted"))}
        for tr in trs[1:]:
            cells = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", tr)
            if len(cells) < len(hdr):
                continue
            cc = cells[col["company"]]
            links = _a_links(cc)
            url = _archive.fda_url(links[0][0]) if links else None
            company = _one(re.sub(r"(?is)<span[^>]*>\s*\[ARCHIVED\]\s*</span>", "", links[0][1] if links else cc))

            def dated(i):
                if i is None:
                    return []
                out = []
                for h, inner in _a_links(cells[i]):
                    d = _us_date(_one(inner))
                    if d:
                        out.append((d, _archive.fda_url(h) if not h.startswith("#") else None))
                if not out and _us_date(_one(cells[i])):
                    out.append((_us_date(_one(cells[i])), None))
                return out
            rows.append({"url": url, "company": company,
                         "posted": _us_date(_one(cells[col["posted"]])) if col["posted"] is not None else None,
                         "issued": _us_date(_one(cells[col["issued"]])),
                         "office": _unquote(_one(cells[col["office"]])) if col["office"] is not None else None,
                         "subject": _unquote(_one(cells[col["subject"]])) if col["subject"] is not None else None,
                         "response": dated(col["response"]), "closeout": dated(col["closeout"])})
    return rows, sorted(pager_links(page, OLD_LIST.format(0)))


_PAGER = re.compile(r"""(?i)\bhref\s*=\s*["']((?:[^"'?\s]*/WarningLetters/\d{4}/[\w.-]+\.htm)?\?page=(\d+))["']""")


def pager_links(page, url):
    """Old-site year listing -> {page number: fda.gov URL of that page}, read from the listing's own pager.
    Most captures link <year>/default.htm?Page=N; some link the same listing under its content id
    (the 2013 listing captured in 2019 links 2013/ucm20035841.htm?Page=N, which Archive-It holds and
    default.htm?Page=N does not for that crawl). A relative "?Page=N" ("Next") is resolved against `url`
    and yields to an explicit link with the same number."""
    out = {}
    for h, n in _PAGER.findall(page or ""):
        u = url.split("?", 1)[0] + h if h.startswith("?") else _archive.fda_url(h)
        if u and (int(n) not in out or not h.startswith("?")):
            out[int(n)] = u
    return out


def _salutation_person(company, salutations):
    """True when a salutation surname ("Dear Dr. Smith:") appears in a Company Name with no organization word."""
    if not company or _ORG_WORD.search(company):
        return False
    words = {_squash(w) for w in re.split(r"[\s,.]+", company) if w}
    return any(_squash(s.split()[-1]) in words for s in salutations or [])


def fetch_archive(dest, delay=1.0, log=print, years=ARCHIVE_YEARS):
    """Read the old-site year listings and device letter pages from Archive-It 7993 -> archive.json."""
    dest = Path(dest)
    rd = _archive.Reader(dest / "archive", delay=delay, log=log)
    listings, rows, no_listing, failed, refused = [], [], [], [], None

    def save(stage):                     # written as the run goes, so a partial run can be transformed
        (dest / "archive.json.part").write_text(json.dumps({
            "date": base.today(), "stage": stage,
            "entry": "https://wayback.archive-it.org/7993/ (FDA.gov Web Archive, Archive-It collection 7993)",
            "listing_url": OLD_LIST, "listings": listings, "no_readable_listing": no_listing,
            "rows": rows, "failed": failed, "refused": refused, "host_refusals": rd.f.refusals}, indent=1),
            encoding="utf-8")
        os.replace(dest / "archive.json.part", dest / "archive.json")

    for y in years:
        url = OLD_LIST.format(y)
        try:
            caps = rd.ai.cdx(url.split("://", 1)[1], f"aicdx_list_{y}.txt",
                             fl="urlkey,timestamp,original,mimetype,statuscode,digest,length")
        except _archive.Missing as e:
            failed.append([url, str(e)])
            continue
        except _archive.Refused as e:
            refused = str(e)
            log(f"  warning_letters archive: STOPPED, {refused}")
            break
        ok = sorted(c["timestamp"] for c in caps if c.get("statuscode") == "200" and "html" in c.get("mimetype", ""))
        if not ok:
            no_listing.append([y, sorted({c.get("statuscode") for c in caps})])
            continue
        # newest capture of the year page that holds the listing table (a few later captures are
        # FDA's "page not available" body served with status 200)
        seen, got, pages_read, m1, r1, pages = set(), [], 0, None, [], []
        for ts in list(reversed(ok))[:4]:
            try:
                m1, p1 = rd.get(("ai", ts, url))
            except _archive.Missing as e:
                failed.append([url + " @" + ts, str(e)])
                continue
            except _archive.Refused as e:
                refused = str(e)
                break
            r1, pages = parse_archived_list(p1)
            if r1:
                break
        if refused:
            break
        if not r1:
            no_listing.append([y, ["200 without a listing table"]])
            continue
        todo, links = [(m1, r1)], pager_links(p1, url)
        for n in pages:
            if n == 1:
                continue
            try:
                mn, pn = rd.get(("ai", ts, links[n]))
                todo.append((mn, parse_archived_list(pn)[0]))
            except _archive.Missing as e:
                failed.append([links[n], str(e)])
            except _archive.Refused as e:
                refused = str(e)
                break
        for meta, rr in todo:
            pages_read += 1
            for r in rr:
                k = r["url"] or (r["company"], r["issued"])
                if k in seen:
                    continue
                seen.add(k)
                got.append(dict(r, year=y, listing=meta["archive_url"], ts=ts))
        dev = sum(is_device(r) for r in got)
        listings.append({"year": y, "ts": ts, "archive_url": m1["archive_url"], "pages": pages_read,
                         "rows": len(got), "device_rows": dev})
        log(f"  warning_letters archive: {y}: {len(got)} letters on {pages_read} pages, {dev} device ({m1['archive_url']})")
        rows += got
        if refused:
            save(f"listings through {y}")
            log(f"  warning_letters archive: STOPPED, {refused}")
            break
        # the year's device letter pages, from the same archive (nearest capture)
        dev_rows = [r for r in got if is_device(r) and r["url"]]
        for i, r in enumerate(dev_rows, 1):
            try:
                r["page"] = rd.get(("ai", r["ts"], r["url"]))[0]
            except _archive.Missing as e:
                failed.append([r["url"], str(e)])
            except _archive.Refused as e:             # the host kept refusing after long pauses: stop
                refused = str(e)
                break
            if i % 25 == 0:
                log(f"  warning_letters archive: {y}: {i}/{len(dev_rows)} letter pages "
                    f"(fetched {rd.f.fetched}, cached {rd.f.cached})")
                save(f"{y}: letter pages {i}/{len(dev_rows)}")
        save(f"through {y}")
        if refused:
            log(f"  warning_letters archive: STOPPED, {refused}")
            break
    keep = [r for r in rows if is_device(r) and r["url"]]
    save("stopped: " + refused if refused else "complete")
    log(f"  warning_letters archive: {len(rows)} listed letters ({len(keep)} device) in {len(listings)} years; "
        f"no readable listing for {[y for y, _ in no_listing]}; fetched {rd.f.fetched}, cached {rd.f.cached}, "
        f"failed {len(failed)}")
    return len(keep)


# ---------------------------------------------------------------------------
#  Letters of May 2019 - December 2020: new-site letter pages in Archive-It 7993 (fetch_archive_newsite)
# ---------------------------------------------------------------------------
NEW_SITE_LETTERS = SITE + "/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/"
NEWSITE_FROM, NEWSITE_TO = "2019-01-01", "2020-12-31"     # ISO dates of the letter date in the URL
_TITLE = re.compile(r"(?is)<title>(.*?)</title>")
_TITLE_KEY = re.compile(r"^(.*?)\s+-\s+\d{4,8}\s+-\s+\d{1,2}/\d{1,2}/\d{4}$")


def key_date(url):
    """'...-609781-09012020' -> '2020-09-01' (None if the URL has no CMS-date key)."""
    m = _KEY.search((url or "").rstrip("/"))
    if not m:
        return None
    d = m.group(2)
    return f"{d[4:]}-{d[:2]}-{d[2:4]}"


def parse_newsite_page(page):
    """Archived new-site letter page -> {company, office, product}: header fields only. The company comes
    from the page title; the recipient's name and title are never read here."""
    out = {"company": None, "office": None, "product": None}
    t = _TITLE.search(page or "")
    if t:
        title = re.sub(r"\s*\|\s*FDA\s*$", "", _one(t.group(1)) or "")
        m = _TITLE_KEY.match(title)
        out["company"] = m.group(1).strip() if m else None
    m = re.search(r"(?s)<dt[^>]*>\s*Issuing Office:\s*</dt>\s*<dd[^>]*>(.*?)</dd>", page or "")
    if m:
        out["office"] = _unquote(_one(m.group(1)))
    m = re.search(r"(?s)<dt[^>]*>\s*Product:\s*</dt>\s*<dd[^>]*>(.*?)</dd>", page or "")
    if m:
        out["product"] = _one(m.group(1))
    return out


def _iso_us(d):
    return f"{d[5:7]}/{d[8:10]}/{d[:4]}"


def fetch_archive_newsite(dest, delay=1.0, log=print, date_from=NEWSITE_FROM, date_to=NEWSITE_TO):
    """Letter pages of the new fda.gov site, letter dates date_from..date_to, from Archive-It 7993
    -> archive_newsite.json (rows like archive.json's, each with the parsed page metadata)."""
    dest = Path(dest)
    rd = _archive.Reader(dest / "archive", delay=delay, log=log)
    caps = rd.ai.cdx(NEW_SITE_LETTERS.split("://", 1)[1], "aicdx_newsite_letters.txt", matchType="prefix",
                     fl="timestamp,original,statuscode,mimetype", filter="statuscode:200")
    best = {}
    for c in caps:
        u = _archive.fda_url(c.get("original"))
        if not u or "?" in u or "html" not in (c.get("mimetype") or "html"):
            continue
        u = u.rstrip("/")
        d = key_date(u)
        if d and date_from <= d <= date_to and c.get("timestamp", "") > best.get(u, ""):
            best[u] = c["timestamp"]
    rows, failed, refused = [], [], None
    live = _cfweb.Fetcher(dest, delay=delay)

    def save(stage):
        (dest / "archive_newsite.json.part").write_text(json.dumps({
            "date": base.today(), "stage": stage,
            "entry": "https://wayback.archive-it.org/7993/ (FDA.gov Web Archive, Archive-It collection 7993)",
            "cdx_prefix": NEW_SITE_LETTERS, "date_from": date_from, "date_to": date_to,
            "letter_urls": len(best), "rows": rows, "failed": failed, "refused": refused,
            "host_refusals": rd.f.refusals}, indent=1), encoding="utf-8")
        os.replace(dest / "archive_newsite.json.part", dest / "archive_newsite.json")

    log(f"  warning_letters newsite: {len(caps)} captures, {len(best)} letter URLs dated {date_from}..{date_to}")
    for i, u in enumerate(sorted(best), 1):
        ts, meta = best[u], None
        try:                                     # fda.gov still serves most of these pages
            body = live.get(u, _page_name(u))
        except _cfweb.Unavailable as e:
            body = None
            failed.append([u, "fda.gov: " + str(e)])
        if body is None or not parse_newsite_page(body)["office"]:
            try:
                meta, body = rd.get(("ai", ts, u))
            except _archive.Missing as e:
                failed.append([u, str(e)])
                continue
            except _archive.Refused as e:
                refused = str(e)
                break
        p = parse_newsite_page(body)
        d = key_date(u)
        r = {"url": u, "company": p["company"], "posted": None, "issued": _iso_us(d), "office": p["office"],
             "subject": None, "product": p["product"], "response": [], "closeout": [], "year": int(d[:4]),
             "listing": _archive.view_url(("ai", ts, LIST_PAGE)), "ts": ts, "page": meta, "via": "newsite",
             "live": meta is None}
        r["device"] = is_device({"office": r["office"], "subject": r["product"]})
        rows.append(r)
        if i % 50 == 0:
            log(f"  warning_letters newsite: {i}/{len(best)} pages (fetched {rd.f.fetched}, cached {rd.f.cached}, "
                f"device {sum(x['device'] for x in rows)})")
            save(f"pages {i}/{len(best)}")
    save("stopped: " + refused if refused else "complete")
    log(f"  warning_letters newsite: {len(rows)} pages read ({sum(x['live'] for x in rows)} from fda.gov), "
        f"{sum(x['device'] for x in rows)} device letters, "
        f"failed {len(failed)}" + (f", STOPPED: {refused}" if refused else ""))
    return sum(x["device"] for x in rows)


# ---------------------------------------------------------------------------
#  Letters before 2005: FDA's FOI reading-room month index on web.archive.org (fetch_archive_foi)
# ---------------------------------------------------------------------------
FOI_INDEX_PREFIX = "www.accessdata.fda.gov/scripts/wlcfm/"
FOI_FIRST, FOI_LAST = (1996, 11), (2004, 12)
_FOI_MONTH = re.compile(r"(?i)/wlcfm/(date|date_archive)\.cfm\?MONTH=(\d{1,2})&YEAR=(\d{4})$")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]
_LAST_FIRST = re.compile(r"^[A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?,\s+[A-Z][A-Za-z'.\-]*(?:\s+[A-Z][A-Za-z'.\-]*)*"
                         r"(?:,?\s*(?:Jr|Sr|II|III)\.?)?$")
_ORG_EXTRA = re.compile(r"(?i)\b(incorporat\w*|s\.\s?a\.?|s\.?p\.?a\.?|s\.?r\.?l\.?|n\.v\.?|b\.v\.?|a\.g\.?|limited|"
                        r"ltda|international|industries|enterprises|products|systems|services|imaging|radiology|"
                        r"technolog\w*|instruments?|dental|optical|labs?|x-ray|mammography)\b")
_DEGREE_ANY = re.compile(r"(?:^Dr\.?\s|[,\s]\s*(?:M\.?\s?D|D\.?\s?O|Ph\.?\s?D|D\.?D\.?S|D\.?P\.?M)\.?(?=\s*(?:,|&|\)|$)))")


def last_first_person(company):
    """An old FOI index row that names a person: 'Roe, John' / 'Roe, John A.' with no organization word,
    or any name carrying a physician's degree ('Kias, MD., PC.', 'Lucas & Bernstein, M.D., P.A.'): the
    reading room listed letters to physicians' practices under the physician's name."""
    c = (company or "").strip()
    if not c:
        return False
    if _DEGREE_ANY.search(c):
        return True
    return bool(_LAST_FIRST.match(c) and not _ORG_WORD.search(c) and not _ORG_EXTRA.search(c))


def parse_foi_list(page):
    """FOI reading-room month index (wlcfm/date.cfm, date_archive.cfm) -> rows like parse_archived_list's
    (plus 'pdf' / 'htm', the letter files named in the File column). Columns: Company Name | Date Issued
    (later 'Date Warning Letter Issued') | Issuing Office | Subject | File [| Response Posted]; the 1990s
    pages leave <TH> unclosed."""
    hdr = [(_one(h) or "").lower() for h in
           re.findall(r"(?is)<th\b[^>]*>(.*?)(?=<th\b|</th>|<tr\b|</tr>|</thead>|<td\b)", page or "")]
    col = {k: next((i for i, h in enumerate(hdr) if w in h), None) for k, w in
           (("company", "company"), ("issued", "issued"), ("office", "issuing office"), ("subject", "subject"),
            ("file", "file"))}
    if col["company"] is None or col["issued"] is None:
        return []
    rows = []
    for tr in re.findall(r"(?is)<tr\b[^>]*>(.*?)(?=<tr\b|</table>)", page or ""):
        cells = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", tr)
        if len(cells) < 5:
            continue
        files = [_archive.fda_url(h) for h, _ in _a_links(cells[col["file"]])] if col["file"] is not None else []
        files = [f for f in files if f]
        htm = next((f for f in files if re.search(r"(?i)\.html?$", f)), None)
        pdf = next((f for f in files if re.search(r"(?i)\.pdf$", f)), None)
        rows.append({"url": htm or pdf or (files[0] if files else None), "htm": htm, "pdf": pdf,
                     "company": _one(cells[col["company"]]), "posted": None,
                     "issued": _us_date(_one(cells[col["issued"]])),
                     "office": _unquote(_one(cells[col["office"]])) if col["office"] is not None else None,
                     "subject": _unquote(_one(cells[col["subject"]])) if col["subject"] is not None else None,
                     "response": [], "closeout": []})
    return rows


def _stem(url):
    return re.sub(r"(?i)\.(pdf|html?)$", "", (url or "").rsplit("/", 1)[-1]).lower()


def fetch_archive_foi(dest, delay=1.0, log=print, first=FOI_FIRST, last=FOI_LAST):
    """Month index pages (first..last) and the device letters' HTML pages from web.archive.org -> archive_foi.json."""
    dest = Path(dest)
    rd = _archive.Reader(dest / "archive", delay=delay, log=log)
    caps = rd.wb.cdx(FOI_INDEX_PREFIX, "wbcdx_wlcfm_len.json", matchType="prefix",
                     fl="timestamp,original,statuscode,mimetype,length", filter="statuscode:200")
    files = rd.wb.cdx("www.fda.gov/foi/warning_letters/", "wbcdx_foi_warning_letters.json", matchType="prefix",
                      fl="timestamp,original,statuscode", filter="statuscode:200")
    file_ts = {}
    for c in files:
        u = _archive.fda_url(c.get("original"))
        if u:
            file_ts.setdefault(u.lower(), []).append(c["timestamp"])
    months = {}
    for c in caps:
        m = _FOI_MONTH.search(htmllib.unescape(c.get("original") or ""))
        if not m:
            continue
        ym = (int(m.group(3)), int(m.group(2)))
        if first <= ym <= last:
            months.setdefault((ym, m.group(1).lower()), {})[c["timestamp"]] = (c["original"], int(c.get("length") or 0))
    listings, rows, failed, refused, seen = [], [], [], None, {}

    def save(stage):
        (dest / "archive_foi.json.part").write_text(json.dumps({
            "date": base.today(), "stage": stage,
            "entry": "https://web.archive.org/web/*/http://www.fda.gov (linked from "
                     "https://www.fda.gov/about-fda/about-website/fdagov-archive)",
            "index": "http://" + FOI_INDEX_PREFIX + "date.cfm?MONTH=m&YEAR=y (and date_archive.cfm)",
            "first": list(first), "last": list(last), "listings": listings, "rows": rows,
            "failed": failed, "refused": refused, "host_refusals": rd.f.refusals}, indent=1), encoding="utf-8")
        os.replace(dest / "archive_foi.json.part", dest / "archive_foi.json")

    for (ym, kind) in sorted(months):
        caps_m = months[(ym, kind)]
        got = None
        # many later captures of a month page are FDA's short "no letters" page (letters moved to the
        # archive index), so the largest capture (CDX record length), then the newest, is read first
        for ts in sorted(caps_m, key=lambda x: (caps_m[x][1], x), reverse=True)[:3]:
            orig = re.sub(r"(?i)^https?://www\.accessdata\.fda\.gov(?::80)?/", "http://www.accessdata.fda.gov/",
                          htmllib.unescape(caps_m[ts][0]))
            try:
                meta, body = rd.get(("wb", ts, orig))
            except _archive.Missing as e:
                failed.append([orig + " @" + ts, str(e)])
                continue
            except _archive.Refused as e:
                refused = str(e)
                break
            rr = parse_foi_list(body)
            if rr:
                got = (meta, rr)
                break
        if refused:
            break
        if not got:
            listings.append({"year": ym[0], "month": ym[1], "index": kind, "rows": 0})
            continue
        meta, rr = got
        new = 0
        for r in rr:
            k = _stem(r["url"]) or (r["company"], r["issued"])
            if k in seen:
                if r.get("htm") and not seen[k].get("htm"):           # keep the HTML file when one listing has it
                    seen[k].update(url=r["htm"], htm=r["htm"])
                continue
            r.update(year=ym[0], month=ym[1], listing=meta["archive_url"], listing_ts=meta["capture_ts"],
                     ts=meta["capture_ts"], via="foi")
            seen[k] = r
            rows.append(r)
            new += 1
        listings.append({"year": ym[0], "month": ym[1], "index": kind, "ts": meta["capture_ts"],
                         "archive_url": meta["archive_url"], "rows": len(rr), "new_rows": new})
    for r in rows:
        r["device"] = is_device(r)
    log(f"  warning_letters foi: {len(listings)} month pages, {len(rows)} letters, "
        f"{sum(r['device'] for r in rows)} device")
    save("listings")
    dev = [r for r in rows if r["device"] and r["url"]]
    for i, r in enumerate(dev, 1):
        for f in (r.get("pdf"), r.get("htm")):
            if f and file_ts.get(f.lower()):
                r.setdefault("file_ts", {})[f] = max(file_ts[f.lower()])
        if refused or not r.get("htm"):
            continue
        try:
            r["page"] = rd.get(("wb", r["listing_ts"], r["htm"]))[0]
        except _archive.Missing as e:
            failed.append([r["htm"], str(e)])
        except _archive.Refused as e:
            refused = str(e)
        if i % 50 == 0:
            log(f"  warning_letters foi: {i}/{len(dev)} device letters (fetched {rd.f.fetched}, cached {rd.f.cached})")
            save(f"letter pages {i}/{len(dev)}")
    save("stopped: " + refused if refused else "complete")
    log(f"  warning_letters foi: {len(dev)} device letters, {sum(1 for r in dev if r.get('page'))} HTML pages read, "
        f"failed {len(failed)}" + (f", STOPPED: {refused}" if refused else ""))
    return len(dev)


ARCHIVE_FILES = (("archive.json", "oldsite"), ("archive_newsite.json", "newsite"), ("archive_foi.json", "foi"))


def _archived_rows(src):
    """Device rows of the archive runs (old-site listings, new-site pages, FOI index), each tagged with
    'via' and its run date '_date'. Returns (rows, date of the old-site run)."""
    out, first = [], None
    for fn, via in ARCHIVE_FILES:
        p = Path(src) / fn
        if not p.exists():
            continue
        a = json.loads(p.read_text(encoding="utf-8"))
        if a.get("stage", "complete") != "complete":
            print(f"  warning_letters: {fn} is partial ({a['stage']})")
        if via == "oldsite":
            first = a.get("date")
        for r in a["rows"]:
            if not r.get("url") or not (is_device(r) if via == "oldsite" else r.get("device")):
                continue
            out.append(dict(r, via=r.get("via", via), _date=a.get("date")))
    return out, first


def transform(src, out, retrieved_at=None, only_via=None):
    """only_via=("newsite", "foi") writes only the letters those archive runs add (after the same duplicate
    checks against the current index and the old-site listings): warning_letters_archive.ttl."""
    src, out = Path(src), Path(out)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    rows = [r for r in _rows(src) if is_device(r) and r["url"]]
    arows, arch_date = _archived_rows(src)
    current = {letter_key(r["url"]) for r in rows}
    st = base.Stats(source=NAME)
    firms, codes, counts = {}, set(), {"with_page": 0, "with_fei": 0, "with_pc": 0, "with_sub": 0, "with_cfr": 0}
    # same letter -> one node: a letter already present is not written again. Same key (MARCS-CMS number +
    # date, or file name) for every run; a new-site page also when a listed letter has the same issue date
    # and company (the 2019 old-site listing names those letters by ucm id). The CMS number alone is not a
    # letter id: FDA reuses a case number for later letters of the same case (Spectranetics 2016 and 2019).
    # FOI-index rows with the same company and date but different files stay separate (FDA posted them as
    # separate letters, e.g. one per facility or per program).
    have_key = set(current)
    have_dc = {(_us_date(r["issued"]), _squash(r["company"])) for r in rows}
    for r in arows:
        r["archived"] = not r.get("live")              # a new-site page read from fda.gov itself is not
        via, k = r.get("via", "oldsite"), letter_key(r["url"])
        if via == "oldsite":
            if k in current:
                continue
        else:
            if k in have_key or (via == "newsite" and (_us_date(r["issued"]), _squash(r["company"])) in have_dc):
                counts[f"{via}_duplicates_skipped"] = counts.get(f"{via}_duplicates_skipped", 0) + 1
                continue
        have_key.add(k)
        have_dc.add((_us_date(r["issued"]), _squash(r["company"])))
        counts[f"{via}_letters"] = counts.get(f"{via}_letters", 0) + 1
        rows.append(r)
    if only_via:
        rows = [r for r in rows if r.get("via") in only_via]
    keys = {}
    with base.TurtleWriter(out) as w:
        for r in sorted(rows, key=lambda r: letter_key(r["url"])):
            key = letter_key(r["url"])
            if key in keys:                                  # two URLs with the same CMS+date: keep full slug
                key = r["url"].rstrip("/").rsplit("/", 1)[-1]
            keys[key] = r["url"]
            arch = r.get("archived")
            pm = r.get("page") if arch else None             # archive page metadata (fetch_archive)
            if arch:
                p = src / "archive" / "pages" / f"{pm['page']}.html" if pm else None
                d = parse_letter(p.read_text(encoding="utf-8", errors="replace"), archived=True) \
                    if p is not None and p.exists() else None
                counts["archive_letters"] = counts.get("archive_letters", 0) + 1
                counts["archive_with_page"] = counts.get("archive_with_page", 0) + (d is not None)
            else:
                p = src / "pages" / f"{_page_name(r['url'])}.html"
                d = parse_letter(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else None
            if d is None:
                st.skipped += 1                              # metadata still written below
            else:
                counts["with_page"] += 1
            subj = iri.warning_letter(key)
            # a letter to a person (clinical investigator, sole individual): no name, address, firm link,
            # FEI or URL (FDA's URLs embed the name); the record keeps dates, office, subject and citations
            person = is_individual(r["company"], r["subject"], (d or {}).get("_recipient_names", ())) or \
                (bool(arch) and _salutation_person(r["company"], (d or {}).get("_salutation"))) or \
                (r.get("via") == "foi" and last_first_person(r["company"]))
            counts["individual_withheld"] = counts.get("individual_withheld", 0) + person
            name = None if person else r["company"]
            via = r.get("via", "oldsite") if arch else None
            if arch:                                         # read from FDA's archive: point at the archive copy
                if pm:
                    au = pm["archive_url"]
                elif via == "foi":                           # a PDF-only letter: its own capture, if any
                    fts = (r.get("file_ts") or {}).get(r["url"])
                    au = _archive.view_url(("wb", fts, r["url"])) if fts else None
                else:
                    au = _archive.view_url(("ai", r["ts"], r["url"]))
                r = dict(r, original=r["url"], url=au)
            pairs = [("a", "ex:WarningLetter"),
                     ("rdfs:label", common.label(f"Warning letter to {name}" if name else "Warning letter",
                                                 r["issued"], sep=", ")),
                     ("ex:companyName", common.lit(common.norm_text(name))),
                     ("ex:postedDate", common.date_lit(r["posted"])),
                     ("ex:letterIssueDate", common.date_lit(r["issued"])),
                     ("ex:issuingOffice", common.lit(r["office"])),
                     ("ex:letterSubject", common.lit(r["subject"])),
                     ("ex:url", None if person else common.lit(r["url"]))]
            for dt, u in r["response"]:
                pairs += [("ex:responseLetterDate", common.date_lit(dt)),
                          ("ex:responseLetterUrl", None if person else common.lit(u))]
            for dt, u in r["closeout"]:
                pairs += [("ex:closeoutLetterDate", common.date_lit(dt)),
                          ("ex:closeoutLetterUrl", None if person else common.lit(u))]
            if person:
                pairs.append(("ex:dataQualityNote", common.lit(
                    "letter addressed to an individual; the name, address and letter URL are not ingested")))
            if r.get("live"):
                pairs.append(("ex:dataQualityNote", common.lit(
                    "not in FDA's current warning-letter listing (which starts in 2021); found through the index of "
                    "FDA's web archive (Archive-It collection 7993) and read from fda.gov")))
            if arch:
                pairs.append(("ex:archivedFrom", None if person else common.lit(r["original"])))
                if via == "newsite":
                    where = ("; found through the collection's index of fda.gov warning-letter pages (FDA's "
                             "2019-2020 listing was a JavaScript table that the archive did not keep)")
                elif via == "foi":
                    where = (f"; listed on FDA's FOI reading-room warning-letter index for "
                             f"{_MONTHS[r['month'] - 1]} {r['year']} (web.archive.org copy, captured "
                             f"{r['listing_ts'][:4]}-{r['listing_ts'][4:6]}-{r['listing_ts'][6:8]})")
                else:
                    where = "; listed on FDA's archived " + str(r["year"]) + " warning letter page"
                if d:
                    note = _archive.note(pm.get("kind", "ai") if pm else "ai", pm["capture_ts"] if pm else None, where)
                elif via == "foi":
                    note = (where[2:] + "; the letter was posted only as a PDF, which was not read" if not r.get("htm")
                            else where[2:] + "; the letter page could not be read from the archive")
                else:
                    note = ("listed on FDA's archived " + str(r["year"]) + " warning letter page (Archive-It "
                            "collection 7993); the letter page could not be read from the archive")
                pairs.append(("ex:dataQualityNote", common.lit(note)))
            m = _KEY.search((r.get("original") or r["url"]).rstrip("/"))
            cms = (d or {}).get("cms") or (m.group(1) if m else None)
            pairs.append(("ex:marcsCmsNumber", common.lit(cms)))
            fei = []
            if d:
                pairs.append(("ex:letterProductType", common.lit(d["product"])))
                a = {} if person else d["address"]
                pairs += [("ex:address", common.lit(common.norm_address(a.get("line1"), a.get("line2")))),
                          ("ex:city", common.lit(a.get("city"))), ("ex:state", common.lit(a.get("state"))),
                          ("ex:postalCode", common.lit(a.get("postal"))), ("ex:country", common.lit(a.get("country")))]
                fei = [] if person else d["fei"]
                for x in fei:
                    pairs += [("ex:feiNumber", common.lit(x)), ("ex:hasManufacturer", f"<{iri.manufacturer(fei=x)}>")]
                for k in d["k510"]:
                    pairs += [("ex:kNumber", common.lit(k)), ("ex:mentionsK510Record", f"<{iri.k510(k)}>")]
                for pnum in d["pma"]:
                    pairs += [("ex:pmaNumber", common.lit(pnum)), ("ex:mentionsPMARecord", f"<{iri.pma(pnum)}>")]
                for pc in d["product_codes"]:
                    pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
                    codes.add(pc)
                for s in d["cfr_sections"]:
                    pairs.append(("ex:citesCfrSection", f"<{iri.cfr_section(s)}>"))
                for pt in d["cfr_parts"]:
                    pairs.append(("ex:citesCfrPart", f"<{iri.cfr_part(pt)}>"))
                counts["with_fei"] += bool(fei)
                counts["with_pc"] += bool(d["product_codes"])
                counts["with_sub"] += bool(d["k510"] or d["pma"])
                # numbers FDA cannot have issued (firm document numbers shaped like a PMA number): not linked
                counts["impossible_numbers_dropped"] = (counts.get("impossible_numbers_dropped", 0)
                                                        + len(d["impossible_numbers"]))
                counts["with_cfr"] += bool(d["cfr_sections"] or d["cfr_parts"])
            if not fei and name:
                nf = normalize_org(name)
                if nf:
                    pairs.append(("ex:hasRecipientFirm", f"<{iri.firm_name(nf)}>"))
                    firms.setdefault(nf, name)
            pairs += common.provenance(DATASET, key,
                                       source_url=(r["listing"] if arch else LIST_PAGE) if person or not r["url"]
                                       else r["url"],
                                       retrieved_at=(r.get("_date") or arch_date or retrieved_at) if arch
                                       else (r.get("_date") or retrieved_at))
            w.block(subj, pairs)
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    (out.with_suffix(".counts.json")).write_text(json.dumps(dict(counts, records=st.records), indent=1), encoding="utf-8")
    print(f"  warning_letters: {st.records} device letters; {counts}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim samples fetched 2026-10-28: two rows of the datatables JSON (one CDRH letter with a closeout
# letter, one food letter that the device filter must drop) and a shortened letter page (header blocks
# verbatim; body cut to a few sentences, an FEI sentence added from the same page family).
_WL = "/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/"
_LIST = json.dumps({"draw": 1, "recordsTotal": 3779, "recordsFiltered": 3779, "data": [
    ['<time datetime="2024-07-18T18:00:00Z">07/18/2024</time>\n',
     '<time datetime="2024-07-18T04:00:00Z">07/18/2024</time>\n',
     '<a href="' + _WL + 'jiangsu-caina-medical-co-ltd-687033-07182024">Jiangsu Caina Medical Co., Ltd</a>',
     "Center for Devices and Radiological Health", "CGMP/QSR/Medical Devices/Adulterated", "",
     '<a href="' + _WL + 'jiangsu-caina-medical-co-ltd-687033-08242026"><time datetime="2026-08-31T04:00:00Z">'
     '08/31/2026</time>\n</a>', ""],
    ['<time datetime="2024-07-16T13:20:00Z">07/16/2024</time>\n',
     '<time datetime="2024-06-03T04:00:00Z">06/03/2024</time>\n',
     '<a href="' + _WL + 'dalian-lingfa-food-co-ltd-681016-06032024">Dalian Lingfa Food Co., Ltd.</a>',
     "Center for Food Safety and Applied Nutrition (CFSAN)",
     "CGMP/Food/Prepared, Packed or Held Under Insanitary Conditions/Adulterated", "",
     '<a href="' + _WL + 'dalian-lingfa-food-co-ltd-681016-09232024"><time datetime="2024-09-27T04:00:00Z">'
     '09/27/2024</time>\n</a>', ""],
    ['<time datetime="2026-10-27T04:00:00Z">10/27/2026</time>\n',
     '<time datetime="2026-08-26T04:00:00Z">08/26/2026</time>\n',
     '<a href="' + _WL + 'peter-schiff-enterprises-727888-08262026">Peter Schiff Enterprises</a>',
     "Center for Devices and Radiological Health",
     "Medical Device/Adulterated/Misbranded/Lacks PMA and/or 510(k)<br />\r\n", "", "", ""],
    # fictitious row in FDA's format: a letter to a clinical investigator (a person)
    ['<time datetime="2025-05-06T04:00:00Z">05/06/2025</time>\n',
     '<time datetime="2025-04-01T04:00:00Z">04/01/2025</time>\n',
     '<a href="' + _WL + 'jane-doe-md-700001-04012025">Jane Doe, M.D.</a>',
     "Center for Devices and Radiological Health",
     "Investigational Device Exemptions (Clinical Investigator)", "", "", ""]]})
_LETTER = '''<div class="col-md-8" role="main">
<div class="inset-column">
  <dl class="lcds-description-list--grid">
              <dt class="cell-1_1">Delivery Method:</dt>
        <dd class="cell-2_1">VIA Electronic Mail
            </dd>
              <dt class="cell-1_3">Product:</dt>
        <dd class="cell-2_3">Medical Devices
            </dd>
          </dl>
  </div>
<hr>
<div class="row inset-column">
  <div class="col-xs-12 col-md-6">
    <dl class="">
        <dt>Recipient:</dt>
                      <dd>
  <div class="field field--name-field-recipient-name field--type-string field--label-above">
    <div class="field--label">Recipient Name</div>
              <div class="field--item">Peter Schiff</div>
          </div>
</dd>
                                <dd>
  <div class="field field--name-field-recipient-title field--type-string field--label-above">
    <div class="field--label">Recipient Title</div>
              <div class="field--item">Owner</div>
          </div>
</dd>
            <dd>Peter Schiff Enterprises</dd>
                      <dd><p class="address" translate="no"><span class="address-line1">4400 Log Cabin Ln.</span><br>
<span class="locality">Cookeville</span>, <span class="administrative-area">TN</span> <span class="postal-code">38506</span><br>
<span class="country">United States</span></p></dd>
    <dd><span class="fa fa-envelope" aria-hidden="true"></span><a href="mailto:(b)(6), (b)(7)(C)"> (b)(6), (b)(7)(C)</a></dd>
</dd>
    </dl>
  </div>
       <div class="col-xs-12 col-md-6">
    <dl class="">
          <dt>Issuing Office:</dt>
          <dd>Center for Devices and Radiological Health</dd>
          <dd><p class="address" translate="no"><span class="country">United States</span></p></dd>
    </dl>
  </div>
</div>
<hr>
<p class="text-align-center"><strong>WARNING LETTER</strong><br>CMS # 727888</p><p>August 26, 2026</p><p>Dear Mr. Schiff,</p>
<p>During an inspection of your firm (FEI 3003456789) located in Cookeville, TN ...</p>
<p>The devices are also misbranded under section 502(o) of the Act, 21 U.S.C. &sect; 352(o), because your firm did not timely notify the agency of its intent to begin the introduction or delivery for introduction into interstate commerce for commercial distribution of the devices, as required by section 510(k) of the Act, 21 U.S.C. &sect; 360(k), and 21 CFR 807.81(a).</p>
<p>Your firm asserts that the Model 310C Fibrillator is an unclassified pre-amendments device and you have listed it under product code LIW. However ... See 21 CFR 807.81(a)(3).</p>
<p>... in accordance with the Quality Management System Regulation (QMSR) (21 CFR part 820) to ensure adequate performance.</p>
<p>2. Failure to establish and maintain procedures to control the design of the device ..., as required by 21 CFR 820.30(a). In particular, 21 CFR 830.300(a) and 830.320(b), both of which were promulgated ... cleared under K123456 and approved under P990010/S012.</p>
<p>Your firm&rsquo;s response should be sent via email to Melissa Michurski, Establishment Assessment Team 2, Assistant Director at CDRHEnforcement@fda.hhs.gov. If you have any questions about the contents of this letter, please contact: Ashley Mutawakkil, Compliance Officer, Ashley.Mutawakkil@fda.hhs.gov.</p>
<p>Sincerely,<br>/S/<br>Jane Signer<br>Director</p>
Content current as of: 10/27/2026'''


# Verbatim fragments read 2026-11-03 from Archive-It collection 7993: the 2016 year listing (header,
# one device row, one food row, the pager), a 2005 row marked [ARCHIVED], and the Nomax letter page
# shortened to its header block and two sentences (names as published, to prove they are not stored).
# The last listing row (a person) and its letter page are constructed in the same format.
_A_LIST = '''<table width="100%" cellpadding="3" border="1" id='WarningLetter_sortid' class='tablesorter' summary="Layout showing WarningLetter table with 5 columns: Letter Issue Date, Company Name, Issuing Office, Subject, Close Out Date" style="margin-bottom:28px"> <thead> <tr> <th scope="col" data-type='numeric' data-toggle='true'> Letter Issue Date </th> <th scope="col" > Company Name </th> <th scope="col" data-hide='phone'> Issuing Office </th> <th scope="col" data-hide='phone'> Subject </th> <th scope="col" data-type='numeric' data-hide='phone,tablet'> Close Out Date </th> </tr> </thead> <tbody>
<tr>
		 <td>December 15, 2016 </td>
		 <td style="text-decoration: underline;"><a href="/ICECI/EnforcementActions/WarningLetters/2016/ucm534145.htm">Nomax Inc</a></td>
		 <td> Kansas City District Office</td>
		 <td> Medical Devices/Adulterated/Quality System Regulation (QSR)</td>
		 <td nowrap>
				<a href="#wldisclaimer">Not Issued *</a>
		</td>
  </tr>
<tr> <td>December 22, 2016 </td> <td style="text-decoration: underline;"><a href="/ICECI/EnforcementActions/WarningLetters/2016/ucm534556.htm">Tofu Yu, LLC</a></td> <td> San Francisco District Office</td> <td> CGMP/Food/Prepared, Packed or Held Under Insanitary Conditions/Adulterated</td> <td nowrap> <a href="#wldisclaimer">Not Issued *</a> </td> </tr>
<tr>
		 <td>December 29, 2005 </td>
		 <td style="text-decoration: underline;"><a href="/ICECI/EnforcementActions/WarningLetters/2005/ucm075752.htm">Edgar Martin Dairy					<span style="margin-left:4px;">[ARCHIVED]</span>
</a></td>
		 <td> Minneapolis District Office</td>
		 <td> Illegal Drug Residue /Adulterated</td>
		 <td nowrap>
				<a href="#wldisclaimer">N/A *</a>
		</td>
  </tr>
<tr> <td>03/14/2017 </td> <td style="text-decoration: underline;"><a href="/ICECI/EnforcementActions/WarningLetters/2017/ucm999001.htm">John Roe</a></td> <td> Center for Devices and Radiological Health</td> <td> Medical Devices/Adulterated</td> <td nowrap> <a href="/ICECI/EnforcementActions/WarningLetters/2017/ucm999002.htm">06/01/2017</a> </td> </tr>
</tbody></table>
<ul class="pagination-clean" > <li class="previous-off">Previous</li> <li class="active">1</li> <li><a href="/ICECI/EnforcementActions/WarningLetters/2016/default.htm?Page=2">2</a></li> <li><a href="/ICECI/EnforcementActions/WarningLetters/2016/default.htm?Page=31">31</a></li></ul>'''
_A_LETTER = '''<div role="main"><p>Kansas City District Office 8050 Marshall Drive &ndash; Suite 205 Lenexa, KS 66214 (913) 495-5100</p>
<p>December 15, 2016</p><p>VIA UNITED PARCEL SERVICE DELIVERY SIGNATURE REQUESTED</p>
<p>CMS Case: 512566 FEI: 1937310</p><p>WARNING LETTER</p>
<p>Charles L. Voellinger, President/CEO Nomax Inc. 9735 Green Park Industrial Dr. Saint Louis, MO 63123-7241</p>
<p>Dear Mr. Voellinger:</p>
<p>This inspection revealed that your firm's devices are adulterated within the meaning of section 501(h) of the Act, 21 U.S.C. &sect; 351(h), in that the methods used in, or the facilities or controls used for, their manufacture, packing, storage, or installation are not in conformity with the current good manufacturing practice requirements of the Quality System (QS) regulation found at Title 21, Code of Federal Regulations (CFR), Part 820.</p>
<p>Failure to establish and maintain procedures for implementing corrective and preventive action, as required by 21 CFR 820.100(a).</p>
<p>Sincerely, /S/ Cheryl A. Bigham, District Director, Kansas City District</p>
Page Last Updated: 01/05/2017</div>'''
_A_PERSON = '''<div role="main"><p>WARNING LETTER</p><p>Dear Dr. Roe:</p><p>... as required by 21 CFR 812.100.</p></div>'''
# Verbatim pager of the 2013 year listing, Archive-It 7993 capture 20190422151413 (read 2026-11-03;
# the items for pages 4-12 are left out). It links ucm20035841.htm?Page=N, not default.htm?Page=N,
# so the v0.0.7 archive run read only page 1 of 2013 (50 of about 700 letters).
_A_PAGER_2013 = ('\t\t<ul class="pagination-clean" >\n\n\t\t\t<li class="previous-off">Previous</li>\n'
                 '\t\t\t\t\t<li class="active">1</li>\n\t\t\t\t\t\t\n'
                 '\t\t\t\t\t<li><a href="/ICECI/EnforcementActions/WarningLetters/2013/ucm20035841.htm?Page=2">2</a></li>\n\t\t\n'
                 '\t\t\t\t\t<li><a href="/ICECI/EnforcementActions/WarningLetters/2013/ucm20035841.htm?Page=3">3</a></li>\n\t\t\n'
                 '\t\t\t\t\t<li><a href="/ICECI/EnforcementActions/WarningLetters/2013/ucm20035841.htm?Page=13">13</a></li>\n\t\t\n'
                 '\t\t\t\t\t<li><a href="/ICECI/EnforcementActions/WarningLetters/2013/ucm20035841.htm?Page=14">14</a></li>\n'
                 '\t\t\t<li class="next"><a href="?Page=2">Next</a></li>\n\t\t</ul>\n')


def _selftest_archive():
    import tempfile
    rows, pages = parse_archived_list(_A_LIST)
    assert pages == [2, 31] and len(rows) == 4, (pages, rows)
    assert pager_links(_A_LIST, OLD_LIST.format(2016))[31] == OLD_LIST.format(2016) + "?Page=31"
    p13 = pager_links(_A_PAGER_2013, OLD_LIST.format(2013))
    assert sorted(p13) == [2, 3, 13, 14] and parse_archived_list(_A_PAGER_2013)[1] == [2, 3, 13, 14], p13
    assert p13[2] == SITE + "/ICECI/EnforcementActions/WarningLetters/2013/ucm20035841.htm?Page=2", p13
    assert pager_links('<a href="?Page=2">Next</a>', OLD_LIST.format(2013)) == {2: OLD_LIST.format(2013) + "?Page=2"}
    nomax = rows[0]
    assert nomax["issued"] == "12/15/2016" and nomax["company"] == "Nomax Inc" and nomax["closeout"] == [], nomax
    assert nomax["url"] == SITE + "/ICECI/EnforcementActions/WarningLetters/2016/ucm534145.htm"
    assert rows[2]["company"] == "Edgar Martin Dairy" and rows[2]["issued"] == "12/29/2005", rows[2]
    assert rows[3]["closeout"] == [("06/01/2017", SITE + "/ICECI/EnforcementActions/WarningLetters/2017/ucm999002.htm")]
    assert [is_device(r) for r in rows] == [True, False, False, True]
    d = parse_letter(_A_LETTER, archived=True)
    assert d["cms"] == "512566" and d["fei"] == ["1937310"] and d["cfr_sections"] == ["820.100"], d
    assert d["cfr_parts"] == ["820"] and d["_salutation"] == ["Voellinger"], d
    assert not _salutation_person("Nomax Inc", ["Voellinger"]) and _salutation_person("John Roe", ["Roe"])
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "archive" / "pages").mkdir(parents=True)
        (t / "pages" / "list_s00000.html").write_text(_LIST, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"total": 3779, "lists": ["list_s00000"]}), encoding="utf-8")
        (t / "archive" / "pages" / "l1.html").write_text(_A_LETTER, encoding="utf-8")
        (t / "archive" / "pages" / "l2.html").write_text(_A_PERSON, encoding="utf-8")
        lst = "https://wayback.archive-it.org/7993/20170110235711/" + OLD_LIST.format(2016)
        arows = [dict(r, year=2016, listing=lst, ts="20170110235711") for r in rows]
        arows[0]["page"] = {"page": "l1", "capture_ts": "20170111134149",
                            "archive_url": "https://wayback.archive-it.org/7993/20170111134149/" + nomax["url"]}
        arows[3]["page"] = {"page": "l2", "capture_ts": "20170401000000",
                            "archive_url": "https://wayback.archive-it.org/7993/20170401000000/" + rows[3]["url"]}
        (t / "archive.json").write_text(json.dumps({"date": "2026-11-03", "rows": arows}), encoding="utf-8")
        st = transform(t, t / "wl.ttl", retrieved_at="2026-10-28")
        ttl = (t / "wl.ttl").read_text(encoding="utf-8")
    assert st.records == 5, st                                  # 3 current device letters + 2 archived
    blk = ttl.split(f"<{iri.warning_letter('ucm534145.htm')}>")[1].split("\n\n")[0]
    assert 'ex:letterIssueDate "2016-12-15"^^xsd:date' in blk and 'ex:marcsCmsNumber "512566"' in blk, blk
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='1937310')}>" in blk and f"<{iri.cfr_section('820.100')}>" in blk
    assert f'ex:archivedFrom "{nomax["url"]}"' in blk and 'ex:sourceUrl "https://wayback.archive-it.org/7993/20170111134149/' in blk
    assert "read from FDA's web archive copy (Archive-It collection 7993, captured 2017-01-11" in blk
    assert 'ex:retrievedAt "2026-11-03"^^xsd:date' in blk
    roe = ttl.split(f"<{iri.warning_letter('ucm999001.htm')}>")[1].split("\n\n")[0]
    assert "Roe" not in roe and "ex:url" not in roe and "ex:archivedFrom" not in roe, roe
    assert f'ex:sourceUrl "{lst}"' in roe and "letter addressed to an individual" in roe
    assert f"<{iri.cfr_section('812.100')}>" in roe and 'ex:closeoutLetterDate "2017-06-01"^^xsd:date' in roe
    for s in ("Voellinger", "Bigham", "Marshall Drive", "Dear", "Tofu Yu", "Edgar Martin"):
        assert s not in ttl, s
    print("warning_letters.py archive self-test PASSED")


# Verbatim rows of FDA's FOI reading-room month index (wlcfm/date.cfm), web.archive.org captures read
# 2026-11-11 (indentation turned into spaces): March 1998 (header with unclosed <TH>, a biologics row and a
# device row) and March 2004 (later header, PDF + HTML files). The last row ("Roe, John") is constructed in
# the 1998 format.
_FOI_1998 = """<TABLE cellpadding = 3 BORDER = 2 WIDTH = 100%>
<CAPTION><B><FONT SIZE = 3>Warning Letters Index - March, 1998</CAPTION>
<THEAD>
<TH><B>Company Name</B>
<TH><B>Date Issued</B>
<TH><B>Issuing Office</B>
<TH><B>Subject</B>
<TH><B>File</B>


    <TR>
    <TD><FONT SIZE =2>Curative Health Services, Inc.</TD>
    <TD><CENTER><FONT SIZE =2>3/02/98</CENTER></TD>
    <TD><FONT SIZE =2>Minneapolis District Office</TD>
    <TD><FONT SIZE =2>Biologic Drugs</TD>
    <TD><FONT SIZE =2><A HREF = "http://www.fda.gov/foi/warning_letters/d1554b.pdf">View File</A></TD>
    </TR>


    <TR>
    <TD><FONT SIZE =2>Burke/Neutech Medical Systems, Inc.</TD>
    <TD><CENTER><FONT SIZE =2>3/02/98</CENTER></TD>
    <TD><FONT SIZE =2>Florida District Office</TD>
    <TD><FONT SIZE =2>Medical Devices</TD>
    <TD><FONT SIZE =2><A HREF = "http://www.fda.gov/foi/warning_letters/d1561b.pdf">View File</A></TD>
    </TR>

    <TR>
    <TD><FONT SIZE =2>Roe, John</TD>
    <TD><CENTER><FONT SIZE =2>3/09/98</CENTER></TD>
    <TD><FONT SIZE =2>Center for Devices and Radiological Health</TD>
    <TD><FONT SIZE =2>Medical Devices</TD>
    <TD><FONT SIZE =2><A HREF = "http://www.fda.gov/foi/warning_letters/d9999b.pdf">View File</A></TD>
    </TR>
</TABLE>"""
_FOI_2004 = ("<TABLE><TR><TH><B>Company Name</B></th> <TH><B>Date Warning Letter Issued</B></th> <TH><B>Issuing "
             "Office</B></th> <TH><B>Subject</B></th> <TH><B>File</B></th> <th><b>Response Posted</b></th> "
             "<th>&nbsp;</th> <TR> <TD><FONT SIZE =2>MedRx, Inc.</TD> <TD><CENTER><FONT SIZE =2>3/01/04</CENTER>"
             "</TD> <TD><FONT SIZE =2>Florida District Office</TD> <TD><FONT SIZE =2>QSR for Medical Devices/"
             "Medical Device Reporting/Misbranded/Adulterated</TD> <TD> <FONT SIZE =2><A HREF = "
             "\"http://www.fda.gov/foi/warning_letters/g4618d.pdf\">[PDF]</A> <br><FONT SIZE =2><A HREF = "
             "\"http://www.fda.gov/foi/warning_letters/g4618d.htm\">[HTML]</A> </TD> <td> <center><FONT SIZE =2>"
             "No</font></center> </td> <td> &nbsp; </td> </TR></TABLE>")
# A new-site letter page as captured in Archive-It 7993 in 2020: header blocks in FDA's format (verbatim
# markup of a 2020 capture), with a fictitious firm, recipient and e-mail address.
_NEWSITE = """<html><head><title>Acme Surgical Devices Inc - 600001 - 03/05/2020 | FDA</title></head><body>
<div class="col-md-8" role="main"> <div class="inset-column"> <dl class="lcds-description-list--grid">
<dt class="cell-1_3">Product:</dt> <dd class="cell-2_3">Medical Devices </dd> </dl> </div> <hr>
<div class="row inset-column"> <div class="col-xs-12 col-md-6"> <dl class=""> <dt>Recipient:</dt> <dd>
<div class="field field--name-field-recipient-name field--type-string field--label-above">
<div class="field--label">Recipient Name</div> <div class="field--item">John Roe</div> </div> </dd>
<dd>Acme Surgical Devices Inc</dd> <dd><p class="address" translate="no"><span class="address-line1">1 Main St.</span><br>
<span class="locality">Springfield</span>, <span class="administrative-area">IL</span> <span class="postal-code">62701</span><br>
<span class="country">United States</span></p></dd> <dd><span class="fa fa-envelope" aria-hidden="true"></span>
<a href="mailto:jroe@example.com"> jroe@example.com</a></dd> </dl> </div> <div class="col-xs-12 col-md-6"> <dl class="">
<dt>Issuing Office:</dt> <dd>Center for Devices and Radiological Health</dd> </dl> </div> </div> <hr>
<p>WARNING LETTER</p><p>CMS # 600001</p><p>Dear Mr. Roe:</p><p>... (FEI 3001234567) ... 21 CFR 820.198(a) ...</p>
Content current as of: 03/10/2020</body></html>"""


def _selftest_archive2():
    """fetch_archive_newsite / fetch_archive_foi parsers and their records in transform()."""
    import tempfile
    assert _us_date("3/02/98") == "03/02/1998" and _us_date("3/01/04") == "03/01/2004"
    assert _us_date("12/29/17") == "12/29/2017"
    r98, r04 = parse_foi_list(_FOI_1998), parse_foi_list(_FOI_2004)
    assert len(r98) == 3 and len(r04) == 1, (r98, r04)
    assert r98[1]["company"] == "Burke/Neutech Medical Systems, Inc." and r98[1]["issued"] == "03/02/1998", r98[1]
    assert r98[1]["url"] == SITE + "/foi/warning_letters/d1561b.pdf" and r98[1]["htm"] is None, r98[1]
    assert [is_device(r) for r in r98] == [False, True, True]
    assert r04[0]["url"] == SITE + "/foi/warning_letters/g4618d.htm" and r04[0]["pdf"].endswith("g4618d.pdf"), r04
    assert r04[0]["office"] == "Florida District Office" and r04[0]["issued"] == "03/01/2004" and is_device(r04[0])
    assert last_first_person("Roe, John") and last_first_person("Law, Peter K.")
    assert not last_first_person("Burke/Neutech Medical Systems, Inc.") and not last_first_person("Acme, Inc.")
    assert not last_first_person("Ambu, Incorporation") and not last_first_person("Stedim, S.A.")
    assert not last_first_person("Dentomed, Incorporated") and last_first_person("Lowe, Sylvia")
    assert last_first_person("Thomas N. Kias, MD., PC.") and last_first_person("Franz Lucas & Bernstein, M.D., P.A.")
    assert not last_first_person("Madison Radiology") and not last_first_person("Omron Healthcare")
    assert not last_first_person("DO-IT Corp") and last_first_person("Dr. Jane Roe") and last_first_person("Roe, John, D.O.")
    p = parse_newsite_page(_NEWSITE)
    assert p == {"company": "Acme Surgical Devices Inc", "office": "Center for Devices and Radiological Health",
                 "product": "Medical Devices"}, p
    u = NEW_SITE_LETTERS + "acme-surgical-devices-inc-600001-03052020"
    assert key_date(u) == "2020-03-05" and letter_key(u) == "600001-03052020"
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "archive" / "pages").mkdir(parents=True)
        (t / "pages" / "list_s00000.html").write_text(_LIST, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"total": 3779, "lists": ["list_s00000"]}), encoding="utf-8")
        (t / "archive" / "pages" / "n1.html").write_text(_NEWSITE, encoding="utf-8")
        ns = {"url": u, "company": p["company"], "posted": None, "issued": "03/05/2020", "office": p["office"],
              "subject": None, "product": p["product"], "response": [], "closeout": [], "year": 2020,
              "listing": "https://wayback.archive-it.org/7993/20200310000000/" + LIST_PAGE, "ts": "20200310000000",
              "via": "newsite", "device": True,
              "page": {"page": "n1", "kind": "ai", "capture_ts": "20200311120000",
                       "archive_url": "https://wayback.archive-it.org/7993/20200311120000/" + u}}
        dup = dict(ns, url=NEW_SITE_LETTERS + "acme-surgical-devices-inc-600001-03052020-0", page=None)
        u2 = NEW_SITE_LETTERS + "beta-devices-llc-600002-06012020"
        (t / "pages" / f"{_page_name(u2)}.html").write_text(_NEWSITE.replace("Acme Surgical Devices Inc", "Beta Devices LLC")
                                                           .replace("600001", "600002"), encoding="utf-8")
        lv = dict(ns, url=u2, company="Beta Devices LLC", issued="06/01/2020", page=None, live=True)
        (t / "archive_newsite.json").write_text(json.dumps({"date": "2026-11-11", "rows": [ns, dup, lv]}),
                                                 encoding="utf-8")
        lst = "https://web.archive.org/web/20010107171400/http://www.accessdata.fda.gov/scripts/wlcfm/date.cfm?MONTH=3&YEAR=1998"
        foi = [dict(r, year=1998, month=3, listing=lst, listing_ts="20010107171400", ts="20010107171400", via="foi",
                    device=is_device(r)) for r in r98]
        foi[1]["file_ts"] = {foi[1]["url"]: "20020101000000"}
        (t / "archive_foi.json").write_text(json.dumps({"date": "2026-11-11", "rows": foi}), encoding="utf-8")
        st = transform(t, t / "wl.ttl", retrieved_at="2026-10-28")
        ttl = (t / "wl.ttl").read_text(encoding="utf-8")
        st2 = transform(t, t / "wl_archive.ttl", retrieved_at="2026-10-28", only_via=("newsite", "foi"))
        ttl2 = (t / "wl_archive.ttl").read_text(encoding="utf-8")
    assert st2.records == 4 and "Jiangsu" not in ttl2 and "600001_03052020" in ttl2, st2
    assert st.records == 3 + 2 + 2, st                       # 3 current + 2 new-site (duplicate dropped) + 2 FOI
    lb = ttl.split(f"<{iri.warning_letter('600002-06012020')}>")[1].split("\n\n")[0]
    assert f'ex:url "{u2}"' in lb and f'ex:sourceUrl "{u2}"' in lb and "ex:archivedFrom" not in lb, lb
    assert "found through the index of FDA's web archive" in lb and 'ex:retrievedAt "2026-11-11"^^xsd:date' in lb
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='3001234567')}>" in lb
    blk = ttl.split(f"<{iri.warning_letter('600001-03052020')}>")[1].split("\n\n")[0]
    assert 'ex:letterIssueDate "2020-03-05"^^xsd:date' in blk and 'ex:marcsCmsNumber "600001"' in blk, blk
    assert 'ex:issuingOffice "Center for Devices and Radiological Health"' in blk and "ex:letterSubject" not in blk
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='3001234567')}>" in blk and f'ex:archivedFrom "{u}"' in blk
    assert "Archive-It collection 7993, captured 2020-03-11" in blk and 'ex:retrievedAt "2026-11-11"^^xsd:date' in blk
    assert 'ex:city "Springfield"' in blk and 'rdfs:label "Warning letter to Acme Surgical Devices Inc, 03/05/2020"' in blk
    b98 = ttl.split(f"<{iri.warning_letter('d1561b.pdf')}>")[1].split("\n\n")[0]
    assert 'ex:letterIssueDate "1998-03-02"^^xsd:date' in b98 and 'ex:letterSubject "Medical Devices"' in b98, b98
    assert 'ex:url "https://web.archive.org/web/20020101000000/' + SITE + '/foi/warning_letters/d1561b.pdf"' in b98
    assert "FOI reading-room warning-letter index for March 1998" in b98 and "PDF, which was not read" in b98
    roe = ttl.split(f"<{iri.warning_letter('d9999b.pdf')}>")[1].split("\n\n")[0]
    assert "letter addressed to an individual" in roe and "ex:url" not in roe and f'ex:sourceUrl "{lst}"' in roe, roe
    for s in ("Roe", "jroe", "@example.com", "Curative", "Dear"):
        assert s not in ttl, s
    print("warning_letters.py archive (2019-2020 pages, FOI index) self-test PASSED")


def _selftest():
    import tempfile
    _selftest_archive()
    _selftest_archive2()
    total, rows = parse_list(_LIST)
    assert total == 3779 and len(rows) == 4, (total, rows)
    assert is_individual("Jane Doe, M.D.", "x") and is_individual("Jane Doe", "Clinical Investigator")
    assert is_individual("Jane Doe", "x", ["Jane Doe"]) and not is_individual("Jane Doe Enterprises", "x", ["Jane Doe"])
    assert not is_individual("Acme Medical Inc.", "Investigational Device Exemptions (Clinical Investigator)")
    assert rows[0]["closeout"] == [("08/31/2026", SITE + "/inspections-compliance-enforcement-and-criminal-investigations/warning-letters/jiangsu-caina-medical-co-ltd-687033-08242026")], rows[0]
    assert rows[2]["subject"] == "Medical Device/Adulterated/Misbranded/Lacks PMA and/or 510(k)", rows[2]
    assert [is_device(r) for r in rows] == [True, False, True, True]
    assert letter_key(rows[2]["url"]) == "727888-08262026"
    d = parse_letter(_LETTER)
    assert d["product"] == "Medical Devices" and d["cms"] == "727888", d
    assert d["fei"] == ["3003456789"] and d["k510"] == ["K123456"] and d["pma"] == ["P990010"], d
    assert d["product_codes"] == ["LIW"], d
    assert _unquote('"OHT2: Office of Cardiovascular Devices"') == "OHT2: Office of Cardiovascular Devices"
    assert not _PC.search("the universal product code (UPC), as required by 21 CFR 820.120(b)")
    assert _PC.search("product codes MKJ and NSA, until").group(1) == "MKJ and NSA"
    assert d["cfr_sections"] == ["807.81", "820.30", "830.300", "830.320"], d["cfr_sections"]
    assert d["cfr_parts"] == ["807", "820", "830"], d["cfr_parts"]
    assert d["address"] == {"line1": "4400 Log Cabin Ln.", "city": "Cookeville", "state": "TN",
                            "postal": "38506", "country": "United States"}, d["address"]
    s, p = _cfr_refs("21 CFR 820.198(a), (b) and 820.100; 21 C.F.R. Part 803; 21 CFR 801.20(a)(1)"
                     " and 21 U.S.C. 351(h)")
    assert s == ["801.20", "820.100", "820.198"] and p == ["801", "803", "820"], (s, p)
    # a Federal Register volume after a CFR citation is not a CFR part (was part 90 in the 2026-10-29 build)
    s, p = _cfr_refs("See 21 CFR 888.2910(b)(2); 90 Fed. Reg. 25889, 25890 (June 2025). 21 CFR 820.30, 53 FR 23872")
    assert s == ["820.30", "888.2910"] and p == ["820", "888"], (s, p)
    # sections retired with the QMSR (820.30 ...) are still extracted as cited; reference_fallback.py names them
    assert _cfr_refs("as required by 21 CFR 820.30(a) and 820.198")[0] == ["820.30", "820.198"]
    # a firm document number shaped like a PMA number (P + 6 digits, e.g. "P017333; Rev. B") is dropped:
    # FDA never issued a PMA sequence of 1000 or more in a year. A possible number that FDA data lacks
    # (K113091, P870050) is kept; reference_fallback.py names it.
    d2 = parse_letter('<div role="main"><p>under "Design Verification Protocol" (P017333; Rev. B), '
                      'K113091, P870050/S012, DEN170029, K995555 and K401234.</p></div>')
    assert d2["pma"] == ["P870050"] and d2["k510"] == ["DEN170029", "K113091", "K995555"], d2
    assert d2["impossible_numbers"] == ["K401234", "P017333"], d2
    assert possible_number("P990010") and possible_number("P010068") and possible_number("K261234", 2026)
    assert not possible_number("P011000") and not possible_number("K271234", 2026) and not possible_number("P750001", 2026)
    assert not possible_number("KO12383") and possible_number("K761234", 2026)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "list_s00000.html").write_text(_LIST, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"total": 3779, "lists": ["list_s00000"]}), encoding="utf-8")
        (t / "pages" / f"{_page_name(rows[2]['url'])}.html").write_text(_LETTER, encoding="utf-8")
        st = transform(t, t / "warning_letters.ttl", retrieved_at="2026-10-28")
        ttl = (t / "warning_letters.ttl").read_text(encoding="utf-8")
    assert st.records == 3 and st.skipped == 2, st
    assert "Doe" not in ttl and "jane-doe" not in ttl and "letter addressed to an individual" in ttl
    assert f"<{iri.warning_letter('700001-04012025')}>" in ttl
    wl = iri.warning_letter("727888-08262026")
    assert wl.endswith("/id/warning-letter/727888_08262026") and f"<{wl}>" in ttl
    assert 'ex:letterIssueDate "2026-08-26"^^xsd:date' in ttl and 'ex:postedDate "2026-10-27"^^xsd:date' in ttl
    assert 'ex:closeoutLetterDate "2026-08-31"^^xsd:date' in ttl
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='3003456789')}>" in ttl
    assert f"ex:hasRecipientFirm <{iri.firm_name('JIANGSU CAINA MEDICAL')}>" in ttl, ttl
    assert f"ex:citesCfrSection <{iri.cfr_section('820.30')}>" in ttl and f"ex:citesCfrPart <{iri.cfr_part('820')}>" in ttl
    assert f"ex:mentionsK510Record <{iri.k510('K123456')}>" in ttl and f"ex:mentionsPMARecord <{iri.pma('P990010')}>" in ttl
    assert f"<{iri.product_code('LIW')}>\n    a ex:ProductCode" in ttl
    assert 'ex:city "Cookeville"' in ttl and 'ex:letterProductType "Medical Devices"' in ttl
    assert "Dalian" not in ttl                                         # food letter filtered out
    for s in ("Schiff,", "Owner", "Michurski", "Mutawakkil", "Signer", "@fda.hhs.gov", "Dear", "inspection of your firm"):
        assert s not in ttl, s                                         # no people, no body text
    assert ttl.count("rdfs:label") == st.records + 1                   # one label per letter + one FirmName
    assert 'rdfs:label "Warning letter, 04/01/2025"' in ttl
    print(f"warning_letters.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
