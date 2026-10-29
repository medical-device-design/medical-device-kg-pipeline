#!/usr/bin/env python3
"""
rh_codes.py -- Radiation-Emitting Electronic Product Codes (cfPCD_rh) -> RDF.

FDA publishes no download for this database. The full code list is the "Product Code"
drop-down on the Advanced Search page (646 codes on 2026-10-12):
    https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD_rh/classification.cfm
(the text search TextResults.cfm?uq=* stops at 500 results, so it is not used). Each
code has a detail page classification.cfm?PCD=<code> with, as label/value table rows:
    Product, Definition, Regulation Description, Product Description (medical specialty
    and radiological product group, one per line), Product Code, Radiation Type,
    Radiation Safety Report Type Required?, Annual Reports Required?, Regulation Number,
    Applicable Performance Standard(s) ("Laser products. [1040.10]" or the general
    "[1000-1005]"), Guidance Document(s).

Emits
    ex:RadiationProductClassification   iri.radiation_product_classification(code)
        ex:hasProductCode <iri.product_code(code)>  (+ typed ex:ProductCode hub node)
        ex:hasPerformanceStandard <iri.cfr_section('1040.10')>   for section citations
        ex:performanceStandardCitation "1040.10" / "1000-1005"  (always, as published)
        ex:hasRegulationNumber <iri.regulation('892.5050')>
        ex:hasGuidanceDocument <.../radiation-product-classification/<code>/guidance/<n>>
    ex:GuidanceDocument (rdfs:label = title, ex:url)

Proposed minter (used locally via iri._instance, not added to iri.py):
    radiation_product_guidance(code, n) -> .../id/radiation-product-classification/<code>/guidance/<n>
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

NAME = "rh_codes"
DATASET = "cdrh/cfPCD_rh"
INDEX_URL = _cfweb.BASE + "cfPCD_rh/classification.cfm"
DETAIL_URL = _cfweb.BASE + "cfPCD_rh/classification.cfm?PCD={}"

_OPT = re.compile(r'(?i)<option\s+value="([A-Za-z]{3})"')
_ROW = re.compile(r"(?is)<tr[^>]*>\s*<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>\s*</tr>")
_STD = re.compile(r"(?s)(.*?)\[\s*(?:<a[^>]*>)?\s*([\d.]+(?:-\d+)?)\s*(?:</a>)?\s*\]")
_GUIDE = re.compile(r'(?is)<a[^>]*href="([^"]+)"[^>]*>(.*?)(?:</a>|</li>)')
_SEC = re.compile(r"^\d{4}\.\d+$")


def guidance_iri(code, n):
    return iri._instance("radiation-product-classification", iri.slug(code), "guidance", str(int(n)))


# ---------------------------------------------------------------------------
def parse_index(html):
    """Codes from the ProductCode <select> on the advanced search page."""
    i = html.find('NAME="ProductCode"')
    if i < 0:
        i = html.lower().find('name="productcode"')
    if i < 0:
        raise ValueError("cfPCD_rh advanced search page changed: no ProductCode select")
    j = html.lower().find("</select>", i)
    return sorted({c.upper() for c in _OPT.findall(html[i:j])})


def parse_detail(html):
    """-> dict of fields from one classification.cfm?PCD= page, or None if no record."""
    a = html.find("Back To Search Results")
    if a < 0:
        return None
    b = html.find("end database content", a)
    seg = html[a:b if b > 0 else len(html)]
    d = {"fields": {}, "standards": [], "guidance": []}
    for th, td in _ROW.findall(seg):
        lab = _cfweb.one_line(th)
        if not lab:
            continue
        if lab.startswith("Applicable Performance Standard"):
            for m in _STD.finditer(td):
                d["standards"].append((_cfweb.one_line(m.group(1)) or None, m.group(2)))
            if not d["standards"]:                      # e.g. "None" (27 codes on 2026-10-12)
                d["standards_text"] = _cfweb.one_line(td)
        elif lab == "Product Description":
            d["fields"][lab] = _cfweb.text(td)
        else:
            d["fields"][lab] = _cfweb.one_line(td)
    g = re.search(r"(?is)<strong>\s*Guidance Documents?\s*</strong>(.*)", seg)
    if g:
        for url, title in _GUIDE.findall(g.group(1)):
            d["guidance"].append((_cfweb.one_line(title), url.strip()))
    if not d["fields"].get("Product Code"):
        return None
    return d


# ---------------------------------------------------------------------------
def fetch(dest, delay=1.0):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    codes = parse_index(f.get(INDEX_URL, "index", max_age_days=7))
    (dest / "codes.json").write_text(json.dumps(codes), encoding="utf-8")
    failed = []
    for c in codes:
        try:
            f.get(DETAIL_URL.format(c), f"pcd_{c}")       # detail pages are cached as final
        except _cfweb.Unavailable as e:
            failed.append([c, str(e)])
    (dest / "fetch_log.json").write_text(json.dumps({
        "date": base.today(), "codes": len(codes), "fetched_now": f.fetched,
        "from_cache": f.cached, "failed": failed}, indent=1), encoding="utf-8")
    print(f"  rh_codes: {len(codes)} codes; fetched {f.fetched}, cached {f.cached}, failed {len(failed)}")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    return m


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    codes = json.loads((src / "codes.json").read_text(encoding="utf-8"))
    st = base.Stats(source=NAME)
    hubs = set()
    with base.TurtleWriter(out) as w:
        for c in codes:
            p = src / "pages" / f"pcd_{c}.html"
            d = parse_detail(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else None
            if not d:
                st.skipped += 1
                continue
            fl = d["fields"]
            pc = common.product_code(fl.get("Product Code")) or c
            hubs.add(pc)
            desc = (fl.get("Product Description") or "").split("\n")
            desc = [x.strip() for x in desc if x.strip()]
            pairs = [("a", "ex:RadiationProductClassification"),
                     ("rdfs:label", common.label(pc, fl.get("Product"))),
                     ("ex:productCode", common.lit(pc)),
                     ("ex:hasProductCode", f"<{iri.product_code(pc)}>"),
                     ("ex:deviceName", common.lit(fl.get("Product"))),
                     ("ex:definition", common.lit(fl.get("Definition"))),
                     ("ex:regulationDescription", common.lit(fl.get("Regulation Description"))),
                     ("ex:productDescription", common.lit(" / ".join(desc)) if desc else None),
                     ("ex:radiationProductGroup", common.lit(desc[-1]) if desc else None),
                     ("ex:medicalSpecialty", common.lit(" / ".join(desc[:-1])) if len(desc) > 1 else None),
                     ("ex:radiationType", common.lit(fl.get("Radiation Type"))),
                     ("ex:radiationSafetyReportType", common.lit(fl.get("Radiation Safety Report Type Required?"))),
                     ("ex:annualReportsRequired", common.bool_lit(fl.get("Annual Reports Required?"))
                      or common.lit(fl.get("Annual Reports Required?")))]
            reg = fl.get("Regulation Number")
            if reg:
                pairs.append(("ex:regulationNumber", common.lit(reg)))
                if re.match(r"^\d{3,4}\.\d+$", reg):
                    pairs.append(("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>"))
            for title, cit in d["standards"]:
                pairs.append(("ex:performanceStandardCitation", common.lit(cit)))
                pairs.append(("ex:performanceStandardTitle", common.lit(title)))
                if _SEC.match(cit):
                    pairs.append(("ex:hasPerformanceStandard", f"<{iri.cfr_section(cit)}>"))
            pairs.append(("ex:performanceStandardText", common.lit(d.get("standards_text"))))
            known = {"Product", "Definition", "Regulation Description", "Product Description", "Product Code",
                     "Radiation Type", "Radiation Safety Report Type Required?", "Annual Reports Required?",
                     "Regulation Number"}
            for lab in sorted(set(fl) - known):          # any field FDA adds later: kept, labelled
                pairs.append(("ex:additionalField", common.lit(f"{lab}: {fl[lab]}") if fl[lab] else None))
            for n, _ in enumerate(d["guidance"], 1):
                pairs.append(("ex:hasGuidanceDocument", f"<{guidance_iri(pc, n)}>"))
            pairs += common.provenance(DATASET, pc, source_url=DETAIL_URL.format(pc), retrieved_at=retrieved_at)
            w.block(iri.radiation_product_classification(pc), pairs)
            for n, (title, url) in enumerate(d["guidance"], 1):
                w.block(guidance_iri(pc, n), [("a", "ex:GuidanceDocument"),
                                              ("rdfs:label", common.lit(title or url)),
                                              ("ex:url", common.lit(url))])
            st.records += 1
        for pc in sorted(hubs):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim fragments of classification.cfm (advanced search) and ?PCD=EAP / ?PCD=RAA,
# fetched 2026-10-12.
_INDEX = ('<td><SELECT NAME="ProductCode" ID="ProductCode"><OPTION SELECTED><OPTION VALUE="BWA">BWA'
          '<OPTION VALUE="CBA">CBA<OPTION VALUE="EAP">EAP</SELECT></td>')
_EAP = """Back To Search Results</A></td>
				<table BORDER=0 CELLPADDING=0 CELLSPACING=5 width="500">
					<TR style="font-family: arial; color: #23238E;"><TH align="left">Product</TH><TD align="left" style="text-transform: capitalize">unit, x-ray, intraoral</TD></TR>
					<TR valign="top"><TH align="left" nowrap>Regulation Description</TH><TD align="left">Intraoral source x-ray system.</TD></TR>
				<TR valign="top"><TH align="left">Product Description</TH>
				<TD>
							Dental
							Dental Diagnostic X-Ray Equipment
				</TD></TR>
				<TR><TH align="left" nowrap>Product Code</TH><TD>EAP</TD></TR>
					<TR>
					<TH align="left" nowrap>Radiation Type</TH>
					<TD>
								Ionizing
					</TD>
					</TR>
					<TR>
					<TH align="left" nowrap>Radiation Safety Report Type Required?</TH>
					<TD>None</A>
					</TD>
					</TR>
					<TR>
					<TH align="left" nowrap>Annual Reports Required?</TH>
					<TD>No</A>
					</TD>
					</TR>
						<TR>
						<TH align="left" nowrap>Regulation Number</TH>
						<TD><A style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfCFR/CFRSearch.cfm?FR=872.1810">872.1810</A></TD>
						</TR>
				<TR valign="top">
				<TH align="left" nowrap>Applicable Performance Standard(s)</TH>
				<td>
							Diagnostic x-ray systems and their major components. [<a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfCFR/CFRSearch.cfm?FR=1020.30">1020.30</a>]<br>
							 Radiographic equipment. [<a style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfCFR/CFRSearch.cfm?FR=1020.31">1020.31</a>]<br>
							<br>
				</td></TR>
					<tr valign="top">
					<td valign="top" align="left" colspan="2"><strong>Guidance Document</strong>
						<table class="hardbreak" cellpadding="0" cellspacing="0" border="0" align="left" style="margin:0 10px; width:100%">
						<tr><td><ul><li><A style="text-decoration:underline;" href="https://www.fda.gov/regulatory-information/search-fda-guidance-documents/medical-x-ray-imaging-devices-conformance-iec-standards">Medical X-Ray Imaging Devices Conformance with IEC Standards: Guidance for Industry and Food and Drug Administration Staff</li></ul></td></tr>
						</table>
					</td>
					</tr>
				</table>
<!-- ********  end database content"""
_RAA = """Back To Search Results</A></td>
				<table BORDER=0 CELLPADDING=0 CELLSPACING=5 width="500">
					<TR style="font-family: arial; color: #23238E;"><TH align="left">Product</TH><TD align="left" style="text-transform: capitalize">laser products (pre-standard)</TD></TR>
				<TR valign="middle"><TH align="left" nowrap>Definition</TH>
				<td class="hardbreak">
					<div style="width:700px; word-wrap: break-word;">A laser product assembled or manufactured before August 1, 1976</div>
				</td></tr>
				<TR valign="top"><TH align="left">Product Description</TH>
				<TD>
							Laser Products (Pre-Standard)
				</TD></TR>
				<TR><TH align="left" nowrap>Product Code</TH><TD>RAA</TD></TR>
					<TR>
					<TH align="left" nowrap>Radiation Type</TH>
					<TD>
								Optical
					</TD>
					</TR>
					<TR>
					<TH align="left" nowrap>Annual Reports Required?</TH>
					<TD>No</A>
					</TD>
					</TR>
				<TR valign="top">
				<TH align="left" nowrap>Applicable Performance Standard(s)</TH>
				<td>
							No standard applicable. General Radiological Health requirements apply. [1000-1005]<br>
				</td></TR>
				</table>
<!-- ********  end database content"""


def _selftest():
    import tempfile
    assert parse_index(_INDEX) == ["BWA", "CBA", "EAP"]
    d = parse_detail(_EAP)
    assert d["fields"]["Product Code"] == "EAP" and d["fields"]["Radiation Type"] == "Ionizing", d
    assert d["standards"] == [("Diagnostic x-ray systems and their major components.", "1020.30"),
                              ("Radiographic equipment.", "1020.31")], d["standards"]
    assert d["guidance"][0][1].startswith("https://www.fda.gov/regulatory-information/"), d["guidance"]
    assert parse_detail(_RAA)["standards"][0][1] == "1000-1005"
    none = parse_detail(_RAA.replace(  # the cell as published for QAF, 2026-10-12
        "No standard applicable. General Radiological Health requirements apply. [1000-1005]<br>",
        "\n\t\t\t\t\t\n\t\t\t\t\t\tNone\n\t\t\t\t\t\n"))
    assert none["standards"] == [] and none["standards_text"] == "None", none
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "pcd_EAP.html").write_text(_EAP, encoding="utf-8")
        (t / "pages" / "pcd_RAA.html").write_text(_RAA, encoding="utf-8")
        (t / "codes.json").write_text(json.dumps(["EAP", "RAA", "ZZZ"]), encoding="utf-8")
        st = transform(t, t / "rh_codes.ttl", retrieved_at="2026-10-12")
        ttl = (t / "rh_codes.ttl").read_text(encoding="utf-8")
    assert st.records == 2 and st.skipped == 1, st
    assert f"<{iri.radiation_product_classification('EAP')}>" in ttl
    assert f"ex:hasProductCode <{iri.product_code('EAP')}>" in ttl
    assert f"<{iri.product_code('RAA')}>\n    a ex:ProductCode" in ttl
    assert f"ex:hasPerformanceStandard <{iri.cfr_section('1020.31')}>" in ttl
    assert 'ex:performanceStandardCitation "1000-1005"' in ttl and ttl.count("ex:hasPerformanceStandard ") == 2
    assert f"ex:hasRegulationNumber <{iri.regulation('872.1810')}>" in ttl
    assert 'ex:medicalSpecialty "Dental"' in ttl and 'ex:radiationProductGroup "Dental Diagnostic X-Ray Equipment"' in ttl
    assert 'ex:definition "A laser product assembled or manufactured before August 1, 1976"' in ttl
    assert 'ex:annualReportsRequired "false"^^xsd:boolean' in ttl
    assert 'rdfs:label "EAP: unit, x-ray, intraoral"' in ttl and "a ex:GuidanceDocument" in ttl
    print(f"rh_codes.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
