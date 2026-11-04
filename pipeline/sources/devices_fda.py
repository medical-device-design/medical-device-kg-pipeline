#!/usr/bin/env python3
"""
devices_fda.py -- Devices@FDA catalog, derived from the premarket bulk files -> RDF.

Devices@FDA (cfdocs/cfdevicesatfda) is a search front end over FDA's 510(k), De Novo,
PMA and HDE databases; it has no bulk file of its own. The legacy graph held 12,386
records scraped from it. This module rebuilds the catalog from the bulk downloads the
pipeline already validates -- downloads/k510 (openFDA device/510k, which includes
De Novo DEN numbers), downloads/pma (openFDA device/pma, originals and supplements)
and downloads/hde (hde.zip) -- so every decision document FDA lists appears once:

  ex:DeviceFDARecord  pathway (510(k) / De Novo / PMA / PMA supplement / HDE / HDE
                      supplement), document number, device name, applicant, decision
                      date, product code, and ex:describesRecord -> the full 510(k),
                      PMA or HDE node in the premarket layer.

Summary / decision-letter PDFs: ex:summaryUrl is written only for documents whose PDF
URL was checked with an HTTP HEAD request and answered 200 with a PDF. fetch() runs
those checks newest-first at 1 request per second, resumable through summary_urls.tsv
in its download folder (number, url or "none", checked date); a legacy URL list
(legacy_summary_urls.tsv: number<TAB>url) is re-checked first. URLs are never guessed
into the graph.

510(k) and De Novo PDFs for every record (fetch_k510): FDA files each 510(k) summary or
statement as cdrh_docs/<folder>/<K number>.pdf, and the pmn.cfm detail page links exactly
that file (sample of 40 detail pages, November 3, 2026). The folder follows the first two
digits of the K number: K02-K75 (years 2002-2075) -> pdf2 ... pdf9, pdf10 ... pdf75 (no
zero padding); K76-K99 and K00-K01 -> pdf. De Novo decision summaries are
cdrh_docs/reviews/DEN<number>.pdf. Each number gets exactly one HEAD request (1 per
second, newest first), recorded in k510_pdf_heads.tsv (number, url or "none", HTTP status,
checked date); only 200-with-PDF is a hit, 404 is a final "none", refusals (403, 429, 5xx,
timeouts) are retried with backoff and never cached, and the run stops if FDA keeps
refusing. Numbers already resolved in summary_urls.tsv are not asked again. On the document
node the 510(k)'s ex:statementOrSummary (openFDA statement_or_summary) says whether the
PDF is a 510(k) Summary or a 510(k) Statement.
"""
import datetime as dt
import os
import sys
import time
import urllib.error
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

NAME = "devices_fda"
DATASET = "cdrh/devicesatfda-derived"
SOURCE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfdevicesatfda/index.cfm"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
PDF_ROOT = "https://www.accessdata.fda.gov/cdrh_docs/"
CACHE = "summary_urls.tsv"
K510_CACHE = "k510_pdf_heads.tsv"


# ---------------------------------------------------------------- documents
def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def documents(downloads):
    """Yield one dict per decision document from the k510, pma and hde download folders."""
    downloads = Path(downloads)
    import k510, pma, hde                     # noqa: E401  (sibling source modules)
    if (downloads / "k510").exists():
        for r in k510._iter_records(downloads / "k510"):
            num = str(r.get("k_number") or "").strip()
            if not num:
                continue
            of = r.get("openfda") or {}
            yield {"number": num, "pathway": "De Novo" if num.upper().startswith("DEN") else "510(k)",
                   "record": iri.k510(num), "device": r.get("device_name"), "applicant": r.get("applicant"),
                   "date": r.get("decision_date"), "pc": r.get("product_code") or _first(of.get("product_code")),
                   "sos": r.get("statement_or_summary")}
    if (downloads / "pma").exists():
        for r in pma._iter_records(downloads / "pma"):
            num = str(r.get("pma_number") or "").strip()
            if not num:
                continue
            supp = str(r.get("supplement_number") or "").strip()
            of = r.get("openfda") or {}
            yield {"number": num + supp, "pathway": "PMA supplement" if supp else "PMA",
                   "record": iri.pma(num, supp) if supp else iri.pma(num),
                   "device": r.get("trade_name") or r.get("generic_name"), "applicant": r.get("applicant"),
                   "date": r.get("decision_date"), "pc": r.get("product_code") or _first(of.get("product_code"))}
    if (downloads / "hde").exists():
        for r in hde._rows(downloads / "hde"):
            num = r.get("HDENUMBER", "")
            if not num:
                continue
            supp = r.get("SUPPLEMENTNUMBER", "")
            yield {"number": num + supp, "pathway": "HDE supplement" if supp else "HDE",
                   "record": iri.hde(num, supp) if supp else iri.hde(num),
                   "device": r.get("TRADENAME") or r.get("GENERICNAME"), "applicant": r.get("APPLICANT"),
                   "date": r.get("DECISIONDATE"), "pc": r.get("PRODUCTCODE")}


# ---------------------------------------------------------------- summary PDFs
def candidates(number):
    """FDA's PDF folder conventions; every candidate is HEAD-checked before use."""
    n = number.upper()
    if n.startswith("DEN"):
        return [PDF_ROOT + f"reviews/{n}.pdf"]
    if n[:1] in "KPH" and n[1:3].isdigit():
        yy = n[1:3]
        folders = [f"pdf{int(yy)}", f"pdf{yy}", "pdf"]
        seen, out = set(), []
        for f in folders:
            if f not in seen:
                seen.add(f)
                out.append(PDF_ROOT + f"{f}/{n}.pdf")
        return out
    return []


def _head_ok(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status == 200 and "pdf" in (r.headers.get("Content-Type") or "").lower()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def read_cache(dest):
    p = Path(dest) / CACHE
    out = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2:
                out[parts[0]] = parts[1]
    return out


def fetch(dest, delay=1.0, limit=5000, downloads=None):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    downloads = Path(downloads) if downloads else dest.parent
    cache = read_cache(dest)
    legacy = {}
    lp = dest / "legacy_summary_urls.tsv"
    if lp.exists():
        for ln in lp.read_text(encoding="utf-8").splitlines():
            a = ln.split("\t")
            if len(a) == 2:
                legacy[a[0].strip()] = a[1].strip()
    docs = sorted(((d.get("date") or "", d["number"]) for d in documents(downloads)), reverse=True)
    todo = [n for n in legacy if n not in cache] + [n for _, n in docs if n not in cache]
    done = 0
    with open(dest / CACHE, "a", encoding="utf-8") as fh:
        for n in todo:
            if done >= limit:
                break
            found = "none"
            for url in ([legacy[n]] if n in legacy else []) + candidates(n):
                time.sleep(delay)
                if _head_ok(url):
                    found = url
                    break
            fh.write(f"{n}\t{found}\t{dt.date.today().isoformat()}\n")
            fh.flush()
            cache[n] = found
            done += 1
    print(f"devices_fda: checked {done} documents this run; cache holds {len(cache)}, "
          f"{sum(1 for v in cache.values() if v != 'none')} verified PDFs")
    m = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(),
                      files=[{"path": CACHE, "url": PDF_ROOT}])
    m.write(dest / "manifest.json")
    return m


# ---------------------------------------------------------------- 510(k) / De Novo PDFs
def k510_pdf_url(number):
    """The one PDF URL FDA uses for a 510(k) (K number) or De Novo (DEN number), else None."""
    n = (number or "").strip().upper()
    if n.startswith("DEN") and n[3:].isdigit():
        return PDF_ROOT + f"reviews/{n}.pdf"
    if n[:1] == "K" and n[1:].isdigit() and len(n) == 7:
        yy = int(n[1:3])
        return PDF_ROOT + (f"pdf{yy}" if 2 <= yy <= 75 else "pdf") + f"/{n}.pdf"
    return None


def read_k510_cache(dest):
    """number -> url or 'none' from k510_pdf_heads.tsv (final answers only)."""
    p = Path(dest) / K510_CACHE
    out = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            parts = ln.split("\t")
            if len(parts) >= 2 and parts[0]:
                out[parts[0]] = parts[1]
    return out


def _head(url):
    """-> (status or None, content type, final url)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, (r.headers.get("Content-Type") or ""), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("Content-Type") or "") if e.headers else "", url
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, str(e), url


def fetch_k510(dest, downloads=None, delay=1.0, limit=None, tries=6, max_refused=20, log=print,
               recheck_since=None):
    """One HEAD request per 510(k) / De Novo number, newest first, resumable.
    Returns a dict of counts. Stops (status 'refused') after max_refused numbers in a row
    that FDA refused even after backoff."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    downloads = Path(downloads) if downloads else dest.parent
    done_old = read_cache(dest)
    cache = read_k510_cache(dest)
    docs = sorted({(d.get("date") or "", d["number"]) for d in documents(downloads)
                   if d["pathway"] in ("510(k)", "De Novo")}, reverse=True)
    # FDA posts a PDF some weeks after the decision, so a "none" for a recent decision is
    # asked again when recheck_since (an ISO decision date) is given; a later line wins.
    todo = [n for d_, n in docs if k510_pdf_url(n) and n not in done_old
            and (n not in cache or (recheck_since and cache[n] == "none" and d_ >= recheck_since))]
    todo = list(dict.fromkeys(todo))
    total = len(todo)
    log(f"devices_fda k510: {len(docs)} 510(k)/De Novo numbers, {len(cache)} cached, "
        f"{sum(1 for _, n in docs if n in done_old)} resolved earlier, {total} to check")
    nxt, t0, n_done, hits, refused_run, refused = 0.0, time.time(), 0, 0, 0, 0
    status = "complete"
    with open(dest / K510_CACHE, "a", encoding="utf-8") as fh:
        for n in todo:
            if limit is not None and n_done >= limit:
                status = "limit"
                break
            url = k510_pdf_url(n)
            result = None
            for i in range(tries):
                wait = nxt - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                nxt = time.monotonic() + delay
                code, ctype, final = _head(url)
                if code == 200:
                    ok = "pdf" in ctype.lower() and final.startswith(PDF_ROOT)
                    result = (final if ok else "none", "200" if ok else f"200-{ctype.split(';')[0] or 'unknown'}")
                    break
                if code in (404, 410):
                    result = ("none", str(code))
                    break
                time.sleep(min(300, 2 ** (i + 1)))      # 403, 429, 5xx, timeouts: back off
                log(f"  {n}: {code} {ctype[:80]} (attempt {i + 1})")
            if result is None:
                refused += 1
                refused_run += 1
                if refused_run >= max_refused:
                    status = "refused"
                    log(f"devices_fda k510: FDA refused {refused_run} numbers in a row; stopping")
                    break
                continue
            refused_run = 0
            fh.write(f"{n}\t{result[0]}\t{result[1]}\t{dt.date.today().isoformat()}\n")
            fh.flush()
            n_done += 1
            hits += result[0] != "none"
            if n_done % 500 == 0:
                rate = n_done / max(1e-9, time.time() - t0)
                left = (total - n_done) / rate if rate else 0
                log(f"  {n_done}/{total} checked, {hits} PDFs ({100.0 * hits / n_done:.1f}%), "
                    f"{rate:.2f}/s, ETA {dt.datetime.now() + dt.timedelta(seconds=left):%Y-%m-%d %H:%M}")
    log(f"devices_fda k510: {status}; checked {n_done} this run, {hits} PDFs, {refused} refused")
    return {"status": status, "checked": n_done, "hits": hits, "refused": refused, "todo": total}


# ---------------------------------------------------------------- transform
def transform(src, out, retrieved_at=None, downloads=None):
    src = Path(src)
    downloads = Path(downloads) if downloads else src.parent
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    pdfs = {k: v for k, v in read_cache(src).items() if v.startswith("http")}
    for k, v in read_k510_cache(src).items():
        if v.startswith("http"):
            pdfs.setdefault(k, v)
    st = base.Stats(source=NAME)
    seen = set()
    with base.TurtleWriter(out) as w:
        for d in documents(downloads):
            if d["number"] in seen:
                st.skipped += 1
                continue
            seen.add(d["number"])
            pairs = [("a", "ex:DeviceFDARecord"),
                     ("rdfs:label", common.lit(d["device"] or d["number"])),
                     ("ex:pathway", common.lit(d["pathway"])),
                     ("ex:documentNumber", common.lit(d["number"])),
                     ("ex:deviceName", common.lit(d["device"])),
                     ("ex:applicant", common.lit(d["applicant"])),
                     ("ex:decisionDate", common.date_lit(d["date"])),
                     ("ex:describesRecord", f"<{d['record']}>")]
            if d.get("sos"):
                pairs.append(("ex:statementOrSummary", common.lit(d["sos"])))
            pc = (d.get("pc") or "").strip()
            if pc:
                pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
            if d["number"] in pdfs:
                pairs.append(("ex:summaryUrl", f'"{pdfs[d["number"]]}"^^xsd:anyURI'))
            pairs += common.provenance(DATASET, d["number"], source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(iri.fda_document(d["number"]), pairs)
            st.records += 1
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import json
    import tempfile
    assert candidates("K190001") == [PDF_ROOT + "pdf19/K190001.pdf", PDF_ROOT + "pdf/K190001.pdf"]
    assert candidates("K050001")[0] == PDF_ROOT + "pdf5/K050001.pdf"
    assert candidates("DEN170001") == [PDF_ROOT + "reviews/DEN170001.pdf"]
    # folders confirmed against pmn.cfm detail pages (November 3, 2026)
    assert k510_pdf_url("K961270") == PDF_ROOT + "pdf/K961270.pdf"
    assert k510_pdf_url("K013488") == PDF_ROOT + "pdf/K013488.pdf"
    assert k510_pdf_url("K020628") == PDF_ROOT + "pdf2/K020628.pdf"
    assert k510_pdf_url("K092088") == PDF_ROOT + "pdf9/K092088.pdf"
    assert k510_pdf_url("K230553") == PDF_ROOT + "pdf23/K230553.pdf"
    assert k510_pdf_url("DEN170001") == PDF_ROOT + "reviews/DEN170001.pdf"
    assert k510_pdf_url("P050001") is None and k510_pdf_url("K12") is None
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for sub in ("k510", "pma", "hde", "devices_fda"):
            (d / sub).mkdir()
        (d / "k510" / "k.json").write_text(json.dumps({"results": [
            {"k_number": "K190001", "device_name": "Widget", "applicant": "Acme", "decision_date": "2019-03-01",
             "product_code": "ABC", "statement_or_summary": "Summary"},
            {"k_number": "DEN170001", "device_name": "Novel", "applicant": "Beta", "decision_date": "2017-05-02"},
            {"k_number": "K020628", "device_name": "Gadget", "applicant": "Eta", "decision_date": "2002-05-01",
             "statement_or_summary": "Statement"},
            {"k_number": "K930763", "device_name": "Old", "applicant": "Zeta", "decision_date": "1993-05-01",
             "statement_or_summary": "Statement"}]}))
        (d / "pma" / "p.json").write_text(json.dumps({"results": [
            {"pma_number": "P050001", "supplement_number": "", "trade_name": "Valve", "applicant": "Gamma",
             "decision_date": "2005-06-07", "product_code": "DEF"},
            {"pma_number": "P050001", "supplement_number": "S001", "trade_name": "Valve", "applicant": "Gamma",
             "decision_date": "2006-01-01"}]}))
        (d / "hde" / "hde.txt").write_text("HDENUMBER|SUPPLEMENTNUMBER|APPLICANT|PRODUCTCODE|DECISIONDATE|TRADENAME\n"
                                           "H010001||Delta|GHI|01/02/2002|Rare Pump\n", encoding="latin-1")
        (d / "devices_fda" / CACHE).write_text(f"K190001\t{PDF_ROOT}pdf19/K190001.pdf\t2026-10-10\n"
                                               "P050001\tnone\t2026-10-10\n", encoding="utf-8")
        (d / "devices_fda" / K510_CACHE).write_text(f"K020628\t{PDF_ROOT}pdf2/K020628.pdf\t200\t2026-11-03\n"
                                                    "K930763\tnone\t404\t2026-11-03\n", encoding="utf-8")
        st = transform(d / "devices_fda", d / "f.ttl", retrieved_at="2026-10-10")
        ttl = (d / "f.ttl").read_text(encoding="utf-8")
        # fetch_k510 against a stand-in for FDA (no network): K190001 resolved earlier, K020628
        # and K930763 cached; only DEN170001 is asked; then a recheck of recent "none" answers
        global _head
        real, asked = _head, []
        _head = lambda u: (asked.append(u), (200, "application/pdf", u))[1]   # noqa: E731
        try:
            r1 = fetch_k510(d / "devices_fda", d, delay=0, log=lambda s: None)
            r2 = fetch_k510(d / "devices_fda", d, delay=0, log=lambda s: None)
            r3 = fetch_k510(d / "devices_fda", d, delay=0, log=lambda s: None, recheck_since="1990-01-01")
            _head = lambda u: (503, "text/html", u)                            # noqa: E731
            (d / "devices_fda" / K510_CACHE).write_text("", encoding="utf-8")
            r4 = fetch_k510(d / "devices_fda", d, delay=0, tries=1, max_refused=2, log=lambda s: None)
        finally:
            _head = real
    assert asked == [PDF_ROOT + "reviews/DEN170001.pdf", PDF_ROOT + "pdf/K930763.pdf"], asked
    assert (r1["checked"], r2["checked"], r3["checked"]) == (1, 0, 1), (r1, r2, r3)
    assert r4["status"] == "refused" and r4["checked"] == 0, r4
    assert st.records == 7, st
    assert ttl.count("a ex:DeviceFDARecord") == 7
    assert 'ex:pathway "De Novo"' in ttl and 'ex:pathway "PMA supplement"' in ttl and 'ex:pathway "HDE"' in ttl
    assert ttl.count("ex:summaryUrl") == 2 and "pdf19/K190001.pdf" in ttl
    k2 = next(b for b in ttl.split("\n\n") if b.startswith(f"<{iri.fda_document('K020628')}>"))
    assert (f'ex:summaryUrl "{PDF_ROOT}pdf2/K020628.pdf"^^xsd:anyURI' in k2
            and 'ex:statementOrSummary "Statement"' in k2 and f"ex:describesRecord <{iri.k510('K020628')}>" in k2), k2
    assert ttl.count('ex:statementOrSummary "Summary"') == 1
    assert f"ex:describesRecord <{iri.pma('P050001', 'S001')}>" in ttl
    assert f"<{iri.fda_document('H010001')}>" in ttl and '"2002-01-02"^^xsd:date' in ttl
    print(f"devices_fda.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch-k510":
        # devices_fda.py fetch-k510 <devices_fda download dir> [<downloads root>] [<limit>|-] [<recheck since>]
        r = fetch_k510(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None,
                       limit=int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] != "-" else None,
                       recheck_since=sys.argv[5] if len(sys.argv) > 5 else None,
                       log=lambda s: print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {s}", flush=True))
        sys.exit(3 if r["status"] == "refused" else 0)
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
