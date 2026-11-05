#!/usr/bin/env python3
"""
cecv.py -- CDRH Export Certificate Validation (CECV) -> RDF.

Search page: https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/DTLS/rl_cert.cfm
No download exists. A search returns at most 500 rows ("The maximum 500 records meeting
your search criteria returned"), so the database is enumerated by CERTIFICATE EXPIRATION
DATE windows, using the same GET the result page's own sort links use:
    rl_cert.cfm?start_search=1&facilityName=&applicationNumber=&certDateFrom=MM/DD/YYYY
               &certDateTo=MM/DD/YYYY&PAGENUM=500&sortcolumn=eda
Windows start as calendar years (1990-2035); any window that hits the 500 cap is halved,
down to a single day. A single day that still hits the cap is fetched again sorted by
certificate number descending (sortcolumn=and) and the union kept; if that union is still
capped the day is listed in fetch_log.json as possibly incomplete.
Observed 2026-10-12: expiration dates run from 01/2024 to 12/28/2025 (certificates issued
2021-2023); windows from 10/2026 on return 0.

Result rows give facility name, certificate type, expiration date and certificate number,
and link to a detail page rl_cert.cfm?rid=<certificate>&mid=<facility>, which adds
"Number of Pages per Certificate". A certificate can name more than one facility; the list
then repeats the number once per facility (each with its own detail page), and the one
certificate node carries every facility. The pages show no FEI or registration number, so no
establishment link is made.

Emits ex:ExportCertificate  iri.export_certificate(number)
        ex:certificateNumber, ex:certificateType, ex:expirationDate (xsd:date),
        ex:pagesPerCertificate (xsd:integer), ex:facilityName,
        ex:hasFacilityFirm <iri.firm_name(normalized facility)>  (+ ex:FirmName node)
"""
import json
import os
import re
import sys
import urllib.parse
from datetime import date, timedelta
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

NAME = "cecv"
DATASET = "cdrh/cecv"
PAGE = _cfweb.BASE + "cfRL/DTLS/rl_cert.cfm"
LIST_URL = (PAGE + "?start_search=1&facilityName=&applicationNumber=&certDateFrom={f}&certDateTo={t}"
            "&PAGENUM=500&sortcolumn={s}")
CAP = 500
FIRST_YEAR, LAST_YEAR = 1990, 2035
LIST_MAX_AGE_DAYS = 7          # result lists are refreshed weekly; detail pages are final

_ROWS = re.compile(r'(?is)<a[^>]*href="(\./rl_cert\.cfm\?rid=[^"]*)"[^>]*>(.*?)</a>\s*</td>\s*'
                   r'<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>')
_COUNT = re.compile(r"(?i)(\d[\d,]*)\s+results?\b")
_DET = re.compile(r"(?is)<td[^>]*>\s*<b>\s*([^<:]+?)\s*:?\s*</b>\s*</td>\s*<td[^>]*>(.*?)</td>")


def _us(d):
    return d.strftime("%m/%d/%Y")


def list_url(d1, d2, sort="eda"):
    return LIST_URL.format(f=urllib.parse.quote(_us(d1), safe=""), t=urllib.parse.quote(_us(d2), safe=""), s=sort)


def parse_list(html):
    """-> (count shown, capped?, rows [dict]) for one result page."""
    m = _COUNT.search(html)
    n = int(m.group(1).replace(",", "")) if m else 0
    capped = n >= CAP or "The maximum <B>500</B> records" in html
    rows = []
    for href, fac, typ, exp, num in _ROWS.findall(html):
        rows.append({"href": href.replace("&amp;", "&"), "facility": _cfweb.one_line(fac),
                     "type": _cfweb.one_line(typ), "expiration": _cfweb.one_line(exp),
                     "number": _cfweb.one_line(num)})
    return n, capped, rows


def parse_detail(html):
    a = html.find("Back To Search Results")
    if a < 0:
        return {}
    b = html.find("end database content", a)
    return {k.strip(): _cfweb.one_line(v) for k, v in _DET.findall(html[a:b if b > 0 else len(html)])}


def _name(d1, d2, sort):
    return f"list_{d1.isoformat()}_{d2.isoformat()}_{sort}"


# ---------------------------------------------------------------------------
def fetch(dest, delay=1.0, details=True, log=print):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    leaves, incomplete, failed = [], [], []

    def window(d1, d2):
        try:
            body = f.get(list_url(d1, d2), _name(d1, d2, "eda"), max_age_days=LIST_MAX_AGE_DAYS)
        except _cfweb.Unavailable as e:
            failed.append([d1.isoformat(), d2.isoformat(), str(e)])
            return
        n, capped, rows = parse_list(body)
        if not capped:
            if n:
                leaves.append(_name(d1, d2, "eda"))
            return
        if d1 < d2:
            mid = d1 + timedelta(days=(d2 - d1).days // 2)
            window(d1, mid)
            window(mid + timedelta(days=1), d2)
            return
        leaves.append(_name(d1, d2, "eda"))           # one day over the cap: add the reverse sort
        try:
            body2 = f.get(list_url(d1, d2, "and"), _name(d1, d2, "and"), max_age_days=LIST_MAX_AGE_DAYS)
            leaves.append(_name(d1, d2, "and"))
            got = {r["number"] for r in rows} | {r["number"] for r in parse_list(body2)[2]}
            if len(got) >= 2 * CAP:
                incomplete.append(d1.isoformat())
        except _cfweb.Unavailable as e:
            failed.append([d1.isoformat(), d2.isoformat(), str(e)])
            incomplete.append(d1.isoformat())

    for y in range(FIRST_YEAR, LAST_YEAR + 1):
        window(date(y, 1, 1), date(y, 12, 31))
        log(f"  cecv: year {y} done; {len(leaves)} result pages so far")
    (dest / "windows.json").write_text(json.dumps(leaves), encoding="utf-8")

    rows = _collect(dest, leaves)
    log(f"  cecv: {len(rows)} certificates listed; fetching detail pages = {details}")
    det_failed = 0
    if details:
        for i, (num, rs) in enumerate(sorted(rows.items()), 1):
            for k, r in enumerate(rs):                 # one detail page per facility on the certificate
                try:
                    f.get(urllib.parse.urljoin(PAGE, r["href"]), _detail_name(num, k))
                except _cfweb.Unavailable:
                    det_failed += 1
            if i % 500 == 0:
                log(f"  cecv: details {i}/{len(rows)} (fetched {f.fetched}, cached {f.cached}, failed {det_failed})")
    (dest / "fetch_log.json").write_text(json.dumps({
        "date": base.today(), "result_pages": len(leaves), "certificates": len(rows),
        "fetched_now": f.fetched, "from_cache": f.cached, "detail_failed": det_failed,
        "possibly_incomplete_days": incomplete, "failed_windows": failed}, indent=1), encoding="utf-8")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    return m


def _detail_name(num, k):
    return f"cert_{num}" if k == 0 else f"cert_{num}_{k}"


def _collect(src, leaves):
    """certificate number -> list of result rows, one per facility (in page order). A
    certificate can name several facilities (52 of 21,030 on 2026-10-12, e.g. COVIDIEN and
    COVIDIEN LLC); the result list then repeats the number once per facility."""
    rows = {}
    for nm in leaves:
        p = Path(src) / "pages" / f"{nm}.html"
        if p.exists():
            for r in parse_list(p.read_text(encoding="utf-8", errors="replace"))[2]:
                if r["number"]:
                    lst = rows.setdefault(r["number"], [])
                    if all(x["href"] != r["href"] for x in lst):
                        lst.append(r)
    return rows


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    leaves = json.loads((src / "windows.json").read_text(encoding="utf-8"))
    rows = _collect(src, leaves)
    st = base.Stats(source=NAME)
    firms = {}
    with base.TurtleWriter(out) as w:
        for num in sorted(rows):
            rs = rows[num]
            dets = []
            for k in range(len(rs)):
                p = src / "pages" / f"{_cfweb.safe_name(_detail_name(num, k))}.html"
                det = parse_detail(p.read_text(encoding="utf-8", errors="replace")) if p.exists() else {}
                if det.get("Certificate Number") and det["Certificate Number"] != num:
                    det = {}                              # page belongs to another certificate
                dets.append(det)
            facs = sorted({d.get("Facility Name") or r["facility"] for d, r in zip(dets, rs)} - {None})
            pages = sorted({d.get("Number of Pages per Certificate") for d in dets} - {None})
            types = sorted({d.get("Certificate Type") or r["type"] for d, r in zip(dets, rs)} - {None})
            exps = sorted({d.get("Expiration Date") or r["expiration"] for d, r in zip(dets, rs)} - {None})
            pairs = [("a", "ex:ExportCertificate"),
                     ("rdfs:label", common.label(num, " / ".join(facs))),
                     ("ex:certificateNumber", common.lit(num))]
            pairs += [("ex:certificateType", common.lit(t)) for t in types]
            pairs += [("ex:expirationDate", common.date_lit(e)) for e in exps]
            pairs += [("ex:pagesPerCertificate", common.int_lit(n) or common.lit(n)) for n in pages]
            pairs += [("ex:facilityName", common.lit(fac)) for fac in facs]
            for fac in facs:
                nf = normalize_org(fac)
                if nf:
                    pairs.append(("ex:hasFacilityFirm", f"<{iri.firm_name(nf)}>"))
                    firms.setdefault(nf, fac)
            pairs += common.provenance(DATASET, num, source_url=urllib.parse.urljoin(PAGE, rs[0]["href"]),
                                       retrieved_at=retrieved_at)
            w.block(iri.export_certificate(num), pairs)
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim fragments of a result page (facilityName=a, sorted by expiration) and of the
# detail page rid=3179-12-2021, fetched 2026-10-12.
_LIST = """<span>500 Results</span>
					<TR bgcolor="#ffffff">
				<td align="left">
						<A style="text-decoration:underline;" href="./rl_cert.cfm?rid=3179%2D12%2D2021&mid=META%20BIOMED%2C%20INC%2E">META BIOMED, INC.</A>
				</td>
				<td align="left">
							Certificate to Foreign Government (CFG)
				</td>
				<td align="center">
							01/02/2024
				</td>
				<td align="center">
						3179-12-2021
				</td>
				</tr>
					<TR bgcolor="#ffffff">
				<td align="left">
						<A style="text-decoration:underline;" href="./rl_cert.cfm?rid=NE106%2D12%2D2021&mid=T%2EA%2EG%2E%20MEDICAL%20PRODUCTS%20CORPORATION%20%20LTD">T.A.G. MEDICAL PRODUCTS CORPORATION  LTD</A>
				</td>
				<td align="left">
							Certificate for Device Not Exported from the United States (CDNE)
				</td>
				<td align="center">
							01/02/2024
				</td>
				<td align="center">
						NE106-12-2021
				</td>
				</tr>
					<TR bgcolor="#ffffff">
				<td align="left">
						<A style="text-decoration:underline;" href="./rl_cert.cfm?rid=4377%2D1%2D2022&mid=COVIDIEN">COVIDIEN</A>
				</td>
				<td align="left">
							Certificate of Exportability Section 801 (e) (1)
				</td>
				<td align="center">
							01/31/2024
				</td>
				<td align="center">
						4377-1-2022
				</td>
				</tr>
					<TR bgcolor="#f9f9f3">
				<td align="left">
						<A style="text-decoration:underline;" href="./rl_cert.cfm?rid=4377%2D1%2D2022&mid=COVIDIEN%20LLC">COVIDIEN LLC</A>
				</td>
				<td align="left">
							Certificate of Exportability Section 801 (e) (1)
				</td>
				<td align="center">
							01/31/2024
				</td>
				<td align="center">
						4377-1-2022
				</td>
				</tr>
			* &nbsp; The maximum <B>500</B> records meeting your search criteria returned. Please narrow your search."""
_DETAIL = """Back To Search Results</A></td>
		<TABLE CELLPADDING=0 CELLSPACING=10 BORDER="0" ALIGN="CENTER">
		<TR ALIGN="LEFT">
		<TD><B>Facility Name: </B> </td>
		<TD>META BIOMED, INC.</td>
		</tr>
		<TR ALIGN="LEFT">
		<TD><B>Certificate Number: </B></td>
		<TD>3179-12-2021</td>
		</tr>
		<TR ALIGN="LEFT">
		<TD><B>Certificate Type: </B></td>
		<TD>Certificate to Foreign Government (CFG) </td>
		</tr>
		<TR ALIGN="LEFT">
		<TD><B>Number of Pages per Certificate:</B></td>
		<TD>3 </td>
		</tr>
		<TR ALIGN="LEFT">
		<TD><B>Expiration Date: </B></td>
		<TD>01/02/2024</td></tr>
		</table>
<!-- ********  end database content"""


def _selftest():
    import tempfile
    n, capped, rows = parse_list(_LIST)
    assert n == 500 and capped and len(rows) == 4, (n, capped, rows)
    assert rows[1]["number"] == "NE106-12-2021" and rows[1]["type"].startswith("Certificate for Device Not"), rows
    assert parse_list("<b>0 results found</b>")[:2] == (0, False)
    d = parse_detail(_DETAIL)
    assert d["Number of Pages per Certificate"] == "3" and d["Certificate Number"] == "3179-12-2021", d
    assert list_url(date(2024, 1, 1), date(2024, 1, 31)).endswith(
        "certDateFrom=01%2F01%2F2024&certDateTo=01%2F31%2F2024&PAGENUM=500&sortcolumn=eda")
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "list_a.html").write_text(_LIST, encoding="utf-8")
        (t / "pages" / "cert_3179-12-2021.html").write_text(_DETAIL, encoding="utf-8")
        (t / "windows.json").write_text(json.dumps(["list_a"]), encoding="utf-8")
        st = transform(t, t / "cecv.ttl", retrieved_at="2026-10-12")
        ttl = (t / "cecv.ttl").read_text(encoding="utf-8")
    assert st.records == 3, st
    covidien = ttl.split(f"<{iri.export_certificate('4377-1-2022')}>\n", 1)[1].split("\n\n")[0]
    assert covidien.count("ex:facilityName") == 2 and covidien.count("ex:hasFacilityFirm") >= 1, covidien
    assert 'rdfs:label "4377-1-2022: COVIDIEN / COVIDIEN LLC"' in covidien
    assert f"<{iri.export_certificate('3179-12-2021')}>" in ttl and f"<{iri.export_certificate('NE106-12-2021')}>" in ttl
    assert 'ex:expirationDate "2024-01-02"^^xsd:date' in ttl and 'ex:pagesPerCertificate "3"^^xsd:integer' in ttl
    assert f"ex:hasFacilityFirm <{iri.firm_name(normalize_org('META BIOMED, INC.'))}>" in ttl
    assert "a ex:FirmName" in ttl and "establishment" not in ttl
    assert 'ex:sourceUrl "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/DTLS/rl_cert.cfm?rid=3179%2D12%2D2021' in ttl
    print(f"cecv.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
