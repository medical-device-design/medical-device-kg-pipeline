#!/usr/bin/env python3
"""
device_eua.py -- FDA Emergency Use Authorizations (EUAs) for medical devices -> RDF.

FDA publishes no downloadable EUA table for devices (checked October 28, 2026), so this
module reads the HTML pages. The tables are server-rendered (DataTables only adds
sorting in the browser), so no JavaScript is needed.

PAGES (start URL https://www.fda.gov/medical-devices/emergency-use-authorizations-medical-devices
answered 404 on October 28, 2026; the hub now lives under emergency-situations-medical-devices)
  main     .../emergency-situations-medical-devices/emergency-use-authorizations-medical-devices
           accordion entries for Zika, 2014 Ebola, 2013 coronavirus (MERS-CoV) and influenza
           A(H7N9) EUAs: heading "Test name (Holder) - Month D, YYYY", a paragraph "On <date>,
           the FDA issued ...", and a list of document links
  covid    .../emergency-use-authorizations-medical-devices/covid-19-emergency-use-authorizations-medical-devices
           (hub). Category pages are discovered from its links (blood purification, CRRT and
           hemodialysis, in vitro diagnostics (hub for 5 IVD pages), other devices, PPE, remote
           monitoring, respiratory assist, ventilators). Links with a #fragment are not followed,
           because FDA links the same IVD page under several slugs.
  mpox     .../monkeypox-mpox-emergency-use-authorizations-medical-devices
  hist     .../historical-information-about-device-emergency-use-authorizations (revoked EUAs)
  ffr      .../revoked-euas-non-niosh-approved-disposable-filtering-facepiece-respirators
Every table row is one record (ex:DeviceEUA). Columns are mapped by header text, so the
different table layouts share one parser; a header the parser does not know is kept as
ex:additionalField "header: value" and counted in device_eua.report.json.
NOT INGESTED: contact tables ("If you have a question about ... Send email to"; no e-mail
is kept), the narrative paragraphs of the main page, the PDF contents, and a holder written
as a person's name (one row on October 28, 2026: a ventilator splitter whose "Manufacturer"
is an individual physician); such a holder gets no literal and no firm node, and is
counted in device_eua.report.json as holders_withheld_as_person_names.

WHAT FDA DOES NOT PUBLISH ON THESE PAGES: the EUA number (EUA200001 ...). It appears only
inside the letters (PDF), which are linked, not read. Status is therefore taken from the
page and table a row sits in: "authorized" (current tables), "revoked" (historical page and
the revoked FFR page, with the revocation date when shown), "no longer authorized",
"withdrawn" or "removed from appendix" (tables FDA labels that way).

ex:euaListingType tells an individual EUA from a row in an umbrella EUA's appendix or
exhibit, a laboratory added under the CLIA high-complexity umbrella EUA, and a row of an
amendment exhibit (pooling/serial testing), which amends an existing EUA rather than
being a new one.

MINTERS (pipeline/iri.py):
    iri.device_eua(key): a device EUA row. FDA publishes no EUA number on its pages, so the key
        is the first 16 hex digits of sha1("page|section|holder|product") (ordinal suffix if a
        page repeats the same row).  -> .../id/device-eua/0123456789abcdef
    iri.eua_document(key): a document linked from an EUA (letter, fact sheet, IFU, summary,
        notice), keyed on FDA's media id when the link is www.fda.gov/media/<id>/download, else
        sha1(url)[:16].  iri.eua_document('media_165581') -> .../id/eua-document/media_165581
"""
import os
import re
import sys
import json
import time
import hashlib
import html as htmllib
import urllib.request
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
from identity import normalize_org   # noqa: E402

NAME = "device_eua"
DATASET = "cdrh/device-eua"
FDA = "https://www.fda.gov"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
_E = FDA + "/medical-devices/emergency-use-authorizations-medical-devices/"
COVID_PREFIX = "/medical-devices/covid-19-emergency-use-authorizations-medical-devices/"
SEED = [  # (key, url, kind, emergency)
    ("main", FDA + "/medical-devices/emergency-situations-medical-devices/emergency-use-authorizations-medical-devices",
     "main", None),
    ("covid-19", _E + "covid-19-emergency-use-authorizations-medical-devices", "hub", "COVID-19"),
    ("mpox", _E + "monkeypox-mpox-emergency-use-authorizations-medical-devices", "tables", "mpox"),
    ("historical", _E + "historical-information-about-device-emergency-use-authorizations", "tables", None),
    ("revoked-ffr", _E + "revoked-euas-non-niosh-approved-disposable-filtering-facepiece-respirators", "tables",
     "COVID-19"),
]
_LINK = re.compile(r'href="((?:https://www\.fda\.gov)?/medical-devices/'
                   r'(?:covid-19|coronavirus-disease-2019-covid-19)-emergency-use-authorizations-medical-devices/'
                   r'([a-z0-9-]+))"', re.I)
_DATE = r"\d{1,2}/\d{1,2}/\d{4}"
_LONG_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)"
                        r"\s+(\d{1,2}),\s*(\d{4})")
_MEDIA = re.compile(r"^https://www\.fda\.gov/media/(\d+)/download")


# ===========================================================================
#  Polite fetching: 1 request per second, retries with backoff, page cache
# ===========================================================================
class Fetcher:
    def __init__(self, pages, delay=1.0, max_age_hours=20):
        self.pages, self.delay, self.max_age = Path(pages), delay, max_age_hours * 3600
        self.pages.mkdir(parents=True, exist_ok=True)
        self._next = 0.0
        self.fetched = self.cached = 0

    def get(self, key, url, tries=5):
        """-> (html, final_url). A page fetched within max_age_hours is reused."""
        p = self.pages / f"{key}.html"
        meta = self.pages / f"{key}.url"
        if p.exists() and p.stat().st_size > 1000 and time.time() - p.stat().st_mtime < self.max_age:
            self.cached += 1
            return p.read_text(encoding="utf-8"), (meta.read_text(encoding="utf-8") if meta.exists() else url)
        err = None
        for i in range(tries):
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + self.delay
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=90) as r:
                    body, final = r.read().decode("utf-8", errors="replace"), r.geturl()
                tmp = p.with_suffix(".part")
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(p)
                meta.write_text(final, encoding="utf-8")
                self.fetched += 1
                return body, final
            except Exception as e:
                err = e
                code = getattr(e, "code", None)
                if code is not None and code < 500 and code != 429:
                    break                      # 404 and other refusals are reported, not retried
                time.sleep(2 ** (i + 1))
        raise RuntimeError(f"{url}: {err}")


def _discover(page):
    """COVID category / IVD page URLs linked from a hub page (no #fragment links, no FAQ)."""
    out = []
    for m in _LINK.finditer(page):
        end = m.end(1)
        if page[end:end + 1] == "#" or "faq" in m.group(2):
            continue
        out.append((m.group(2), FDA + COVID_PREFIX + m.group(2)))
    return out


def fetch(dest):
    dest = Path(dest)
    f = Fetcher(dest / "pages")
    plan, done, finals, errors = list(SEED), [], set(), []
    i = 0
    while i < len(plan):
        key, url, kind, emergency = plan[i]
        i += 1
        try:
            body, final = f.get(key, url)
        except Exception as e:
            errors.append(str(e))
            print(f"  device_eua: FAILED {url}: {e}")
            continue
        if final in finals:                     # a second slug for a page already read
            continue
        finals.add(final)
        done.append({"key": key, "url": url, "final_url": final, "kind": kind, "emergency": emergency})
        if key == "covid-19" or key == "in-vitro-diagnostics-euas":
            for slug_, u in _discover(body):
                if slug_ not in {p[0] for p in plan}:
                    plan.append((slug_, u, "tables", "COVID-19"))
    (dest / "pages.json").write_text(json.dumps(done, indent=1), encoding="utf-8")
    files = [base.FileRef(url=d["final_url"], path=f"pages/{d['key']}.html",
                          sha256=base.sha256_file(dest / "pages" / f"{d['key']}.html"),
                          bytes=(dest / "pages" / f"{d['key']}.html").stat().st_size) for d in done]
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)
    man.write(dest / "manifest.json")
    (dest / "fetch_log.json").write_text(json.dumps(
        {"date": base.today(), "pages": len(done), "fetched_now": f.fetched, "cached": f.cached,
         "errors": errors}, indent=1), encoding="utf-8")
    print(f"  device_eua: {len(done)} pages ({f.fetched} fetched now, {f.cached} cached), {len(errors)} errors")
    if errors:
        raise RuntimeError(f"{len(errors)} EUA pages could not be fetched: {errors}")
    return man


# ===========================================================================
#  Parsing helpers
# ===========================================================================
def _text(fragment):
    if fragment is None:
        return None
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = htmllib.unescape(t).replace("\xa0", " ")
    lines = [" ".join(ln.split()) for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln) or None


def _one(fragment):
    t = _text(fragment)
    return " ".join(t.split()) if t else None


def _none(v):
    return v is None or v.strip().lower() in ("", "none", "n/a")


def _long_date(s):
    m = _LONG_DATE.search(s or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _abs(href):
    h = htmllib.unescape(href.strip())
    if h.startswith("//"):
        return "https:" + h
    if h.startswith("/"):
        return FDA + h
    return h


def _links(fragment):
    """[(url, text, title)] for every real link in an HTML fragment (mailto and anchors dropped)."""
    out = []
    for m in re.finditer(r'(?is)<a\b([^>]*)>(.*?)</a>', fragment or ""):
        attrs = m.group(1)
        h = re.search(r'(?i)href="([^"]*)"', attrs)
        if not h or h.group(1).startswith(("mailto:", "#")) or not h.group(1).strip():
            continue
        t = re.search(r'(?i)title="([^"]*)"', attrs)
        out.append((_abs(h.group(1)), _one(m.group(2)), htmllib.unescape(t.group(1)).strip() if t else None))
    return out


def doc_type(text, title, url):
    s = f"{text or ''} {title or ''}".lower()
    if "federalregister.gov" in url:
        return "Federal Register Notice"
    if "revocation" in s or "revoked" in (text or "").lower():
        return "Revocation Letter"
    if re.search(r"letter granting|amendment|revision|reissu|clarification", s):
        return "Revision, Amendment or Clarification Letter"
    if re.search(r"letter of authori[sz]ation|authori[sz]ation letter|^authori[sz]ation\b|letter granting inclusion", s.strip()):
        return "Letter of Authorization"
    if re.search(r"fact ?sheet|\bhcp\b|^patients|^recipients|^individuals|healthcare providers|health care providers", s.strip()):
        return "Fact Sheet"
    if re.search(r"\bifu\b|instruction|package insert|manual", s):
        return "Instructions for Use"
    if "summary" in s:
        return "EUA Summary"
    return "Other"


def doc_key(url):
    m = _MEDIA.match(url)
    return f"media_{m.group(1)}" if m else hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def col_kind(h):
    h = re.sub(r"[\d*]+$", "", (h or "").lower()).strip()
    h = re.sub(r"\s+", " ", h)
    if h.startswith("date"):
        if "last updated" in h and "issued" in h:
            return "date_updated"
        if "last updated" in h:
            return "date_updated"
        if "withdrawal" in h:
            return "date_withdrawn"
        if "no longer" in h:
            return "date_no_longer"
        if "removed" in h:
            return "date_removed"
        if h in ("date", "date *", "date **"):
            return "date_events"
        return "date_issued"
    if h in ("entity", "manufacturer", "eua holder", "laboratory"):
        return "holder"
    if h.startswith("most recent letter") or h.startswith("fact sheet") or h in (
            "authorization documents", "other documents", "instructions for use", "eua summary"):
        return "docs"
    if h.startswith("diagnostic") or "letter of authorization" in h or h.startswith("letter granting inclusion"):
        return "product_letter"
    if h in ("medical product", "product name", "test name") or "product name" in h or "model(s)" in h:
        return "product"
    if h == "device type":
        return "device_type"
    if h == "device description":
        return "description"
    if h == "attributes":
        return "attributes"
    if h.startswith("authorized setting"):
        return "setting"
    if "product code" in h or "procode" in h:
        return "product_code"
    if "indication" in h:
        return "indication"
    if h == "intended use":
        return "intended_use"
    if h == "country of manufacture":
        return "country"
    if h.startswith("other brand name"):
        return "brands"
    return None


def listing_type(section, product):
    s = f"{section or ''} || {product or ''}".lower()
    if "laboratories certified under clia" in s:
        return "laboratory included under umbrella EUA"
    if "pooling" in s or "amendment" in s.split("||")[0]:
        return "amendment exhibit entry"
    if re.search(r"umbrella|appendix|exhibit", s):
        return "umbrella EUA or umbrella listing entry"
    return "individual EUA"


def _headings(page):
    """[(pos, level, text, raw)] for h1-h3."""
    return [(m.start(), int(m.group(1)), _one(m.group(2)) or "", m.group(0))
            for m in re.finditer(r"(?is)<h([1-3])\b[^>]*>(.*?)</h\1>", page)]


def _h1(page):
    for _, lvl, t, _r in _headings(page):
        if lvl == 1:
            return t
    return None


def parse_tables(page):
    """-> [dict] one per table body row: section, fields (kind -> raw HTML list), unknown (header -> text)."""
    heads = [h for h in _headings(page) if h[1] in (2, 3)]
    out = []
    for tm in re.finditer(r"(?is)<table\b.*?</table>", page):
        tbl = tm.group(0)
        thead = re.search(r"(?is)<thead\b.*?</thead>", tbl)
        hrow = thead.group(0) if thead else next((r for r in re.findall(r"(?is)<tr\b.*?</tr>", tbl) if "<th" in r.lower()), "")
        headers = [_one(c) or "" for c in re.findall(r"(?is)<th\b[^>]*>(.*?)</th>", hrow)]
        if not headers or headers[0].lower().startswith("if you have a question"):
            continue
        prev = [h for h in heads if h[0] < tm.start()]
        h2 = [h for h in prev if h[1] == 2]
        h3 = [h for h in prev if h[1] == 3 and (not h2 or h[0] > h2[-1][0])]
        section = " > ".join(x[-1][2] for x in (h2, h3) if x) or None
        body = tbl[thead.end():] if thead else tbl
        kinds = [col_kind(h) for h in headers]
        if "product" not in kinds and kinds[0] is None:
            kinds[0] = "product"     # e.g. "Personal Protective Equipment", "Decontamination System for PPE"
        if "product" in kinds:       # then a "Letter of Authorization" column holds documents only
            kinds = ["docs" if k == "product_letter" else k for k in kinds]
        for r in re.findall(r"(?is)<tr\b.*?</tr>", body):
            cells = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", r)
            if not cells:
                continue
            row = {"section": section, "fields": {}, "unknown": {}, "row_html": r}
            for h, k, c in zip(headers, kinds, cells):
                if k:
                    row["fields"].setdefault(k, []).append(c)
                elif not _none(_one(c)):
                    row["unknown"][h] = _one(c)
            out.append(row)
    return out


def parse_accordion(page):
    """Main-page entries: [dict(section, product, holder, updated, issued, body)]."""
    start = page.find("On This Page")
    m = re.search(r"(?is)<h2\b[^>]*>\s*Other Resources\s*</h2>", page)   # not the table-of-contents link
    stop = m.start() if m else -1
    heads = [h for h in _headings(page) if h[1] in (2, 3) and h[0] > max(start, 0)
             and (stop < 0 or h[0] < stop)]
    out, section = [], None
    for i, (pos, _lvl, t, raw) in enumerate(heads):
        is_entry = "data-parent" in raw or 'role="button"' in raw
        if not is_entry:
            section = t
            continue
        end = heads[i + 1][0] if i + 1 < len(heads) else (stop if stop > 0 else len(page))
        body = page[pos + len(raw):end]
        m = re.match(r"^(.*?)\s+[-–]\s+((?:January|February|March|April|May|June|July|August|September|"
                     r"October|November|December)\s+\d{1,2},\s*\d{4})\s*$", t)
        name, updated = (m.group(1), _long_date(m.group(2))) if m else (t, None)
        h = re.match(r"^(.*)\(([^()]+)\)\s*$", name)
        product, holder = (h.group(1).strip(), h.group(2).strip()) if h else (name.strip(), None)
        iss = re.search(r"On\s+((?:January|February|March|April|May|June|July|August|September|October|"
                        r"November|December)\s+\d{1,2},\s*\d{4}),\s+(?:the\s+)?FDA\s+issued", _one(body) or "")
        out.append({"section": section, "product": product, "holder": holder, "updated": updated,
                    "issued": _long_date(iss.group(1)) if iss else None, "body": body})
    return out


# ===========================================================================
#  Transform
# ===========================================================================
def _record_from_row(row, page_status):
    """Normalize one table row into a record dict."""
    f, rec = row["fields"], {"section": row["section"], "docs": [], "codes": [], "revisions": [],
                             "unknown": row["unknown"], "status": page_status}
    get = lambda k: _one(f[k][0]) if k in f and f[k] else None   # noqa: E731
    rec["holder"] = None if _none(get("holder")) else get("holder")
    letter = None
    if "product_letter" in f:
        cell = f["product_letter"][0]
        ls = _links(cell)
        first = re.split(r"(?i)<br\s*/?>", cell, maxsplit=1)
        rec["product"] = _one(first[0]) or (ls[0][1] if ls else None)
        rest = _one(first[1]) if len(first) > 1 else None
        if rest:
            dates = re.findall(_DATE, rest)
            if re.search(r"(?i)reissued", rest):
                rec["revisions"] += dates
            elif dates:
                rec["issued"] = dates[0]
        if ls:
            letter = ls[0]
            rec["docs"].append(ls[0] + ("Letter of Authorization",))
            rec["docs"] += [x + (None,) for x in ls[1:]]
    elif "product" in f:
        rec["product"] = _text(f["product"][0])
        rec["product"] = " | ".join(rec["product"].split("\n")) if rec["product"] else None
    rec["letter"] = letter
    if "date_issued" in f:
        rec["issued"] = get("date_issued")
    if "date_updated" in f:
        rec["updated"] = get("date_updated")
    for k, st in (("date_withdrawn", "withdrawn"), ("date_no_longer", "no longer authorized"),
                  ("date_removed", "removed from appendix")):
        if k in f:
            rec["ended"], rec["status"] = get(k), st
    if "date_events" in f:
        ev = _one(f["date_events"][0]) or ""
        for d, what in re.findall(r"(" + _DATE + r")\s*\(([^)]*)\)", ev):
            w = what.lower()
            if "revoked" in w:
                rec["ended"], rec["status"] = d, "revoked"
            elif "issued" in w and "reissued" not in w:
                rec["issued"] = d
            else:
                rec["revisions"].append(d)
    for k in ("device_type", "description", "attributes", "setting", "indication", "intended_use", "country"):
        v = _one(f[k][0]) if k in f else None
        rec[k] = None if _none(v) else v
    b = get("brands")
    rec["brands"] = None if _none(b) else b
    for c in f.get("product_code", []):
        rec["codes"] += [pc for pc in (common.product_code(x) for x in re.findall(r"\b[A-Za-z]{3}\b", _one(c) or "")) if pc]
    for c in f.get("docs", []) + f.get("date_events", []) + f.get("product", []):
        rec["docs"] += [x + (None,) for x in _links(c)]
    if rec["letter"] is None:
        rec["letter"] = next((d[:3] for d in rec["docs"] if doc_type(d[1], d[2], d[0]) == "Letter of Authorization"), None)
    return rec


_PERSON = re.compile(r"^(?:Dr|Mr|Mrs|Ms|Miss|Prof)\.?\s+\S|,?\s+(?:M\.?D\.?|Ph\.?D\.?|D\.?O\.?)$", re.I)


def _person_like(name):
    """A holder written as a person's name ("Dr. Bryan Lai" on the ventilator page). Such a
    holder is withheld (no literal, no firm node) because people are never ingested."""
    return bool(_PERSON.search((name or "").strip()))


def _page_status(key):
    return "revoked" if key in ("historical", "revoked-ffr") else "authorized"


def records(src):
    """-> [(page dict, record dict)] for every cached page, in page order."""
    src = Path(src)
    pages = json.loads((src / "pages.json").read_text(encoding="utf-8"))
    out = []
    for pg in pages:
        page = (src / "pages" / f"{pg['key']}.html").read_text(encoding="utf-8")
        pg = dict(pg, title=_h1(page))
        if pg["kind"] == "main":
            for e in parse_accordion(page):
                rec = {"section": e["section"], "product": e["product"], "holder": e["holder"],
                       "issued": e["issued"], "updated": e["updated"], "status": "authorized",
                       "docs": [], "codes": [], "revisions": [], "unknown": {}, "emergency": e["section"]}
                ls = _links(e["body"])
                for lk in ls:
                    t = doc_type(lk[1], lk[2], lk[0])
                    rec["docs"].append(lk + (t,))
                rec["letter"] = next((lk for lk in ls if doc_type(lk[1], lk[2], lk[0]) == "Letter of Authorization"), None)
                out.append((pg, rec))
        else:
            for row in parse_tables(page):
                rec = _record_from_row(row, _page_status(pg["key"]))
                if rec["status"] == "revoked" and not rec.get("ended"):
                    m = re.search(r"(?i)revoked effective\s+([A-Z][a-z]+\s+\d{1,2},\s*\d{4})", rec["section"] or "")
                    rec["ended"] = _long_date(m.group(1)) if m else None
                rec["emergency"] = pg.get("emergency")
                out.append((pg, rec))
    return out


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    recs = records(src)
    keys, firms, hub_codes, docs, unknown = {}, {}, set(), {}, {}
    by_status, by_page, by_type, withheld = {}, {}, {}, 0
    with base.TurtleWriter(out) as w:
        for pg, r in recs:
            product = common.norm_text(r.get("product"))
            if not product:
                st.skipped += 1
                continue
            key_holder = None if _person_like(r.get("holder")) else r.get("holder")
            raw_key = "|".join(x or "" for x in (pg["key"], r["section"], key_holder, product)).lower()
            n = keys.get(raw_key, 0) + 1
            keys[raw_key] = n
            key = hashlib.sha1((raw_key if n == 1 else f"{raw_key}|{n}").encode("utf-8")).hexdigest()[:16]
            holder = r.get("holder")
            if holder and _person_like(holder):
                holder = None                  # an individual named as manufacturer: never ingested
                withheld += 1
            nf = normalize_org(holder) if holder else None
            lt = listing_type(r["section"], product)
            pairs = [("a", "ex:DeviceEUA"),
                     ("rdfs:label", common.lit(f"{product} ({holder})" if holder else product)),
                     ("ex:deviceName", common.lit(product)),
                     ("ex:manufacturerName", common.lit(holder)),
                     ("ex:emergency", common.lit(r.get("emergency"))),
                     ("ex:euaCategory", common.lit(pg.get("title"))),
                     ("ex:euaSection", common.lit(r["section"])),
                     ("ex:euaListingType", common.lit(lt)),
                     ("ex:euaStatus", common.lit(r["status"])),
                     ("ex:euaIssueDate", common.date_lit(r.get("issued"))),
                     ("ex:euaLastUpdatedDate", common.date_lit(r.get("updated"))),
                     ("ex:euaEndDate", common.date_lit(r.get("ended")))]
            pairs += [("ex:euaRevisionDate", common.date_lit(d)) for d in dict.fromkeys(r.get("revisions") or [])]
            pairs += [("ex:euaDeviceType", common.lit(r.get("device_type"))),
                      ("ex:deviceDescription", common.lit(r.get("description"))),
                      ("ex:euaAttributes", common.lit(r.get("attributes"))),
                      ("ex:authorizedSetting", common.lit(r.get("setting"))),
                      ("ex:euaIndication", common.lit(r.get("indication"))),
                      ("ex:intendedUse", common.lit(r.get("intended_use"))),
                      ("ex:country", common.lit(r.get("country"))),
                      ("ex:otherBrandName", common.lit(r.get("brands")))]
            for h, v in sorted(r["unknown"].items()):
                pairs.append(("ex:additionalField", common.lit(f"{h}: {v}")))
                unknown[h] = unknown.get(h, 0) + 1
            for pc in dict.fromkeys(r["codes"]):
                pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
                hub_codes.add(pc)
            if nf:
                pairs.append(("ex:hasManufacturerFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, holder)
            if r.get("letter"):
                pairs.append(("ex:letterOfAuthorization", f"<{iri.eua_document(doc_key(r['letter'][0]))}>"))
            for url, text, title, typ in r["docs"]:
                dk = doc_key(url)
                if dk not in docs:
                    # a bare link text such as "IFU" is labeled with the product it belongs to
                    lab = title or (f"{text} - {product}" if text and len(text) < 40 else text)
                    docs[dk] = (url, lab, typ or doc_type(text, title, url))
                pairs.append(("ex:hasEUADocument", f"<{iri.eua_document(dk)}>"))
            pairs += common.provenance(DATASET, key, source_url=pg["final_url"], retrieved_at=retrieved_at)
            w.block(iri.device_eua(key), list(dict.fromkeys(p for p in pairs if p[1] is not None)))
            st.records += 1
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
            by_page[pg["key"]] = by_page.get(pg["key"], 0) + 1
            by_type[lt] = by_type.get(lt, 0) + 1
        for dk in sorted(docs):
            url, lab, typ = docs[dk]
            w.block(iri.eua_document(dk), [("a", "ex:EUADocument"), ("rdfs:label", common.lit(lab or typ)),
                                           ("ex:documentType", common.lit(typ)), ("ex:url", common.lit(url))])
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hub_codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    rep = {"records": st.records, "skipped": st.skipped, "by_page": by_page, "by_status": by_status,
           "by_listing_type": by_type, "holders_withheld_as_person_names": withheld, "documents": len(docs), "firms": len(firms),
           "product_codes": sorted(hub_codes), "unknown_headers": unknown}
    Path(out).with_suffix(".report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"  device_eua: {st.records} EUA rows, {len(docs)} documents, {len(firms)} firms; status {by_status}; "
          f"unknown headers {unknown or 'none'}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- rows copied from the FDA pages on October 28, 2026 (data-entity-*,
#  class and style attributes removed; cell content verbatim)
# ===========================================================================
_ANTIGEN = """<h1>In Vitro Diagnostics EUAs - Antigen Diagnostic Tests for SARS-CoV-2</h1>
<h2>Individual EUAs for Antigen Diagnostic Tests for SARS-CoV-2</h2>
<table><thead><tr><th>Date EUA Issued or Last Updated</th><th>Entity</th><th>Diagnostic (Most Recent Letter of Authorization) and Date EUA Originally Issued</th><th>Attributes</th><th>Authorized Setting(s) <sup>1</sup></th><th>Authorization Documents <sup>2</sup></th><th>Other Documents</th><th>Other Brand Name(s)</th></tr></thead><tbody>
<tr><td>06/16/2023</td><td>PHASE Scientific International, Ltd.</td><td><a href="/media/156956/download?attachment" title="PHASE OTCAg - Letter of Authorization">INDICAID COVID-19 Rapid Antigen At-Home Test</a><br>03/16/2022</td><td>Lateral Flow, Visual Read, Over the Counter (OTC) Home Testing, Serial Screening, Single Target</td><td>Home, H, M, W</td><td><a href="/media/156953/download?attachment" title="PHASE OTCAg Fact Sheet for Health Care Providers">HCP</a>, <a href="/media/156954/download?attachment" title="PHASE OTCAg - Instructions for Use">IFU</a>, <a href="/media/156955/download?attachment" title="PHASE OTCAg - Instructions for Use Home Test">IFU (Home Test)</a></td><td><ul><li><a href="/media/158408/download?attachment" title="INDICAID COVID-19 Rapid Antigen At-Home Test - Letter Granting EUA Revision(s) (May 12, 2022)">Letter Granting EUA Revision(s) (May 12, 2022)</a></li></ul></td><td>None</td></tr>
</tbody></table>
<table><thead><tr><th>If you have a question about:</th><th>Send email to:</th></tr></thead>
<tr><td>Masks, including surgical masks</td><td><a href="mailto:CDRH-COVID19-SurgicalMasks@fda.hhs.gov">CDRH-COVID19-SurgicalMasks@fda.hhs.gov</a></td></tr></table>"""

_VENT = """<h1>Ventilators and Ventilator Accessories EUAs</h1><h3>Ventilators Removed from Appendix B</h3>
<table><thead><tr><th>Date No Longer Authorized</th><th>Manufacturer</th><th>Ventilator Model(s) No longer Authorized</th><th>Device Description</th><th>Assigned Product Code</th></tr></thead>
<tr role="row"><td>06/21/2023</td><td>GE Healthcare</td><td>pNeuton Model A-E Ventilator</td><td>Ventilator, Continuous, Facility Use</td><td>QOS</td></tr></table>
<h3>Ventilator Accessories Removed from Appendix B</h3>
<table><thead><tr><th>Date Removed from Appendix B</th><th>Manufacturer</th><th>Ventilator Tubing Connector Model(s) No Longer Authorized</th></tr></thead>
<tr role="row"><td>02/09/2021</td><td>Dr. Bryan Lai</td><td>VentSplitter Emergency Ventilator Circuit Splitter and Flow Limiters</td></tr></table>"""

_HIST = """<h1>Historical Information about Device Emergency Use Authorizations</h1><h2>Other Devices</h2>
<table><thead><tr><th>Medical Product</th><th>Date **</th><th>Most Recent Letter of Authorization and Other EUA-Related Information</th><th>Fact Sheets and Manufacturer Instructions / Package Insert</th></tr></thead>
<tr><td>ExThera Medical Corporation Seraph 100 Microbind Affinity Blood Filter</td><td>04/17/2020 (issued)<br>11/24/2025 (revoked)</td><td><ul><li><a href="/media/137101/download?attachment" title="ExThera Medical Corporation Extracorporeal Blood Purification (EBP) Device - Letter of Authorization">Letter of Authorization</a> (04/17/2020) (PDF, 280KB)</li><li><a href="/media/190175/download?attachment" title="ExThera Medical Corporation Extracorporeal Blood Purification (EBP) Device - Revocation Letter">Revocation Letter</a> (11/24/2025) (PDF, 157KB)</li></ul></td><td><ul><li><a href="/media/137102/download?attachment" title="ExThera Medical Corporation Extracorporeal Blood Purification (EBP) Device - Fact Sheet for Healthcare Providers">Fact Sheet for Healthcare Providers</a> (PDF, 169KB)</li></ul></td></tr></table>"""

_MAIN = """<h2>On This Page:</h2><ul><li><a href="#ebola">2014 Ebola Virus Emergency Use Authorizations</a></li><li><a href="#other">Other Resources</a></li></ul><hr><h2>2014 Ebola Virus Emergency Use Authorizations</h2><p>intro</p>
<div open="no"><div role="tab"><h2>
 <a role="button" data-parent="#accordion" href="#6ae1cb81ac2ab" aria-controls="6ae1cb81ac2ab">EZ1 Real-time RT-PCR Assay (DoD) - October 10, 2014</a>
 </h2></div><div><div>
 <p>On August 5, 2014, the FDA issued an Emergency Use Authorization (EUA) to authorize the emergency use of the U.S. Department of Defense (DoD) EZ1 Real-time RT-PCR Assay for the presumptive detection of Ebola Zaire virus.</p><ul><li><a href="/media/89984/download" target>Letter of Authorization</a></li><li><a href="/media/89986/download" target>Fact Sheet for Healthcare Providers</a></li><li><a href="/media/89989/download" target>Manufacturer Instructions/Package Insert</a></li></ul><p>
 </p></div></div></div><h2>Other Resources</h2>"""


def _selftest():
    import tempfile
    assert col_kind("Authorized Setting(s) 1") == "setting" and col_kind("Date **") == "date_events"
    assert col_kind("Date EUA Issued or Last Updated") == "date_updated" and col_kind("Entity") == "holder"
    assert col_kind("Most Recent Letter of Authorization and Other EUA-Related Information") == "docs"
    assert col_kind("PPE (Letter of Authorization)") == "product_letter" and col_kind("Procode Assigned") == "product_code"
    assert doc_type("HCP", "x Fact Sheet", "u") == "Fact Sheet" and doc_type("Revocation Letter", None, "u") == "Revocation Letter"
    assert doc_key("https://www.fda.gov/media/156956/download?attachment") == "media_156956"
    assert len(parse_tables("<p>" + "x" * 5000 + "</p>" + _ANTIGEN)) == 1      # table far into the page
    assert len(parse_accordion(_MAIN)) == 1
    ppe = parse_tables("<h2>Personal Protective Equipment (PPE)</h2><table><thead><tr><th>Personal Protective "
                       "Equipment</th><th>Date **</th></tr></thead><tr><td>Face Shields</td><td>04/09/2020 (issued)"
                       "</td></tr></table>")
    assert ppe[0]["fields"]["product"] == ["Face Shields"] and not ppe[0]["unknown"], ppe
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        pages = []
        for key, body, kind, em in (("in-vitro-diagnostics-euas-antigen", _ANTIGEN, "tables", "COVID-19"),
                                    ("ventilators", _VENT, "tables", "COVID-19"),
                                    ("historical", _HIST, "tables", None), ("main", _MAIN, "main", None)):
            (t / "pages" / f"{key}.html").write_text(body, encoding="utf-8")
            pages.append({"key": key, "url": f"{FDA}/{key}", "final_url": f"{FDA}/{key}", "kind": kind, "emergency": em})
        (t / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-28", files=[]).write(t / "manifest.json")
        st = transform(t, t / "device_eua.ttl")
        ttl = (t / "device_eua.ttl").read_text(encoding="utf-8")
        rep = json.loads((t / "device_eua.report.json").read_text(encoding="utf-8"))
    assert st.records == 5 and st.skipped == 0, st
    assert rep["by_status"] == {"authorized": 2, "no longer authorized": 1, "revoked": 1,
                                "removed from appendix": 1}, rep
    assert "Bryan" not in ttl and rep["holders_withheld_as_person_names"] == 1     # a person: never ingested
    assert 'rdfs:label "VentSplitter Emergency Ventilator Circuit Splitter and Flow Limiters"' in ttl
    assert 'rdfs:label "INDICAID COVID-19 Rapid Antigen At-Home Test (PHASE Scientific International, Ltd.)"' in ttl
    assert 'ex:euaIssueDate "2022-03-16"^^xsd:date' in ttl and 'ex:euaLastUpdatedDate "2023-06-16"^^xsd:date' in ttl
    assert 'ex:euaAttributes "Lateral Flow, Visual Read, Over the Counter (OTC) Home Testing, Serial Screening, Single Target"' in ttl
    assert 'ex:authorizedSetting "Home, H, M, W"' in ttl and 'ex:emergency "COVID-19"' in ttl
    assert f"ex:letterOfAuthorization <{iri.eua_document('media_156956')}>" in ttl
    assert f"<{iri.eua_document('media_156954')}>\n    a ex:EUADocument" in ttl
    assert 'ex:documentType "Instructions for Use"' in ttl and 'ex:documentType "Revision, Amendment or Clarification Letter"' in ttl
    assert 'ex:url "https://www.fda.gov/media/156956/download?attachment"' in ttl
    assert "mailto" not in ttl and "SurgicalMasks" not in ttl and "ex:otherBrandName" not in ttl
    assert 'ex:euaStatus "no longer authorized"' in ttl and 'ex:euaEndDate "2023-06-21"^^xsd:date' in ttl
    assert f"ex:hasProductCode <{iri.product_code('QOS')}>" in ttl and f"<{iri.product_code('QOS')}>\n    a ex:ProductCode" in ttl
    assert 'ex:deviceDescription "Ventilator, Continuous, Facility Use"' in ttl
    assert f"ex:hasManufacturerFirm <{iri.firm_name(normalize_org('GE Healthcare'))}>" in ttl
    assert 'ex:euaStatus "revoked"' in ttl and 'ex:euaEndDate "2025-11-24"^^xsd:date' in ttl
    assert 'ex:euaIssueDate "2020-04-17"^^xsd:date' in ttl and 'ex:documentType "Revocation Letter"' in ttl
    assert 'rdfs:label "EZ1 Real-time RT-PCR Assay (DoD)"' in ttl and 'ex:emergency "2014 Ebola Virus Emergency Use Authorizations"' in ttl
    assert 'ex:euaIssueDate "2014-08-05"^^xsd:date' in ttl and 'ex:euaLastUpdatedDate "2014-10-10"^^xsd:date' in ttl
    assert f"ex:letterOfAuthorization <{iri.eua_document('media_89984')}>" in ttl
    assert 'ex:euaListingType "individual EUA"' in ttl
    print(f"device_eua.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
