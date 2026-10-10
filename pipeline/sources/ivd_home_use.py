#!/usr/bin/env python3
"""
ivd_home_use.py -- FDA database of in vitro diagnostic tests cleared or approved for
home use / over-the-counter sale (cfdocs/cfivd) -> RDF.

FDA publishes no bulk file. The search (results.cfm, POST) returns at most 500 rows,
but every results page carries a hidden "id" field listing all detail ids for the
query, and ivdexcel.cfm (POST id=<list>&posted=1) returns those rows as CSV:

    Web Address, Test Name, Document Number, Analyte, Effective Date

fetch() walks effective-date windows, halving any window that comes back with 500
or more ids, so no window is truncated; one CSV is saved per window. The legacy graph
had 53 tests from a capped scrape. Detail pages list FDA contact persons; they are
never fetched or ingested.

One ex:IVDHomeUseTest per detail id; a test listed with several analytes gets one
ex:analyteName per analyte. Document numbers starting K/DEN or P link to the
510(k) or PMA node.
"""
import csv
import datetime as dt
import io
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "ivd_home_use"
DATASET = "cdrh/cfivd"
BASE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfivd/"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
CAP = 500
FIRST = dt.date(1976, 1, 1)


def _post(url, data, delay):
    time.sleep(delay)
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=120).read().decode("utf-8", "replace")


def ids_in(html):
    m = re.search(r'name="id"\s+value="([^"]*)"', html, re.I)
    if not m:
        return []
    return [x for x in urllib.parse.unquote(m.group(1)).split(",") if x.strip().isdigit()]


def _search(d0, d1, delay):
    return ids_in(_post(BASE_URL + "results.cfm", {
        "test_name": "", "manufacturer_name": "", "document_number": "", "analyte_id": "",
        "effective_date_from": d0.strftime("%m/%d/%Y"), "effective_date_to": d1.strftime("%m/%d/%Y"),
        "sortcolumn": "", "search": "Search"}, delay))


def fetch(dest, delay=1.0):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    windows, files, total = [(FIRST, dt.date.today())], [], 0
    while windows:
        d0, d1 = windows.pop(0)
        ids = _search(d0, d1, delay)
        if len(ids) >= CAP:
            if d0 == d1:
                raise RuntimeError(f"{len(ids)} tests on a single day {d0}; cannot split further")
            mid = d0 + (d1 - d0) // 2
            windows[:0] = [(d0, mid), (mid + dt.timedelta(days=1), d1)]
            continue
        if not ids:
            continue
        body = _post(BASE_URL + "ivdexcel.cfm", {"id": ",".join(ids), "posted": "1"}, delay)
        if "Web Address" not in body[:400]:
            raise RuntimeError(f"ivdexcel.cfm returned no CSV for {d0}..{d1}")
        p = dest / f"cfivd_{d0:%Y%m%d}_{d1:%Y%m%d}.csv"
        p.write_text(body, encoding="utf-8")
        files.append({"url": BASE_URL + "ivdexcel.cfm", "path": p.name, "ids": len(ids)})
        total += len(ids)
    print(f"ivd_home_use: {total} detail ids in {len(files)} windows")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)
    m.write(dest / "manifest.json")
    return m


def _date(v):
    v = (v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return dt.datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _rows(src):
    for p in sorted(Path(src).glob("*.csv")):
        text = p.read_text(encoding="utf-8", errors="replace").replace("<br>", "")
        for r in csv.DictReader(io.StringIO(text)):
            yield {(k or "").strip(): (v or "").strip() for k, v in r.items()}


def _doc_link(doc):
    u = (doc or "").upper()
    try:
        if u.startswith("K") or u.startswith("DEN"):
            return ("ex:relatedK510Record", f"<{iri.k510(doc)}>")
        if u.startswith("P"):
            return ("ex:relatedPMARecord", f"<{iri.pma(doc.split('/')[0])}>")
    except ValueError:
        return None
    return None


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    tests = {}
    for r in _rows(src):
        m = re.search(r"id=(\d+)", r.get("Web Address", ""))
        if not m:
            st.skipped += 1
            continue
        t = tests.setdefault(m.group(1), {"name": r.get("Test Name", ""), "docs": [], "analytes": [],
                                          "dates": [], "url": r.get("Web Address", "")})
        for k, key in (("docs", "Document Number"), ("analytes", "Analyte"), ("dates", "Effective Date")):
            v = r.get(key, "")
            if v and v not in t[k]:
                t[k].append(v)
    with base.TurtleWriter(out) as w:
        for tid in sorted(tests, key=int):
            t = tests[tid]
            url = t["url"] if t["url"].startswith("http") else BASE_URL + "detail.cfm?id=" + tid
            pairs = [("a", "ex:IVDHomeUseTest"), ("rdfs:label", common.lit(t["name"])),
                     ("ex:testName", common.lit(t["name"]))]
            pairs += [("ex:documentNumber", common.lit(d)) for d in t["docs"]]
            pairs += [("ex:analyteName", common.lit(a)) for a in t["analytes"]]
            for d in t["dates"]:
                iso = _date(d)
                pairs.append(("ex:dateEffective", common.date_lit(iso)) if iso else ("ex:dateEffectiveText", common.lit(d)))
            for d in t["docs"]:
                link = _doc_link(d)
                if link:
                    pairs.append(link)
            pairs += common.provenance(DATASET, tid, source_url=url, retrieved_at=retrieved_at)
            w.block(iri.ivd_home_use_test(tid), pairs)
            st.records += 1
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import tempfile
    html = '<form><input type="hidden" name="id" value="2332%2C1104%2C2528"></form>'
    assert ids_in(html) == ["2332", "1104", "2528"]
    csvtext = ("Web Address,Test Name,Document Number,Analyte,Effective Date\n"
               "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfivd/detail.cfm?id=2332,Acme hCG Test,K150001,hCG,03/18/2015<br>\n"
               "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfivd/detail.cfm?id=2332,Acme hCG Test,K150001,LH,03/18/2015<br>\n"
               "detail.cfm?id=1104,Glucose Meter X,P990001/S002,Glucose,1/5/2015<br>\n"
               "no link,Bad row,,,\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "cfivd_a.csv").write_text(csvtext, encoding="utf-8")
        st = transform(d, d / "i.ttl", retrieved_at="2026-10-10")
        ttl = (d / "i.ttl").read_text(encoding="utf-8")
    assert st.records == 2 and st.skipped == 1, st
    blk = ttl.split(f"<{iri.ivd_home_use_test('2332')}>\n", 1)[1].split("\n\n")[0]
    assert blk.count("ex:analyteName") == 2 and blk.count("ex:documentNumber") == 1
    assert '"2015-03-18"^^xsd:date' in blk and f"<{iri.k510('K150001')}>" in blk
    assert f"<{iri.pma('P990001')}>" in ttl and '"2015-01-05"^^xsd:date' in ttl
    assert "detail.cfm?id=1104" in ttl
    print(f"ivd_home_use.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
