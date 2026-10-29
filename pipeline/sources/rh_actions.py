#!/usr/bin/env python3
"""
rh_actions.py -- Radiation-Emitting Product Corrective Actions and Recalls (cfPCD_RH/rh_res.cfm).

STATUS 2026-10-12: FDA REFUSES EVERY SEARCH. The form page itself loads, but every
submission -- POST exactly as the form defines it (FirmName, DeviceName, Panel,
performanceStandards, RecallDateFrom/RecallDateTo in mm/dd/yyyy, PageNum 5-500,
Search=Search; no hidden fields; with or without the form's cookies and Referer), the
GET equivalent, a firm-name-only search, a product-name-only search, and a detail-style
URL -- returns HTTP 503 with FDA's "FDA Internet Application Site (Accessdata) Error /
The page you are requesting is currently unavailable" page. Per project rules the block
is reported, not worked around, so this module:
    fetch()      submits the search once (all fields blank, 500 rows per page); if FDA
                 answers with the error page it records the refusal in fetch_log.json and
                 returns an empty manifest;
    transform()  writes a header-only Turtle file when nothing was fetched, and stops
                 with an error if result pages exist, because their layout has never been
                 seen and a parser written blind would be a guess.
When the search works again: save one result page and one detail page, add parse_list /
parse_detail with a verbatim self-test sample (model: rh_codes.py), and emit
ex:RadiationCorrectiveAction at iri.radiation_corrective_action(<FDA id from the detail
link>), firm -> iri.firm_name, product code -> iri.product_code hub.
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
import iri        # noqa: E402,F401
import common     # noqa: E402,F401
import base       # noqa: E402
import _cfweb     # noqa: E402

NAME = "rh_actions"
DATASET = "cdrh/cfPCD_RH/rh_res"
FORM_URL = _cfweb.BASE + "cfPCD_RH/rh_res.cfm"
FIELDS = ["FirmName", "DeviceName", "Panel", "performanceStandards", "RecallDateFrom", "RecallDateTo", "PageNum"]


def form_fields(html):
    """Names of the inputs/selects in the BasicSearch form (to detect form changes)."""
    m = re.search(r'(?is)<form name="BasicSearch".*?</form>', html)
    if not m:
        raise ValueError("rh_res.cfm: BasicSearch form not found")
    return re.findall(r'(?i)<(?:input|select)[^>]*\bname="([^"]+)"', m.group(0))


def fetch(dest, delay=1.0):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay, retries=2)
    log = {"date": base.today(), "status": None}
    form = f.get(FORM_URL, "form", max_age_days=0)
    names = form_fields(form)
    missing = [n for n in FIELDS if n not in names]
    if missing:
        log.update(status="form changed", missing_fields=missing, fields=names)
    else:
        data = {k: "" for k in FIELDS}
        data.update(PageNum="500", Search="Search")
        try:
            f.get(FORM_URL, "results_all_p1", data=data, max_age_days=0)
            log["status"] = "results fetched (parser not yet written; see module docstring)"
        except _cfweb.Unavailable as e:
            log.update(status="refused by FDA", error=str(e))
    (dest / "fetch_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(f"  rh_actions: {log['status']}")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    m.write(dest / "manifest.json")
    return m


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if list((src / "pages").glob("results_*.html")) if (src / "pages").exists() else []:
        raise NotImplementedError("rh_actions: result pages exist but their parser is not written yet "
                                  "(the search was refused by FDA when the module was built)")
    st = base.Stats(source=NAME)
    with base.TurtleWriter(out) as w:
        pass
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim fragments (2026-10-12): the search form, and the page FDA returns (HTTP 503)
# for every search.
_FORM = """<form name="BasicSearch" id="BasicSearch" action="rh_res.cfm" method="POST" onreset="status='';" onsubmit="return _CF_checkBasicSearch(this)">
<td><INPUT type="text" name="FirmName" size="30" maxlength="30"></td>
<td><INPUT type="text" name="DeviceName" size="30" maxlength="30"></td>
<SELECT NAME="Panel"> <OPTION SELECTED> <option value="AN">Anesthesiology</option></SELECT>
<select name="performanceStandards"><option value=""></option><option value="1040.10">[1040.10] Laser products. </option></select>
<input name="RecallDateFrom" type="text" maxlength="10"  id="RecallDateFrom"  size="10"  />
<input name="RecallDateTo" type="text" maxlength="10"  id="RecallDateTo"  size="10"  />
<SELECT NAME="PageNum"><OPTION VALUE="5" SELECTED>5<OPTION VALUE="500">500</SELECT>
<INPUT TYPE="submit" Name="Search" VALUE="Search">
</form>"""
_REFUSED = ('<div id="main-content" class="article main-content container-fluid" role="article"> '
            '<h1>FDA Internet Application Site (Accessdata) Error</h1> <p>The page you are requesting is '
            'currently unavailable.</p>')


def _selftest():
    import tempfile
    names = form_fields(_FORM)
    assert all(n in names for n in FIELDS) and "Search" in names, names
    assert _cfweb.is_error_page(_REFUSED)
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        st = transform(t, t / "rh_actions.ttl")
        assert st.records == 0 and (t / "rh_actions.ttl").read_text(encoding="utf-8").startswith("@prefix")
        (t / "pages").mkdir()
        (t / "pages" / "results_all_p1.html").write_text("x", encoding="utf-8")
        try:
            transform(t, t / "rh_actions.ttl")
        except NotImplementedError:
            pass
        else:
            raise AssertionError("unparsed result pages must stop the transform")
    print("rh_actions.py self-test PASSED (search refused by FDA on 2026-10-12; no records).")


if __name__ == "__main__":
    _selftest()
