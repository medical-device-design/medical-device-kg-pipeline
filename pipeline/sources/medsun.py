#!/usr/bin/env python3
"""
medsun.py -- FDA MedSun Reports database (Medical Product Safety Network) -> RDF.

SOURCE (checked October 12, 2026)
    Search:  https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/Medsun/searchreport.cfm
             POST searchdevicemanufacturer, searchdevicebrand, searchdevicetype,
             searcheventdescription, searchdeviceproblemlist, reportstartdate,
             reportenddate (MM/DD/YYYY), pagenum, submit_button=Search.
             A search returns at most 500 records ("please narrow your search").
             The results page carries every matching report id in the hidden ID field
             of the "Export to Excel" form (medsunform), whatever the page size, so one
             request per date window yields the complete id list for that window.
             "None Found for <start> <end>" means an empty window.
    Detail:  medsun_details.cfm?id=NNNNNN  (ids reach about 104712)
    Export:  medsunExcel.cfm (POST ID=<list>) returns CSV with six columns -- Device
             Brand, Device Manufacturer, Device Type, Device Problem, Date Report, Event
             Description -- but NO report id, and FDA strips commas from the values
             ("Carefusion 303, Inc." -> "Carefusion 303 Inc."; commas in narratives become
             " -"). It is therefore not used: the detail pages hold the same six fields
             verbatim plus the id.

    The public detail page shows exactly these fields (no product code, model, catalog,
    lot, event date, facility type, patient outcome or report type are published):
        Type of Device / Device Brand Name / Device Manufacturer's Name /
        Date of this Report (mm/dd/yyyy) / Describe the Event or Problem /
        the device(s) may have caused or contributed to
    Some descriptions embed a manufacturer response after a "=====" banner:
        "...drip chamber and bag.<br>======================<br>
         Manufacturer response for IV tubing primary set, carefusion (per site reporter)
         <br>======================<br>have not recevied communication at this time"

FETCH
    Date windows from 2002-01-01 to today, one calendar month each; a window that hits
    500 is split in half until each part returns fewer than 500. Search pages are cached
    in dest/search/<YYYYMMDD>_<YYYYMMDD>.html (windows that ended more than FINAL_DAYS ago
    are final and not fetched again); detail pages, trimmed to the verbatim report table
    because of VM disk space, are appended as gzip members to
    dest/pages/shard_<id // 1000>.jsonl.gz (see "Page cache" below; final once they parse
    with content). At most one request START per second, retries with backoff.
    dest/ids.json lists every id with its window; dest/fetch_log.json records coverage
    and any window that could not be resolved.

MODEL
    iri.medsun_report(id)  a ex:MedSunReport
        rdfs:label "MedSun report 43205: SmartSite Infusion Set"
        ex:medsunReportId, ex:reportDate (xsd:date), ex:genericName (Type of Device),
        ex:brandName, ex:manufacturerName, ex:causedOrContributedTo,
        ex:hasManufacturerFirm -> iri.firm_name(normalize_org(manufacturer)), provenance.
    Narratives go to medsun_text.ttl after scrub.scrub_text:
        ex:eventDescription, ex:manufacturerResponse
    No product code is published, so no ex:hasProductCode / hub nodes are written.
    No person names, phone numbers or e-mails are published as fields; narratives are
    scrubbed (scrub.scrub_text plus a mixed-case contact-name pass, scrub_narrative) and
    kept out of the main file.

PROPOSALS FOR THE ONTOLOGY (not yet declared): class ex:MedSunReport; properties
    ex:medsunReportId, ex:causedOrContributedTo, ex:manufacturerResponse, ex:brandName.
"""
import os
import re
import sys
import json
import time
import html as htmllib
import urllib.parse
import urllib.request
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
import scrub      # noqa: E402
from identity import normalize_org   # noqa: E402

NAME = "medsun"
DATASET = "cdrh/medsun"
BASE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/Medsun/"
SEARCH_URL = BASE_URL + "searchreport.cfm"
PAGE_URL = BASE_URL + "medsun_details.cfm?id={}"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
START = date(2002, 1, 1)
LIMIT = 500                  # FDA's cap per search
FINAL_DAYS = 180             # a search window that ended this long ago is treated as final

_FIELDS = [                  # detail-page label prefix (lower case) -> key
    ("type of device", "device_type"),
    ("device brand name", "brand"),
    ("device manufacturer's name", "manufacturer"),
    ("date of this report", "report_date"),
    ("describe the event", "description"),
    ("the device(s) may have", "caused"),
]
_COUNT = re.compile(r"\ball\s+(\d+)\s+records\b", re.I)
_IDS = re.compile(r'name="ID"\s+value="([^"]*)"', re.I)
_BANNER = re.compile(r"\s*={5,}\s*")
# scrub.py's contact-name rule expects upper-case MAUDE text ("CONTACT JANE DOE AT ...");
# MedSun narratives are mixed case ("please contact Jane Doe at ..."). Same rule, with the
# trigger words case-insensitive and the name in Title Case, applied after scrub_text.
_CONTACT_MIXED = re.compile(
    r"\b((?i:contact|call|reach|e-?mail))\s+((?:(?:Dr|Mr|Ms|Mrs)\.?\s+)?[A-Z][a-z.'-]+(?:\s+[A-Z][a-z.'-]+){0,2})"
    r"\s+((?i:at|on|via))\b(?=\s*:?\s*(?:\[PHONE REMOVED\]|\[EMAIL REMOVED\]))")


def scrub_narrative(text):
    text, hits = scrub.scrub_text(text)
    n = [0]

    def _sub(m):
        n[0] += 1
        return f"{m.group(1)} [NAME REMOVED] {m.group(3)}"
    text = _CONTACT_MIXED.sub(_sub, text)
    if n[0]:
        hits["contact_name_mixed_case"] += n[0]
    return text, hits


# ===========================================================================
#  HTTP: one request start per `delay` seconds across all threads, retries
# ===========================================================================
class _Throttle:
    def __init__(self, delay):
        import threading
        self.delay, self.lock, self.next = delay, threading.Lock(), time.monotonic()

    def wait(self):
        with self.lock:
            w = self.next - time.monotonic()
            self.next = max(self.next, time.monotonic()) + self.delay
        if w > 0:
            time.sleep(w)


def _get(url, throttle, data=None, tries=4):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    err = None
    for k in range(tries):
        throttle.wait()
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                final = r.geturl()
                if "accessdata.fda.gov" not in final:      # redirected away: a block, report it
                    raise RuntimeError(f"redirected to {final}")
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:                             # backoff 5 s, 20 s, 60 s
            err = e
            if "redirected" in str(e):
                break
            time.sleep((5, 20, 60, 60)[k])
    raise RuntimeError(f"{url}: {err}")


# ===========================================================================
#  Parsing
# ===========================================================================
def _cell_text(fragment):
    t = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = htmllib.unescape(t).replace("\r", "")
    lines = [" ".join(ln.split()) for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln).strip()


def parse_search(page):
    """-> dict(ok, count, ids, narrow). ok=False when the page is not a result page."""
    if "None Found for" in page:
        return {"ok": True, "count": 0, "ids": [], "narrow": False}
    m = _IDS.search(page)
    ids = [s for s in urllib.parse.unquote(m.group(1)).split(",") if s.strip().isdigit()] if m else []
    c = _COUNT.search(page)
    if not ids or not c:
        return {"ok": False, "count": None, "ids": [], "narrow": False}
    count = int(c.group(1))
    narrow = "narrow your search" in page.lower() or count >= LIMIT
    return {"ok": True, "count": count, "ids": [s.strip() for s in ids], "narrow": narrow}


def parse_detail(page):
    """-> dict of the six published fields (missing ones None), plus 'unknown' labels."""
    i, j = page.find("Back to search results"), page.find("New Search")
    seg = page[i:j] if 0 <= i < j else page
    out = {k: None for _, k in _FIELDS}
    out["unknown"] = []
    for chunk in re.split(r"(?i)<th\b[^>]*>", seg)[1:]:
        lab, _, rest = chunk.partition("</th>")
        lab = " ".join(_cell_text(lab).split()).lower()
        key = next((k for p, k in _FIELDS if lab.startswith(p)), None)
        val = _cell_text(rest) or None
        if key:
            out[key] = val
        elif lab:
            out["unknown"].append(lab)
    return out


#  Page cache. The VM disk had ~400 MB free on 2026-10-13, and 79,433 full pages of
#  ~34 KB would need 2.7 GB (or ~320 MB as one small file each, at 4 KB per block).
#  So each page is trimmed to its verbatim report table and appended as one gzip member
#  holding a JSON line {"id", "html"} to pages/shard_<id // 1000>.jsonl.gz (~55 MB total).
#  Concatenated gzip members read back as one stream; a member cut short by a crash is
#  dropped on read and that page is fetched again.
import threading as _threading      # noqa: E402
_WLOCK = _threading.Lock()


def shard_path(pages, rid):
    return Path(pages) / f"shard_{int(rid) // 1000:03d}.jsonl.gz"


def trim_page(text):
    """Keep only the report table (from 'Back to search results' to 'New Search'),
    verbatim; drop FDA's ~32 KB site header and footer. A page without those markers
    (an error page) is kept whole."""
    i, j = text.find("Back to search results"), text.find("New Search")
    if 0 <= i < j:
        return "<!-- trimmed: report table only -->\n" + text[i:j + len("New Search")]
    return text


def write_page(pages, rid, text):
    import gzip
    rec = json.dumps({"id": str(rid), "html": trim_page(text)}, ensure_ascii=False) + "\n"
    blob = gzip.compress(rec.encode("utf-8"), 9)
    with _WLOCK:
        fd = os.open(shard_path(pages, rid), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, blob)                          # one write per complete member
        finally:
            os.close(fd)


def _members(raw):
    import zlib
    while raw:
        d = zlib.decompressobj(31)
        try:
            out = d.decompress(raw)
        except zlib.error:
            return
        if not d.eof:                                   # truncated member: stop here
            return
        yield out
        raw = d.unused_data


def repair_shard(p):
    """Drop a truncated tail (a crash mid-append) so later appends stay readable.
    Returns True if the file was rewritten."""
    import gzip
    raw = p.read_bytes()
    good = list(_members(raw))
    try:
        if gzip.decompress(raw) == b"".join(good):    # decodes completely: nothing to do
            return False
    except Exception:
        pass
    tmp = p.with_suffix(".part")
    tmp.write_bytes(b"".join(gzip.compress(m, 9) for m in good))
    tmp.replace(p)
    return True


def load_pages(pages):
    """{id: html} from the shards (later entries win), plus any single-file cache
    (<id>.html.gz or <id>.html) from earlier runs."""
    import gzip
    pages, out = Path(pages), {}
    if not pages.exists():
        return out
    for p in sorted(pages.glob("shard_*.jsonl.gz")):
        for m in _members(p.read_bytes()):
            for line in m.decode("utf-8", errors="replace").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                out[str(r["id"])] = r["html"]
    for p in pages.glob("*.html.gz"):
        out.setdefault(p.name.split(".")[0], gzip.decompress(p.read_bytes()).decode("utf-8", errors="replace"))
    for p in pages.glob("*.html"):
        out.setdefault(p.stem, p.read_text(encoding="utf-8", errors="replace"))
    return out


def read_page(pages, rid):
    return load_pages(pages).get(str(rid))


def has_content(d):
    return any(d.get(k) for k in ("device_type", "brand", "manufacturer", "report_date", "description"))


def split_response(desc):
    """Event text and the manufacturer response(s) that MedSun appends after '=====' banners."""
    if not desc:
        return None, None
    parts = _BANNER.split(desc)
    if len(parts) >= 3 and parts[1].lower().startswith("manufacturer response"):
        event = parts[0].strip() or None
        resp = []
        rest = parts[1:]
        for k in range(0, len(rest), 2):
            head = rest[k].strip()
            body = rest[k + 1].strip() if k + 1 < len(rest) else ""
            resp.append(f"{head}: {body}" if body else head)
        return event, "\n".join(r for r in resp if r) or None
    return desc, None


# ===========================================================================
#  Date windows
# ===========================================================================
def _mmddyyyy(d):
    return d.strftime("%m/%d/%Y")


def month_windows(start, end):
    out, d = [], start
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        out.append((d, min(nxt - timedelta(days=1), end)))
        d = nxt
    return out


def walk_windows(search, windows):
    """search(d1, d2) -> parse_search dict. Splits any window that hits the cap.
    Returns (resolved [(d1, d2, count, ids)], failed [(d1, d2, reason)])."""
    resolved, failed, todo = [], [], list(windows)
    while todo:
        d1, d2 = todo.pop(0)
        try:
            r = search(d1, d2)
        except Exception as e:
            failed.append((d1, d2, f"error: {e}"))
            continue
        if not r["ok"]:
            failed.append((d1, d2, "unrecognized result page"))
        elif r["narrow"]:
            if d1 == d2:
                failed.append((d1, d2, f"{r['count']} records on one day (cap {LIMIT})"))
            else:
                mid = d1 + (d2 - d1) // 2
                todo[0:0] = [(d1, mid), (mid + timedelta(days=1), d2)]
        else:
            if len(r["ids"]) != r["count"]:
                failed.append((d1, d2, f"id list {len(r['ids'])} != count {r['count']}"))
            resolved.append((d1, d2, r["count"], r["ids"]))
    return resolved, failed


# ===========================================================================
#  Contract
# ===========================================================================
def fetch(dest, delay=1.0, workers=3, start=START, end=None):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    dest = Path(dest)
    (dest / "search").mkdir(parents=True, exist_ok=True)
    (dest / "pages").mkdir(parents=True, exist_ok=True)
    today = date.today()
    end = end or today
    thr = _Throttle(delay)
    n_search = [0]

    def search(d1, d2):
        p = dest / "search" / f"{d1:%Y%m%d}_{d2:%Y%m%d}.html"
        if p.exists() and (today - d2).days > FINAL_DAYS:
            r = parse_search(p.read_text(encoding="utf-8", errors="replace"))
            if r["ok"]:
                return r
        page = _get(SEARCH_URL, thr, data={
            "searchdevicemanufacturer": "", "searchdevicebrand": "", "searchdevicetype": "",
            "searcheventdescription": "", "searchdeviceproblemlist": "",
            "reportstartdate": _mmddyyyy(d1), "reportenddate": _mmddyyyy(d2),
            "pagenum": "5", "submit_button": "Search"})
        n_search[0] += 1
        tmp = p.with_suffix(".part")
        tmp.write_text(page, encoding="utf-8")
        tmp.replace(p)
        r = parse_search(page)
        print(f"  search {d1} .. {d2}: {r['count']}{' (split)' if r['narrow'] else ''}", flush=True)
        return r

    resolved, failed = walk_windows(search, month_windows(start, end))
    ids = {}
    for d1, d2, _, lst in resolved:
        for i in lst:
            ids.setdefault(i, f"{d1:%Y-%m-%d}/{d2:%Y-%m-%d}")
    (dest / "ids.json").write_text(json.dumps(sorted(ids.items(), key=lambda kv: int(kv[0]))), encoding="utf-8")
    print(f"  medsun: {len(resolved)} windows, {len(ids):,} report ids, {len(failed)} failed windows", flush=True)

    for p in sorted((dest / "pages").glob("shard_*.jsonl.gz")):
        if repair_shard(p):
            print(f"  repaired truncated tail of {p.name}", flush=True)
    cached = load_pages(dest / "pages")
    legacy = list((dest / "pages").glob("*.html.gz")) + list((dest / "pages").glob("*.html"))
    in_shards = set()
    for p in sorted((dest / "pages").glob("shard_*.jsonl.gz")):
        for m in _members(p.read_bytes()):
            in_shards.update(json.loads(ln)["id"] for ln in m.decode("utf-8", "replace").splitlines() if ln)
    for p in legacy:                                   # earlier single-file cache -> shards
        rid = p.name.split(".")[0]
        if rid not in in_shards and rid in cached:
            write_page(dest / "pages", rid, cached[rid])
        p.unlink()

    def needs(i):
        page = cached.get(i)
        return page is None or not has_content(parse_detail(page))

    todo = [i for i in sorted(ids, key=int) if needs(i)]
    del cached
    print(f"  medsun: {len(todo):,} detail pages to fetch", flush=True)
    lock, counts = threading.Lock(), {"fetched": 0, "errors": 0}

    def get(i):
        try:
            page = _get(PAGE_URL.format(i), thr)
            write_page(dest / "pages", i, page)
            (dest / "pages" / f"{i}.error").unlink(missing_ok=True)
            with lock:
                counts["fetched"] += 1
                if counts["fetched"] % 1000 == 0:
                    print(f"  pages fetched {counts['fetched']:,}/{len(todo):,}", flush=True)
        except Exception as e:
            (dest / "pages" / f"{i}.error").write_text(str(e), encoding="utf-8")
            with lock:
                counts["errors"] += 1

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(get, todo))
    dates = [w for w in resolved if w[2]]
    log = {"date": base.today(), "range": [str(start), str(end)],
           "first_window_with_reports": str(dates[0][0]) if dates else None,
           "last_window_with_reports": str(dates[-1][1]) if dates else None,
           "windows": len(resolved), "search_requests": n_search[0], "ids": len(ids),
           "pages_fetched_now": counts["fetched"], "page_errors": counts["errors"],
           "failed_windows": [[str(a), str(b), r] for a, b, r in failed]}
    (dest / "fetch_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")
    print("  medsun fetch log: " + json.dumps(log), flush=True)
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    man.write(dest / "manifest.json")
    return man


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    if (src / "ids.json").exists():
        ids = [i for i, _ in json.loads((src / "ids.json").read_text(encoding="utf-8"))]
    else:
        ids = None
    pages = load_pages(src / "pages")
    if ids is None:
        ids = sorted(pages, key=int)
    st = base.Stats(source=NAME)
    firms, unknown, hits = {}, {}, {}
    text_out = out.with_name(f"{out.stem}_text.ttl")
    with base.TurtleWriter(out) as w, base.TurtleWriter(text_out) as wt:
        for rid in ids:
            page = pages.get(rid)
            if page is None:
                st.skipped += 1
                continue
            d = parse_detail(page)
            if not has_content(d):
                st.skipped += 1
                continue
            for u in d["unknown"]:
                unknown[u] = unknown.get(u, 0) + 1
            subj = iri.medsun_report(rid)
            nf = normalize_org(d["manufacturer"])
            pairs = [
                ("a", "ex:MedSunReport"),
                ("rdfs:label", common.label(f"MedSun report {rid}", d["brand"] or d["device_type"])),
                ("ex:medsunReportId", common.lit(rid)),
                ("ex:reportDate", common.date_lit(d["report_date"])),
                ("ex:genericName", common.lit(d["device_type"])),
                ("ex:brandName", common.lit(d["brand"])),
                ("ex:manufacturerName", common.lit(d["manufacturer"])),
                ("ex:causedOrContributedTo", common.lit(d["caused"])),
            ]
            if nf:
                pairs.append(("ex:hasManufacturerFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, d["manufacturer"])
            pairs += common.provenance(DATASET, rid, source_url=PAGE_URL.format(rid), retrieved_at=retrieved_at)
            w.block(subj, pairs)
            st.records += 1
            event, resp = split_response(d["description"])
            tp = []
            for pred, body in (("ex:eventDescription", event), ("ex:manufacturerResponse", resp)):
                if body:
                    body, h = scrub_narrative(body)
                    for k, v in h.items():
                        hits[k] = hits.get(k, 0) + v
                    tp.append((pred, common.lit(body)))
            if tp:
                wt.block(subj, tp)
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
    st.triples = w.triples
    print(f"  medsun: {st.records:,} reports, {len(firms):,} firms; narratives {wt.triples:,} triples -> "
          f"{text_out.name}; scrub hits {hits}; unknown labels {unknown}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- no network. Samples are verbatim from FDA pages saved 2026-10-12
#  (whitespace between tags shortened); the scrub sample is marked synthetic.
# ===========================================================================
_DETAIL_43205 = (
    '<a style="text-decoration:underline;" href="javascript: history.go(-1)">Back to search results</a></td></tr>\n'
    '\t</table>\n\n\t<table cellspacing=5 cellpadding=0 border=0>\n\t\n\t\n<tr valign="top">\n'
    '<th align="left">Type of Device:</th>\n<td>\n<table cellspacing=0 cellpadding=0 border=0 width="100%">\n'
    '<tr valign="top">\n<td align="left">\nset, administration, intravascular</td>\n</tr>\n</table>\n</td>\n</tr>\n'
    '<tr valign="top">\n<th align="left">Device Brand Name:</th>\n<td>\n<table cellspacing=0 cellpadding=0 border=0>\n'
    '<tr>\n<td>\nSmartSite Infusion Set</td>\n</tr>\n</table>\n</td>\n</tr>\n<tr valign="top">\n'
    '<th align="left">Device Manufacturer\'s Name:</th>\n<td>\n<table cellspacing=0 cellpadding=0 border=0>\n<tr>\n'
    '<td>\nCarefusion 303, Inc.</td>\n</tr>\n</table>\n</td>\n</tr>\n<tr valign="top">\n'
    '<th align="left">Date of this Report:<br>(mm/dd/yyyy)</th>\n<td>\n<table cellspacing=0 cellpadding=0 border=0>\n'
    '<tr>\n<td>01/30/2015</td>\n</tr>\n</table>\n</td>\n</tr>\n<tr valign="top">\n'
    '<th align="left">Describe the Event<br>or Problem:</th>\n<td>\n<table cellspacing=0 cellpadding=0 border=0>\n'
    '<tr>\n<td>The secondary/piggyback IV backflowed into the main/primary IV fluid tubing drip chamber and bag.\n'
    '<br>======================<br>\nManufacturer response for IV tubing primary set, carefusion (per site reporter)\n'
    '<br>======================<br>\nhave not recevied communication at this time</td>\n</tr>\n</table>\n</td>\n</tr>\n'
    '<tr valign="middle">\n<th align="left">the device(s) may have<br>caused or contributed to:</th>\n<td>\n'
    '<table cellspacing=0 cellpadding=0 border=0>\n<tr>\n<td>\nNot applicable</td>\n</tr>\n</table>\n</td>\n</tr>\n'
    '\n\t\n\t</table>\n\t\n\t<table cellspacing=0 cellpadding=5 width="90%">\n'
    '\t<tr><td align="right"><a style="text-decoration:underline;" href="./searchreport.cfm">New Search</a></td></tr>')
_DETAIL_EMPTY = (  # medsun_details.cfm?id=500 (no public report), abridged to three rows
    'Back to search results</a></td></tr>\n\t</table>\n\t<table cellspacing=5 cellpadding=0 border=0>\n'
    '<tr valign="top">\n<th align="left">Type of Device:</th>\n<td>\n<table><tr valign="top">\n<td align="left">\n'
    '</td>\n</tr>\n</table>\n</td>\n</tr>\n<tr valign="top">\n<th align="left">Device Brand Name:</th>\n<td>\n'
    '<table><tr>\n<td>\n</td>\n</tr>\n</table>\n</td>\n</tr>\n'
    '<tr valign="top">\n<th align="left">Date of this Report:<br>(mm/dd/yyyy)</th>\n<td>\n<table><tr>\n<td></td>\n'
    '</tr>\n</table>\n</td>\n</tr>\n</table>\n<a href="./searchreport.cfm">New Search</a>')
_SEARCH_265 = (
    '<form name="submedsunform" action="medsunExcel.cfm" method="post" id="medsunform" autocomplete="off">'
    '<input type="hidden" name="ID" value="43209%2C43208%2C43207" /><a title="Export to Excel" '
    'href="javascript: document.getElementById(\'medsunform\').submit();"><img src="/scripts/cdrh/cfdocs/search/'
    'excel.gif" border="0" alt="Export all 3 Records to Excel">Export to Excel</a></form>'
    '<img src="images/arrow.down.gif" border="0" height="20" width="20" alt="sort all 3 records by device brand a-z">')
_SEARCH_NARROW = (
    '<input type="hidden" name="ID" value="1%2C2" />'
    '<img alt="sort all 500 records by device brand a-z">records meeting your search criteria returned\n'
    '\t\t\t\t\t\t\t.\n\t\t\t\t\t\t\t\tplease narrow your search.')
_SEARCH_NONE = '<td>&nbsp;</td> None Found for 01/01/2015 01/01/2015 <a href="./searchreport.cfm">New Search</a>'


def _selftest():
    import tempfile
    d = parse_detail(_DETAIL_43205)
    assert d["device_type"] == "set, administration, intravascular" and d["brand"] == "SmartSite Infusion Set", d
    assert d["manufacturer"] == "Carefusion 303, Inc." and d["report_date"] == "01/30/2015", d
    assert d["caused"] == "Not applicable" and not d["unknown"], d
    ev, resp = split_response(d["description"])
    assert ev == ("The secondary/piggyback IV backflowed into the main/primary IV fluid tubing drip chamber and bag."), ev
    assert resp == ("Manufacturer response for IV tubing primary set, carefusion (per site reporter): "
                    "have not recevied communication at this time"), resp
    assert split_response("Plain text only.") == ("Plain text only.", None)
    assert not has_content(parse_detail(_DETAIL_EMPTY))

    s = parse_search(_SEARCH_265)
    assert s == {"ok": True, "count": 3, "ids": ["43209", "43208", "43207"], "narrow": False}, s
    assert parse_search(_SEARCH_NARROW)["narrow"] and parse_search(_SEARCH_NONE)["count"] == 0
    assert not parse_search("<html>Service unavailable</html>")["ok"]

    # window splitting: a fake index of one report per day; cap of 500 forces splits
    assert month_windows(date(2002, 1, 1), date(2002, 3, 10))[-1] == (date(2002, 3, 1), date(2002, 3, 10))
    per_day = {date(2015, 1, 1) + timedelta(days=k): 30 for k in range(31)}      # 930 in January
    per_day[date(2015, 1, 20)] = 600                                               # one day over the cap

    def fake(d1, d2):
        n, ids, x = 0, [], d1
        while x <= d2:
            ids += [f"{x:%m%d}{j:03d}" for j in range(per_day.get(x, 0))]
            x += timedelta(days=1)
        n = len(ids)
        return {"ok": True, "count": min(n, LIMIT), "ids": ids[:LIMIT], "narrow": n >= LIMIT}
    res, fail = walk_windows(fake, [(date(2015, 1, 1), date(2015, 1, 31))])
    got = sum(c for _, _, c, _ in res)
    assert got == 930 - 30 and len(fail) == 1 and fail[0][0] == date(2015, 1, 20), (got, fail)
    assert all(c < LIMIT for _, _, c, _ in res)

    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "43205.html").write_text(_DETAIL_43205, encoding="utf-8")
        (t / "pages" / "500.html").write_text(_DETAIL_EMPTY, encoding="utf-8")
        synthetic = _DETAIL_43205.replace(  # SYNTHETIC: contact details added to test the scrub
            "have not recevied communication at this time",
            "please contact Jane Doe at 555-123-4567 or jdoe@example.org").replace("Carefusion 303, Inc.", "ACME Corp")
        write_page(t / "pages", "43206", synthetic)                  # gzip cache path
        assert read_page(t / "pages", "43206").startswith("<!-- trimmed: report table only -->\nBack to search")
        with open(shard_path(t / "pages", "43999"), "ab") as fh:     # a member cut short by a crash
            fh.write(__import__("gzip").compress(b'{"id": "43999", "html": "x"}\n')[:15])
        assert "43206" in load_pages(t / "pages") and "43999" not in load_pages(t / "pages")
        assert repair_shard(shard_path(t / "pages", "43206")) and not repair_shard(shard_path(t / "pages", "43206"))
        write_page(t / "pages", "43998", "<html>after repair</html>")
        assert load_pages(t / "pages")["43998"] == "<html>after repair</html>"
        assert parse_detail(read_page(t / "pages", "43206"))["brand"] == "SmartSite Infusion Set"
        (t / "ids.json").write_text(json.dumps([["500", "x"], ["43205", "x"], ["43206", "x"], ["43207", "x"]]))
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-12", files=[]).write(t / "manifest.json")
        st = transform(t, t / "medsun.ttl")
        ttl = (t / "medsun.ttl").read_text(encoding="utf-8")
        txt = (t / "medsun_text.ttl").read_text(encoding="utf-8")
    assert st.records == 2 and st.skipped == 2, st
    assert f"<{iri.medsun_report('43205')}>" in ttl and "a ex:MedSunReport" in ttl
    assert 'ex:reportDate "2015-01-30"^^xsd:date' in ttl
    assert 'rdfs:label "MedSun report 43205: SmartSite Infusion Set"' in ttl
    assert 'ex:genericName "set, administration, intravascular"' in ttl
    assert 'ex:causedOrContributedTo "Not applicable"' in ttl
    assert f"ex:hasManufacturerFirm <{iri.firm_name(normalize_org('Carefusion 303, Inc.'))}>" in ttl
    assert f"<{iri.firm_name(normalize_org('Carefusion 303, Inc.'))}>\n    a ex:FirmName" in ttl
    assert 'ex:sourceUrl "' + PAGE_URL.format("43205") + '"' in ttl and '"2026-10-12"^^xsd:date' in ttl
    assert "backflowed" not in ttl and "recevied" not in ttl            # narratives kept apart
    assert 'ex:eventDescription "The secondary/piggyback IV backflowed' in txt
    assert 'ex:manufacturerResponse "Manufacturer response for IV tubing primary set' in txt
    assert "555-123-4567" not in txt and "jdoe@example.org" not in txt and "Jane Doe" not in txt, txt
    assert ttl.count("rdfs:label") == 4                                 # 2 reports + 2 firms, one each
    print(f"medsun.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
