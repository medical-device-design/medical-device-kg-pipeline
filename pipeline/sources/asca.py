#!/usr/bin/env python3
"""
asca.py -- Accreditation Scheme for Conformity Assessment (ASCA): accredited testing
laboratories, recognized accreditation bodies, and their scopes (standards) -> RDF.

PAGES (accessdata.fda.gov ColdFusion, cfStandards; verified November 3, 2026)
  B = https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfStandards/
  B + asca-accredited-testing-laboratories.cfm           list, 98 labs (ASCA ID TL-n)
  B + withdrawn-asca-testing-laboratories.cfm            list of labs whose ASCA accreditation
                                                         was withdrawn (FDA-initiated or voluntary)
  B + asca-recognized-accreditation-bodies.cfm           list, 5 accreditation bodies (AB-n)
  ...?stds_id=<n>                                        one detail page per lab / body with the
                                                         "Scope of ASCA Accreditation/Recognition"
  FDA's "Export to Excel" (StandardsTLExcel.cfm) returns only the list columns, and mixes the
  point-of-contact person into the address, so the HTML pages are used.
  The fda.gov overview pages (.../division-standards-and-conformity-assessment/asca-*) link to
  these database pages and carry no further data.

PERSONAL DATA
  The "Point Of Contact" / "Primary Contact" columns (person name, e-mail, address in one
  cell) are never read. Only state and country from the list are kept.

MODEL
  ex:ASCATestingLaboratory  at asca_testing_laboratory(ASCA ID)   e.g. .../asca-testing-laboratory/tl_8
  ex:ASCAAccreditationBody  at asca_accreditation_body(ASCA ID)   e.g. .../asca-accreditation-body/ab_11
      rdfs:label (FDA's organization name), ex:ascaId, ex:ascaCategory (one per category),
      ex:ascaStatus ("Accreditation Granted", "FDA Initiated Withdrawal", "Recognition Granted"...),
      ex:ascaStatusDate (date the current status took effect: start / recognition date, or the
      withdrawal date), ex:ascaStartDate (accreditation start, withdrawn labs only), ex:state, ex:country,
      ex:hasOrganizationFirm -> ex:FirmName (iri.firm_name(normalize_org(name))),
      lab: ex:accreditedBy -> the accreditation body node,
      ex:ascaScopeStandard -> ex:RecognizedConsensusStandard (iri.consensus_standard(recognition
      number)), one per standard in scope.
  ex:ASCAScopeEntry at asca_scope(ASCA ID, recognition number or designation[, test method])
      ex:scopeOf -> lab or body, ex:hasConsensusStandard -> standard (when FDA gives the
      recognition number), ex:recognitionNumber, ex:standardsDevelopmentOrganization,
      ex:standardDesignation (always kept as a literal), ex:fdaStandardId, ex:ascaCategory,
      ex:biocompatibilityTestMethod, ex:ascaStatus, ex:ascaStatusDate, ex:scopeExclusion.
  Typed hub nodes: RecognizedConsensusStandard (ex:recognitionNumber only), FirmName.
  Not ingested: the "Scope History" table on the accreditation body pages (transitioned /
  inactive standards); only the current scope is modeled.

MINTERS (iri.py)
    iri.asca_testing_laboratory(asca_id), iri.asca_accreditation_body(asca_id),
    iri.asca_scope(asca_id, *key)
"""
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

NAME = "asca"
DATASET = "cdrh/asca"
B = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfStandards/"
LISTS = {"lab": "asca-accredited-testing-laboratories.cfm",
         "withdrawn": "withdrawn-asca-testing-laboratories.cfm",
         "ab": "asca-recognized-accreditation-bodies.cfm"}
CATEGORY_NAMES = ["Basic safety and essential performance", "Biocompatibility"]


lab_iri = iri.asca_testing_laboratory
ab_iri = iri.asca_accreditation_body
scope_iri = iri.asca_scope


def detail_url(kind, stds_id):
    return B + LISTS[kind] + f"?stds_id={stds_id}"


# ===========================================================================
#  Parsing
# ===========================================================================
_TR = re.compile(r"(?is)<tr\b([^>]*)>(.*?)</tr>")
_TD = re.compile(r"(?is)<td\b[^>]*>(.*?)</td>")


def categories(fragment):
    """'Basic safety and essential performance Biocompatibility' (one cell) -> both names."""
    parts = [p for p in re.split(r"(?i)<br\s*/?>", fragment or "")]
    out = []
    for p in parts:
        t = _cfweb.one_line(p) or ""
        for name in CATEGORY_NAMES:
            if name.lower() in t.lower():
                out.append(name)
                t = re.sub(re.escape(name), " ", t, flags=re.I)
        t = " ".join(t.split())
        if t:
            out.append(t)
    return list(dict.fromkeys(out))


def parse_list(page, kind):
    """-> list of dicts: stds_id, asca_id, name, categories, status, date (withdrawn only),
    state, country. The contact cell is not read."""
    i = page.find('id="stds-results-table"')
    if i < 0:
        raise ValueError(f"ASCA {kind} list: results table not found")
    out = []
    for attrs, row in _TR.findall(page[i:]):
        if 'valign="top"' not in attrs or "stds_id=" not in row:
            continue
        c = _TD.findall(row)
        m = re.search(r"stds_id=(\d+)", c[0]) if c else None
        if not m:
            continue
        if len(c) != (8 if kind == "withdrawn" else 7):
            raise ValueError(f"ASCA {kind} list: row has {len(c)} cells (layout changed)")
        rec = {"stds_id": m.group(1), "asca_id": _cfweb.one_line(c[0]), "name": _cfweb.one_line(c[1]),
               "categories": categories(c[2]), "status": _cfweb.one_line(c[3])}
        rest = c[4:-1]                                   # last cell = point of contact, skipped
        if kind == "withdrawn":
            rec["date"], rest = _cfweb.one_line(rest[0]), rest[1:]
        rec["state"] = _cfweb.one_line(rest[0]) if len(rest) > 0 else None
        rec["country"] = _cfweb.one_line(rest[1]) if len(rest) > 1 else None
        if not re.match(r"^(TL|AB)-\d+$", rec["asca_id"] or ""):
            raise ValueError(f"ASCA {kind} list: unexpected ASCA ID {rec['asca_id']!r}")
        out.append(rec)
    return out


def _field(page, label):
    m = re.search(rf"(?is)>\s*{re.escape(label)}\s*</span>(.*?)</td>", page)
    return m.group(1) if m else None


_STATUS = re.compile(r"^(.*?)\s*-\s*(?:Start\s+)?Date:\s*(\d{1,2}/\d{1,2}/\d{4})"
                     r"(?:\s*-\s*End\s+Date:\s*(\d{1,2}/\d{1,2}/\d{4}))?\s*$", re.I)


def parse_detail(page):
    """-> dict: status, status_date (start), end_date, ab_stds_ids (accreditation bodies),
    scope rows. Only the current "Scope of ASCA Accreditation/Recognition" table is read (the
    "Scope History" table below it has other columns). Scope columns are mapped by their
    header text, so a reordered table still parses."""
    d = {"status": None, "status_date": None, "end_date": None, "ab_stds_ids": [], "scope": [],
         "has_scope_history": "Scope History" in page}
    st = _cfweb.one_line(_field(page, "Accreditation Status:") or _field(page, "Recognition Status:") or "")
    if st:
        m = _STATUS.match(st)
        d["status"], d["status_date"], d["end_date"] = (m.group(1).strip(), m.group(2), m.group(3)) if m \
            else (st, None, None)
    abf = _field(page, "Accreditation Body Organization Name:") or ""
    d["ab_stds_ids"] = re.findall(r"asca-recognized-accreditation-bodies\.cfm\?stds_id=(\d+)", abf)
    i = page.find("Scope of ASCA")
    i = page.find("<tbody>", i) if i >= 0 else -1
    if i < 0:
        return d
    j = page.find("</tbody>", i)
    cols = None
    for attrs, row in _TR.findall(page[i:j if j > i else len(page)]):
        cells = _TD.findall(row)
        if cols is None:
            if "Recognition" in row and "Number" in row and "Designation" in row:
                cols = [" ".join((_cfweb.one_line(re.sub(r"(?is)<div class=\"sort\".*?</div>", "", x)) or "").split())
                        .lower() for x in cells]
            continue
        if len(cells) != len(cols) or 'scope="col"' in row:
            continue
        r = {}
        for name, cell in zip(cols, cells):
            if "title" in name:
                ids = [x for x in re.findall(r"standard__identification_no=(\d*)", cell) if x]
                r["fda_standard_id"] = ids[0] if ids else None
                r["title"] = _cfweb.one_line(cell)
                r["fda_standard_ids"] = ids
            elif name.startswith("category"):
                r["categories"] = categories(cell)
            else:
                # one row can name two standards (an ISO test method and its ASTM / USP twin),
                # one per <br>-separated line of the cell
                r[name] = _cfweb.one_line(cell)
                r[name + " list"] = [t for t in (_cfweb.one_line(p) for p in re.split(r"(?i)<br\s*/?>", cell)) if t]
        d["scope"].append(r)
    return d


def scope_value(r, *starts):
    for k, v in r.items():
        if any(k.startswith(s) for s in starts):
            return v
    return None


# ===========================================================================
#  Fetch
# ===========================================================================
def fetch(dest, delay=1.0, refresh_days=7):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    lists, problems = {}, []
    for kind, page in LISTS.items():
        body = f.get(B + page, f"list_{kind}", max_age_days=1)
        lists[kind] = parse_list(body, kind)             # refuse a list whose shape changed
    for kind, recs in lists.items():
        for r in recs:
            try:
                f.get(detail_url(kind, r["stds_id"]), f"{kind}_{r['stds_id']}", max_age_days=refresh_days)
            except _cfweb.Unavailable as e:
                problems.append({"kind": kind, "asca_id": r["asca_id"], "problem": str(e)})
    (dest / "fetch_problems.json").write_text(json.dumps(problems, indent=1), encoding="utf-8")
    files = [base.FileRef(url=B + LISTS[k], path=f"pages/list_{k}.html", sha256=base.sha256_file(f.path(f"list_{k}")),
                          bytes=f.path(f"list_{k}").stat().st_size) for k in LISTS]
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)
    man.write(dest / "manifest.json")
    print(f"  asca: {', '.join(f'{k} {len(v)}' for k, v in lists.items())}; fetched {f.fetched}, "
          f"cached {f.cached}, problems {len(problems)}")
    return man


# ===========================================================================
#  Transform
# ===========================================================================
def _date(s):
    return common.date_lit(s) if s else None


def transform(src, out, retrieved_at=None):
    src = Path(src)
    pages = src / "pages"
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    lists = {k: parse_list((pages / f"list_{k}.html").read_text(encoding="utf-8", errors="replace"), k)
             for k in LISTS}
    ab_by_stds = {r["stds_id"]: r["asca_id"] for r in lists["ab"]}
    st = base.Stats(source=NAME)
    firms, stds, seen = {}, set(), set()
    rep = {"labs": 0, "labs_withdrawn": 0, "accreditation_bodies": 0, "scope_entries": 0,
           "scope_with_recognition_number": 0, "scope_without_recognition_number": [],
           "missing_detail_pages": [], "unknown_ab_links": []}
    with base.TurtleWriter(out) as w:
        for kind in ("ab", "lab", "withdrawn"):
            for r in sorted(lists[kind], key=lambda x: int(x["asca_id"].split("-")[1])):
                aid = r["asca_id"]
                if aid in seen:                      # a lab listed twice keeps its first block
                    st.skipped += 1
                    continue
                seen.add(aid)
                node = ab_iri(aid) if kind == "ab" else lab_iri(aid)
                url = detail_url(kind, r["stds_id"])
                dp = pages / f"{_cfweb.safe_name(kind + '_' + r['stds_id'])}.html"
                d = parse_detail(dp.read_text(encoding="utf-8", errors="replace")) if dp.exists() else None
                if d is None:
                    rep["missing_detail_pages"].append(aid)
                elif d["has_scope_history"]:
                    rep["pages_with_scope_history_not_ingested"] = rep.get("pages_with_scope_history_not_ingested", 0) + 1
                nf = normalize_org(r["name"])
                status = r["status"] or (d or {}).get("status")
                # date the current status took effect: withdrawal (end) date for a withdrawn lab,
                # otherwise the accreditation / recognition start date
                end = r.get("date") or (d or {}).get("end_date")
                sdate = end or (d or {}).get("status_date")
                start = (d or {}).get("status_date") if end else None
                pairs = [("a", "ex:ASCAAccreditationBody" if kind == "ab" else "ex:ASCATestingLaboratory"),
                         ("rdfs:label", common.lit(r["name"])),
                         ("ex:ascaId", common.lit(aid))]
                pairs += [("ex:ascaCategory", common.lit(c)) for c in r["categories"]]
                pairs += [("ex:ascaStatus", common.lit(status)),
                          ("ex:ascaStatusDate", _date(sdate)),
                          ("ex:ascaStartDate", _date(start)),
                          ("ex:state", common.lit(r["state"])),
                          ("ex:country", common.lit(r["country"]))]
                if nf:
                    pairs.append(("ex:hasOrganizationFirm", f"<{iri.firm_name(nf)}>"))
                    firms.setdefault(nf, r["name"])
                for sid in (d or {}).get("ab_stds_ids", []):
                    if sid in ab_by_stds:
                        pairs.append(("ex:accreditedBy", f"<{ab_iri(ab_by_stds[sid])}>"))
                    else:
                        rep["unknown_ab_links"].append({"lab": aid, "ab_stds_id": sid})
                scope_blocks = []
                for s in (d or {}).get("scope", []):
                    recs = re.findall(r"\b\d{1,2}-\d+\b", scope_value(s, "recognition") or "")
                    desigs = s.get("standard designation number and date list") or \
                        [x for x in [scope_value(s, "standard designation")] if x]
                    sdos = s.get("standards development organization list") or \
                        [x for x in [scope_value(s, "standards development")] if x]
                    tm = scope_value(s, "biocompatibility")
                    sdt = scope_value(s, "status date")
                    key = [" ".join(recs) or " ".join(sdos + desigs)]
                    if tm:
                        key.append(tm)
                    if not key[0]:
                        continue
                    sn = scope_iri(aid, *key)
                    if sdt and any(sn == b[0] and b[2] != sdt for b in scope_blocks):
                        # FDA lists one standard twice when it was granted again on a later date
                        key.append(sdt)
                        sn = scope_iri(aid, *key)
                    sp = [("a", "ex:ASCAScopeEntry"),
                          ("rdfs:label", common.label(aid, " ".join(recs + sdos + desigs), tm)),
                          ("ex:scopeOf", f"<{node}>")]
                    sp += [("ex:recognitionNumber", common.lit(x)) for x in recs]
                    sp += [("ex:standardsDevelopmentOrganization", common.lit(x)) for x in sdos]
                    sp += [("ex:standardDesignation", common.lit(x)) for x in desigs]
                    sp += [("ex:fdaStandardId", common.lit(x)) for x in s.get("fda_standard_ids", [])]
                    sp += [("ex:biocompatibilityTestMethod", common.lit(tm)),
                           ("ex:ascaStatus", common.lit(scope_value(s, "status") if "status" in s else None)),
                           ("ex:ascaStatusDate", _date(sdt)),
                           ("ex:scopeExclusion", common.lit(scope_value(s, "exclusion")))]
                    sp += [("ex:ascaCategory", common.lit(c)) for c in s.get("categories", [])]
                    for rec in recs:
                        sp.append(("ex:hasConsensusStandard", f"<{iri.consensus_standard(rec)}>"))
                        pairs.append(("ex:ascaScopeStandard", f"<{iri.consensus_standard(rec)}>"))
                        stds.add(rec)
                    if recs:
                        rep["scope_with_recognition_number"] += 1
                    else:
                        rep["scope_without_recognition_number"].append({"asca_id": aid, "designation": key[0]})
                    sp += common.provenance(DATASET, f"{aid} {' '.join(key)}", source_url=url, retrieved_at=retrieved_at)
                    scope_blocks.append((sn, sp, sdt))
                pairs += common.provenance(DATASET, aid, source_url=url, retrieved_at=retrieved_at)
                w.block(node, list(dict.fromkeys(pairs)))
                st.records += 1
                rep["accreditation_bodies" if kind == "ab" else "labs_withdrawn" if kind == "withdrawn" else "labs"] += 1
                merged = {}
                for sn, sp, _ in scope_blocks:
                    # a row FDA lists twice (same standard, same date) merges into one node
                    merged.setdefault(sn, [])
                    merged[sn] += [p for p in sp if p not in merged[sn]]
                for sn, sp in merged.items():
                    w.block(sn, sp)
                    rep["scope_entries"] += 1
                rep["scope_rows_merged"] = rep.get("scope_rows_merged", 0) + len(scope_blocks) - len(merged)
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for rec in sorted(stds, key=lambda x: [int(p) for p in x.split("-")]):
            w.block(iri.consensus_standard(rec), [("a", "ex:RecognizedConsensusStandard"),
                                                  ("ex:recognitionNumber", common.lit(rec))])
    st.triples = w.triples
    rep["distinct_standards"] = len(stds)
    rep["firms"] = len(firms)
    Path(out).with_suffix(".report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"  asca: {rep['labs']} labs, {rep['labs_withdrawn']} withdrawn labs, {rep['accreditation_bodies']} "
          f"bodies, {rep['scope_entries']} scope entries, {len(stds)} standards")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- fragments copied verbatim from the FDA pages (November 3, 2026); the
#  contact cells are kept in the sample to prove they are not ingested.
# ===========================================================================
_LIST_LAB = """<table id="stds-results-table">
	<tbody>
		<tr valign="top" bgcolor="#ffffff" style="font-size:11px; text-transform: none;">
		<td align="left" style="text-transform: none;">
			<a href="asca-accredited-testing-laboratories.cfm?stds_id=82" style="text-decoration:underline;text-transform: none;">TL-8</a>
		</td>
		<td align="left" style="text-transform: none;"><A href="asca-accredited-testing-laboratories.cfm?stds_id=82" style="text-decoration:underline;text-transform: none;" >UL LLC - Northbrook </A></td>
		<td align="left" style="text-transform: none;"> Basic safety and essential performance</td>
		<td align="left" style="text-transform: none;"> Accreditation Granted </td>
		<td align="left" style="text-transform: none;"> CA </td>
		<td align="left" style="text-transform: none;"> UNITED STATES</td>
		<td align="left" style="text-transform: none;">
		Pamela Gwynn Pamela.K.Gwynn@ul.com<BR>
		333 Pfingsten Rd. Northbrook,Unit B,<BR>Brea CA,92821
		</td>
		</tr>
	</tbody></table>"""

_LIST_WD = """<table id="stds-results-table">
	<tbody>
		<tr valign="top" bgcolor="#ffffff" style="font-size:11px; text-transform: none;">
		<td align="left" style="text-transform: none;"><a href="withdrawn-asca-testing-laboratories.cfm?stds_id=79" style="text-decoration:underline;text-transform: none;">TL-77</a></td>
		<td align="left" style="text-transform: none;"><A href="withdrawn-asca-testing-laboratories.cfm?stds_id=79" style="text-decoration:underline;text-transform: none;" >SGS-CSTC Standards Technical Services Co., Ltd. Guangzhou Branch </A></td>
		<td align="left" style="text-transform: none;"> Basic safety and essential performance</td>
		<td align="left" style="text-transform: none;"> FDA Initiated Withdrawal </td>
		<td align="left" style="text-transform: none;"> 05/01/2025 </td>
		<td align="left" style="text-transform: none;">  CN</td>
		<td align="left" style="text-transform: none;"> CHINA</td>
		<td align="left" style="text-transform: none;">
		Bonnie Liao Bonnie.liao@sgs.com<BR>
		No. 198 of Kezhu Road, Science Park, Guangzhou Economic & Technology Development District, Guangzhou,Guangdong  CN<br>CHINA
		</td>
		</tr>
	</tbody></table>"""

_LIST_AB = """<table id="stds-results-table">
	<tbody>
		<tr valign="top" bgcolor="#ffffff" style="font-size:11px; text-transform: none;">
		<td align="left" style="text-transform: none;"><A style="text-decoration:underline;text-transform: none;" href="asca-recognized-accreditation-bodies.cfm?stds_id=4">AB-14</A></td>
		<td align="left" style="text-transform: none;"><A style="text-decoration:underline;text-transform: none;" href="asca-recognized-accreditation-bodies.cfm?stds_id=4">International Acccreditation Service, Inc. (IAS)</A></td>
		<td align="left" style="text-transform: none;">Basic safety and essential performance Biocompatibility</td>
		<td align="left" style="text-transform: none;font-weight:bold;">Recognition Granted</td>
		<td align="center" style="text-transform: none;">CA</td>
		<td align="center" style="text-transform: none;">UNITED STATES</td>
		<td align="left" style="text-transform: none;">Tassos Papadopoulos ptassos@iasonline.org<BR>3060 Saturn Street Suite 100,Brea CA,92821 </td>
		</tr>
	</tbody></table>"""

_DETAIL_LAB = """<table border="0" cellpadding="0" cellspacing="0" align="center" style="font-family: arial; color: #000000; font-size:12px;">
		<tr>
		<td colspan="3" align="left">
		<span style="font-weight:bold;font-size:14px;">Test Lab:</span>
		<span style="font-weight:normal;font-size:14px;">UL LLC - Northbrook [TL-8]</span>
		</td>
		</tr>
		<tr>
		<td colspan="3" align="left" >
		<span style="font-weight:bold;font-size:14px;">Accreditation Status:</span>
		<span style="font-weight:bold;font-size:14px;">Accreditation Granted - Start Date: 04/12/2021
		</span>
		</td>
		</tr>
		<tr>
		<td colspan="3" align="left" >
		<span style="font-weight:bold;font-size:14px;">Accreditation Body Organization Name:</span>
		<A href="asca-recognized-accreditation-bodies.cfm?stds_id=4" style="text-decoration:underline;text-transform: none;" > International Acccreditation Service, Inc. (IAS)</A>
		</td>
		</tr>
		<tr>
		<td colspan="3" align="left" >
		<span style="font-weight:bold;font-size:14px;">Test Lab Primary Contact:</span>
		<span style="font-weight:normal;font-size:14px;"> Pamela Gwynn, Pamela.K.Gwynn@ul.com, 333 Pfingsten Rd. Northbrook Unit B Brea CA US 92821</span>
		</td>
		</tr>
			<tr valign="bottom"><td bgcolor="#e9e9e3"><table border="0" width="100%" style="font-size:12px;background-color: #e9e9e3; border-bottom:#e9e9e3;"><tr><td bgcolor="#e9e9e3" id="stds-form-title-left" nowrap align="left" style="font-weight:bold;text-decoration:underline;border-bottom:#e9e9e3;">Scope of ASCA Accreditation</td><td style="font-size:12px;border-bottom:#e9e9e3;" id="stds-form-title-right" align="right" nowrap><form name="substandardsform2" action="StandardsTLScopeExcel.cfm" method="post" id="standardsform2"><input type="hidden" name="stds_id_list" value="82" /></form></td><td id="" style="font-size:12px;border-bottom:#e9e9e3;" nowrap align="right"><a href="javascript: history.go(-1)">Return to list of Testing Labs</a></td></tr></table></td></tr>
				<tbody>
				<tr>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Status</div></td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;">
				<div style="float: left; font-size:10px;text-transform: none;">Status<br>Date</div>
				<div class="sort" style="float: right; vertical-align: middle">
				<a href="asca-accredited-testing-laboratories.cfm?stds_id=82&sortscope=sd#scope" title="Sort by Standard Designation Number and Date [0-9]"><img width="9" height="9" border="0" id="img_up_3" src="/scripts/cdrh/images/arrow_up-black.GIF" alt="Sort by Standard Designation Number and Date [0 - 9]"></a><br>
</div>
				</td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;">
				<div style="float: left; font-size:10px;text-transform: none;">Category</div>
				</td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;">
				<div style="float: left; font-size:10px;text-transform: none;">Biocompatibility<br>Test<br>Method</div>
				</td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;">
				<div style="float: left; font-size:10px;text-transform: none;">Recognition<br>Number</div>
				</td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Standards<br>Development<br>Organization</div></td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Standard Designation<br>Number and Date</div></td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Title</div></td>
				<td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Exclusions</div></td>
				</tr>
						<tr valign="middle" bgcolor="#f9f9f3">
					<td align="center">Accreditation Granted</td>
					<td align="center">04/12/2021</td>
					<td align="left">Basic safety and essential performance</td>
						<td align="left">&nbsp;</td>
					<td align="center">
					6-421
					</td>
					<td align="center">
					ISO
					</td>
					<td align="left">
					80601-2-56 Second edition 2017-03 [Including AMD1:2018]
					</td>
					<td>
							<A style="text-decoration:underline;text-transform: none;" href="./detail.cfm?standard__identification_no=39379">Medical electrical equipment - Part 2-56: Particular requirements for basic safety and essential performance of clinical thermometers for body temperature measurement. [Including: Amendment 1 (2018)].</A>
					</td>
					<td align="left">
					<div style="overflow-y:auto;">
					</div>
					</td>
					</tr>
						<tr valign="middle" bgcolor="#ffffff">
					<td align="center">Accreditation Granted</td>
					<td align="center">04/12/2021</td>
					<td align="left">Basic safety and essential performance</td>
						<td align="left">&nbsp;</td>
					<td align="center">
					19-39
					</td>
					<td align="center">
					IEC
					</td>
					<td align="left">
					60601-1-12 Edition 1.1 2020-07 CONSOLIDATED VERSION
					</td>
					<td>
							<A style="text-decoration:underline;text-transform: none;" href="./detail.cfm?standard__identification_no=41100">Medical electrical equipment - Part 1-12</A>
					</td>
					<td align="left">
					<div style="overflow-y:auto;">
					</div>
					</td>
					</tr>
				</tbody>"""

_DETAIL_AB = """		<span style="font-weight:bold;">AB Organization Name:</span>
		<span style="font-weight:normal;">International Acccreditation Service, Inc. (IAS) [AB-14]</span>
		</td>
		</tr>
		<tr>
		<td colspan="3" align="left" >
		<span style="font-weight:bold;">Recognition Status:</span>
		<span style="font-weight:bold;">Recognition Granted - Date: 11/18/2020 </span>
		</td>
		</tr>
		<tr>
		<td colspan="3" align="left" >
		<span style="font-weight:bold;">AB Primary Contact:</span>
		<span style="font-weight:normal;">Tassos Papadopoulos, ptassos@iasonline.org, 3060 Saturn Street Suite 100 Brea CA US 92821</span>
		</td>
		</tr>
		<tr><td align="left" style="font-weight:bold;text-decoration:underline;">Scope of ASCA Recognition</td></tr>
		<tbody> <tr> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"> <div style="float: center; font-size:10px;text-transform: none;">Category</div></td> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"> <div style="float: left; font-size:10px;text-transform: none;">Biocompatibility<br>Test Method</div> </td> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Status</div></td> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"> <div style="float: left; font-size:10px;text-transform: none;">Recognition<br>Number</div> </td> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Standards<br>Development<br>Organization</div></td> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Standard Designation<br>Number and Date</div></td> <td align="center" scope="col" valign="top" valign="top" style="font-weight:bold;"><div style="float: center; font-size:10px;text-transform: none;">Title</div></td> </tr>
<tr valign="middle" bgcolor="#f9f9f3"> <td align="left">Basic safety and essential performance</td> <td align="left">&nbsp;</td> <td align="left"><strong>Recognition Granted</strong></td> <td
align="center">1-105</td> <td align="center">ISO <br><br></td> <td align="left">80601-2-72 First edition 2015-04-11</td> <td align="left"> <A style="text-decoration:underline;"
href="./detail.cfm?standard__identification_no=44730">Medical electrical equipment - Part 2-72: Particular requirements for basic safely and essential performance of home healthcare environment
ventilators for ventilator-dependent patients</A> <br><br> <A style="text-decoration:underline;" href="./detail.cfm?standard__identification_no="></A> </td> </tr>
<tr valign="middle" bgcolor="#f9f9f3"> <td align="left">Biocompatibility</td> <td align="left">Direct and Indirect Hemolysis</td> <td align="left"><strong>Recognition Granted</strong></td> <td
align="center">2-248<br><br>2-250</td> <td align="center">ISO <br><br>ASTM </td> <td align="left">10993-4 Third edition 2017-04<br><br>F756-17</td> <td align="left"> <A style="text-decoration:underline;"
href="./detail.cfm?standard__identification_no=46605">Biological evaluation of medical devices--Part 4: Selection of tests for interactions with blood</A> <br><br> <A style="text-decoration:underline;"
href="./detail.cfm?standard__identification_no=36854">Standard Practice for Assessment of Hemolytic Properties of Materials</A> </td> </tr>
</tbody>"""


def _selftest():
    import tempfile
    assert categories("Basic safety and essential performance Biocompatibility") == CATEGORY_NAMES
    lab = parse_list(_LIST_LAB, "lab")
    assert lab == [{"stds_id": "82", "asca_id": "TL-8", "name": "UL LLC - Northbrook",
                    "categories": ["Basic safety and essential performance"], "status": "Accreditation Granted",
                    "state": "CA", "country": "UNITED STATES"}], lab
    wd = parse_list(_LIST_WD, "withdrawn")
    assert wd[0]["date"] == "05/01/2025" and wd[0]["country"] == "CHINA" and wd[0]["state"] == "CN", wd
    d = parse_detail(_DETAIL_LAB)
    assert d["status"] == "Accreditation Granted" and d["status_date"] == "04/12/2021" and d["ab_stds_ids"] == ["4"], d
    m = _STATUS.match("FDA Initiated Withdrawal - Start Date: 06/24/2021 - End Date: 05/01/2025")
    assert m.groups() == ("FDA Initiated Withdrawal", "06/24/2021", "05/01/2025"), m.groups()
    # rows of the "Scope History" table (other columns) must not be read as current scope
    hist = _DETAIL_AB.replace("</tbody>", "</tbody></table><b>Scope History</b><table><tbody><tr><td>Action<BR>Type</td>"
                              "<td>Recognition<br>Number</td><td>Designation<BR>Number</td></tr><tr valign=\"middle\">"
                              "<td>Transition</td><td> 1-98<br> </td><td> 80601-2-12 First edition 2011-04-15<br> </td>"
                              "</tr></tbody>")
    assert len(parse_detail(hist)["scope"]) == 2 and parse_detail(hist)["has_scope_history"]
    assert len(d["scope"]) == 2 and scope_value(d["scope"][0], "recognition") == "6-421", d["scope"]
    assert d["scope"][0]["fda_standard_id"] == "39379"
    a = parse_detail(_DETAIL_AB)
    assert a["status"] == "Recognition Granted" and a["status_date"] == "11/18/2020", a
    assert scope_value(a["scope"][0], "recognition") == "1-105" and scope_value(a["scope"][0], "standards dev") == "ISO"
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        p = t / "pages"
        p.mkdir()
        (p / "list_lab.html").write_text(_LIST_LAB, encoding="utf-8")
        (p / "list_withdrawn.html").write_text(_LIST_WD, encoding="utf-8")
        (p / "list_ab.html").write_text(_LIST_AB, encoding="utf-8")
        (p / "lab_82.html").write_text(_DETAIL_LAB, encoding="utf-8")
        (p / "ab_4.html").write_text(_DETAIL_AB, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-03", files=[]).write(t / "manifest.json")
        st = transform(t, t / "asca.ttl")
        ttl = (t / "asca.ttl").read_text(encoding="utf-8")
    assert st.records == 3, st
    assert f"<{lab_iri('TL-8')}>\n    a ex:ASCATestingLaboratory" in ttl and lab_iri("TL-8").endswith("/asca-testing-laboratory/tl_8")
    assert f"ex:accreditedBy <{ab_iri('AB-14')}>" in ttl
    assert f"ex:ascaScopeStandard <{iri.consensus_standard('6-421')}>" in ttl
    assert f"ex:hasConsensusStandard <{iri.consensus_standard('19-39')}>" in ttl
    assert f"<{scope_iri('TL-8', '6-421')}>" in ttl and f"ex:scopeOf <{lab_iri('TL-8')}>" in ttl
    assert 'ex:standardDesignation "80601-2-56 Second edition 2017-03 [Including AMD1:2018]"' in ttl
    assert 'ex:ascaStatusDate "2021-04-12"^^xsd:date' in ttl and 'ex:ascaStatusDate "2025-05-01"^^xsd:date' in ttl
    assert 'ex:ascaStatus "FDA Initiated Withdrawal"' in ttl and 'ex:ascaCategory "Biocompatibility"' in ttl
    assert f"ex:hasOrganizationFirm <{iri.firm_name(normalize_org('UL LLC - Northbrook'))}>" in ttl
    assert f"<{iri.consensus_standard('1-105')}>\n    a ex:RecognizedConsensusStandard" in ttl
    # one row, two standards (ISO 10993-4 and ASTM F756): both linked, designations kept
    hem = scope_iri("AB-14", "2-248 2-250", "Direct and Indirect Hemolysis")
    assert f"<{hem}>" in ttl, hem
    blk = ttl.split(f"<{hem}>", 1)[1].split("\n\n", 1)[0]
    assert f"ex:hasConsensusStandard <{iri.consensus_standard('2-248')}>" in blk
    assert f"ex:hasConsensusStandard <{iri.consensus_standard('2-250')}>" in blk
    assert 'ex:standardDesignation "F756-17"' in blk and 'ex:standardsDevelopmentOrganization "ASTM"' in blk
    assert 'ex:fdaStandardId "36854"' in blk and 'ex:biocompatibilityTestMethod "Direct and Indirect Hemolysis"' in blk
    for pii in ("Gwynn", "Pfingsten", "Papadopoulos", "iasonline", "Liao", "Kezhu", "@"):
        assert pii not in ttl.replace("@prefix", ""), pii
    print(f"asca.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
