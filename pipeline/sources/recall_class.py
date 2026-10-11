#!/usr/bin/env python3
"""
recall_class.py -- recall CLASS (I/II/III) for recalls that only the FDA Medical
Device Recall database carries, read from FDA's own recall pages (cfRES).

WHY
---
The openFDA recall database download (device/recall, recall_db.py) has the firm FEI,
root cause and timeline but NOT the recall class. The class arrives only through the
enforcement reports (device/enforcement, recalls.py). On 2026-10-10, 19,359 of the
59,471 recalls in the graph (about a third) were in the recall database only, so
they had no class. FDA's cfRES page for every recall states it in the title:
    "Class 2 Device Recall Sedecal SPHF 4.0 Portable XRay System"

WHAT IT DOES
  fetch(dest)   reads the recall_db and recalls downloads that sit next to dest,
                selects recalls with no enforcement record, and fetches each cfRES
                page at 1 request/second into dest/pages/<cfres_id>.html.
                Pages are CACHED. A cached page is fetched again only while the
                recall is still unclassified or not yet terminated, so weekly runs
                fetch new recalls plus the few that changed -- not 19,000 pages.
  transform()   parses the cached pages and writes, on the SAME iri.recall() node
                that recall_db.py and recalls.py write:
                    ex:recallClassification "Class II"   (same literal form as the
                                                         enforcement feed)
                    ex:recallClassificationSource "FDA recall database web page (cfRES)"
                    ex:cfresStatus "Terminated on March 24, 2004"
                    ex:cfresId, provenance (dataset cdrh/cfRES, the page URL)

NOT INGESTED: the "For Additional Information Contact" person and phone number.
"""
import os
import re
import sys
import json
import html as htmllib
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

NAME = "recall_class"
DATASET = "cdrh/cfRES"
PAGE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRES/res.cfm?id={}"
SOURCE_LABEL = "FDA recall database web page (cfRES)"
UA = "medical-device-kg/rebuild (Hofstra University; weekly, 1 request per second)"
_ROMAN = {"1": "Class I", "2": "Class II", "3": "Class III"}
_TITLE = re.compile(r"<title>\s*(.*?)\s*</title>", re.S | re.I)
_CLASS = re.compile(r"\bClass\s+([123])\s+Device\s+Recall\b", re.I)
_RNUM = re.compile(r"\bZ-\d{4}-\d{2,4}\b")         # Z-0001-04 (2-digit year) and Z-1552-2026


# ===========================================================================
#  Which recalls need a class
# ===========================================================================
def targets(downloads):
    """[(recall_number, cfres_id)] for recall-database records with no enforcement record.
    `downloads` is the downloads root holding recall_db/ and recalls/."""
    downloads = Path(downloads)
    enf = {str(r.get("recall_number") or "").strip()
           for r in base.iter_openfda_results(downloads / "recalls")}
    out, seen = [], set()
    for r in base.iter_openfda_results(downloads / "recall_db"):
        rn = str(r.get("product_res_number") or "").strip()
        cid = str(r.get("cfres_id") or "").strip()
        if rn and cid.isdigit() and rn not in enf and rn not in seen:
            seen.add(rn)
            out.append((rn, cid))
    return sorted(out)


# ===========================================================================
#  Page parsing
# ===========================================================================
def _text(html):
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return " ".join(htmllib.unescape(t).split())


def parse_page(html):
    """-> dict(cls, title, status, recall_number) from one cfRES page; cls None if unclassified."""
    m = _TITLE.search(html)
    title = " ".join(htmllib.unescape(m.group(1)).split()) if m else ""
    c = _CLASS.search(title) or _CLASS.search(_text(html)[:3000])
    text = _text(html)
    st = re.search(r"Recall Status\s*\d?\s*(.+?)\s+Recall Number", text)
    rn = _RNUM.search(text[text.find("Recall Number"):] if "Recall Number" in text else text)
    status = re.sub(r"\s+\d\s+on\s+", " on ", st.group(1)).strip() if st else None
    return {"cls": _ROMAN.get(c.group(1)) if c else None,
            "title": title or None,
            "status": status,
            "recall_number": rn.group(0) if rn else None}


def _needs_refetch(path):
    """A cached page is final once it shows a class AND a terminated status."""
    if not path.exists() or path.stat().st_size < 1000:
        return True
    p = parse_page(path.read_text(encoding="utf-8", errors="replace"))
    return not (p["cls"] and p["status"] and p["status"].lower().startswith("terminated"))


# ===========================================================================
#  Contract
# ===========================================================================
def fetch(dest, delay=1.0, limit=None, workers=3):
    """At most one request START per `delay` seconds overall (FDA politeness), with up to
    `workers` requests in flight so the ~2 s page latency does not cut the rate to 1/3."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    dest = Path(dest)
    pages = dest / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    tg = targets(dest.parent)
    (dest / "targets.json").write_text(json.dumps(tg), encoding="utf-8")
    todo = [(rn, cid) for rn, cid in (tg[:limit] if limit else tg)
            if _needs_refetch(pages / f"{cid}.html")]
    lock, next_slot = threading.Lock(), [time.monotonic()]
    counts = {"fetched": 0, "errors": 0}

    def get(item):
        _, cid = item
        with lock:                                     # global rate limit: 1 start per `delay`
            wait = next_slot[0] - time.monotonic()
            next_slot[0] = max(next_slot[0], time.monotonic()) + delay
        if wait > 0:
            time.sleep(wait)
        p = pages / f"{cid}.html"
        req = urllib.request.Request(PAGE_URL.format(cid), headers={"User-Agent": UA})
        try:
            body = urllib.request.urlopen(req, timeout=60).read()
            tmp = p.with_suffix(".part")
            tmp.write_bytes(body)
            tmp.replace(p)                             # never leave a half-written page
            (pages / f"{cid}.error").unlink(missing_ok=True)
            with lock:
                counts["fetched"] += 1
        except Exception as e:                         # one bad page must not stop the run
            (pages / f"{cid}.error").write_text(str(e), encoding="utf-8")
            with lock:
                counts["errors"] += 1

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(get, todo))
    fetched, errors = counts["fetched"], counts["errors"]
    (dest / "fetch_log.json").write_text(json.dumps(
        {"date": base.today(), "targets": len(tg), "fetched_now": fetched, "errors": errors}), encoding="utf-8")
    print(f"  recall_class: {len(tg):,} recalls need a class; fetched {fetched:,} pages now; {errors} errors")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    tg = json.loads((src / "targets.json").read_text(encoding="utf-8")) if (src / "targets.json").exists() else []
    st = base.Stats(source=NAME)
    classes = {}
    with base.TurtleWriter(out) as w:
        for rn, cid in tg:
            p = src / "pages" / f"{cid}.html"
            if not p.exists():
                st.skipped += 1
                continue
            d = parse_page(p.read_text(encoding="utf-8", errors="replace"))
            if d["recall_number"] and d["recall_number"] != rn:
                st.skipped += 1                       # page does not belong to this recall
                continue
            pairs = [("a", "ex:RecallRecord"),
                     ("ex:recallNumber", common.lit(rn)),
                     ("ex:cfresId", common.lit(cid)),
                     ("ex:cfresStatus", common.lit(d["status"]))]
            if d["cls"]:
                pairs += [("ex:recallClassification", common.lit(d["cls"])),
                          ("ex:recallClassificationSource", common.lit(SOURCE_LABEL))]
                classes[d["cls"]] = classes.get(d["cls"], 0) + 1
            else:
                classes["unclassified"] = classes.get("unclassified", 0) + 1
            pairs += common.provenance(DATASET, cid, source_url=PAGE_URL.format(cid), retrieved_at=retrieved_at)
            w.block(iri.recall(rn), pairs)
            st.records += 1
    st.triples = w.triples
    (Path(out).with_suffix(".classes.json")).write_text(json.dumps(classes, sort_keys=True), encoding="utf-8")
    print(f"  recall_class: {st.records:,} pages parsed; classes {classes}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    page = ("<html><head><title>Class 2 Device Recall  Sedecal SPHF 4.0 Portable XRay System</title></head>"
            "<body><table><tr><th>Date Initiated by Firm</th><td>October 27, 2003</td></tr>"
            "<tr><th>Recall Status<sup>1</sup></th><td>Terminated <sup>3</sup> on March 24, 2004</td></tr>"
            "<tr><th>Recall Number</th><td>Z-0001-04</td></tr>"
            "<tr><th>For Additional Information Contact</th><td>Mr. Devan Moser 847-394-6960</td></tr>"
            "</table></body></html>")
    open_page = ("<html><head><title>Device Recall Widget</title></head><body>"
                 "Recall Status 1 Open 3 , Classified Recall Number Z-0002-26</body></html>")
    d = parse_page(page)
    assert d["cls"] == "Class II" and d["recall_number"] == "Z-0001-04", d
    assert d["status"] == "Terminated on March 24, 2004", d
    assert parse_page(open_page)["cls"] is None
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "30043.html").write_text(page + " " * 1000, encoding="utf-8")
        (t / "pages" / "99999.html").write_text(open_page + " " * 1000, encoding="utf-8")
        (t / "targets.json").write_text(json.dumps([["Z-0001-04", "30043"], ["Z-0002-26", "99999"],
                                                    ["Z-0003-26", "11111"]]), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-10", files=[]).write(t / "manifest.json")
        st = transform(t, t / "recall_class.ttl")
        ttl = (t / "recall_class.ttl").read_text(encoding="utf-8")
        assert not _needs_refetch(t / "pages" / "30043.html")       # classified + terminated: final
        assert _needs_refetch(t / "pages" / "99999.html")           # unclassified: fetch again next run
    assert st.records == 2 and st.skipped == 1, st
    assert f"<{iri.recall('Z-0001-04')}>" in ttl and 'ex:recallClassification "Class II"' in ttl
    assert 'ex:recallClassificationSource "FDA recall database web page (cfRES)"' in ttl
    assert 'ex:cfresStatus "Terminated on March 24, 2004"' in ttl
    assert "Moser" not in ttl and "847-394" not in ttl                # contact person excluded
    assert ttl.count("ex:recallClassification ") == 1                 # unclassified gets no class
    print(f"recall_class.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
