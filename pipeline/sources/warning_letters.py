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
of the recipient block), FEI number(s), 510(k)/De Novo and PMA numbers, three-letter product codes
introduced by "product code", and the 21 CFR sections/parts cited. The letter body, the recipient name
and title, signatories, phone numbers and e-mail addresses are NEVER stored.

Firm link: ex:hasManufacturer iri.manufacturer(fei=...) when the letter shows an FEI, otherwise
ex:hasRecipientFirm iri.firm_name(normalize_org(company)).

LETTERS TO INDIVIDUALS: some letters (clinical investigators, sole individuals) carry a PERSON's name in
the "Company Name" column. is_individual() flags them (degree suffix such as "M.D.", a company name equal
to the page's recipient name, or a "Clinical Investigator" subject without an organization word); for
those the name, address, FEI, firm link and FDA URLs (which embed the name) are dropped, provenance points
at the listing page, and ex:dataQualityNote says why.

Minter: iri.warning_letter(key) -> .../id/warning-letter/<slug(key)>
        key = "<MARCS-CMS number>-<MMDDYYYY>" taken from the letter URL ("...-727888-08262026"),
        else the whole URL slug.   iri.warning_letter('727888-08262026') -> .../id/warning-letter/727888_08262026
"""
import html as htmllib
import json
import os
import re
import sys
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
                  # a following "21 CFR ..." or "21 U.S.C. ..." starts a new citation, not another section
                  r"(?:\d{1,4}(?:\.\d{1,4})?(?!\d|\s*(?:C\.?\s*F\.?\s*R|U\.?\s*S\.?\s*C))(?:\s*\([a-zA-Z0-9]{1,4}\))*"
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


def parse_letter(page):
    """Letter page HTML -> dict of the few fields we keep (never the body text)."""
    a = page.find('role="main"')
    main = page[a:] if a >= 0 else page
    e = main.find("Content current as of")
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
    c = _CMS.search(flat)
    out["cms"] = c.group(1) if c else None
    fei = []
    for m in _FEI.finditer(flat):
        fei += [x for x in re.findall(r"\d{7,10}", m.group(1)) if x not in fei]
    out["fei"] = fei
    out["k510"] = sorted(set(_K510.findall(flat)))
    out["pma"] = sorted({p for p, _ in _PMA.findall(flat)})
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


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    rows = [r for r in _rows(src) if is_device(r) and r["url"]]
    st = base.Stats(source=NAME)
    firms, codes, counts = {}, set(), {"with_page": 0, "with_fei": 0, "with_pc": 0, "with_sub": 0, "with_cfr": 0}
    keys = {}
    with base.TurtleWriter(out) as w:
        for r in sorted(rows, key=lambda r: letter_key(r["url"])):
            key = letter_key(r["url"])
            if key in keys:                                  # two URLs with the same CMS+date: keep full slug
                key = r["url"].rstrip("/").rsplit("/", 1)[-1]
            keys[key] = r["url"]
            p = src / "pages" / f"{_page_name(r['url'])}.html"
            d = parse_letter(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else None
            if d is None:
                st.skipped += 1                              # metadata still written below
            else:
                counts["with_page"] += 1
            subj = iri.warning_letter(key)
            # a letter to a person (clinical investigator, sole individual): no name, address, firm link,
            # FEI or URL (FDA's URLs embed the name); the record keeps dates, office, subject and citations
            person = is_individual(r["company"], r["subject"], (d or {}).get("_recipient_names", ()))
            counts["individual_withheld"] = counts.get("individual_withheld", 0) + person
            name = None if person else r["company"]
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
            m = _KEY.search(r["url"].rstrip("/"))
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
                counts["with_cfr"] += bool(d["cfr_sections"] or d["cfr_parts"])
            if not fei and name:
                nf = normalize_org(name)
                if nf:
                    pairs.append(("ex:hasRecipientFirm", f"<{iri.firm_name(nf)}>"))
                    firms.setdefault(nf, name)
            pairs += common.provenance(DATASET, key, source_url=LIST_PAGE if person else r["url"],
                                       retrieved_at=retrieved_at)
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


def _selftest():
    import tempfile
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
