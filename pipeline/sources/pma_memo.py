#!/usr/bin/env python3
"""
pma_memo.py -- PMA Summary Review Memos for 180-Day Design Changes (cfPMA pmamemos.cfm) -> RDF.

FDA lists every 180-day PMA supplement for which it publishes a summary review memo
(325 records on October 12, 2026) on one search page, sorted by decision date:
    https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pmamemos.cfm
        ?start_search=1&device=&sort=ddd&pagenum=500
Each row: device name | "P020056/S054" (link to pma.cfm?id=P020056S054) | decision date
(MM/DD/YYYY) | Review Memo PDF (may be blank) | Approval Order PDF (may be blank).
The page states "1 to 325 of 325 records"; if a later run shows fewer rows than the
total, the next page (start_search=501, ...) is fetched as well.

Modeling
  * One ex:PMAChangeMemo per row, IRI iri.pma_memo(pma, supplement).
  * ex:relatedPMARecord <iri.pma(pma, supplement)> -- the supplement node pma.py writes.
  * PDF links kept as ex:reviewMemoUrl / ex:approvalOrderUrl. The page gives no product
    code, so no ex:hasProductCode is written.
The list page changes as FDA adds memos, so it is fetched again on every run (one
request); the previous copy is kept as pages/list_<start>.html.
"""
import os
import re
import sys
import html as htmllib
import threading
import time
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

NAME = "pma_memo"
DATASET = "cdrh/pma-memos"
PAGE_SIZE = 500
LIST_URL = ("https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pmamemos.cfm"
            "?start_search={start}&device=&sort=ddd&pagenum=" + str(PAGE_SIZE))
PMA_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pma.cfm?id={}"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
END_MARK = "end database content"           # present on every complete cfdocs page
BUSY_MARK = "Your request is being processed"   # FDA's queue page; never cache it

_ROW = re.compile(r"(?is)<tr>\s*<td>(.*?)</tr>")
_CELL = re.compile(r"(?is)<td[^>]*>(.*?)</td>")
_APP = re.compile(r"pma\.cfm\?id=(P\d{6})(S\d+)?", re.I)
_APP_TEXT = re.compile(r"\b(P\d{6})\s*/\s*(S\d+)\b", re.I)
_HREF = re.compile(r'(?is)href="([^"]+)"[^>]*>\s*([^<]*?)\s*</a>')
_TOTAL = re.compile(r"of\s+([\d,]+)\s+records", re.I)


# ===========================================================================
#  Polite fetching: 1 request per second, retries with backoff, FDA queue page
# ===========================================================================
class Fetcher:
    def __init__(self, delay=1.0):
        self.delay, self.lock, self.next = delay, threading.Lock(), time.monotonic()

    def _slot(self):
        with self.lock:
            wait = self.next - time.monotonic()
            self.next = max(self.next, time.monotonic()) + self.delay
        if wait > 0:
            time.sleep(wait)

    def pause(self, secs):
        with self.lock:
            self.next = max(self.next, time.monotonic() + secs)

    def get(self, url, tries=5):
        """Body text of a complete page. Raises after `tries` failures."""
        err = None
        for i in range(tries):
            self._slot()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                body = urllib.request.urlopen(req, timeout=90).read()
                text = body.decode("utf-8", errors="replace")
                if END_MARK in text and BUSY_MARK not in text:
                    return text
                err = "FDA queue page" if BUSY_MARK in text else "incomplete page"
                self.pause(30 * 2 ** i)
            except Exception as e:                  # network error, 5xx, timeout
                err = str(e)
                self.pause(5 * 2 ** i)
        raise RuntimeError(f"{url}: {err}")


def _save(path, text):
    tmp = path.with_suffix(".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)                               # never leave a half-written page


# ===========================================================================
#  Parsing
# ===========================================================================
def _text(fragment):
    t = re.sub(r"(?s)<[^>]+>", " ", fragment or "")
    return " ".join(htmllib.unescape(t).replace("\xa0", " ").split())


def parse_list(page):
    """-> (total_or_None, [row dict]) from one pmamemos.cfm result page."""
    m = _TOTAL.search(_text(page[:200000]))
    total = int(m.group(1).replace(",", "")) if m else None
    rows = []
    for r in _ROW.findall(page):
        cells = _CELL.findall("<td>" + r)
        if len(cells) < 3:
            continue
        a = _APP.search(cells[1]) or _APP_TEXT.search(_text(cells[1]))
        if not a or not a.group(2):
            continue
        links = {t.lower(): u for u, t in _HREF.findall(" ".join(cells[3:]))}
        rows.append({
            "pma": a.group(1).upper(), "supplement": a.group(2).upper(),
            "device": _text(cells[0]),
            "decision_date": _text(cells[2]),
            "review_memo": next((u for t, u in links.items() if t.startswith("review")), None),
            "approval_order": next((u for t, u in links.items() if t.startswith("approval")), None),
        })
    return total, rows


# ===========================================================================
#  Contract
# ===========================================================================
def fetch(dest, delay=1.0):
    dest = Path(dest)
    pages = dest / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    f, start, seen, files = Fetcher(delay), 1, 0, []
    while True:
        url = LIST_URL.format(start=start)
        text = f.get(url)
        p = pages / f"list_{start}.html"
        _save(p, text)
        total, rows = parse_list(text)
        seen += len(rows)
        files.append(base.FileRef(url=url, path=str(p.relative_to(dest)),
                                  sha256=base.sha256_file(p), bytes=p.stat().st_size))
        if not rows or total is None or seen >= total:
            break
        start += PAGE_SIZE
    print(f"  pma_memo: {seen} rows on {len(files)} page(s); FDA total {total}")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def _list_pages(src):
    pages = sorted((Path(src) / "pages").glob("list_*.html"),
                   key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    return pages


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    recs, total = {}, None
    for p in _list_pages(src):
        t, rows = parse_list(p.read_text(encoding="utf-8", errors="replace"))
        total = total or t
        for r in rows:
            key = (r["pma"], r["supplement"])
            if key in recs:
                st.skipped += 1                    # same supplement listed twice
                continue
            recs[key] = r
    with base.TurtleWriter(out) as w:
        for (pma, supp), r in sorted(recs.items()):
            pairs = [
                ("a", "ex:PMAChangeMemo"),
                ("rdfs:label", common.label(f"{pma}/{supp}", r["device"])),
                ("ex:pmaNumber", common.lit(pma)),
                ("ex:supplementNumber", common.lit(supp)),
                ("ex:deviceName", common.lit(r["device"])),
                ("ex:decisionDate", common.date_lit(r["decision_date"])),
                ("ex:reviewMemoUrl", common.lit(r["review_memo"])),
                ("ex:approvalOrderUrl", common.lit(r["approval_order"])),
                ("ex:pmaDatabaseUrl", common.lit(PMA_URL.format(pma + supp))),
                ("ex:relatedPMARecord", f"<{iri.pma(pma, supp)}>"),
            ]
            pairs += common.provenance(DATASET, f"{pma}/{supp}", source_url=LIST_URL.format(start=1),
                                       retrieved_at=retrieved_at)
            w.block(iri.pma_memo(pma, supp), pairs)
            st.records += 1
    st.triples = w.triples
    if total is not None and st.records != total:
        print(f"  pma_memo: WARNING {st.records} records written, FDA states {total}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- rows copied verbatim from pmamemos.cfm on October 12, 2026
# ===========================================================================
_SAMPLE = """<td align="left" id="pma-results-number">

1 to 325 of 325 records

</td>
		<th scope="col" style="width:15%;" align="center">Review<br>Memo</th>
		<th scope="col" style="width:15%;" align="center">Approval<br>Order</th>
	</tr>
		<tr>
		<td>natrelle silicone filled breast implants</td>
		<td align="center"><A style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfpma/pma.cfm?id=P020056S054">P020056/S054</A></td>
		<td align="center">10/27/2021</td>

			<td>&nbsp;</td>


			<td align="center"><A style="text-decoration:underline;" HREF="https://www.accessdata.fda.gov/cdrh_docs/pdf2/P020056S054A.pdf" TITLE="PDF for P020056S054A"  style="text-decoration:underline;">Approval</A></td>

		</tr>
<tr>
<td>attain ability mri , plus mri,straight mri surescan; attain performa mri, straight ans s mri surescan</td>
<td align="center"><A style="text-decoration:underline;" href="/scripts/cdrh/cfdocs/cfpma/pma.cfm?id=P080006S085">P080006/S085</A></td>
<td align="center">02/01/2016</td>
<td align="center"><A style="text-decoration:underline;" HREF="https://www.accessdata.fda.gov/cdrh_docs/pdf8/P080006S085M.pdf" TITLE="PDF for P080006S085M"  style="text-decoration:underline;">Review</A></td>
<td>&nbsp;</td>
</tr>
<!-- ********  end database content ******** -->"""


def _selftest():
    import tempfile
    total, rows = parse_list(_SAMPLE)
    assert total == 325 and len(rows) == 2, (total, rows)
    a, b = rows
    assert (a["pma"], a["supplement"], a["review_memo"]) == ("P020056", "S054", None), a
    assert a["approval_order"].endswith("/pdf2/P020056S054A.pdf"), a
    assert b["review_memo"].endswith("/pdf8/P080006S085M.pdf") and b["approval_order"] is None, b
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "pages").mkdir()
        _save(d / "pages" / "list_1.html", _SAMPLE)
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-12", files=[]).write(d / "manifest.json")
        st = transform(d, d / "pma_memo.ttl")
        ttl = (d / "pma_memo.ttl").read_text(encoding="utf-8")
    assert st.records == 2, st
    assert f"<{iri.pma_memo('P020056', 'S054')}>" in ttl
    assert f"ex:relatedPMARecord <{iri.pma('P020056', 'S054')}>" in ttl
    assert 'ex:decisionDate "2021-10-27"^^xsd:date' in ttl
    assert ttl.count("ex:reviewMemoUrl") == 1 and ttl.count("ex:approvalOrderUrl") == 1
    assert 'rdfs:label "P080006/S085: attain ability mri' in ttl
    assert "ex:hasProductCode" not in ttl
    assert ttl.count("rdfs:label") == 2
    print(f"pma_memo.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
