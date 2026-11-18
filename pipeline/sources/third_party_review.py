#!/usr/bin/env python3
"""
third_party_review.py -- 510(k) Third Party Review Program: FDA-recognized third party
review organizations (Accredited Persons), their status, and what each may review -> RDF.

PAGES (verified November 3, 2026; "Database updated November 02, 2026")
  T = https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfthirdparty/
  T + accredit.cfm                 current list, 7 organizations, each linked accredit.cfm?party_key=<n>
  T + accredit.cfm?party_key=<n>   one page per organization: name, organization address, then the
                                   classification regulations (21 CFR) it may review, by panel.
                                   FDA: "Product codes eligible to review by a specific 3P510k Review
                                   Organization are listed under the following regulations."
  T + current.cfm?panel=<XX>       the list of devices eligible for third party review, one page per
                                   review panel (20 panels): regulation -> eligible product codes
  https://www.fda.gov/about-fda/cdrh-transparency/510k-third-party-performance-metrics-and-accreditation-status
                                   "Accreditation Status" table: organizations whose recognition FDA
                                   withdrew (Accelerated Device Approval Services, LLC, 08/13/2021)

PERSONAL DATA
  Each organization page prints a contact person, a phone number and an e-mail address. None of
  them is read: the parser stops the address at the first e-mail / "contact:" cell.

MODEL
  ex:ThirdPartyReviewOrganization at third_party_review_organization(normalize_org(name)):
  ONE node per organization, keyed on the normalized firm name only, so that
  classification_detail's ex:accreditedPerson literal (lower-case on FDA's product code pages,
  e.g. "center for measurement standards of industrial") resolves to the same node through
  identity.normalize_org, and a withdrawn organization (no party key) is keyed the same way.
      rdfs:label FDA's name, ex:fdaPartyKey, ex:thirdPartyReviewStatus ("Recognized" /
      "Recognition withdrawn"), ex:withdrawalNoticeDate, ex:withdrawalDate, ex:relatedPageUrl
      (withdrawal letter), ex:address (organization address), ex:hasOrganizationFirm -> FirmName,
      ex:mayReviewRegulation -> iri.regulation(section),
      ex:mayReviewProductCode -> iri.product_code(code) for every product code FDA lists as
      eligible under those regulations (current.cfm).
  Typed hub nodes for every ProductCode, RegulationNumber and FirmName referenced.

MINTER (iri.py)
    iri.third_party_review_organization(normalize_org(name))
        -> .../id/third-party-review-organization/center_for_measurement_standards_industrial
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

NAME = "third_party_review"
DATASET = "cdrh/third-party-review"
T = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfthirdparty/"
LIST_URL = T + "accredit.cfm"
ORG_URL = T + "accredit.cfm?party_key={}"
ELIGIBLE_URL = T + "current.cfm"
PANEL_URL = T + "current.cfm?panel={}"
STATUS_URL = ("https://www.fda.gov/about-fda/cdrh-transparency/"
              "510k-third-party-performance-metrics-and-accreditation-status")


def org_iri(name):
    """iri.third_party_review_organization on the normalized firm name."""
    return iri.third_party_review_organization(normalize_org(name))


# ===========================================================================
#  Parsing
# ===========================================================================
def parse_list(page):
    """-> [(party_key, name)] from accredit.cfm (each organization link)."""
    out = []
    for k, name in re.findall(r'(?is)<a href="accredit\.cfm\?party_key=(\d+)">(.*?)</a>', page):
        out.append((k, _cfweb.one_line(name)))
    if not out:
        raise ValueError("third party list: no accredit.cfm?party_key links found")
    return list(dict.fromkeys(out))


_ORG_NAME = re.compile(r'(?is)<tr valign="bottom"><td\s*><b>(.*?)</b></td></tr>')
_ADDR_TD = re.compile(r'(?is)<tr><td colspan=2 align="left">(.*?)</td></tr>')
_PANEL_OR_REG = re.compile(r'<td colspan=4 align="left"><b>(.*?)</b></td>'
                           r'|<td align="center" width=15%">\s*([0-9]{3,4}\.[0-9]+)\s*</td>\s*'
                           r'<td width="70%">(.*?)</td>\s*<td align="center" width="10%">(.*?)</td>', re.I | re.S)


def parse_org(page):
    """-> dict: name, address, regulations [(panel, section, name, class)]."""
    m = _ORG_NAME.search(page)
    if not m:
        raise ValueError("third party organization page: name row not found")
    d = {"name": _cfweb.one_line(m.group(1)), "address": None, "regulations": []}
    lines = []
    for cell in _ADDR_TD.findall(page[m.end():]):
        if "mailto:" in cell.lower() or "contact:" in cell.lower() or "phone:" in cell.lower():
            break
        t = _cfweb.one_line(cell)
        if t:
            lines.append(t)
    d["address"] = ", ".join(lines) or None
    panel = None
    for x in _PANEL_OR_REG.finditer(page):
        if x.group(1) is not None:
            panel = _cfweb.one_line(x.group(1))
        else:
            d["regulations"].append((panel, x.group(2), _cfweb.one_line(x.group(3)), _cfweb.one_line(x.group(4))))
    return d


def parse_panels(page):
    return sorted(set(re.findall(r"current\.cfm\?panel=([A-Z]{2})", page)))


_ELIG = re.compile(r'(?is)<td align="center" nowrap>\s*([0-9]{3,4}\.[0-9]+)\s*</td>'
                   r'|classification\.cfm\?id=\d+"[^>]*>\s*([A-Z]{3})&nbsp;-&nbsp;')


def parse_eligible(page):
    """current.cfm?panel=XX -> {regulation: [product codes]} in page order."""
    out, reg = {}, None
    for x in _ELIG.finditer(page):
        if x.group(1):
            reg = x.group(1)
            out.setdefault(reg, [])
        elif reg:
            pc = common.product_code(x.group(2))
            if pc and pc not in out[reg]:
                out[reg].append(pc)
    return out


def parse_withdrawn(page):
    """fda.gov status page -> [{name, notice_date, withdrawal_date, letter_url}]."""
    i = page.find("Withdraw the Recognition of Third-Party Review Organization")
    if i < 0:
        return []
    table = page[i:page.find("</table>", i)]
    out = []
    for row in re.findall(r"(?is)<tr>(.*?)</tr>", table.split("<tbody>", 1)[-1]):
        c = re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", row)
        if len(c) < 5:
            continue
        h = re.search(r'href="([^"]+)"', c[4])
        url = htmllib.unescape(h.group(1)) if h else None
        if url and url.startswith("/"):
            url = "https://www.fda.gov" + url
        out.append({"name": _cfweb.one_line(c[0]), "notice_date": _cfweb.one_line(c[1]),
                    "withdrawal_date": _cfweb.one_line(c[3]), "letter_url": url})
    return out


# ===========================================================================
#  Fetch
# ===========================================================================
def fetch(dest, delay=1.0, refresh_days=1):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    orgs = parse_list(f.get(LIST_URL, "list", max_age_days=refresh_days))
    for k, _ in orgs:
        f.get(ORG_URL.format(k), f"org_{k}", max_age_days=refresh_days)
    panels = parse_panels(f.get(ELIGIBLE_URL, "eligible", max_age_days=refresh_days))
    for p in panels:
        f.get(PANEL_URL.format(p), f"panel_{p}", max_age_days=refresh_days)
    f.get(STATUS_URL, "status", max_age_days=refresh_days)
    files = [base.FileRef(url=u, path=f"pages/{n}.html", sha256=base.sha256_file(f.path(n)),
                          bytes=f.path(n).stat().st_size)
             for u, n in [(LIST_URL, "list"), (ELIGIBLE_URL, "eligible"), (STATUS_URL, "status")]]
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)
    man.write(dest / "manifest.json")
    print(f"  third_party_review: {len(orgs)} organizations, {len(panels)} panels; "
          f"fetched {f.fetched}, cached {f.cached}")
    return man


# ===========================================================================
#  Transform
# ===========================================================================
def transform(src, out, retrieved_at=None):
    src = Path(src)
    pages = src / "pages"
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    rd = lambda n: (pages / f"{n}.html").read_text(encoding="utf-8", errors="replace")  # noqa: E731
    orgs = parse_list(rd("list"))
    eligible = {}
    for p in parse_panels(rd("eligible")):
        if (pages / f"panel_{p}.html").exists():
            for reg, codes in parse_eligible(rd(f"panel_{p}")).items():
                eligible.setdefault(reg, [])
                eligible[reg] += [c for c in codes if c not in eligible[reg]]
    withdrawn = parse_withdrawn(rd("status")) if (pages / "status.html").exists() else []
    st = base.Stats(source=NAME)
    firms, regs, pcs, seen = {}, {}, set(), set()
    rep = {"organizations": 0, "recognized": 0, "withdrawn": 0, "eligible_regulations": len(eligible),
           "eligible_product_codes": len({c for v in eligible.values() for c in v}),
           "per_organization": {}, "regulations_without_eligible_codes": {}}
    blocks = []
    for k, listed_name in orgs:
        d = parse_org(rd(f"org_{k}"))
        name = d["name"] or listed_name
        codes, missing = [], []
        for panel, sec, rname, cls in d["regulations"]:
            regs.setdefault(sec, rname)
            if sec in eligible and eligible[sec]:
                codes += [c for c in eligible[sec] if c not in codes]
            else:
                missing.append(sec)
        pairs = [("a", "ex:ThirdPartyReviewOrganization"),
                 ("rdfs:label", common.lit(name)),
                 ("ex:fdaPartyKey", common.lit(k)),
                 ("ex:thirdPartyReviewStatus", common.lit("Recognized")),
                 ("ex:address", common.lit(d["address"]))]
        pairs += [("ex:mayReviewRegulation", f"<{iri.regulation(sec)}>") for _, sec, _, _ in d["regulations"]]
        pairs += [("ex:mayReviewProductCode", f"<{iri.product_code(c)}>") for c in sorted(codes)]
        pcs.update(codes)
        rep["per_organization"][name] = {"party_key": k,
                                         "regulations": len({sec for _, sec, _, _ in d["regulations"]}),
                                         "product_codes": len(codes)}
        if missing:
            rep["regulations_without_eligible_codes"][name] = sorted(set(missing))
        blocks.append((name, ORG_URL.format(k), k, pairs))
        rep["recognized"] += 1
    for wd in withdrawn:
        pairs = [("a", "ex:ThirdPartyReviewOrganization"),
                 ("rdfs:label", common.lit(wd["name"])),
                 ("ex:thirdPartyReviewStatus", common.lit("Recognition withdrawn")),
                 ("ex:withdrawalNoticeDate", common.date_lit(wd["notice_date"])),
                 ("ex:withdrawalDate", common.date_lit(wd["withdrawal_date"])),
                 ("ex:relatedPageUrl", common.lit(wd["letter_url"]))]
        blocks.append((wd["name"], STATUS_URL, normalize_org(wd["name"]), pairs))
        rep["withdrawn"] += 1
    with base.TurtleWriter(out) as w:
        for name, url, rid, pairs in sorted(blocks, key=lambda b: normalize_org(b[0])):
            nf = normalize_org(name)
            if not nf or nf in seen:
                st.skipped += 1
                continue
            seen.add(nf)
            pairs = pairs + [("ex:hasOrganizationFirm", f"<{iri.firm_name(nf)}>")]
            firms.setdefault(nf, name)
            pairs += common.provenance(DATASET, rid, source_url=url, retrieved_at=retrieved_at)
            w.block(org_iri(name), list(dict.fromkeys(pairs)))
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for sec in sorted(regs):
            w.block(iri.regulation(sec), [("a", "ex:RegulationNumber"), ("ex:regulationNumber", common.lit(sec))])
        for pc in sorted(pcs):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    rep["organizations"] = st.records
    rep["distinct_product_codes"] = len(pcs)
    rep["distinct_regulations"] = len(regs)
    Path(out).with_suffix(".report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(f"  third_party_review: {st.records} organizations ({rep['recognized']} recognized, "
          f"{rep['withdrawn']} withdrawn), {len(regs)} regulations, {len(pcs)} product codes")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- fragments copied verbatim from the FDA pages (November 3, 2026); the
#  contact person, phone and e-mail are kept in the sample to prove they are not ingested.
# ===========================================================================
_LIST = """<table border="0"><tr><td>
							<a href="accredit.cfm?party_key=3">Center For Measurement Standards of Industrial</a>
					</td></tr></table>
					<table border="0"><tr><td>
							<a href="accredit.cfm?party_key=12">COLA, Inc.</a>
					</td></tr></table>"""

_ORG3 = """			<tr valign="bottom"><td ><b>Center For Measurement Standards of Industrial</b></td></tr>
			<tr><td>&nbsp;</td></tr>
			<tr><td colspan=2 align="left">

				Bldg. 16, 321 Kuang Fu Rd.,Sec2


			</td></tr>


				<tr><td colspan=2 align="left">




				</td></tr>


			<tr><td colspan=2 align="left">

				Hsinchu,

					TW


			</td></tr>



				<tr><td colspan=2 align="left"><A href="mailto:3P510k_CMS@itri.org.tw">3P510k_CMS@itri.org.tw</A></td></tr>

				<table border=0 cellpadding=1 cellspacing=0>
				<tr><td colspan=2 align="left">
				<tr><td>contact:</td><td> ALBERT T.W.  LI, MANAGER, OFFICE OF MEDICAL DEVICE EVALUATION</td></tr>
				<tr><td>phone:</td><td>886   357  32227  </td></tr>

				</table>
			<tr bgcolor="#f9f9f3" style="font-family: arial; color: #23238e;">
			<th align="left" width="5%">Panel</th><th align="left" width="15%">Section Number</th><th width="70%" align="left">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Regulation Name&nbsp;/&nbsp;Device Name&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</th><th align="center" width="10%">Class</th>
			</tr>
			<tr bgcolor="#e9e9e3" style="font-family: arial; color: #23238e;">
			<td colspan=4 align="left"><b>Anesthesiology</b></td>
			</tr>
			<tr bgcolor="#ffffff" style="font-family: arial; color: #23238e;">
			<td width="5%">&nbsp;</td>
			<td align="center" width=15%">868.5440</td>
			<td width="70%"><a href="../cfpcd/classification.cfm?start_search=1&regulationnumber=868%2E5440&thirdparty=y&panel=AN" title="details about corresponding device(s) from the products classification database for 868.5440">Portable oxygen generator.</a></td>
			<td align="center" width="10%">II</td>
			</tr>
			<tr bgcolor="#e9e9e3" style="font-family: arial; color: #23238e;">
			<td colspan=4 align="left"><b>Clinical Chemistry</b></td>
			</tr>
			<tr bgcolor="#ffffff" style="font-family: arial; color: #23238e;">
			<td width="5%">&nbsp;</td>
			<td align="center" width=15%">862.1050</td>
			<td width="70%"><a href="../cfpcd/classification.cfm?start_search=1&regulationnumber=862%2E1050&thirdparty=y&panel=CH" title="details about corresponding device(s) from the products classification database for 862.1050">Alkaline phosphatase or isoenzymes test system.</a></td>
			<td align="center" width="10%">II</td>
			</tr>"""

_ORG12 = _ORG3.replace("Center For Measurement Standards of Industrial", "COLA, Inc.")

_ELIGIBLE = """<li><a href="current.cfm?panel=AN#toppage">Anesthesiology</a></li>
<li><a href="current.cfm?panel=CH#toppage">Clinical Chemistry</a></li>"""

_PANEL_AN = """			<tr valign="top"  bgcolor="#e9e9e3" >
				<td align="center" nowrap>868.5430</td>
				<td align="left">Gas-scavenging apparatus.</td>
				<td align="center">II</td>
				<td>&nbsp;</td>
			</tr>
				<tr bgcolor="#f9f9f3">
				<td align="center">&nbsp;</td>
				<td align="left"><a href="../cfpcd/classification.cfm?id=101" title="details on device: Apparatus, Gas-Scavenging in the CDRH Products Classification Database" style="text-decoration:underline;font-size:9pt;"> CBN&nbsp;-&nbsp; Apparatus, Gas-Scavenging</a></td>
				<td align="center">II</td>
				<td align="center">
				<ul>
				</ul>
				</td>
				</tr>
			<tr valign="top"  bgcolor="#e9e9e3" >
				<td align="center" nowrap>868.5440</td>
				<td align="left">Portable oxygen generator.</td>
				<td align="center">II</td>
				<td>&nbsp;</td>
			</tr>
				<tr bgcolor="#f9f9f3">
				<td align="center">&nbsp;</td>
				<td align="left"><a href="../cfpcd/classification.cfm?id=91" title="details on device: Generator, Oxygen, Portable in the CDRH Products Classification Database" style="text-decoration:underline;font-size:9pt;"> CAW&nbsp;-&nbsp; Generator, Oxygen, Portable</a></td>
				<td align="center">II</td>
				<td align="center">
				<ul>
				<li>1-148&nbsp;ISO&nbsp;80601-2-69 Second edition 2020-11
				</li>
				</ul>
				</td>
				</tr>"""

_STATUS = """<p>The FDA has withdrawn the recognition of third party review organizations listed in the table below:</p><div class="table-responsive"><table class="table table-striped" style="width:100%;" summary="Notice of Intent to Withdraw the Recognition of Third-Party Review Organization"><thead><tr><th scope="col">Organization</th><th scope="col">Date of Notice</th><th scope="col">Notice of Intent Letter</th><th scope="col">Date of Withdrawal</th><th scope="col">Withdrawal Letter</th></tr></thead><tbody><tr><td><a href="/media/148803/download?attachment" data-entity-type="media" data-entity-uuid="d642f34a-b056-42d1-869c-7a08b92e256e" data-entity-substitution="media_download" title="ADAS - NOOH Letter 3/12/2021">Accelerated Device Approval Services, LLC&nbsp;</a></td><td>03/12/2021</td><td><a href="/media/148803/download">Notice of Intent to Withdraw Accreditation</a>&nbsp;- (PDF-90KB)</td><td>08/13/2021</td><td><a href="/media/151597/download?attachment" data-entity-type="media" data-entity-uuid="828975c9-6e87-4fec-ab09-4014decdc6a5" data-entity-substitution="media_download" title="Withdrawal of Accreditation - Accelerated Device Approval Services">Withdrawal of Accreditation</a> - (378KB)</td></tr></tbody></table></div><h2>Additional Information</h2>"""


def _selftest():
    import tempfile
    assert parse_list(_LIST) == [("3", "Center For Measurement Standards of Industrial"), ("12", "COLA, Inc.")]
    d = parse_org(_ORG3)
    assert d["address"] == "Bldg. 16, 321 Kuang Fu Rd.,Sec2, Hsinchu, TW", d["address"]
    assert d["regulations"] == [("Anesthesiology", "868.5440", "Portable oxygen generator.", "II"),
                                ("Clinical Chemistry", "862.1050", "Alkaline phosphatase or isoenzymes test system.", "II")]
    assert parse_eligible(_PANEL_AN) == {"868.5430": ["CBN"], "868.5440": ["CAW"]}
    assert parse_panels(_ELIGIBLE) == ["AN", "CH"]
    wd = parse_withdrawn(_STATUS)
    assert wd == [{"name": "Accelerated Device Approval Services, LLC", "notice_date": "03/12/2021",
                   "withdrawal_date": "08/13/2021", "letter_url": "https://www.fda.gov/media/151597/download?attachment"}], wd
    # classification_detail writes the accredited person in lower case; it must reach the same node
    assert org_iri("center for measurement standards of industrial") == org_iri("Center For Measurement Standards of Industrial")
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        p = t / "pages"
        p.mkdir()
        for n, s in (("list", _LIST), ("org_3", _ORG3), ("org_12", _ORG12), ("eligible", _ELIGIBLE),
                     ("panel_AN", _PANEL_AN), ("status", _STATUS)):
            (p / f"{n}.html").write_text(s, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-03", files=[]).write(t / "manifest.json")
        st = transform(t, t / "tp.ttl")
        ttl = (t / "tp.ttl").read_text(encoding="utf-8")
    assert st.records == 3 and st.skipped == 0, st
    cms = org_iri("Center For Measurement Standards of Industrial")
    assert cms.endswith("/third-party-review-organization/center_for_measurement_standards_industrial"), cms
    assert f"<{cms}>\n    a ex:ThirdPartyReviewOrganization" in ttl
    assert f"ex:mayReviewRegulation <{iri.regulation('868.5440')}>" in ttl
    assert f"ex:mayReviewProductCode <{iri.product_code('CAW')}>" in ttl
    assert f"ex:mayReviewProductCode <{iri.product_code('CBN')}>" not in ttl      # 868.5430 not on its page
    assert f"<{iri.product_code('CAW')}>\n    a ex:ProductCode" in ttl
    assert f"<{iri.regulation('862.1050')}>\n    a ex:RegulationNumber" in ttl
    assert 'ex:withdrawalDate "2021-08-13"^^xsd:date' in ttl and 'ex:thirdPartyReviewStatus "Recognition withdrawn"' in ttl
    assert 'ex:fdaPartyKey "3"' in ttl and 'ex:address "Bldg. 16, 321 Kuang Fu Rd.,Sec2, Hsinchu, TW"' in ttl
    assert f"ex:hasOrganizationFirm <{iri.firm_name('COLA')}>" in ttl
    for pii in ("ALBERT", "MANAGER", "357", "itri.org", "mailto", "@"):
        assert pii not in ttl.replace("@prefix", ""), pii
    print(f"third_party_review.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
