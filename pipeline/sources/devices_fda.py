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
                   "date": r.get("decision_date"), "pc": r.get("product_code") or _first(of.get("product_code"))}
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
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for sub in ("k510", "pma", "hde", "devices_fda"):
            (d / sub).mkdir()
        (d / "k510" / "k.json").write_text(json.dumps({"results": [
            {"k_number": "K190001", "device_name": "Widget", "applicant": "Acme", "decision_date": "2019-03-01",
             "product_code": "ABC"},
            {"k_number": "DEN170001", "device_name": "Novel", "applicant": "Beta", "decision_date": "2017-05-02"}]}))
        (d / "pma" / "p.json").write_text(json.dumps({"results": [
            {"pma_number": "P050001", "supplement_number": "", "trade_name": "Valve", "applicant": "Gamma",
             "decision_date": "2005-06-07", "product_code": "DEF"},
            {"pma_number": "P050001", "supplement_number": "S001", "trade_name": "Valve", "applicant": "Gamma",
             "decision_date": "2006-01-01"}]}))
        (d / "hde" / "hde.txt").write_text("HDENUMBER|SUPPLEMENTNUMBER|APPLICANT|PRODUCTCODE|DECISIONDATE|TRADENAME\n"
                                           "H010001||Delta|GHI|01/02/2002|Rare Pump\n", encoding="latin-1")
        (d / "devices_fda" / CACHE).write_text(f"K190001\t{PDF_ROOT}pdf19/K190001.pdf\t2026-10-10\n"
                                               "P050001\tnone\t2026-10-10\n", encoding="utf-8")
        st = transform(d / "devices_fda", d / "f.ttl", retrieved_at="2026-10-10")
        ttl = (d / "f.ttl").read_text(encoding="utf-8")
    assert st.records == 5, st
    assert ttl.count("a ex:DeviceFDARecord") == 5
    assert 'ex:pathway "De Novo"' in ttl and 'ex:pathway "PMA supplement"' in ttl and 'ex:pathway "HDE"' in ttl
    assert ttl.count("ex:summaryUrl") == 1 and "pdf19/K190001.pdf" in ttl
    assert f"ex:describesRecord <{iri.pma('P050001', 'S001')}>" in ttl
    assert f"<{iri.fda_document('H010001')}>" in ttl and '"2002-01-02"^^xsd:date' in ttl
    print(f"devices_fda.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
