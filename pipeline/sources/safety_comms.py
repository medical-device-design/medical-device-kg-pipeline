#!/usr/bin/env python3
"""
safety_comms.py -- CDRH Safety Communications and Letters to Health Care Providers -> RDF.

LISTINGS (static HTML tables on fda.gov, checked 2026-10-28)
  https://www.fda.gov/medical-devices/medical-device-safety/safety-communications
      Date | Safety Communication (link) | Product Area
  https://www.fda.gov/medical-devices/medical-device-safety/letters-health-care-providers
      Date | Letter (link) | devices
  Each links its previous years as fda.gov pages ("2024 Safety Communications", "2023 ...",
  "2024 Letters to Health Care Providers", "2023 ..."), which this module follows. Column order
  differs between years (the 2023 letters table is Title | Date), so columns are found from the
  header row, not by position.
  ARCHIVED YEARS (fetch_archive, PI approval of 2026-11-03): "2011-2022 Safety Communications
  (Archived)" and "2016-2022 Letters to Health Care Providers (Archived)" point to FDA's web archive
  (public4.pagefreezer.com, capture of 2024-08-01). Each archived hub lists the year pages, which FDA
  links in turn to Pagefreezer captures (2019-2021) or to its earlier Archive-It collection 7993
  (2011-2018 safety communications); 2022 is a relative link inside the 2024 capture. fetch_archive()
  follows exactly those links (_archive.py), reads every year page and every detail page from the
  archive copy FDA links (a relative link is read from the same archive at the same time), and
  caches everything under archive/. A few 2023 rows on the fda.gov listing also link to an archived
  copy; their pages are read the same way. Old-site pages (2011-2016, www.fda.gov/MedicalDevices/...
  ucmNNNNNN.htm) list Spanish versions as separate rows ("(en Espanol)"), which are skipped as on the
  current listings, write two-digit years (12/22/16) and end with "Page Last Updated:".
  An archive-derived record keeps the same fields and IRI rule; ex:url and ex:sourceUrl are the
  archive URL, ex:archivedFrom the original fda.gov URL, and ex:dataQualityNote says it was read
  from FDA's archive copy. A communication already listed on fda.gov is not duplicated.
  BEFORE 2011 (added November 5, 2026): the archived hub also links "Safety Communications Issued
  Prior to 2011" (Archive-It 7993, ucm559436.htm, captured 2017-07-22). That page lists FDA's
  archived pre-2011 sections; fetch_archive() reads three of them, as the same kind of record:
    "Archived Medical Device Safety Communications" (ucm463040.htm)  -> "Safety Communication"
    "Archived Public Health Notifications"                           -> "Public Health Notification"
    "Archived Patient Alerts (Medical Devices)"                      -> "Patient Alert"
  (Public Health Notifications were published until 2009 and Patient Alerts until 2010, when FDA
  replaced both with Safety Communications.) The first two are tables (Title | Date; a date cell
  carries data-value="YYYYMMDD", used before the two-digit year shown); the patient alerts are a
  link list with the date after a <br />. Each listed page is read from the same archive (nearest
  capture). Not read from that page: "Archived Tips and Articles on Device Safety" (consumer articles,
  not communications) and "Information About Heparin" (a topic page).
  If an archived page cannot be read, the row is kept from its listing with a note.

DETAIL PAGES (fda.gov only, 1 request/second, cached under pages/)
  kept: title, "Date Issued", the dates in the "Timeline of Communication Updates" table
  (ex:updateDate), "Content current as of", the FDA-written lead paragraph (ex:summary, after
  scrub.scrub_text), device names from table columns headed "Device Name"/"Product Name"/"Product"
  (ex:deviceName) and "Brand Name"/"Trade Name" (ex:brandName),
  product codes introduced by "product code" or in a "Product Code" column, 510(k)/De Novo and
  PMA numbers, and GS1 device identifiers (GTIN-14 with a valid check digit) quoted on the page.
  The rest of the page is not stored. FDA communications name no private persons; the summary is
  scrubbed anyway.

Minter: iri.safety_communication(page_slug) -> .../id/safety-communication/<slug(last URL path segment)>
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
import scrub      # noqa: E402
import _cfweb     # noqa: E402
import _archive   # noqa: E402

NAME = "safety_comms"
DATASET = "cdrh/safety-communications"
SITE = "https://www.fda.gov"
ROOTS = [
    ("Safety Communication", SITE + "/medical-devices/medical-device-safety/safety-communications",
     re.compile(r"^/medical-devices/safety-communications/(\d{4})-safety-communications/?$")),
    ("Letter to Health Care Providers", SITE + "/medical-devices/medical-device-safety/letters-health-care-providers",
     re.compile(r"^/medical-devices/letters-health-care-providers/(\d{4})-letters-health-care-providers/?$")),
]

_A = re.compile(r'(?s)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>')
_TR = re.compile(r"(?s)<tr\b[^>]*>(.*?)</tr>")
_CELL = re.compile(r"(?s)<(t[hd])\b[^>]*>(.*?)</t[hd]>")
_TABLE = re.compile(r"(?s)<table\b.*?</table>")
_USDATE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_LONGDATE = re.compile(r"\b((?:January|February|March|April|May|June|July|August|September|October|November|"
                       r"December)\s+\d{1,2},\s+\d{4})\b")
_K510 = re.compile(r"\b((?:K|DEN)\d{6})\b")
_PMA = re.compile(r"\b(P\d{6})\b")
_PC = re.compile(r"(?<![Uu]niversal )(?i:product\s+codes?)\s*(?:\(s\))?\s*[:#]?\s*\(?((?:[A-Z]{3})(?:\s*(?:,|;|/|&|\band\b|\bor\b)\s*[A-Z]{3}\b)*)\b")
_NAME_HDR = re.compile(r"(?i)^(?:manufacturer\s+)?(?:device|product)\s+names?$|^products?$|^devices?$")
_BRAND_HDR = re.compile(r"(?i)^(?:device\s+)?(?:brand|trade)\s+names?$")
_PC_HDR = re.compile(r"(?i)^product\s*codes?$")


def _one(fragment):
    return _cfweb.one_line(fragment)


def _abs(href):
    h = htmllib.unescape(href or "").strip()
    return SITE + h if h.startswith("/") else h


def _iso(text):
    """MM/DD/YYYY or 'June 3, 2026' -> 'YYYY-MM-DD' (None if neither)."""
    from datetime import datetime
    s = " ".join(str(text or "").split())
    for fmt in ("%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def is_fda(url):
    return url.startswith(SITE + "/")


def key_of(url):
    """Last path segment of the fda.gov URL (for an archived copy, of the fda.gov URL inside it)."""
    u = url.split("://www.fda.gov/", 1)[-1] if "www.fda.gov/" in url else url
    return u.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


def _main(page):
    a = page.find('role="main"')
    if a < 0:
        a = page.find("<main")
    return page[a:] if a >= 0 else page


def _tables(html):
    """[(headers [str], rows [[cell html]])] for every table in the fragment."""
    out = []
    for t in _TABLE.findall(html):
        hdr, rows = [], []
        for tr in _TR.findall(t):
            cells = _CELL.findall(tr)
            if cells and all(k == "th" for k, _ in cells) and not hdr:
                hdr = [_one(c) or "" for _, c in cells]
            elif cells:
                rows.append([c for _, c in cells])
        out.append((hdr, rows))
    return out


def parse_listing(page):
    """-> (items [dict(title, url, date, product_area)], year page paths, archived listing urls)"""
    main = _main(page)
    items = []
    for hdr, rows in _tables(main):
        h = [x.lower() for x in hdr]
        di = next((i for i, x in enumerate(h) if "date" in x), None)
        pi = next((i for i, x in enumerate(h) if "product" in x or "device" in x), None)
        for cells in rows:
            ti = next((i for i, c in enumerate(cells) if _A.search(c) and i != pi), None)
            if ti is None:
                continue
            a = _A.search(cells[ti])
            date = None
            for i, c in enumerate(cells):
                if (di is not None and i == di) or (di is None and _USDATE.search(_one(c) or "")):
                    m = _USDATE.search(_one(c) or "")
                    date = m.group(1) if m else None
                    break
            items.append({"title": _one(a.group(2)), "url": _abs(a.group(1)), "date": date,
                          "product_area": _one(cells[pi]) if pi is not None and pi < len(cells) else None})
    years, archived = [], []
    for href, txt in _A.findall(main):
        h = htmllib.unescape(href)
        if "pagefreezer.com" in h and "archiv" in (page[page.find(href):page.find(href) + 400].lower()):
            archived.append(h)
        elif h.startswith("/"):
            years.append(h.split("?", 1)[0])
    return items, years, sorted(set(archived))


# Old-site header labels written inside paragraphs: "Audience: ...", "Specialties Plastic surgeons, ..."
# (these labels never open a lead sentence, so the colon is optional), "Device: ..." / "Purpose: ..."
# (colon required), or a bare label paragraph ("Specialties") above its text.
_LABEL_LOOSE = re.compile(r"(?i)^(date issued|audiences?|medical specialties|specialties)\b\s*:?\s*(.*)$")
_LABEL_STRICT = re.compile(r"(?i)^(devices?|products?|purpose)(?:\s*:\s*(.*)|\s*)$")


def _label(s):
    """'Audience: Nurses' -> ('audience', 'Nurses'); 'Purpose' -> ('purpose', ''); else None."""
    m = _LABEL_LOOSE.match(s or "") or _LABEL_STRICT.match(s or "")
    return (m.group(1).lower(), (m.group(2) or "").strip()) if m else None


def parse_detail(page, archived=False):
    """archived=True also reads the old-site layout (dc.title, "Page Last Updated:", the "Purpose:"
    paragraph as the lead) of pages read from FDA's archive."""
    out = {"title": None, "date_issued": None, "updates": [], "current_as_of": None, "summary": None,
           "device_names": [], "brand_names": [], "product_codes": [], "k510": [], "pma": [], "dis": []}
    m = re.search(r'<meta name="dcterms.title" content="([^"]*)"', page)
    if not m and archived:
        m = re.search(r'<meta name="dc.title" content="(?:Safety Communications - |Letters to Health Care Providers - )?([^"]*)"', page)
    out["title"] = _one(m.group(1)) if m else None
    main = _main(page)
    e = main.find("Content current as of")
    if e < 0 and archived:
        e = main.find("Page Last Updated")
    if e > 0:
        tail = main[e:e + 600]
        cm = _USDATE.search(_one(tail) or "")
        out["current_as_of"] = cm.group(1) if cm else None
        main = main[:e]
    if archived:
        # 2011-2018 pages are sectioned by headings (Date Issued, Audience, Specialties, Device(s),
        # Purpose, Summary of Problem and Scope, ...): the lead is the Purpose paragraph, else the first
        # substantial paragraph outside the header sections
        parts = re.split(r"(?is)<h[1-6]\b[^>]*>(.*?)</h[1-6]>", main)
        sections = [((_one(parts[i]) or "").rstrip(":").strip().lower(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
        skip = re.compile(r"^(date issued|audiences?|medical specialties|specialties|devices?|products?)$")
        pick, least = None, 80
        if not any(n == "date issued" for n, _ in sections):
            sections = []                                  # current layout: the generic rule below
        for name, body in sections:
            if name.startswith("purpose"):
                pick, least = body, 20                     # the Purpose paragraph even when short
                break
        for name, body in ([] if pick else sections):
            if not skip.match(name) and any(len(_one(p) or "") >= 80 for p in re.findall(r"(?s)<p\b[^>]*>(.*?)</p>", body)):
                pick = body
                break
        for p in re.findall(r"(?s)<p\b[^>]*>(.*?)</p>", pick or ""):
            s = _one(p) or ""
            if len(s) >= least:
                out["summary"] = s
                break
        if not out["summary"]:
            # 2011-2015 pages put the labels inside paragraphs ("<p><strong>Audience:</strong> ...")
            # a label paragraph is "Label: text" or the bare label ("Specialties") over its text
            texts = [_one(p) or "" for p in re.findall(r"(?s)<p\b[^>]*>(.*?)</p>", main)]
            first, purpose, after = None, None, None
            for s in texts:
                m = _label(s)
                if m:
                    name, rest = m
                    if name == "purpose" and len(rest) >= 80:
                        purpose = rest
                        break
                    after = name if not rest else None
                    continue
                if after == "purpose" and len(s) >= 80:
                    purpose = s
                    break
                if after:                                   # the text under a bare header label
                    after = None
                    continue
                if first is None and len(s) >= 80 and not s.startswith("Date Issued"):
                    first = s
            if any(_label(s) for s in texts):               # labelled layout: never a label paragraph
                out["summary"] = purpose or first
    text = _cfweb.text(main) or ""
    flat = " ".join(text.split())
    dm = re.search(r"Date Issued:?\s*" + _LONGDATE.pattern, flat)
    if dm:
        out["date_issued"] = dm.group(1)
    else:                                              # letters: a bare long date right under the heading
        lm = _LONGDATE.search(flat[:600])
        out["date_issued"] = lm.group(1) if lm else None
    # lead paragraph: the first substantial <p> of the article (FDA-written)
    for p in re.findall(r"(?s)<p\b[^>]*>(.*?)</p>", main) if not out["summary"] else []:
        s = _one(p) or ""
        if len(s) >= 80 and not s.startswith("Date Issued"):
            out["summary"] = s
            break
    names, brands, codes = [], [], set()
    for hdr, rows in _tables(main):
        h = [x.strip() for x in hdr]
        if len(h) == 2 and h[0].lower() == "date":           # "Timeline of Communication Updates" (Date | Actions/Event/...)
            for cells in rows:
                d = _USDATE.search(_one(cells[0]) or "")
                if d and d.group(1) not in out["updates"]:
                    out["updates"].append(d.group(1))
            continue
        for i, x in enumerate(h):
            target = names if _NAME_HDR.match(x) else brands if _BRAND_HDR.match(x) else None
            if target is not None:
                for cells in rows:
                    if len(cells) < len(h):                    # a spanning note row ("Effective June 24, 2025")
                        continue
                    for v in (_cfweb.text(cells[i]) or "").split("\n"):   # <br>-separated names in one cell
                        v = " ".join(v.split())
                        if v and v not in target:
                            target.append(v)
            if _PC_HDR.match(x):
                for cells in rows:
                    for c in re.findall(r"\b[A-Z]{3}\b", _one(cells[i]) or "") if i < len(cells) else []:
                        codes.add(c)
    for m in _PC.finditer(flat):
        codes.update(re.findall(r"\b[A-Z]{3}\b", m.group(1)))
    out["device_names"] = names
    out["brand_names"] = brands
    out["product_codes"] = sorted(c for c in (common.product_code(x) for x in codes) if c)
    out["k510"] = sorted(set(_K510.findall(flat)))
    out["pma"] = sorted(set(_PMA.findall(flat)))
    out["dis"] = common.gs1_dis(flat)
    return out


# ---------------------------------------------------------------------------
def _name(url):
    return "detail_" + _cfweb.safe_name(key_of(url))


def fetch(dest, delay=1.0, log=print, list_max_age_days=0.5, archive=True):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    listings, archived, failed = [], [], []
    for typ, url, year_rx in ROOTS:
        nm = "list_" + _cfweb.safe_name(key_of(url))
        page = f.get(url, nm, max_age_days=list_max_age_days)
        listings.append([typ, url, nm])
        _, years, arch = parse_listing(page)
        archived += arch
        for y in sorted({y for y in years if year_rx.match(y)}, reverse=True):
            yurl = SITE + y
            ynm = "list_" + _cfweb.safe_name(key_of(yurl))
            try:
                yp = f.get(yurl, ynm, max_age_days=list_max_age_days)
            except _cfweb.Unavailable as e:
                failed.append([yurl, str(e)])
                continue
            listings.append([typ, yurl, ynm])
            archived += parse_listing(yp)[2]
    (dest / "lists.json").write_text(json.dumps({"listings": listings, "archived_not_fetched": sorted(set(archived))},
                                                indent=1), encoding="utf-8")
    items = _items(dest)
    todo = [it for it in items.values() if is_fda(it["url"]) and "/media/" not in it["url"]]
    for i, it in enumerate(todo, 1):
        try:
            f.get(it["url"], _name(it["url"]))
        except _cfweb.Unavailable as e:
            failed.append([it["url"], str(e)])
        if i % 25 == 0:
            log(f"  safety_comms: {i}/{len(todo)} detail pages")
    (dest / "fetch_log.json").write_text(json.dumps({
        "date": base.today(), "listings": len(listings), "items": len(items), "detail_pages": len(todo),
        "fetched_now": f.fetched, "from_cache": f.cached, "failed": failed,
        "archived_not_fetched": sorted(set(archived))}, indent=1), encoding="utf-8")
    log(f"  safety_comms: {len(items)} items from {len(listings)} listings; fetched {f.fetched}, "
        f"cached {f.cached}, failed {len(failed)}; archived listings not fetched: {sorted(set(archived))}")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    if archive:
        fetch_archive(dest, delay=delay, log=log)        # cached: a rerun reads nothing again
    return m


def _items(src):
    """url -> merged listing item (a URL listed on two pages keeps both types / listing urls)."""
    meta = json.loads((Path(src) / "lists.json").read_text(encoding="utf-8"))
    out = {}
    for typ, lurl, nm in meta["listings"]:
        page = (Path(src) / "pages" / f"{nm}.html").read_text(encoding="utf-8", errors="replace")
        for it in parse_listing(page)[0]:
            rec = out.setdefault(it["url"], dict(it, types=[], listed_on=[]))
            if typ not in rec["types"]:
                rec["types"].append(typ)
            if lurl not in rec["listed_on"]:
                rec["listed_on"].append(lurl)
            for k in ("date", "product_area", "title"):
                rec[k] = rec.get(k) or it.get(k)
    return out


# ---------------------------------------------------------------------------
#  FDA's archive copies (fetch_archive)
# ---------------------------------------------------------------------------
ARCHIVE_YEARS = {"Safety Communication": (2011, 2022), "Letter to Health Care Providers": (2016, 2022)}
_YEAR_TEXT = {"Safety Communication": re.compile(r"(?i)^\s*(20\d\d)\s+Safety\s+Communications\s*$"),
              "Letter to Health Care Providers": re.compile(r"(?i)^\s*(20\d\d)\s+Letters\s+to\s+Health\s+Care\s+Providers\s*$")}
_HREF_ANY = re.compile(r"""(?is)<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>(.*?)</a>""")
_SHORTDATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b")
archive_ref = _archive.ref_of          # (kind, timestamp, original fda.gov url) of a link
archive_view_url = _archive.view_url


def _norm_date(t, data_value=None):
    """'12/22/16' or '12/22/2016' -> '12/22/2016'. A cell's data-value="YYYYMMDD" (old-site
    tables) wins; otherwise a two-digit year after the current year is read as 19YY ('9/2/83')."""
    if data_value and re.fullmatch(r"(19|20)\d{6}", data_value):
        return f"{data_value[4:6]}/{data_value[6:8]}/{data_value[:4]}"
    m = _SHORTDATE.search(t or "")
    if not m:
        return None
    y = int(m.group(3))
    if y < 100:
        from datetime import date as _d
        y = y + 2000 if y <= _d.today().year % 100 else y + 1900
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{y}"


_DATA_VALUE = re.compile(r'(?i)<td\b[^>]*\bdata-value\s*=\s*"(\d{8})"')
_CELL_ATTR = re.compile(r"(?s)<(t[hd])\b([^>]*)>(.*?)</t[hd]>")

# "Safety Communications Issued Prior to 2011": the archived sections read, and their record type
PRIOR_SECTIONS = [
    (re.compile(r"(?i)^\s*Archived\s+Medical\s+Device\s+Safety\s+Communications\s*$"), "Safety Communication"),
    (re.compile(r"(?i)^\s*Archived\s+Public\s+Health\s+Notifications\s*$"), "Public Health Notification"),
    (re.compile(r"(?i)^\s*Archived\s+Patient\s+Alerts\b.*$"), "Patient Alert"),
]
PRIOR_NOT_READ = re.compile(r"(?i)^\s*(Archived\s+Tips\s+and\s+Articles|Information\s+About\s+Heparin)")
_LI = re.compile(r"(?is)<li\b[^>]*>(.*?)</li>")


def parse_archived_link_list(page, folder=None):
    """Old-site link list ('<li><a href=...>Title</a> [ARCHIVED] <br />02/18/2010</li>', the archived
    Patient Alerts page) -> [dict(title, href, date, product_area=None)]. folder: keep only links
    under this path (e.g. '/PatientAlerts/'), so page navigation is never read as a row."""
    items = []
    for li in _LI.findall(_main(page)):
        a = _HREF_ANY.search(li)
        if not a:
            continue
        href = next(g for g in a.groups()[:3] if g is not None)
        title = _one(a.group(4))
        if not title or not re.search(r"(?i)/ucm\d+\.htm", href) or is_spanish(title):
            continue
        if folder and folder.lower() not in href.lower():
            continue
        tail = _one(li[a.end():]) or ""
        items.append({"title": title, "href": href, "date": _norm_date(tail) if _SHORTDATE.search(tail) else None,
                      "product_area": None})
    return items


_SPANISH = re.compile(r"(?i)\ben\s+espa(?:ñ|&ntilde;|n)ol\b|\bcomunica(?:do|ción|cion)\s+de\s+seguridad\b|"
                      r"\bcarta\s+(?:a|para)\s+(?:los\s+)?proveedores\b")


def is_spanish(title):
    """The Spanish version of a communication (listed as its own row on old-site year pages)."""
    return bool(_SPANISH.search(title or ""))


def parse_archived_listing(page):
    """Year page read from the archive (current or old-site layout) -> [dict(title, href, date, product_area)].
    Rows for the Spanish version of a communication ('... (en Espanol)') are skipped."""
    main = _main(page)
    items = []
    for t in _TABLE.findall(main):
        hdr, rows, dvals = [], [], []
        for tr in _TR.findall(t):
            cells = _CELL_ATTR.findall(tr)
            if cells and all(k == "th" for k, _, _ in cells) and not hdr:
                hdr = [(_one(c) or "").lower() for _, _, c in cells]
            elif cells:
                rows.append([c for _, _, c in cells])
                dvals.append([(_DATA_VALUE.search(f"<td {a}>") or [None, None])[1] for _, a, _ in cells])
        di = next((i for i, x in enumerate(hdr) if x == "date" or x.startswith("date")), None)
        for cells, dv in zip(rows, dvals):
            ti = next((i for i, c in enumerate(cells) if _HREF_ANY.search(c)), None)
            if ti is None:
                continue
            a = _HREF_ANY.search(cells[ti])
            href = next(g for g in a.groups()[:3] if g is not None)
            title = _one(a.group(4))
            if not title or is_spanish(title):
                continue
            pi = next((i for i, x in enumerate(hdr) if ("product" in x or "device" in x) and i != ti), None)
            date = None
            for i, c in enumerate(cells):
                if i == ti:
                    continue
                if di is None or i == di:
                    date = _norm_date(_one(c), dv[i] if i < len(dv) else None)
                    if date:
                        break
            items.append({"title": title, "href": href, "date": date,
                          "product_area": _one(cells[pi]) if pi is not None and pi < len(cells) else None})
    return items


def _links(page):
    return [(next(g for g in m.groups()[:3] if g is not None), _one(m.group(4)) or "")
            for m in _HREF_ANY.finditer(_main(page))]


def fetch_archive(dest, delay=1.0, log=print):
    """Read the archived listings FDA links (see module docstring) -> archive.json."""
    dest = Path(dest)
    meta = json.loads((dest / "lists.json").read_text(encoding="utf-8"))
    rd = _archive.Reader(dest / "archive", delay=delay, log=log)
    hubs = []
    for href in meta.get("archived_not_fetched", []):
        ref = archive_ref(href)
        typ = "Letter to Health Care Providers" if "letters-health-care-providers" in href else "Safety Communication"
        if ref and ref[2].rstrip("/") in [r[1] for r in ROOTS]:
            hubs.append((typ, ref))
    listings, items, failed, skipped_years, seen_years = [], [], [], [], set()
    prior = []
    for typ, href_ref in hubs:
        try:
            hm, hp = rd.get(href_ref)
        except (_archive.Missing, _archive.Refused) as e:
            failed.append([archive_view_url(href_ref), str(e)])
            if isinstance(e, _archive.Refused):
                raise
            continue
        lo, hi = ARCHIVE_YEARS[typ]
        for href, txt in _links(hp):
            ym = _YEAR_TEXT[typ].match(txt)
            if not ym:
                if "prior to" in txt.lower():
                    pref = archive_ref(href, base=(href_ref[0], href_ref[1]))
                    if pref and pref not in prior:
                        prior.append(pref)
                continue
            year = int(ym.group(1))
            if not lo <= year <= hi:
                continue
            yref = archive_ref(href, base=(href_ref[0], href_ref[1]))
            if not yref or (typ, yref) in seen_years:       # the hub repeats some links in its page navigation
                continue
            seen_years.add((typ, yref))
            try:
                ym_, yp = rd.get(yref)
            except _archive.Missing as e:
                failed.append([archive_view_url(yref), str(e)])
                continue
            rows = parse_archived_listing(yp)
            listings.append(dict(ym_, type=typ, year=year, rows=len(rows)))
            log(f"  safety_comms archive: {typ} {year}: {len(rows)} rows ({ym_['archive_url']})")
            for it in rows:
                iref = archive_ref(it["href"], base=(ym_["kind"], ym_["ts"]))
                items.append(dict(it, type=typ, year=year, listing=ym_["archive_url"], ref=iref))
    # "Safety Communications Issued Prior to 2011" and the archived sections it lists
    for pref in prior:
        try:
            pm, pp = rd.get(pref)
        except _archive.Missing as e:
            failed.append([archive_view_url(pref), str(e)])
            continue
        seen_sec = set()
        for href, txt in _links(pp):
            if PRIOR_NOT_READ.match(txt):
                skipped_years.append(["prior to 2011", txt, href])
                continue
            styp = next((t for rx, t in PRIOR_SECTIONS if rx.match(txt)), None)
            sref = archive_ref(href, base=(pm["kind"], pm["ts"])) if styp else None
            if not sref or sref in seen_sec:
                continue
            seen_sec.add(sref)
            try:
                sm, sp = rd.get(sref)
            except _archive.Missing as e:
                failed.append([archive_view_url(sref), str(e)])
                continue
            rows = parse_archived_listing(sp) or parse_archived_link_list(
                sp, folder=sref[2].split("://", 1)[-1].split("/", 1)[-1].rsplit("/", 1)[0].rsplit("/", 1)[-1] + "/")
            listings.append(dict(sm, type=styp, year=None, rows=len(rows), prior_to_2011=True))
            log(f"  safety_comms archive: before 2011, {styp}: {len(rows)} rows ({sm['archive_url']})")
            for it in rows:
                iref = archive_ref(it["href"], base=(sm["kind"], sm["ts"]))
                y = int(it["date"][-4:]) if it.get("date") else None
                items.append(dict(it, type=styp, year=y, listing=sm["archive_url"], ref=iref))
    # rows on the fda.gov listings that link to an archived copy
    for url, it in _items(dest).items():
        if not is_fda(url):
            ref = archive_ref(url)
            if ref:
                items.append({"title": it["title"], "href": url, "date": it["date"], "product_area": it["product_area"],
                              "type": it["types"][0], "year": None, "listing": it["listed_on"][0], "ref": ref,
                              "fda_listed": True})
    n, refused = 0, None
    for it in items:
        ref = it["ref"]
        if not ref or not ref[2].startswith(SITE + "/") or "/media/" in ref[2] or ref[2].lower().endswith(".pdf"):
            continue
        try:
            dm, _ = rd.get(tuple(ref))
            it["detail"] = dm
        except _archive.Missing as e:
            failed.append([archive_view_url(ref), str(e)])
        except _archive.Refused as e:                     # the host kept refusing after long pauses: stop
            refused = str(e)
            log(f"  safety_comms archive: STOPPED, {refused}")
            break
        n += 1
        if n % 25 == 0:
            log(f"  safety_comms archive: {n} detail pages (fetched {rd.f.fetched}, cached {rd.f.cached})")
    (dest / "archive.json").write_text(json.dumps({
        "date": base.today(), "hubs": [[t, archive_view_url(r)] for t, r in hubs], "listings": listings,
        "items": items, "failed": failed, "not_read": skipped_years, "refused": refused,
        "host_refusals": rd.f.refusals}, indent=1), encoding="utf-8")
    log(f"  safety_comms archive: {len(listings)} year pages, {len(items)} rows, {n} detail pages; "
        f"fetched {rd.f.fetched}, cached {rd.f.cached}, failed {len(failed)}")
    return len(items)


def _archived_records(src):
    """archive.json -> [record dict] (rows of archived year pages and archived links on fda.gov listings)."""
    p = Path(src) / "archive.json"
    if not p.exists():
        return []
    a = json.loads(p.read_text(encoding="utf-8"))
    out = []
    for it in a["items"]:
        ref = it.get("ref")
        if not ref or is_spanish(it.get("title")):
            continue
        ref = tuple(ref)
        d, cap = None, None
        dm = it.get("detail")
        if dm:
            pg = Path(src) / "archive" / "pages" / f"{dm['page']}.html"
            if pg.exists():
                d = parse_detail(pg.read_text(encoding="utf-8", errors="replace"), archived=True)
                cap = dm.get("capture_ts")
        out.append({"title": it["title"], "date": it["date"], "type": it["type"], "product_area": it["product_area"],
                    "original": ref[2], "archive_url": (dm or {}).get("archive_url") or archive_view_url(ref),
                    "kind": ref[0], "capture": cap, "detail": d, "fda_listed": it.get("fda_listed", False),
                    "href": it["href"], "retrieved": a.get("date")})
    return out


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    items = _items(src)
    arch = _archived_records(src)
    by_href = {}
    for r in arch:
        if r["fda_listed"]:
            by_href[r["href"]] = r
    recs = {}
    for url, it in items.items():                     # fda.gov listings (some rows link to an archived copy)
        recs[key_of(url)] = dict(it, url=url, archived=by_href.get(url) if not is_fda(url) else None)
    st = base.Stats(source=NAME)
    hits, codes = {}, set()
    counts = {"detail": 0, "archived_only": 0, "with_pc": 0, "with_sub": 0, "with_di": 0, "with_names": 0,
              "archive_records": 0, "archive_with_detail": 0, "archive_duplicates_of_fda_listing": 0}
    for r in arch:                                    # rows of archived year pages
        if r["fda_listed"]:
            continue
        key = key_of(r["original"])
        if key in recs:
            rec = recs[key]
            if rec.get("archived") is None and is_fda(rec["url"]):
                counts["archive_duplicates_of_fda_listing"] += 1
                continue
            if r["type"] not in rec["types"]:
                rec["types"].append(r["type"])
            continue
        recs[key] = {"title": r["title"], "date": r["date"], "product_area": r["product_area"], "types": [r["type"]],
                     "url": r["archive_url"], "archived": r}
    with base.TurtleWriter(out) as w:
        for key in sorted(recs):
            it = recs[key]
            url = it["url"]
            ar = it.get("archived")
            d = None
            p = src / "pages" / f"{_name(url)}.html"
            if is_fda(url) and p.exists():
                d = parse_detail(p.read_text(encoding="utf-8", errors="replace"))
                counts["detail"] += 1
            elif ar:
                d = ar["detail"]
                counts["archive_records"] += 1
                counts["archive_with_detail"] += bool(d)
            title = it["title"] or (d or {}).get("title")
            issued = _iso(it["date"]) or _iso((d or {}).get("date_issued"))
            pairs = [("a", "ex:SafetyCommunication"),
                     ("rdfs:label", common.lit(title)),
                     ("ex:issueDate", common.date_lit(issued))]
            pairs += [("ex:communicationType", common.lit(t)) for t in it["types"]]
            pairs += [("ex:url", common.lit(ar["archive_url"] if ar else url)),
                      ("ex:productArea", common.lit(it["product_area"]))]
            if ar:
                pairs.append(("ex:archivedFrom", common.lit(ar["original"])))
                pdf = "/media/" in ar["original"] or ar["original"].lower().endswith(".pdf")
                pairs.append(("ex:dataQualityNote", common.lit(
                    _archive.note(ar["kind"], ar["capture"]) if d else
                    "listed on FDA's archived listing as a PDF document, which is not read" if pdf else
                    "listed on FDA's archived listing; the page could not be read from the archive")))
            elif not is_fda(url):
                counts["archived_only"] += 1
                pairs.append(("ex:dataQualityNote", common.lit(
                    "listed by FDA with a link to its web archive; the archived page was not fetched")))
            elif d is None:
                st.skipped += 1
            if d:
                pairs += [("ex:updateDate", common.date_lit(_iso(u))) for u in d["updates"] if _iso(u) != issued]
                pairs.append(("ex:contentCurrentAsOf", common.date_lit(_iso(d["current_as_of"]))))
                if d["summary"]:
                    s, h = scrub.scrub_text(d["summary"])
                    for k, v in h.items():
                        hits[k] = hits.get(k, 0) + v
                    pairs.append(("ex:summary", common.lit(s)))
                pairs += [("ex:deviceName", common.lit(n)) for n in d["device_names"]]
                pairs += [("ex:brandName", common.lit(n)) for n in d["brand_names"]]
                for pc in d["product_codes"]:
                    pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
                    codes.add(pc)
                for k in d["k510"]:
                    pairs += [("ex:kNumber", common.lit(k)), ("ex:mentionsK510Record", f"<{iri.k510(k)}>")]
                for pn in d["pma"]:
                    pairs += [("ex:pmaNumber", common.lit(pn)), ("ex:mentionsPMARecord", f"<{iri.pma(pn)}>")]
                for di in d["dis"]:
                    pairs.append(("ex:namesDeviceIdentifier", f"<{iri.udi_identifier('GS1', di)}>"))
                counts["with_pc"] += bool(d["product_codes"])
                counts["with_sub"] += bool(d["k510"] or d["pma"])
                counts["with_di"] += bool(d["dis"])
                counts["with_names"] += bool(d["device_names"] or d["brand_names"])
            pairs += common.provenance(DATASET, key, source_url=ar["archive_url"] if ar else url,
                                       retrieved_at=(ar.get("retrieved") or retrieved_at) if ar else retrieved_at)
            w.block(iri.safety_communication(key), pairs)
            st.records += 1
        for pc in sorted(codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    (out.with_suffix(".counts.json")).write_text(json.dumps(dict(counts, records=st.records, scrub=hits), indent=1),
                                                encoding="utf-8")
    print(f"  safety_comms: {st.records} items; {counts}; scrub hits {hits}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim fragments fetched 2026-10-28: the current letters table (two rows), the 2023 letters
# table (Title | Date order), the 2023 safety-communications table (one archived row), the
# "previous years" links, and a shortened detail page (lead sentence and device table rows from FDA's
# Olympus letter; the timeline table follows FDA's markup; a product-code sentence and a phone number
# were added to exercise extraction and scrubbing).
_HCP = '''<div role="main">
<table><thead><tr><th scope="col">Date</th><th scope="col">Issue</th><th scope="col">Product Area</th></tr></thead><tbody>
<tr><td>10/20/2026</td><td><a href="/medical-devices/letters-health-care-providers/update-import-alert-certain-olympus-medical-devices-manufactured-japan-letter-health-care-providers" data-entity-type="node" data-entity-uuid="5b8e7b7b-9783-459e-9ab2-2091cd85cbb6" data-entity-substitution="canonical" title="Update: Import Alert for Certain Olympus Medical Devices Manufactured in Japan - Letter to Health Care Providers">Update: Import Alert for Certain Olympus Medical Devices Manufactured in Japan - Letter to Health Care Providers</a></td><td>Ureterorenoscopes, Bronchoscopes, Laparoscopes, Automated endoscope reprocessors</td></tr>
<tr><td>06/16/2026</td><td><a href="/medical-devices/letters-health-care-providers/disruptions-availability-breast-biopsy-needles-letter-health-care-providers" data-entity-type="node" data-entity-uuid="247cc8b5-6c19-4a9b-b65b-e1da4b6f2bdc" data-entity-substitution="canonical" title="Disruptions in Availability of Breast Biopsy Needles - Letter to Health Care Providers ">Disruptions in Availability of Stereotactic Breast Biopsy Needles - Letter to Health Care Providers</a></td><td>Breast Biopsy Needles</td></tr>
</tbody></table>
<h2>Previous Letters to Health Care Providers</h2><ul><li><a href="/medical-devices/letters-health-care-providers/2024-letters-health-care-providers" data-entity-type="node" title="2024 Letters to Health Care Providers">2024 Letters to Health Care Providers</a></li><li><a href="/medical-devices/letters-health-care-providers/2023-letters-health-care-providers" data-entity-type="node" title="2023 Letters to Health Care Providers">2023 Letters to Health Care Providers</a></li><li><a href="https://public4.pagefreezer.com/browse/FDA/01-08-2024T13:49/https:/www.fda.gov/medical-devices/medical-device-safety/letters-health-care-providers">2016-2022 Letters to Health Care Providers</a> (Archived)</li></ul>
</div>'''
_HCP2023 = '''<div role="main"><table><tr> <th scope="col">Title</th> <th scope="col">Date</th> </tr>
<tr> <td><a data-entity-substitution="canonical" data-entity-type="node" data-entity-uuid="3ef9ece2-0b2c-4617-8a79-34d72af9a9e9" href="/medical-devices/letters-health-care-providers/hsv-2-tests-genital-herpes-can-produce-false-reactive-results-letter-clinical-laboratory-staff-and" title="HSV-2 Tests for Genital Herpes Can Produce False Reactive Results - Letter to Clinical Laboratory Staff and Health Care Providers ">HSV-2 Tests for Genital Herpes Can Produce False Reactive Results - Letter to Clinical Laboratory Staff and Health Care Providers</a></td> <td>12/27/2023</td> </tr></table></div>'''
_SC2023 = '''<div role="main"><table><tr><th style="width:76px;" scope="col">Date</th><th style="width:1139px;" scope="col">Safety Communication</th><th scope="col">Product Area</th><th scope="col">Espa&ntilde;ol</th></tr>
<tr><td style="width:76px;">11/30/2023</td><td style="width:1139px;"><a href="https://public4.pagefreezer.com/browse/FDA/25-03-2024T16:14/https://www.fda.gov/medical-devices/safety-communications/evaluating-plastic-syringes-made-china-potential-device-failures-fda-safety-communication">Evaluating Plastic Syringes Made in China for Potential Device Failures: FDA Safety Communication</a></td><td>Syringes</td><td><a class="btn btn-sm btn-default" href="https://public4.pagefreezer.com/browse/FDA/25-03-2024T16:14/https://www.fda.gov/medical-devices/safety-communications/evaluacion-de-jeringuillas-de-plastico-fabricadas-en-china-para-detectar-posibles-fallos-de-los"><span><span class="text">Espa&ntilde;ol</span></span></a></td></tr></table></div>'''
_DETAIL = '''<meta name="dcterms.title" content="Update: Import Alert for Certain Olympus Medical Devices Manufactured in Japan - Letter to Health Care Providers" />
<div role="main"><h1>Update: Import Alert for Certain Olympus Medical Devices Manufactured in Japan - Letter to Health Care Providers</h1>
<p>October 20, 2026</p>
<p>The U.S. Food and Drug Administration (FDA) is alerting health care providers about an import alert for certain medical devices manufactured in Japan by Olympus Medical Systems Corporation (Olympus) and its subsidiaries. Questions? Call 301-555-0100.</p>
<p>The bronchoscopes are classified under product code EOQ and were cleared under K123456.</p>
<table><thead><tr><th>Date</th><th>Actions</th></tr></thead><tbody>
<tr><td>10/20/2026</td><td>The FDA updated this letter.</td></tr>
<tr><td>06/24/2025</td><td>The FDA issued a letter about the import alert. (<a href="https://public4.pagefreezer.com/x">Archived Communication</a>)</td></tr>
</tbody></table>
<table><thead><tr><th>Device Name</th><th>Version or Model</th><th>Device Identifier (DI) Number</th></tr></thead><tbody>
<tr><td colspan="3">Effective June 24, 2025</td></tr>
<tr><td>Bronchofiberscope</td><td>BF-PE2</td><td>04953170062988; 04953170339974</td></tr>
<tr><td>Bronchovideoscope</td><td>BF-1T150</td><td>04953170288968</td></tr>
</tbody></table>
<p>Content current as of:</p><p><time datetime="2026-10-26T12:40:01Z">10/26/2026</time></p></div>'''


# Verbatim fragments read 2026-11-03 from FDA's archive copies (rows trimmed): the archived hub
# (Pagefreezer capture of 2024-08-01, links trimmed to four), the 2016 year page (Archive-It, old-site
# layout with a Spanish row), the 2019 year page (Pagefreezer, "Device Name | Date"), and a shortened
# old-site detail page (headings and paragraphs as published; body cut after the Purpose paragraph).
_A_HUB = '''<div role="main"><h3>Safety Communications</h3>
<ul>
	<li><a data-entity-substitution="canonical" data-entity-type="node" data-entity-uuid="ef6533a6-3eba-489c-ad3e-883802052e85" href="/medical-devices/safety-communications/2022-safety-communications" title="2022 Safety Communications">2022&nbsp;Safety Communications</a></li>
	<li><a href="https://public4.pagefreezer.com/browse/FDA/16-06-2022T13:39/https:/www.fda.gov/medical-devices/safety-communications/2019-safety-communications">2019 Safety Communications</a>&nbsp;(Archived)</li>
	<li><a href="http://wayback.archive-it.org/7993/20171114130910/https://www.fda.gov/MedicalDevices/Safety/AlertsandNotices/ucm553855.htm" target="_blank">2016 Safety Communications</a>&nbsp;(Archived)</li>
	<li><a href="http://wayback.archive-it.org/7993/20170722043349/https://www.fda.gov/MedicalDevices/Safety/AlertsandNotices/ucm559436.htm" target="_blank">Safety Communications Issued Prior to 2011</a>&nbsp;(Archived)</li>
</ul></div>'''
_A_2016 = '''<div role="main"><table width='100%' class='table table-striped tablesorter table-bordered' cellspacing='2' id='customTable'> <thead> <tr><th id='' name='' title='Click to Sort By Medical Device Safety Communication' scope='col'>Medical Device Safety Communication</th><th id='' name='' title='Click to Sort By Date' scope='col'data-type='numeric'>Date</th></tr> </thead> <tbody> <tr> <td> <a href=/MedicalDevices/Safety/AlertsandNotices/ucm534515.htm target='_self'><linktitle>FDA Warns Health Care Providers Against Relying Solely on Zika Virus Serological IgM Assay Results; Reminds them to Wait for Confirmatory Test Results Before Making Patient Management Decisions: FDA Safety Communication</linktitle></a> </td> <td data-value="20161222"> 12/22/16 </td> </tr> <!--displaySectionIncludeName=csfl_loop_nodes--> <tr> <td> <a href=/MedicalDevices/Safety/AlertsandNotices/ucm535502.htm target='_self'><linktitle>La FDA advierte a los proveedores de atención médica que no deben basarse únicamente en los resultados de la prueba serológica de IgM frente al virus del Zika; les recuerda que esperen los resultados de la prueba de confirmación antes de decidir el tratamiento del paciente: Comunicación de Seguridad de la FDA (en Español)</linktitle></a> </td> <td data-value="20161222"> 12/22/16 </td> </tr></tbody></table></div>'''
_A_2019 = '''<div role="main"><h3>2019 Safety Communications</h3> <table align="left" border="0" cellpadding="0" cellspacing="0" class="table table-responsive" style="width: 100%;" summary="2019 Medical Device Safety Communications" width="100%"> <thead> <tr> <th scope="col">Device Name</th> <th scope="col">Date</th> </tr> </thead> <tbody> <tr> <td><a data-entity-substitution="canonical" data-entity-type="node" data-entity-uuid="db83b5ce-23d9-4b57-b68a-8682602fc97b" href="/medical-devices/safety-communications/update-fda-warns-biotin-may-interfere-lab-tests-fda-safety-communication" title="UPDATE: The FDA Warns that Biotin May Interfere with Lab Tests: FDA Safety Communication">UPDATE: The FDA Warns that Biotin May Interfere with Lab Tests: FDA Safety Communication</a></td> <td>11/05/19</td> </tr></tbody></table></div>'''
_A_DETAIL = '''<title>FDA Warns Health Care Providers Against Relying Solely on Zika Virus Serological IgM Assay Results; Reminds them to Wait for Confirmatory Test Results Before Making Patient Management Decisions: FDA Safety Communication</title>
<meta name="dc.title" content="Safety Communications - FDA Warns Health Care Providers Against Relying Solely on Zika Virus Serological IgM Assay Results; Reminds them to Wait for Confirmatory Test Results Before Making Patient Management Decisions: FDA Safety Communication"/>
<div role="main"><h1>FDA Warns Health Care Providers Against Relying Solely on Zika Virus Serological IgM Assay Results; Reminds them to Wait for Confirmatory Test Results Before Making Patient Management Decisions: FDA Safety Communication</h1>
<h3>Date Issued:</h3><p>December 22, 2016</p><h3>Audiences:</h3><ul><li>Health care providers</li><li>Pregnant women potentially exposed to Zika</li></ul><h3>Medical Specialties:</h3><p>Primary care, Obstetrics and Gynecology, and Infectious Disease</p><h3>Product:</h3><p>The ZIKV Detect IgM Capture ELISA is manufactured by InBios International, Inc. The test is used for the preliminary (presumptive) detection of Zika virus IgM antibodies in human sera collected from patients with a history of clinical signs and symptoms associated with Zika virus infection.</p><h3>Purpose:</h3><p>The FDA is alerting physicians who care for pregnant women meeting CDC Zika virus clinical criteria and/or CDC Zika virus epidemiological criteria, that the U.S. commercial testing facility, Laboratory Corporation of America (LabCorp), has reported some false positive results from the ZIKV Detect test.</p>
Page Last Updated: 10/19/2017</li> --> Page Last Updated: 10/19/2017 <!-- </ul> --> <br/></div>'''


# Verbatim fragments read 2026-11-05 from Archive-It 7993 (whitespace trimmed): two links of the
# "Safety Communications Issued Prior to 2011" page (ucm559436.htm), the last row of "Archived
# Medical Device Safety Communications" (ucm463040.htm), a 1983 row of "Archived Public Health
# Notifications" (the date shown as 9/2/83, data-value 19830902) and the first item of "Archived
# Patient Alerts (Medical Devices)".
_P_HUB = '''<div role="main"><ul><li><a href="http://wayback.archive-it.org/7993/20170111070041/http:/www.fda.gov/MedicalDevices/Safety/AlertsandNotices/PatientAlerts/default.htm"><linktitle>Archived Patient Alerts (Medical Devices)</linktitle></a></li>
<li><a href="/MedicalDevices/Safety/AlertsandNotices/ucm463040.htm" ><linktitle>Archived Medical Device Safety Communications</linktitle></a>					<span style="margin-left:4px;">[ARCHIVED]</span></li>
<li><a href="http://wayback.archive-it.org/7993/20170111070037/http:/www.fda.gov/MedicalDevices/Safety/AlertsandNotices/TipsandArticlesonDeviceSafety/default.htm"><linktitle>Archived Tips and Articles on Device Safety</linktitle></a></li></ul></div>'''
_P_SC = '''<div role="main"><h2></h2><table width='100%' class='table table-striped tablesorter table-bordered' cellspacing='2' id='customTable'> <thead> <tr><th id='' name='' title='Click to Sort By Medical Device Safety Communication' scope='col'>Medical Device Safety Communication</th><th id='' name='' title='Click to Sort By Date' scope='col'data-type='numeric'>Date</th></tr> </thead> <tbody>
<tr>
<td>
<a href=/MedicalDevices/Safety/AlertsandNotices/ucm135362.htm target='_self'><linktitle>MRI Safety: FDA Safety Communication</linktitle></a> 					<span style="margin-left:4px;">[ARCHIVED]</span>
</td>
<td data-value="20010918">
09/18/01
</td>
</tr>
</tbody> </table></div>'''
_P_PHN = '''<div role="main"><table width='100%' class='table table-striped tablesorter table-bordered' cellspacing='2' id='customTable'> <thead> <tr><th id='' name='' title='Click to Sort By Title' scope='col'>Title</th><th id='' name='' title='Click to Sort By Date' scope='col'data-type='numeric'>Date</th></tr> </thead> <tbody>
<tr>
<td>
<a href=/MedicalDevices/Safety/AlertsandNotices/PublicHealthNotifications/ucm241839.htm target='_self'><linktitle>FDA Safety Alert - Breathing System Connectors</linktitle></a> 					<span style="margin-left:4px;">[ARCHIVED]</span>
</td>
<td data-value="19830902">
9/2/83
</td>
</tr>
</tbody> </table></div>'''
_P_PA = '''<div role="main"><p>Also available: <a href="/Safety/MedWatch/SafetyInformation/default.htm">additional FDA safety information from MedWatch</a></p>
<div class='panel panel-default box'><div class='panel-heading'><h2 class='panel-title'>List of Patient Alerts</h2></div><div class='panel-body'><p>List of Patient Alerts in descending date order</p><ul>
	<li><a href="/MedicalDevices/Safety/AlertsandNotices/PatientAlerts/ucm200896.htm" ><linktitle>Advice for Patients: Ear Candles</linktitle></a>					<span style="margin-left:4px;">[ARCHIVED]</span>

	<br />02/18/2010
	</li>
	<li><a href="/MedicalDevices/Safety/AlertsandNotices/PatientAlerts/default.htm">More in Patient Alerts (Medical Devices)</a></li>
</ul></div></div></div>'''


def _selftest_prior():
    hub = [(txt, next((t for rx, t in PRIOR_SECTIONS if rx.match(txt)), None), bool(PRIOR_NOT_READ.match(txt)))
           for _, txt in _links(_P_HUB)]
    assert hub == [("Archived Patient Alerts (Medical Devices)", "Patient Alert", False),
                   ("Archived Medical Device Safety Communications", "Safety Communication", False),
                   ("Archived Tips and Articles on Device Safety", None, True)], hub
    ref = archive_ref(_links(_P_HUB)[1][0], base=("ai", "20170722043349"))
    assert ref == ("ai", "20170722043349", SITE + "/MedicalDevices/Safety/AlertsandNotices/ucm463040.htm"), ref
    sc = parse_archived_listing(_P_SC)
    assert sc == [{"title": "MRI Safety: FDA Safety Communication", "href": "/MedicalDevices/Safety/AlertsandNotices/ucm135362.htm",
                   "date": "09/18/2001", "product_area": None}], sc
    phn = parse_archived_listing(_P_PHN)
    assert phn[0]["date"] == "09/02/1983" and phn[0]["title"] == "FDA Safety Alert - Breathing System Connectors", phn
    assert _norm_date("9/2/83") == "09/02/1983" and _norm_date("12/22/16") == "12/22/2016"
    pa = parse_archived_link_list(_P_PA, folder="PatientAlerts/")
    assert pa == [{"title": "Advice for Patients: Ear Candles",
                   "href": "/MedicalDevices/Safety/AlertsandNotices/PatientAlerts/ucm200896.htm",
                   "date": "02/18/2010", "product_area": None}], pa
    print("safety_comms.py before-2011 self-test PASSED")


def _selftest_archive():
    import tempfile
    _selftest_prior()
    links = _links(_A_HUB)
    refs = [(txt, archive_ref(h, base=("pf", "2024-08-01T13:49:00Z"))) for h, txt in links]
    assert refs[0][1] == ("pf", "2024-08-01T13:49:00Z", SITE + "/medical-devices/safety-communications/2022-safety-communications"), refs[0]
    assert refs[1][1] == ("pf", "2022-06-16T13:39:00Z", SITE + "/medical-devices/safety-communications/2019-safety-communications"), refs[1]
    assert refs[2][1] == ("ai", "20171114130910", SITE + "/MedicalDevices/Safety/AlertsandNotices/ucm553855.htm"), refs[2]
    assert [bool(_YEAR_TEXT["Safety Communication"].match(t)) for t, _ in refs] == [True, True, True, False]
    a16 = parse_archived_listing(_A_2016)
    assert len(a16) == 1 and a16[0]["date"] == "12/22/2016" and a16[0]["href"].endswith("ucm534515.htm"), a16
    a19 = parse_archived_listing(_A_2019)
    assert a19[0]["date"] == "11/05/2019" and a19[0]["title"].startswith("UPDATE: The FDA Warns that Biotin"), a19
    assert a19[0]["product_area"] is None                                # "Device Name" is the title column
    d = parse_detail(_A_DETAIL, archived=True)
    assert d["date_issued"] == "December 22, 2016" and d["current_as_of"] == "10/19/2017", d
    assert d["summary"].startswith("The FDA is alerting physicians") and d["title"].startswith("FDA Warns"), d
    # labels inside paragraphs (2011-2015 layout; constructed in that format)
    d2 = parse_detail('<div role="main"><p><strong>Date Issued:</strong> May 1, 2013</p><p><strong>Audience:</strong> '
                      'Health care providers who treat or manage patients with implanted pumps and the staff who support '
                      'them</p><p><strong>Specialties</strong> Nurses and Nurse Practitioners, Pharmacists, Physician '
                      'Assistants and Primary Care Physicians</p><p><strong>Purpose:</strong></p><p>The FDA is informing '
                      'health care providers of a problem that may cause the pump to stop delivering medicine.</p></div>',
                      archived=True)
    assert d2["summary"].startswith("The FDA is informing health care providers"), d2["summary"]
    assert _label("Specialties Plastic surgeons") == ("specialties", "Plastic surgeons") and _label("Devices that use x") is None
    assert archive_view_url(refs[2][1]) == "https://wayback.archive-it.org/7993/20171114130910/" + refs[2][1][2]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "archive" / "pages").mkdir(parents=True)
        (t / "pages" / "list_sc23.html").write_text(_SC2023, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"listings": [
            ["Safety Communication", SITE + "/medical-devices/safety-communications/2023-safety-communications",
             "list_sc23"]]}), encoding="utf-8")
        iref = archive_ref(a16[0]["href"], base=("ai", "20171114130910"))
        (t / "archive" / "pages" / "d1.html").write_text(_A_DETAIL, encoding="utf-8")
        dm = {"kind": "ai", "ts": "20171114130910", "original_url": iref[2], "page": "d1", "capture_ts": "20171114200638",
              "archive_url": "https://wayback.archive-it.org/7993/20171114200638/" + iref[2]}
        sc23 = parse_listing(_SC2023)[0][0]
        (t / "archive.json").write_text(json.dumps({"date": "2026-11-03", "items": [
            dict(a16[0], type="Safety Communication", year=2016, listing="x", ref=iref, detail=dm),
            dict(a19[0], type="Safety Communication", year=2019, listing="y",
                 ref=archive_ref(a19[0]["href"], base=("pf", "2022-06-16T13:39:00Z"))),
            {"title": sc23["title"], "href": sc23["url"], "date": sc23["date"], "product_area": "Syringes",
             "type": "Safety Communication", "year": None, "listing": "z", "ref": archive_ref(sc23["url"]),
             "fda_listed": True},
            dict(parse_archived_listing(_P_PHN)[0], type="Public Health Notification", year=1983, listing="w",
                 ref=archive_ref(parse_archived_listing(_P_PHN)[0]["href"], base=("ai", "20170111063821")))]}),
            encoding="utf-8")
        st = transform(t, t / "sc.ttl", retrieved_at="2026-10-28")
        ttl = (t / "sc.ttl").read_text(encoding="utf-8")
    assert st.records == 4, st                               # 2016 row (with page), 2019 row (no page), 2023 row, 1983 PHN
    phn = ttl.split(f"<{iri.safety_communication('ucm241839.htm')}>")[1].split("\n\n")[0]
    assert 'ex:communicationType "Public Health Notification"' in phn and 'ex:issueDate "1983-09-02"^^xsd:date' in phn, phn
    zika = ttl.split(f"<{iri.safety_communication('ucm534515.htm')}>")[1].split("\n\n")[0]
    assert 'ex:issueDate "2016-12-22"^^xsd:date' in zika and 'ex:contentCurrentAsOf "2017-10-19"^^xsd:date' in zika
    assert f'ex:archivedFrom "{iref[2]}"' in zika and 'ex:sourceUrl "https://wayback.archive-it.org/7993/20171114200638/' in zika
    assert "read from FDA's web archive copy (Archive-It collection 7993, captured 2017-11-14)" in zika, zika
    assert 'ex:retrievedAt "2026-11-03"^^xsd:date' in zika and "ex:summary \"The FDA is alerting physicians" in zika
    assert "La FDA advierte" not in ttl                                    # Spanish row skipped
    assert "the page could not be read from the archive" in ttl            # 2019 row whose page was not read
    syr = ttl.split("evaluating_plastic_syringes")[1].split("\n\n")[0]
    assert "ex:archivedFrom \"https://www.fda.gov/medical-devices/safety-communications/evaluating-plastic" in syr, syr
    assert ttl.count("rdfs:label") == st.records
    print("safety_comms.py archive self-test PASSED")


def _selftest():
    import tempfile
    _selftest_archive()
    items, years, arch = parse_listing(_HCP)
    assert len(items) == 2 and items[0]["date"] == "10/20/2026", items
    assert items[0]["product_area"].startswith("Ureterorenoscopes"), items[0]
    assert "/medical-devices/letters-health-care-providers/2023-letters-health-care-providers" in years
    assert arch and "pagefreezer" in arch[0]
    it23 = parse_listing(_HCP2023)[0]
    assert it23[0]["date"] == "12/27/2023" and it23[0]["title"].startswith("HSV-2"), it23
    sc23 = parse_listing(_SC2023)[0]
    assert sc23[0]["product_area"] == "Syringes" and not is_fda(sc23[0]["url"]), sc23
    assert key_of(sc23[0]["url"]) == "evaluating-plastic-syringes-made-china-potential-device-failures-fda-safety-communication"
    d = parse_detail(_DETAIL)
    assert d["date_issued"] == "October 20, 2026" and d["updates"] == ["10/20/2026", "06/24/2025"], d
    assert d["current_as_of"] == "10/26/2026" and d["product_codes"] == ["EOQ"] and d["k510"] == ["K123456"], d
    assert d["device_names"] == ["Bronchofiberscope", "Bronchovideoscope"], d
    assert d["dis"] == ["04953170062988", "04953170339974", "04953170288968"], d["dis"]
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "list_hcp.html").write_text(_HCP, encoding="utf-8")
        (t / "pages" / "list_sc23.html").write_text(_SC2023, encoding="utf-8")
        (t / "pages" / f"{_name(items[0]['url'])}.html").write_text(_DETAIL, encoding="utf-8")
        (t / "lists.json").write_text(json.dumps({"listings": [
            ["Letter to Health Care Providers", ROOTS[1][1], "list_hcp"],
            ["Safety Communication", SITE + "/medical-devices/safety-communications/2023-safety-communications",
             "list_sc23"]]}), encoding="utf-8")
        st = transform(t, t / "safety_comms.ttl", retrieved_at="2026-10-28")
        ttl = (t / "safety_comms.ttl").read_text(encoding="utf-8")
    assert st.records == 3 and st.skipped == 1, st
    olympus = iri.safety_communication(key_of(items[0]["url"]))
    assert f"<{olympus}>" in ttl and "/id/safety-communication/update_import_alert_certain_olympus" in olympus
    assert 'ex:issueDate "2026-10-20"^^xsd:date' in ttl and 'ex:updateDate "2025-06-24"^^xsd:date' in ttl
    assert 'ex:updateDate "2026-10-20"' not in ttl                      # the issue date is not an update
    assert 'ex:contentCurrentAsOf "2026-10-26"^^xsd:date' in ttl
    assert 'ex:communicationType "Letter to Health Care Providers"' in ttl
    assert f"ex:hasProductCode <{iri.product_code('EOQ')}>" in ttl and f"ex:mentionsK510Record <{iri.k510('K123456')}>" in ttl
    assert f"ex:namesDeviceIdentifier <{iri.udi_identifier('GS1', '04953170062988')}>" in ttl
    assert 'ex:deviceName "Bronchofiberscope"' in ttl and 'ex:productArea "Syringes"' in ttl
    assert "[PHONE REMOVED]" in ttl and "301-555-0100" not in ttl        # summary scrubbed
    assert "ex:dataQualityNote" in ttl and "Evaluating Plastic Syringes" in ttl
    assert ttl.count("rdfs:label") == st.records
    print(f"safety_comms.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
