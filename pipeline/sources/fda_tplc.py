#!/usr/bin/env python3
"""
fda_tplc.py -- FDA's own Total Product Life Cycle pages (cfTPLC/tplc.cfm) -> RDF.

PAGE  GET https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfTPLC/tplc.cfm?id=<CODE>&min_report_year=2011
      One page per product code. The "show TPLC since" drop-down offers 2011-2026; without
      min_report_year FDA shows the last five years (2021 selected on October 26, 2026), so
      2011 is requested for the widest window. Apart from the selected year, the default and
      the 2011 page differ only in how many rows they list (FRN: 16 vs 30 premarket rows,
      97 vs 100 recall rows) and in the min_report_year carried by the drill-down links.
      Redirected by FDA to www.fda.gov from October 12, 2026; served again from October 26.
      Product codes come from classification.ttl (ex:productCode, 7,094 on 2026-10-11).

SECTIONS seen on October 26-27, 2026 (all parsed):
  header            Device, Definition, Regulation Description, Product Code, Regulation
                    Number, Device Class (label/value rows). Unknown labels are kept as
                    ex:additionalField "Label: value" and counted in the report.
  Premarket Reviews 510(k)-type codes: manufacturer rows (FDA spelling, upper case), each
                    followed by decision rows (decision text, count). The decision link
                    carries FDA's internal TPLC ID (tplc.cfm?ID=2837&...&pmndecision=...),
                    recorded as ex:tplcId.
  Premarket Approvals (PMA)
                    PMA codes: one row of years (2011..2026) and one row of counts, each count
                    linking to a pma.cfm search for that year. Written as yearly count rows.
  Recalls           numbered rows: manufacturer (link to cfRES res.cfm?event_id=), recall
                    class, date posted (Mon-DD-YYYY). FDA LISTS AT MOST 100 RECALL ROWS: a
                    page with exactly 100 rows is flagged ex:recallListTruncated true.
  Adverse Events / device problems / patient problems / MDR counts by year: NOT on the
                    page any more (no such section on FRN, LZA, NIQ). Any section title this
                    parser does not know is counted in the report, never silently dropped.
  A product code FDA has no TPLC for (e.g. ZZQ) returns the frame with no tables; it is
  written as a report with no rows.

NOT INGESTED: nothing personal appears on the page (firm names only).

Licence: FDA public data (US public domain records), treated like the other CDRH databases
(PI decision, October 28, 2026). data.gov lists ODbL for TPLC, so uig@fda.hhs.gov was asked
to confirm. Output is fda_tplc.ttl, part of the open release.

Modeling
  ex:FDATPLCReport at iri.fda_tplc(code)
      ex:hasProductCode <iri.product_code(code)> (+ typed hub), ex:productCode,
      ex:tplcId (FDA's internal TPLC ID, when the page shows one), ex:reportWindowStartYear,
      ex:reportWindowEndYear (xsd:gYear), ex:deviceName, ex:definition,
      ex:regulationDescription, ex:regulationNumber / ex:hasRegulationNumber, ex:deviceClass,
      ex:premarketReviewRowCount, ex:pmaYearRowCount, ex:recallRowCount,
      ex:recallListTruncated, ex:inDataset <dataset node>, provenance.
      ex:hasPremarketReview / ex:hasRecallRow / ex:hasYearCount -> child rows.
  Child IRIs (proposed minter fda_tplc_row(code, section, n); used locally through
  iri._instance, not added to iri.py):
      iri._instance("fda-tplc", slug(code), "premarket-review", n)   ex:FDATPLCPremarketReview
          ex:applicant "<FDA spelling>", ex:hasApplicantFirm <iri.firm_name(normalize_org(name))>,
          ex:decision, ex:decisionParameter ("pmndecision"), ex:decisionCount, ex:tplcId
      iri._instance("fda-tplc", slug(code), "recall", n)             ex:FDATPLCRecallRow
          ex:recallingFirm "<as shown>", ex:hasApplicantFirm <iri.firm_name(...)>,
          ex:eventId, ex:partOfRecallEvent <iri.recall_event(event_id)>,
          ex:recallClass ("I"/"II"/"III"), ex:datePosted (xsd:date), ex:rowNumber
      iri._instance("fda-tplc", slug(code), "pma-year", <year>)      ex:FDATPLCYearCount
          ex:section "Premarket Approvals (PMA)", ex:year (xsd:gYear), ex:pmaDecisionCount
  Every child has ex:partOfTPLCReport <report> and provenance.
"""
import calendar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
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

NAME = "fda_tplc"
DATASET = "cdrh/cfTPLC"
MIN_YEAR = 2011
PAGE_URL = _cfweb.BASE + "cfTPLC/tplc.cfm?id={code}&min_report_year=" + str(MIN_YEAR)
DATASET_NODE = iri.ID + "dataset/cdrh-fda-tplc"
DEFAULT_CODES = "/srv/medical-device-kg/work/out-2026-10-11/classification.ttl"
RECALL_CAP = 100
STOP_AFTER_REDIRECTS = 3
USAGE_NOTE = ("FDA's own Total Product Life Cycle pages (cfTPLC/tplc.cfm), min_report_year=2011. "
              "FDA public data (US public domain records), released on the same terms as the other CDRH "
              "databases. FDA lists at most 100 recall "
              "rows per product code (ex:recallListTruncated marks those pages); the page no longer shows "
              "adverse event counts. Counts are FDA's page values, not recomputed.")

_END = "end database content"
_START = "show TPLC since"
_SELECTED = re.compile(r'(?i)<option\s+value="(\d{4})"\s*selected')
_OPTION = re.compile(r'(?i)<option\s+value="(\d{4})"')
_SECTION = re.compile(r'(?is)<th[^>]*colspan="\d+"[^>]*>\s*([^<]+?)\s*</th>')
_TR = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr>")
_TD = re.compile(r"(?is)<td\b[^>]*>(.*?)</td>")
_HDR = re.compile(r"(?is)<tr\b[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>")
_ANCHOR = re.compile(r'(?is)<a\s+name="[^"]*"\s*>(.*?)</a>')
_DECISION = re.compile(r'(?is)<a[^>]*href="[^"]*tplc\.cfm\?ID=(\d+)[^"]*?&(\w*decision)=[^"]*"[^>]*>(.*?)</a>')
_TPLC_ID = re.compile(r"(?i)tplc\.cfm\?ID=(\d+)")
_EVENT = re.compile(r'(?is)<a[^>]*href="[^"]*event_id=(\d+)[^"]*"[^>]*>(.*?)</a>')
_REG = re.compile(r"\b\d{3,4}\.\d+\b")
_MON = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

KNOWN_HEADER = {"Device", "Definition", "Regulation Description", "Product Code",
                "Regulation Number", "Device Class"}
KNOWN_SECTIONS = {"Premarket Reviews", "Premarket Approvals (PMA)", "Recalls"}


def row_iri(code, section, n):
    return iri._instance("fda-tplc", iri.slug(code), section, str(n))


def posted_date(s):
    """'Jun-25-2015' -> '2015-06-25' (None if not that shape)."""
    m = re.match(r"^\s*([A-Za-z]{3})-(\d{1,2})-(\d{4})\s*$", s or "")
    if not m or m.group(1).lower() not in _MON:
        return None
    return f"{m.group(3)}-{_MON[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


def complete(body):
    return _START in body and _END in body


# ---------------------------------------------------------------------------
#  Parsing
# ---------------------------------------------------------------------------
def parse_page(body):
    """-> dict(header, window, end_year, premarket[], pma_years[], recalls[], sections[], unknown_sections[])"""
    a = body.find(_START)
    b = body.find(_END, a if a >= 0 else 0)
    seg = body[a if a >= 0 else 0: b if b > 0 else len(body)]
    sel = _SELECTED.search(seg)
    years = [int(y) for y in _OPTION.findall(seg)]
    d = {"window": int(sel.group(1)) if sel else None, "end_year": max(years) if years else None,
         "header": {}, "premarket": [], "pma_years": [], "recalls": [], "sections": [],
         "unknown_sections": [], "tplc_ids": []}
    # section boundaries: every <th colspan=..>Title</th>
    marks = [(m.start(), _cfweb.one_line(m.group(1))) for m in _SECTION.finditer(seg)]
    marks = [(i, t) for i, t in marks if t not in ("Decision", "Manufacturer")]
    head = seg[:marks[0][0]] if marks else seg
    for th, td in _HDR.findall(head):
        lab = _cfweb.one_line(th)
        if lab:
            d["header"][lab] = _cfweb.one_line(td)
    for k, (i, title) in enumerate(marks):
        j = marks[k + 1][0] if k + 1 < len(marks) else len(seg)
        part = seg[i:j]
        d["sections"].append(title)
        if title == "Premarket Reviews":
            _premarket(part, d)
        elif title.startswith("Premarket Approvals"):
            _year_counts(part, title, d)
        elif title == "Recalls":
            _recalls(part, d)
        else:
            d["unknown_sections"].append(title)
    d["tplc_ids"] = list(dict.fromkeys(_TPLC_ID.findall(seg)))
    return d


def _premarket(part, d):
    firm = None
    for tr in _TR.findall(part):
        an = _ANCHOR.search(tr)
        if an and "decision=" not in tr:
            firm = _cfweb.one_line(an.group(1))
            continue
        m = _DECISION.search(tr)
        if not m:
            continue
        tds = _TD.findall(tr)
        cnt = _cfweb.one_line(tds[-1]) if tds else None
        d["premarket"].append({"firm": firm, "tplc_id": m.group(1), "param": m.group(2),
                               "decision": _cfweb.one_line(m.group(3)),
                               "count": int(cnt) if cnt and cnt.isdigit() else None,
                               "count_text": cnt})


def _year_counts(part, title, d):
    rows = [[_cfweb.one_line(c) for c in _TD.findall(tr)] for tr in _TR.findall(part)]
    rows = [r for r in rows if r]
    yr_row = next((r for r in rows if r and all(re.fullmatch(r"\d{4}", x or "") for x in r)), None)
    if not yr_row:
        d["unknown_sections"].append(title + " (no year row)")
        return
    k = rows.index(yr_row)
    vals = rows[k + 1] if k + 1 < len(rows) else []
    for y, v in zip(yr_row, vals):
        d["pma_years"].append({"section": title, "year": int(y),
                               "count": int(v) if v and v.isdigit() else None, "count_text": v})


def _recalls(part, d):
    for tr in _TR.findall(part):
        m = _EVENT.search(tr)
        if not m:
            continue
        tds = [_cfweb.one_line(c) for c in _TD.findall(tr)]
        num = tds[0] if tds and (tds[0] or "").isdigit() else None
        d["recalls"].append({"n": int(num) if num else None, "event_id": m.group(1),
                             "firm": _cfweb.one_line(m.group(2)),
                             "cls": tds[-2] if len(tds) >= 4 else None,
                             "posted_raw": tds[-1] if len(tds) >= 4 else None,
                             "posted": posted_date(tds[-1]) if len(tds) >= 4 else None})


# ---------------------------------------------------------------------------
#  Codes
# ---------------------------------------------------------------------------
def codes_from_ttl(path):
    codes = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            for c in re.findall(r'ex:productCode "([A-Za-z0-9]{3})"', line):
                codes.add(c.upper())
    return sorted(codes)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is FDA refusing the page; surface it as HTTP 30x instead of following it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl}", headers, fp)


# ---------------------------------------------------------------------------
#  Contract
# ---------------------------------------------------------------------------
def fetch(dest, codes_ttl=DEFAULT_CODES, delay=1.0, limit=None):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    urllib.request.install_opener(urllib.request.build_opener(_NoRedirect))
    cj = dest / "codes.json"
    if Path(codes_ttl).exists():
        codes = codes_from_ttl(codes_ttl)
        cj.write_text(json.dumps(codes), encoding="utf-8")
    else:
        codes = json.loads(cj.read_text(encoding="utf-8"))
    if limit:
        codes = codes[:limit]
    f = _cfweb.Fetcher(dest, delay=delay)
    failed, redirects, stopped = [], [], None
    t0 = time.time()
    for i, c in enumerate(codes, 1):
        name = f"tplc_{c}_{MIN_YEAR}"
        try:
            body = f.get(PAGE_URL.format(code=c), name)
            if not complete(body):                     # never keep a cut page as final
                f.path(name).unlink(missing_ok=True)
                failed.append([c, "incomplete page"])
        except _cfweb.Unavailable as e:
            msg = str(e)
            failed.append([c, msg])
            if re.search(r"HTTP 30\d", msg):
                redirects.append([c, msg])
                print(f"  fda_tplc: REDIRECT for {c}: {msg}", flush=True)
                if len(redirects) >= STOP_AFTER_REDIRECTS:
                    stopped = f"stopped after {len(redirects)} redirects (FDA is redirecting tplc.cfm again)"
                    print("  fda_tplc: " + stopped, flush=True)
                    break
        if i % 100 == 0:
            print(f"  fda_tplc: {i}/{len(codes)} codes; fetched {f.fetched}, cached {f.cached}, "
                  f"failed {len(failed)}; {time.time() - t0:.0f}s", flush=True)
    log = {"date": base.today(), "codes": len(codes), "fetched_now": f.fetched, "from_cache": f.cached,
           "failed": failed, "redirects": redirects, "stopped": stopped}
    (dest / "fetch_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(f"  fda_tplc: {len(codes)} codes; fetched {f.fetched}, cached {f.cached}, failed {len(failed)}"
          + (f"; {stopped}" if stopped else ""), flush=True)
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    if stopped:
        raise RuntimeError(stopped)
    return m


def _page_date(p):
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    codes = json.loads((src / "codes.json").read_text(encoding="utf-8"))
    st = base.Stats(source=NAME)
    hubs, regs, firms = set(), set(), {}
    rep = {"pages": 0, "missing": [], "incomplete": [], "unknown_header": {}, "unknown_sections": {},
           "truncated_recalls": [], "premarket_rows": 0, "pma_year_rows": 0, "recall_rows": 0,
           "no_rows": 0, "multi_tplc_id": [], "window_not_2011": []}
    with base.TurtleWriter(out) as w:
        w.block(DATASET_NODE, [
            ("a", "ex:SourceDataset"),
            ("rdfs:label", common.lit("FDA Total Product Life Cycle (TPLC) pages, since 2011")),
            ("ex:sourceUrl", common.lit(_cfweb.BASE + "cfTPLC/tplc.cfm")),
            ("ex:usageNote", common.lit(USAGE_NOTE)),
            ("ex:retrievedAt", common.date_lit(retrieved_at)),
        ])
        for c in codes:
            p = src / "pages" / f"tplc_{c}_{MIN_YEAR}.html"
            if not p.exists():
                rep["missing"].append(c)
                st.skipped += 1
                continue
            body = p.read_text(encoding="utf-8", errors="replace")
            if not complete(body):
                rep["incomplete"].append(c)
                st.skipped += 1
                continue
            rep["pages"] += 1
            day = _page_date(p)                      # the day this page was fetched
            n_t, n_r = _write_report(w, c, parse_page(body), day, hubs, regs, firms, rep)
            st.records += 1 + n_r
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hubs):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
        for reg in sorted(regs):
            w.block(iri.regulation(reg), [("a", "ex:RegulationNumber"), ("ex:regulationNumber", common.lit(reg))])
    st.triples = w.triples
    Path(out).with_suffix(".report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"  fda_tplc: {rep['pages']} pages -> {st.records} nodes ({rep['premarket_rows']} premarket rows, "
          f"{rep['pma_year_rows']} PMA year rows, {rep['recall_rows']} recall rows); "
          f"{len(rep['missing'])} missing, {len(rep['incomplete'])} incomplete, "
          f"{len(rep['truncated_recalls'])} recall lists at the {RECALL_CAP}-row cap; "
          f"unknown sections {rep['unknown_sections'] or 'none'}; unknown labels {rep['unknown_header'] or 'none'}",
          flush=True)
    return st


def _write_report(w, code, d, day, hubs, regs, firms, rep):
    node = iri.fda_tplc(code)
    url = PAGE_URL.format(code=code)
    h = d["header"]
    pc = common.product_code(h.get("Product Code")) or code
    hubs.add(pc)
    for t in d["unknown_sections"]:
        rep["unknown_sections"][t] = rep["unknown_sections"].get(t, 0) + 1
    if d["window"] != MIN_YEAR:
        rep["window_not_2011"].append([code, d["window"]])
    ids = d["tplc_ids"]
    if len(ids) > 1:
        rep["multi_tplc_id"].append([code, ids])
    truncated = len(d["recalls"]) >= RECALL_CAP
    if truncated:
        rep["truncated_recalls"].append(code)
    nrows = len(d["premarket"]) + len(d["pma_years"]) + len(d["recalls"])
    if not nrows:
        rep["no_rows"] += 1
    pairs = [("a", "ex:FDATPLCReport"),
             ("rdfs:label", common.label(f"FDA TPLC {code}", h.get("Device"))),
             ("ex:productCode", common.lit(pc)),
             ("ex:hasProductCode", f"<{iri.product_code(pc)}>"),
             ("ex:inDataset", f"<{DATASET_NODE}>")]
    pairs += [("ex:tplcId", common.lit(i)) for i in ids]
    pairs += [("ex:reportWindowStartYear", common.gyear_lit(d["window"])),
              ("ex:reportWindowEndYear", common.gyear_lit(d["end_year"])),
              ("ex:deviceName", common.lit(h.get("Device"))),
              ("ex:definition", common.lit(h.get("Definition"))),
              ("ex:regulationDescription", common.lit(h.get("Regulation Description"))),
              ("ex:deviceClass", common.lit(h.get("Device Class")))]
    for reg in _REG.findall(h.get("Regulation Number") or ""):
        pairs += [("ex:regulationNumber", common.lit(reg)), ("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>")]
        regs.add(reg)
    for lab in sorted(set(h) - KNOWN_HEADER):
        rep["unknown_header"][lab] = rep["unknown_header"].get(lab, 0) + 1
        pairs.append(("ex:additionalField", common.lit(f"{lab}: {h[lab]}") if h[lab] else None))
    pairs += [("ex:premarketReviewRowCount", common.int_lit(len(d["premarket"]))),
              ("ex:pmaYearRowCount", common.int_lit(len(d["pma_years"])) if d["pma_years"] else None),
              ("ex:recallRowCount", common.int_lit(len(d["recalls"]))),
              ("ex:recallListTruncated", '"true"^^xsd:boolean' if truncated else '"false"^^xsd:boolean')]
    for n in range(1, len(d["premarket"]) + 1):
        pairs.append(("ex:hasPremarketReview", f"<{row_iri(code, 'premarket-review', n)}>"))
    for y in d["pma_years"]:
        pairs.append(("ex:hasYearCount", f"<{row_iri(code, 'pma-year', y['year'])}>"))
    for n in range(1, len(d["recalls"]) + 1):
        pairs.append(("ex:hasRecallRow", f"<{row_iri(code, 'recall', n)}>"))
    pairs += common.provenance(DATASET, code, source_url=url, retrieved_at=day)
    w.block(node, [p for p in pairs if p[1] is not None])

    def firm_pairs(raw):
        nf = normalize_org(raw) if raw else None
        if nf:
            firms.setdefault(nf, raw)
            return [("ex:hasApplicantFirm", f"<{iri.firm_name(nf)}>")]
        return []

    part = ("ex:partOfTPLCReport", f"<{node}>")
    for n, r in enumerate(d["premarket"], 1):
        s = row_iri(code, "premarket-review", n)
        pp = [("a", "ex:FDATPLCPremarketReview"),
              ("rdfs:label", common.label(f"FDA TPLC {code} premarket review {n}", r["firm"], r["decision"], sep=" / ")),
              part, ("ex:applicant", common.lit(r["firm"]))] + firm_pairs(r["firm"]) + [
              ("ex:decision", common.lit(r["decision"])),
              ("ex:decisionParameter", common.lit(r["param"])),
              ("ex:decisionCount", common.int_lit(r["count"])),
              ("ex:decisionCountText", common.lit(r["count_text"]) if r["count"] is None else None),
              ("ex:tplcId", common.lit(r["tplc_id"]))]
        pp += common.provenance(DATASET, f"{code}/premarket-review/{n}", source_url=url, retrieved_at=day)
        w.block(s, [p for p in pp if p[1] is not None])
    for r in d["pma_years"]:
        s = row_iri(code, "pma-year", r["year"])
        pp = [("a", "ex:FDATPLCYearCount"),
              ("rdfs:label", common.lit(f"FDA TPLC {code} {r['section']} {r['year']}")),
              part, ("ex:section", common.lit(r["section"])),
              ("ex:year", common.gyear_lit(r["year"])),
              ("ex:pmaDecisionCount", common.int_lit(r["count"])),
              ("ex:countText", common.lit(r["count_text"]) if r["count"] is None else None)]
        pp += common.provenance(DATASET, f"{code}/pma-year/{r['year']}", source_url=url, retrieved_at=day)
        w.block(s, [p for p in pp if p[1] is not None])
    for n, r in enumerate(d["recalls"], 1):
        s = row_iri(code, "recall", n)
        pp = [("a", "ex:FDATPLCRecallRow"),
              ("rdfs:label", common.label(f"FDA TPLC {code} recall {n}", r["firm"], f"event {r['event_id']}", sep=" / ")),
              part, ("ex:rowNumber", common.int_lit(r["n"])),
              ("ex:recallingFirm", common.lit(r["firm"]))] + firm_pairs(r["firm"]) + [
              ("ex:eventId", common.lit(r["event_id"])),
              ("ex:partOfRecallEvent", f"<{iri.recall_event(r['event_id'])}>"),
              ("ex:recallClass", common.lit(r["cls"])),
              ("ex:datePosted", common.date_lit(r["posted"]) if r["posted"] else common.lit(r["posted_raw"]))]
        pp += common.provenance(DATASET, f"{code}/recall/{n}", source_url=url, retrieved_at=day)
        w.block(s, [p for p in pp if p[1] is not None])
    rep["premarket_rows"] += len(d["premarket"])
    rep["pma_year_rows"] += len(d["pma_years"])
    rep["recall_rows"] += len(d["recalls"])
    return 1, nrows


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- fragments copied verbatim from tplc.cfm?id=FRN&min_report_year=2011 and
#  ?id=NIQ&min_report_year=2011, fetched October 27, 2026 (rows elided, markup unchanged).
# ===========================================================================
_FRN = """						show TPLC since
						<select name="min_report_year" id="min_report_year" onchange="location.href='./tplc.cfm?id=FRN&amp;min_report_year='+this.value;">
							<option value="2011" selected >2011
							<option value="2012"  >2012
							<option value="2026"  >2026
						</select>
				<table border="0" cellpadding="0" cellspacing="5" width="600" style="font-family: arial; color: #23238e;">
					<tr>
					<th align="left">Device</th>
					<td align="left" style="text-transform: capitalize">pump, infusion</td>
					</tr>
					<tr>
					<th align="left" nowrap>Regulation Description</th>
					<td align="left">Infusion pump.</td>
					</tr>
					<tr>
					<th align="left" nowrap>Product Code</th><td align="left">FRN</td>
					</tr>
					<tr><th align="left" nowrap>Regulation Number</th>
					<td align="left">
							<a style="text-decoration:underline;" href="../cfcfr/cfrsearch.cfm?fr=880.5725" title="information about 21cfr880.5725">880.5725</a>
					</td>
					</tr>
					<tr>
					<th align="left" nowrap>Device Class</th>
					<td align="left">
							2
					</td>
					</tr>
				</table>
		<table border="0" cellpadding="0" cellspacing="0" align="center" style="font-family: arial; color: #23238e;" width="85%">
		<tr valign="top">
		<td bgcolor="#888888">
			<table border="0" cellpadding="5" cellspacing="1" width="100%">
			<tr bgcolor="#e9e9e3" valign="top"><th colspan="3" align="left">Premarket Reviews</th></tr>
			<tr bgcolor="#f9f9f3"><th align="left">Manufacturer</th><th colspan="2" align="left">Decision</th></tr>
				<tr bgcolor="#f9f9f3">
				<td colspan="3" align="left"><A name="B.BRAUN MEDICAL, INC.">B.BRAUN MEDICAL, INC.</A></td>
				</tr>
				<tr bgcolor="#f9f9f3">
				<td align="left">&nbsp;</td>
				<td align="left" style="font-size:9pt;"><a style="text-decoration:underline;"  href="./tplc.cfm?ID=2837&min_report_year=2011&manufacturer=B%2EBRAUN%20MEDICAL%2C%20INC%2E&pmndecision=SUBSTANTIALLY%20EQUIVALENT#B.BRAUN MEDICAL, INC.">SUBSTANTIALLY EQUIVALENT</a>
				</td>
				<td align="center">
				2
				</td>
				</tr>
				<tr bgcolor="#f9f9f3">
				<td colspan="3" align="left"><A name="BAXTER HEALTHCARE CORPORATION">BAXTER HEALTHCARE CORPORATION</A></td>
				</tr>
				<tr bgcolor="#f9f9f3">
				<td align="left">&nbsp;</td>
				<td align="left" style="font-size:9pt;"><a style="text-decoration:underline;"  href="./tplc.cfm?ID=2837&min_report_year=2011&manufacturer=BAXTER%20HEALTHCARE%20CORPORATION&pmndecision=SUBSTANTIALLY%20EQUIVALENT#BAXTER HEALTHCARE CORPORATION">SUBSTANTIALLY EQUIVALENT</a>
				</td>
				<td align="center">
				11
				</td>
				</tr>
			</table>
		</td></tr></table>
		<br>
			<table border="0" cellpadding="0" cellspacing="0" align="center" style="font-family: arial; color: #23238e;" width="85%">
			<tr valign="top">
			<td bgcolor="#888888">
				<table border="0" cellpadding="5" cellspacing="1" width="100%">
				<tr bgcolor="#e9e9e3" valign="top">
				<th colspan="4" align="left">Recalls</th>
				</tr>
				<tr bgcolor="#f9f9f3" valign="top">
				<th colspan="2" align="left">Manufacturer</th>
				<th align="center">Recall Class</th>
				<th align="center">Date Posted</th>
				</tr>
					<tr bgcolor="#f9f9f3" >
					<td>1</td>
					<td><A style="text-decoration:underline;"  href="../cfres/res.cfm?start_search=1&event_id=26046">Abbott Laboratories</A></td>
					<td align="center">II</td>
					<td align="center">Jun-25-2015</td>
					</tr>
					<tr bgcolor="#f9f9f3" >
					<td>100</td>
					<td><A style="text-decoration:underline;"  href="../cfres/res.cfm?start_search=1&event_id=85954">CareFusion 303, Inc.</A></td>
					<td align="center">I</td>
					<td align="center">Aug-06-2020</td>
					</tr>
				</table>
			</td></tr></table>
<!-- ********  end database content ******** -->"""

_NIQ = """						show TPLC since
						<select name="min_report_year" id="min_report_year" onchange="location.href='./tplc.cfm?id=NIQ&amp;min_report_year='+this.value;">
							<option value="2011" selected >2011
							<option value="2026"  >2026
						</select>
				<table border="0" cellpadding="0" cellspacing="5" width="600" style="font-family: arial; color: #23238e;">
					<tr>
					<th align="left">Device</th>
					<td align="left" style="text-transform: capitalize">coronary drug-eluting stent</td>
					</tr>
					<tr valign="top">
					<th align="left" nowrap>Definition</th>
					<td align="left" class="hardbreak">Stent, coronary, drug-eluting -- a metal scaffold with a drug coating placed via a delivery catheter into the coronary artery or saphenous vein graft to maintain the lumen.  The drug coating is intended to inhibit restenosis.</td>
					</tr>
					<tr>
					<th align="left" nowrap>Product Code</th><td align="left">NIQ</td>
					</tr>
					<tr>
					<th align="left" nowrap>Device Class</th>
					<td align="left">
							3
					</td>
					</tr>
				</table>
			<table border="0" cellpadding="0" cellspacing="0" align="center" style="font-family: arial; color: #23238e;" width="85%">
			<tr valign="top">
			<td bgcolor="#888888">
				<table border="0" cellpadding="5" cellspacing="1" width="100%">
				<tr bgcolor="#e9e9e3" valign="top">
				<th colspan="17" align="left">Premarket Approvals (PMA)</th>
				</tr>
				<tr bgcolor="#f9f9f3">
					<td align="right" style="font-size:10pt; font-weight:normal;">2011</td>
					<td align="right" style="font-size:10pt; font-weight:normal;">2012</td>
				</tr>
				<tr bgcolor="#f9f9f3">
					<td align="right">
						<a style="text-decoration:underline;"  href="../cfpma/pma.cfm?start_search=1&applicant=&tradename=&productcode=NIQ&pmanumber=p&advisorycommittee=&docketnumber=&supplementtype=&expeditedreview=&ivdproducts=off&decisiondatefrom=1/1/2011&decisiondateto=1/1/2012&noticedatefrom=&noticedateto=&sortcolumn=do_desc&pagenum=10">
					97
						</a>
					</td>
					<td align="right">
						<a style="text-decoration:underline;"  href="../cfpma/pma.cfm?start_search=1&applicant=&tradename=&productcode=NIQ&pmanumber=p&advisorycommittee=&docketnumber=&supplementtype=&expeditedreview=&ivdproducts=off&decisiondatefrom=1/1/2012&decisiondateto=1/1/2013&noticedatefrom=&noticedateto=&sortcolumn=do_desc&pagenum=10">
					167
						</a>
					</td>
				</tr>
				</table>
			</td>
			</tr>
			</table>
			<br>
				<table border="0" cellpadding="0" cellspacing="0" align="center" style="font-family: arial; color: #23238e;" width="85%">
				<tr valign="top">
				<td bgcolor="#888888">
					<table border="0" cellpadding="5" cellspacing="1" width="100%">
					<tr bgcolor="#e9e9e3" valign="top">
					<th colspan="4" align="left">Recalls</th>
					</tr>
					<tr bgcolor="#f9f9f3" valign="top">
					<th colspan="2" align="left">Manufacturer</th>
					<th align="center">Recall Class</th>
					<th align="center">Date Posted</th>
					</tr>
						<tr bgcolor="#f9f9f3" >
						<td>1</td>
						<td><A style="text-decoration:underline;"  href="../cfres/res.cfm?start_search=1&event_id=85497">Abbott Vascular</A></td>
						<td align="center">II</td>
						<td align="center">Jun-15-2020</td>
						</tr>
					</table>
				</td></tr></table>
<!-- ********  end database content ******** -->"""

_EMPTY = """show TPLC since <select name="min_report_year"><option value="2011" selected >2011
<option value="2026"  >2026</select> Back to Search Results
<!-- ********  end database content ******** -->"""


def _selftest():
    import tempfile
    assert posted_date("Jun-25-2015") == "2015-06-25" and posted_date("x") is None
    d = parse_page(_FRN)
    assert d["window"] == 2011 and d["end_year"] == 2026, d
    assert d["header"]["Device"] == "pump, infusion" and d["header"]["Regulation Number"] == "880.5725", d["header"]
    assert d["sections"] == ["Premarket Reviews", "Recalls"], d["sections"]
    assert [(r["firm"], r["decision"], r["count"], r["tplc_id"]) for r in d["premarket"]] == [
        ("B.BRAUN MEDICAL, INC.", "SUBSTANTIALLY EQUIVALENT", 2, "2837"),
        ("BAXTER HEALTHCARE CORPORATION", "SUBSTANTIALLY EQUIVALENT", 11, "2837")], d["premarket"]
    assert [(r["event_id"], r["cls"], r["posted"]) for r in d["recalls"]] == [
        ("26046", "II", "2015-06-25"), ("85954", "I", "2020-08-06")], d["recalls"]
    n = parse_page(_NIQ)
    assert [(r["year"], r["count"]) for r in n["pma_years"]] == [(2011, 97), (2012, 167)], n["pma_years"]
    assert n["header"]["Device Class"] == "3" and n["tplc_ids"] == [] and not n["unknown_sections"]
    e = parse_page(_EMPTY)
    assert e["window"] == 2011 and not (e["premarket"] or e["recalls"] or e["pma_years"])
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "tplc_FRN_2011.html").write_text(_FRN, encoding="utf-8")
        (t / "pages" / "tplc_NIQ_2011.html").write_text(_NIQ, encoding="utf-8")
        (t / "pages" / "tplc_ZZQ_2011.html").write_text(_EMPTY, encoding="utf-8")
        (t / "codes.json").write_text(json.dumps(["FRN", "NIQ", "ZZQ", "QQQ"]), encoding="utf-8")
        st = transform(t, t / "fda_tplc.ttl", retrieved_at="2026-10-27")
        ttl = (t / "fda_tplc.ttl").read_text(encoding="utf-8")
    assert st.skipped == 1, st                         # QQQ: no page
    frn = f"<{iri.fda_tplc('FRN')}>"
    assert frn + "\n    a ex:FDATPLCReport" in ttl
    assert 'ex:tplcId "2837"' in ttl and 'ex:reportWindowStartYear "2011"^^xsd:gYear' in ttl
    assert f"ex:hasProductCode <{iri.product_code('FRN')}>" in ttl
    assert f"<{iri.product_code('NIQ')}>\n    a ex:ProductCode" in ttl
    assert f"ex:hasRegulationNumber <{iri.regulation('880.5725')}>" in ttl
    pr = row_iri("FRN", "premarket-review", 2)
    assert f"<{pr}>\n    a ex:FDATPLCPremarketReview" in ttl and 'ex:decisionCount "11"^^xsd:integer' in ttl
    assert f"ex:hasApplicantFirm <{iri.firm_name(normalize_org('BAXTER HEALTHCARE CORPORATION'))}>" in ttl
    assert f"ex:partOfRecallEvent <{iri.recall_event('85954')}>" in ttl
    assert 'ex:datePosted "2020-08-06"^^xsd:date' in ttl and 'ex:recallClass "I"' in ttl
    assert f"<{row_iri('NIQ', 'pma-year', 2012)}>" in ttl and 'ex:pmaDecisionCount "167"^^xsd:integer' in ttl
    assert "ex:usageNote" in ttl and "public domain" in ttl
    assert f"<{iri.fda_tplc('ZZQ')}>" in ttl and 'ex:recallListTruncated "false"^^xsd:boolean' in ttl
    print(f"fda_tplc.py self-test PASSED ({st.records} nodes, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
