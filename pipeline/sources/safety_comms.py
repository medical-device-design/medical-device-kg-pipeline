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
  NOT FOLLOWED: "2011-2022 Safety Communications (Archived)" and "2016-2022 Letters to Health Care
  Providers (Archived)" point to FDA's web archive on public4.pagefreezer.com, and a few 2023 rows
  link to archived copies there too. Archive hosts are out of scope for this pipeline, so those
  listings are not read, and an archived row is kept from the fda.gov listing only (title, date,
  product area, link) with an ex:dataQualityNote saying its page was not fetched.

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


def parse_detail(page):
    out = {"title": None, "date_issued": None, "updates": [], "current_as_of": None, "summary": None,
           "device_names": [], "brand_names": [], "product_codes": [], "k510": [], "pma": [], "dis": []}
    m = re.search(r'<meta name="dcterms.title" content="([^"]*)"', page)
    out["title"] = _one(m.group(1)) if m else None
    main = _main(page)
    e = main.find("Content current as of")
    if e > 0:
        tail = main[e:e + 600]
        cm = _USDATE.search(_one(tail) or "")
        out["current_as_of"] = cm.group(1) if cm else None
        main = main[:e]
    text = _cfweb.text(main) or ""
    flat = " ".join(text.split())
    dm = re.search(r"Date Issued:?\s*" + _LONGDATE.pattern, flat)
    if dm:
        out["date_issued"] = dm.group(1)
    else:                                              # letters: a bare long date right under the heading
        lm = _LONGDATE.search(flat[:600])
        out["date_issued"] = lm.group(1) if lm else None
    # lead paragraph: the first substantial <p> of the article (FDA-written)
    for p in re.findall(r"(?s)<p\b[^>]*>(.*?)</p>", main):
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


def fetch(dest, delay=1.0, log=print, list_max_age_days=0.5):
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


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    items = _items(src)
    st = base.Stats(source=NAME)
    hits, codes = {}, set()
    counts = {"detail": 0, "archived_only": 0, "with_pc": 0, "with_sub": 0, "with_di": 0, "with_names": 0}
    with base.TurtleWriter(out) as w:
        for url in sorted(items, key=key_of):
            it = items[url]
            key = key_of(url)
            d = None
            p = src / "pages" / f"{_name(url)}.html"
            if is_fda(url) and p.exists():
                d = parse_detail(p.read_text(encoding="utf-8", errors="replace"))
                counts["detail"] += 1
            title = it["title"] or (d or {}).get("title")
            issued = _iso(it["date"]) or _iso((d or {}).get("date_issued"))
            pairs = [("a", "ex:SafetyCommunication"),
                     ("rdfs:label", common.lit(title)),
                     ("ex:issueDate", common.date_lit(issued))]
            pairs += [("ex:communicationType", common.lit(t)) for t in it["types"]]
            pairs += [("ex:url", common.lit(url)),
                      ("ex:productArea", common.lit(it["product_area"]))]
            if not is_fda(url):
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
            pairs += common.provenance(DATASET, key, source_url=url, retrieved_at=retrieved_at)
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


def _selftest():
    import tempfile
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
