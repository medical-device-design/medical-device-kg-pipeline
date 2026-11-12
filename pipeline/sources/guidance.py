#!/usr/bin/env python3
"""
guidance.py -- FDA guidance documents for medical devices -> RDF (v0.0.7).

SOURCE
------
FDA's "Search for FDA Guidance Documents" page
    https://www.fda.gov/regulatory-information/search-fda-guidance-documents
fills its table from a JSON export that the page itself loads (DataTables ajax source,
verified 2026-10-28 in the page's footer script):
    https://www.fda.gov/files/api/datatables/static/search-for-guidance.json
2,796 rows on 2026-10-28, one per guidance, every FDA center. Fields used:
    title (HTML link to the guidance page), field_associated_media_2 (PDF link),
    field_issue_datetime (MM/DD/YYYY), field_issuing_office_taxonomy (offices,
    "<br><br>"-separated, each a ","-joined path), field_center, topics
    (field_topics + term_node_tid), field_final_guidance_1 (Final/Draft),
    open-comment, field_comment_close_date, field_docket_number (regulations.gov link),
    field_communication_type, field_regulated_product_field, changed.

SCOPE: rows whose center or issuing offices include the Center for Devices and
Radiological Health, plus rows from the Center for Biologics Evaluation and Research
labeled "Medical Devices" or "Radiation-Emitting Products" (605 rows on 2026-10-28).

Each guidance page is fetched once (1 request/second, cached; re-fetched only when the
listing's "changed" time is newer than the cached copy) for the clean Topic(s) and
Regulated Product(s) lists, the "Content current as of" date and the download link,
and for the 21 CFR sections, product codes and standard designations that FDA's page
summary mentions. Only those identifiers are kept; the page prose is not ingested,
and the PDF is linked, never downloaded or parsed. No person names, phone numbers or
e-mail addresses are ingested.

OUTPUT (one node per guidance page)
    <guidance-document/<page slug>> a ex:GuidanceDocument
        rdfs:label, ex:guidanceDocumentTitle, ex:guidanceDocumentUrl (page),
        ex:downloadUrl + ex:fileFormat (the PDF link, as listed),
        ex:issueDate, ex:guidanceStatus, ex:issuingOffice*, ex:fdaCenter, ex:topic*,
        ex:regulatedProduct*, ex:documentType, ex:docketNumber, ex:openForComment,
        ex:commentClosingDate, ex:contentCurrentAsOf,
        ex:citesCfrSection* -> CFRTitle21Section, ex:citesCfrPart* -> CfrPart
        (the same properties warning_letters.py uses),
        ex:hasProductCode* -> ProductCode, ex:mentionedStandardDesignation*
    plus typed hub stubs for every product code, CFR section and CFR part referenced.

MINTER: iri.guidance_document(page_slug), keyed on the last path segment of the fda.gov page.
    iri.guidance_document('infusion-pumps-total-product-life-cycle')
    -> .../id/guidance-document/infusion_pumps_total_product_life_cycle
"""
import os
import re
import sys
import json
import html as htmllib
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
import _cfweb     # noqa: E402

NAME = "guidance"
DATASET = "fda/guidance-documents"
SITE = "https://www.fda.gov"
SEARCH_URL = SITE + "/regulatory-information/search-fda-guidance-documents"
LIST_URL = SITE + "/files/api/datatables/static/search-for-guidance.json"
LIST_FILE = "search-for-guidance.json"
CDRH = "Center for Devices and Radiological Health"
CBER = "Center for Biologics Evaluation and Research"
DEVICE_PRODUCTS = ("Medical Devices", "Radiation-Emitting Products")
EXPECTED = ["title", "field_issue_datetime", "field_final_guidance_1", "field_docket_number",
            "field_issuing_office_taxonomy", "field_center"]

_HREF = re.compile(r'href="([^"]+)"')
_TAGS = re.compile(r"(?s)<[^>]+>")
_CFR_SEC = re.compile(r"21\s*C\.?F\.?R\.?\s*(?:§+|[Ss]ections?|[Ss]ec\.)?\s*(\d{1,4}\.\d+[a-z]?)")
_CFR_PART = re.compile(r"21\s*C\.?F\.?R\.?\s*[Pp]arts?\s*(\d{1,4})(?![\d.])")
_PC_PHRASE = re.compile(r"[Pp]roduct\s+[Cc]odes?\s*:?\s*((?:[A-Z]{3}\b(?:\s*(?:,|and|or|/|;)\s*)?)+)")
_STD = re.compile(r"\b((?:ANSI/)?(?:AAMI|ISO|IEC|ASTM|UL|IEEE|NEMA|CLSI)(?:/(?:ISO|IEC|ANSI|AAMI))*"
                  r"\s+(?:[A-Z]{1,3}\s?)?\d[\w.:-]*\d)")


# ===========================================================================
#  Fetch
# ===========================================================================
class _Fetcher(_cfweb.Fetcher):
    """_cfweb's polite fetcher (UA, 1 request/second, retries, atomic cache), storing pages
    under their own names (the listing is JSON, not HTML)."""
    def path(self, name):
        return self.pages / _cfweb.safe_name(name)


def in_scope(r):
    org = (r.get("field_center") or "") + " " + (r.get("field_issuing_office_taxonomy") or "")
    if CDRH in org:
        return True
    prods = htmllib.unescape(r.get("field_regulated_product_field") or "")
    return CBER in org and any(p in prods for p in DEVICE_PRODUCTS)


def page_url(r):
    m = _HREF.search(r.get("title") or "")
    if not m:
        return None
    u = htmllib.unescape(m.group(1)).strip()
    return SITE + u if u.startswith("/") else u


def page_slug(url):
    return url.rstrip("/").rsplit("/", 1)[-1]


def _changed_epoch(r):
    m = re.search(r'datetime="([^"]+)"', r.get("changed") or "")
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1)).timestamp()
    except ValueError:
        return None


def load_listing(src):
    rows = json.loads((Path(src) / "pages" / LIST_FILE).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("guidance listing is not a non-empty JSON array")
    missing = [k for k in EXPECTED if k not in rows[0]]
    if missing:          # schema drift must stop the build, not silently drop fields
        raise ValueError(f"guidance listing fields changed; missing {missing}")
    return rows


def fetch(dest, delay=1.0, limit=None, details=True):
    dest = Path(dest)
    f = _Fetcher(dest, delay=delay)
    # the listing changes daily: refetch when the cached copy is older than a day
    f.get(LIST_URL, LIST_FILE, max_age_days=1)
    rows = [r for r in load_listing(dest) if in_scope(r)]
    errors = []
    if details:
        todo = rows[:limit] if limit else rows
        for r in todo:
            url = page_url(r)
            if not url:
                continue
            name = "detail/" + page_slug(url) + ".html"
            p = f.pages / "detail" / _cfweb.safe_name(page_slug(url) + ".html")
            p.parent.mkdir(parents=True, exist_ok=True)
            ch = _changed_epoch(r)
            fresh = p.exists() and p.stat().st_size > 1000 and (ch is None or p.stat().st_mtime >= ch)
            if fresh:
                f.cached += 1
                continue
            try:
                f.get(url, name)
                # Fetcher.get wrote pages/<safe_name(name)>; move it into detail/
                src_p = f.path(name)
                if src_p != p and src_p.exists():
                    os.replace(src_p, p)
            except _cfweb.Unavailable as e:
                errors.append(f"{url}: {e}")
    (dest / "fetch_log.json").write_text(json.dumps({
        "date": base.today(), "in_scope": len(rows), "fetched_now": f.fetched,
        "cached": f.cached, "errors": errors}, indent=1), encoding="utf-8")
    print(f"  guidance: {len(rows)} device guidances; fetched {f.fetched}, cached {f.cached}, "
          f"{len(errors)} errors")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])


# ===========================================================================
#  Parse
# ===========================================================================
def _txt(fragment):
    return _cfweb.one_line(fragment)


def _offices(value):
    """'A<br><br>B,Office X,Office Y' -> ['A', 'B > Office X > Office Y']. Inside one office
    path FDA joins levels with a bare ',' (office names themselves use ', ')."""
    out = []
    for part in re.split(r"(?i)<br\s*/?>\s*(?:<br\s*/?>)?", value or ""):
        t = htmllib.unescape(_TAGS.sub("", part)).strip()
        if t:
            out.append(" > ".join(x.strip() for x in re.split(r",(?! )", t) if x.strip()))
    return out


def _split_list(value):
    t = htmllib.unescape(_TAGS.sub("", value or ""))
    return [x.strip() for x in t.split(",") if x.strip()]


def _us_date(s):
    s = (s or "").strip()
    return common.date_lit(s) if s else None


def parse_row(r):
    url = page_url(r)
    title = _txt(r.get("title"))
    media = r.get("field_associated_media_2") or ""
    m = _HREF.search(media)
    pdf = (SITE + htmllib.unescape(m.group(1))) if m and m.group(1).startswith("/") else (m.group(1) if m else None)
    pdf_kind = None
    if m:
        k = re.match(r"\s*([A-Za-z]+)\s*\(", _txt(media) or "")
        pdf_kind = k.group(1).upper() if k else None
    dk = _txt(r.get("field_docket_number"))
    if dk and dk.lower().startswith("none"):
        dk = None
    topics = []
    for t in _split_list(r.get("field_topics")) + _split_list(r.get("term_node_tid")):
        if t not in topics:
            topics.append(t)
    ch = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', r.get("changed") or "")
    return {
        "url": url, "title": title, "pdf": pdf, "pdf_kind": pdf_kind,
        "issue": (r.get("field_issue_datetime") or "").strip(),
        "status": (r.get("field_final_guidance_1") or "").strip() or None,
        "offices": _offices(r.get("field_issuing_office_taxonomy")),
        "center": htmllib.unescape(r.get("field_center") or "").strip() or None,
        "topics": topics,
        "products": _split_list(r.get("field_regulated_product_field")),
        "open": (r.get("open-comment") or "").strip() or None,
        "close": (r.get("field_comment_close_date") or "").strip(),
        "docket": dk,
        "type": htmllib.unescape(r.get("field_communication_type") or "").strip() or None,
        "changed": ch.group(1) if ch else None,
    }


def _li_list(html, heading):
    m = re.search(r"(?is)<h2[^>]*>\s*" + re.escape(heading) + r"\s*</h2>\s*<ul[^>]*>(.*?)</ul>", html)
    return [x for x in (_txt(li) for li in re.findall(r"(?is)<li[^>]*>(.*?)</li>", m.group(1)))
            if x] if m else []


def parse_detail(html):
    """-> dict(topics, products, current_as_of, download, cfr_sections, cfr_parts, product_codes,
    standards). Identifiers come only from FDA's page summary (between the header block and
    'Submit Comments'); no prose is returned."""
    d = {}
    d["topics"] = _li_list(html, "Topic(s)")
    d["products"] = _li_list(html, "Regulated Product(s)")
    m = re.search(r'(?is)Content current as of:.*?<time datetime="(\d{4}-\d{2}-\d{2})', html)
    d["current_as_of"] = m.group(1) if m else None
    m = re.search(r'(?is)<div class="lcds-toolbar[^"]*">.*?<a [^>]*href="([^"]+)"', html)
    d["download"] = (SITE + m.group(1) if m and m.group(1).startswith("/") else (m.group(1) if m else None))
    body = html
    i = body.find('role="main"')
    if i >= 0:
        body = body[i:]
    j = body.find("Submit Comments")
    if j >= 0:
        body = body[:j]
    body = re.sub(r"(?is)<dl class=\"lcds-description-list--grid\">.*?</dl>", " ", body)
    text = htmllib.unescape(_TAGS.sub(" ", body)).replace("\xa0", " ")
    text = " ".join(text.split())
    secs, parts, pcs, stds = [], [], [], []
    for s in _CFR_SEC.findall(text):
        s = re.sub(r"[a-z]$", "", s)
        if s not in secs:
            secs.append(s)
    for p in _CFR_PART.findall(text):
        if p not in parts:
            parts.append(p)
    for grp in _PC_PHRASE.findall(text):
        for c in re.findall(r"\b[A-Z]{3}\b", grp):
            if c not in pcs:
                pcs.append(c)
    for s in _STD.findall(text):
        s = s.rstrip(".:-")
        if s not in stds:
            stds.append(s)
    d.update(cfr_sections=secs, cfr_parts=parts, product_codes=pcs, standards=stds)
    return d


# ===========================================================================
#  Transform
# ===========================================================================
def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    rows = [r for r in load_listing(src) if in_scope(r)]
    st = base.Stats(source=NAME)
    recs = {}
    for r in rows:
        d = parse_row(r)
        if not d["url"] or not d["title"]:
            st.skipped += 1
            continue
        recs.setdefault(page_slug(d["url"]), d)      # one node per page (no duplicates seen)
    hub_pc, hub_sec, hub_part = set(), set(), set()
    with_detail = 0
    with base.TurtleWriter(out) as w:
        for key in sorted(recs):
            d = recs[key]
            det = {}
            dp = src / "pages" / "detail" / _cfweb.safe_name(key + ".html")
            if dp.exists():
                det = parse_detail(dp.read_text(encoding="utf-8", errors="replace"))
                with_detail += 1
            topics = det.get("topics") or d["topics"]
            products = det.get("products") or d["products"]
            pdf = d["pdf"] or det.get("download")
            pairs = [
                ("a", "ex:GuidanceDocument"),
                ("rdfs:label", common.lit(d["title"])),
                ("ex:guidanceDocumentTitle", common.lit(d["title"])),
                ("ex:guidanceDocumentUrl", common.lit(d["url"])),
                ("ex:downloadUrl", common.lit(pdf)),
                ("ex:fileFormat", common.lit(d["pdf_kind"]) if d["pdf"] else None),
                ("ex:issueDate", _us_date(d["issue"])),
                ("ex:guidanceStatus", common.lit(d["status"])),
                ("ex:fdaCenter", common.lit(d["center"])),
                ("ex:documentType", common.lit(d["type"])),
                ("ex:docketNumber", common.lit(d["docket"])),
                ("ex:openForComment", common.bool_lit(d["open"])),
                ("ex:commentClosingDate", _us_date(d["close"])),
                ("ex:contentCurrentAsOf", common.date_lit(det.get("current_as_of") or d["changed"])),
            ]
            pairs += [("ex:issuingOffice", common.lit(o)) for o in d["offices"]]
            pairs += [("ex:topic", common.lit(t)) for t in topics]
            pairs += [("ex:regulatedProduct", common.lit(p)) for p in products]
            for s in det.get("cfr_sections", []):
                pairs.append(("ex:citesCfrSection", f"<{iri.cfr_section(s)}>"))
                hub_sec.add(s)
            for p in det.get("cfr_parts", []):
                pairs.append(("ex:citesCfrPart", f"<{iri.cfr_part(p)}>"))
                hub_part.add(p)
            for c in det.get("product_codes", []):
                pc = common.product_code(c)
                if pc:
                    pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                    hub_pc.add(pc)
            pairs += [("ex:mentionedStandardDesignation", common.lit(s)) for s in det.get("standards", [])]
            pairs += common.provenance(DATASET, key, source_url=d["url"], retrieved_at=retrieved_at)
            w.block(iri.guidance_document(key), list(dict.fromkeys(pairs)))
            st.records += 1
        # typed hub stubs so links never dangle (labels come from cfr.py / classification)
        for pc in sorted(hub_pc):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
        for s in sorted(hub_sec):
            w.block(iri.cfr_section(s), [("a", "ex:CFRTitle21Section"), ("ex:sectionNumber", common.lit(s))])
        for p in sorted(hub_part):
            w.block(iri.cfr_part(p), [("a", "ex:CfrPart"), ("ex:cfrPartNumber", common.lit(p))])
    st.triples = w.triples
    print(f"  guidance: {st.records} guidance documents ({with_detail} with page details); "
          f"{len(hub_pc)} product codes, {len(hub_sec)} CFR sections, {len(hub_part)} CFR parts")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  Self-test on verbatim samples (listing rows and page fragment fetched 2026-10-28)
# ===========================================================================
_ROWS = [
    {"title": "<a href=\"/regulatory-information/search-fda-guidance-documents/infusion-pumps-total-product-life-cycle\">"
              "Infusion Pumps Total Product Life Cycle:  Guidance for Industry and FDA Staff </a>",
     "field_associated_media_2": "<a href=\"/media/78369/download\">PDF (1.06 MB)<span class=\"sr-only\">PDF (1.06 MB) "
                                 "of Infusion Pumps Total Product Life Cycle</span></a>",
     "field_issue_datetime": "12/02/2014",
     "field_issuing_office_taxonomy": "Center for Devices and Radiological Health,Office of Product Evaluation and "
                                      "Quality<br><br>Center for Biologics Evaluation and Research",
     "field_health_topics": "", "term_node_tid": "510(k)", "field_topics": "Premarket",
     "topics-product": "Premarket, 510(k)", "field_final_guidance_1": "Final", "open-comment": "  No ",
     "field_comment_close_date": "",
     "field_docket_number": "<a href=\"https://www.regulations.gov/docket/FDA-2010-D-0194\">FDA-2010-D-0194</a>",
     "field_communication_type": "Guidance Document", "field_center": "Center for Devices and Radiological Health",
     "field_regulated_product_field": "Medical Devices",
     "changed": "<time datetime=\"2018-08-22T10:49:00-04:00\">2018-08-22 10:49</time>\n"},
    {"title": "<a href=\"/regulatory-information/search-fda-guidance-documents/cpg-sec-280100-stability-requirements-"
              "licensed-in-vitro-diagnostic-products\">CPG Sec. 280.100 -  Stability Requirements -  Licensed In Vitro "
              "Diagnostic Products</a>",
     "field_associated_media_2": "", "field_issue_datetime": "05/01/2005",
     "field_issuing_office_taxonomy": "Center for Biologics Evaluation and Research", "field_health_topics": "",
     "term_node_tid": "", "field_topics": "", "topics-product": "", "field_final_guidance_1": "Draft",
     "open-comment": "  Yes  ", "field_comment_close_date": "12/29/2026",
     "field_docket_number": "<a href=\"https://www.regulations.gov/docket/None found\">None found</a>",
     "field_communication_type": "Compliance Policy Guide (CPG)",
     "field_center": "Center for Biologics Evaluation and Research",
     "field_regulated_product_field": "Biologics, Medical Devices", "changed": ""},
    {"title": "<a href=\"/regulatory-information/search-fda-guidance-documents/small-entity-compliance-guide-safe-"
              "handling-statements-labeling-shell-eggs-and-refrigeration-shell\">Small Entity Compliance Guide: Safe "
              "Handling Statements on Labeling of Shell Eggs</a>",
     "field_associated_media_2": "", "field_issue_datetime": "07/01/2001",
     "field_issuing_office_taxonomy": "Human Foods Program", "field_health_topics": "",
     "term_node_tid": "Egg/Egg Product, Retail Food Protection", "field_topics": "",
     "topics-product": "Egg/Egg Product, Retail Food Protection", "field_final_guidance_1": "Final",
     "open-comment": "  No ", "field_comment_close_date": "",
     "field_docket_number": "<a href=\"https://www.regulations.gov/docket/FDA-2020-D-1954\">FDA-2020-D-1954</a>",
     "field_communication_type": "Small Entity Compliance Guide", "field_center": "Human Foods Program",
     "field_regulated_product_field": "Food &amp; Beverages", "changed": ""},
]

_DETAIL = """<div class="lcds-toolbar text-center">
	    	<a class="btn btn-primary" href="/media/78369/download">Download the  Final Guidance Document</a>
      	    </div>
              <div class="col-md-8" role="main">
	<div class="inset-column">
	  <dl class="lcds-description-list--grid">
	  			  	<dt class="cell-1_1">Docket Number:</dt>
		    <dd class="cell-2_1"><a href="https://www.regulations.gov/docket/FDA-2010-D-0194">FDA-2010-D-0194</a></dd>
	  </dl>
	</div>
<p>FDA has developed <a href="/media/78369/download?attachment">this guidance document </a>to assist industry in preparing premarket submissions for infusion pumps and to identify device features that manufacturers should address throughout the total product life cycle. Infusion pumps, as described in 21 CFR 880.5725, are intended for use in a health care facility to pump fluids into a patient in a controlled manner.</p>
<p>(sample addition) Product codes FRN and MEA; see 21 CFR Part 820 and ANSI/AAMI ES60601-1:2005; contact John Doe at 301-555-0100.</p>
	   	<h2>Submit Comments</h2>
      <h2 class="lcds-description-list__item-heading">Content current as of:</h2>
      <p class="lcds-description-list__item-text"><time datetime="2018-08-22T14:49:00Z">08/22/2018</time>
</p>
		 <h2 class="lcds-description-list__item-heading">Regulated Product(s)</h2>
		 <ul role="menu" class="lcds-metadata-list">
						  <li role="menuitem">Medical Devices</li>
					</ul>
			  <h2 class="lcds-description-list__item-heading">Topic(s)</h2>
			  <ul role="menu" class="lcds-metadata-list">
			  						    <li role="menuitem">Premarket</li>
				  				  <li role="menuitem">510(k)</li>
				  			  </ul>"""


def _selftest():
    import tempfile
    assert [in_scope(r) for r in _ROWS] == [True, True, False]
    d = parse_row(_ROWS[0])
    assert d["offices"] == ["Center for Devices and Radiological Health > Office of Product Evaluation and Quality",
                            "Center for Biologics Evaluation and Research"], d["offices"]
    assert d["pdf"] == "https://www.fda.gov/media/78369/download" and d["docket"] == "FDA-2010-D-0194"
    assert parse_row(_ROWS[1])["docket"] is None                      # "None found" dropped
    det = parse_detail(_DETAIL)
    assert det["cfr_sections"] == ["880.5725"] and det["cfr_parts"] == ["820"], det
    assert det["product_codes"] == ["FRN", "MEA"] and det["standards"] == ["ANSI/AAMI ES60601-1:2005"], det
    assert det["topics"] == ["Premarket", "510(k)"] and det["current_as_of"] == "2018-08-22"
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages" / "detail").mkdir(parents=True)
        (t / "pages" / LIST_FILE).write_text(json.dumps(_ROWS), encoding="utf-8")
        (t / "pages" / "detail" / "infusion-pumps-total-product-life-cycle.html").write_text(_DETAIL, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-28", files=[]).write(t / "manifest.json")
        st = transform(t, t / "guidance.ttl")
        ttl = (t / "guidance.ttl").read_text(encoding="utf-8")
        try:
            (t / "pages" / LIST_FILE).write_text(json.dumps([{"a": 1}]), encoding="utf-8")
            load_listing(t)
        except ValueError:
            pass
        else:
            raise AssertionError("schema drift not detected")
    g = iri.guidance_document("infusion-pumps-total-product-life-cycle")
    assert st.records == 2 and st.skipped == 0, st
    assert f"<{g}>\n    a ex:GuidanceDocument" in ttl
    assert 'ex:issueDate "2014-12-02"^^xsd:date' in ttl and 'ex:guidanceStatus "Final"' in ttl
    assert 'ex:commentClosingDate "2026-12-29"^^xsd:date' in ttl and 'ex:openForComment "true"^^xsd:boolean' in ttl
    assert f"ex:citesCfrSection <{iri.cfr_section('880.5725')}>" in ttl
    assert f"ex:citesCfrPart <{iri.cfr_part('820')}>" in ttl and "ex:mentionsCfr" not in ttl
    assert f"ex:hasProductCode <{iri.product_code('FRN')}>" in ttl
    assert f"<{iri.product_code('FRN')}>\n    a ex:ProductCode" in ttl
    assert 'ex:downloadUrl "https://www.fda.gov/media/78369/download"' in ttl and 'ex:fileFormat "PDF"' in ttl
    assert "Doe" not in ttl and "555-0100" not in ttl                  # page prose not ingested
    assert ttl.count("rdfs:label") == 2                                 # one per guidance, none on stubs
    assert "Shell Eggs" not in ttl                                      # out of scope
    print(f"guidance.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
