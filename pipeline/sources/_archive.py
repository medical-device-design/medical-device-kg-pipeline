#!/usr/bin/env python3
"""
_archive.py -- read-only access to FDA's official web archives, for the few listings that fda.gov
now links ONLY to an archived copy (safety_comms, shortages, warning_letters). Not a source module
(the leading underscore keeps it out of base.source_names()).

Use is limited to archive copies that fda.gov itself links to (PI approval of 2026-11-03):
  * Pagefreezer  -- FDA's current archive host. fda.gov links pages as
        https://public4.pagefreezer.com/browse/FDA/<DD-MM-YYYY>T<HH:MM>/<fda.gov url>
    which redirects to a JavaScript viewer (us.pagefreezer.com/en-US/wa/browse/<task id>). The viewer
    reads the capture from its public API and replays a WACZ file (a zip of WARC files, ~24 GB per
    crawl) with HTTP range requests. Pagefreezer below does the same: it asks the public API for the
    capture nearest the linked time, reads the capture's CDXJ index (index.idx + one compressed block
    of index.cdx.gz per lookup) and fetches only the one WARC record of the page by byte range.
  * Wayback      -- web.archive.org snapshots that fda.gov links directly; read in raw mode
        https://web.archive.org/web/<timestamp>id_/<fda.gov url>
    (the original bytes, without the archive's toolbar).
  * ArchiveIt    -- FDA's earlier official archive (Archive-It collection 7993, last capture
    December 2020), linked from https://www.fda.gov/about-fda/about-website/fdagov-archive. Read in
    raw mode (https://wayback.archive-it.org/7993/<timestamp>id_/<url>) and through its CDX API.

Politeness: User-Agent medical-device-kg/rebuild (Hofstra University; 1 request per second), at most
one request per `delay` seconds per fetcher (one fetcher per run; runs are single-threaded), retries
with exponential backoff (2, 4, 8, 16 s; Retry-After is honored) on network errors, 5xx and 429.
A host that keeps answering 403/429 raises Refused, which callers report and do not work around.
Every response (pages, API answers, index blocks, WARC records) is cached under the given cache
directory (downloads/v007/<source>/archive/), so a rerun refetches nothing.
"""
import bisect
import gzip
import hashlib
import json
import os
import re
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
ARCHIVE_NOTE = "read from FDA's web archive copy ({}); fda.gov no longer serves this page"


class Refused(Exception):
    """The archive host refused or rate-limited the requests (403/429 after backoff)."""


class Missing(Exception):
    """The archive has no capture of this URL."""


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_")[:150] or "x"


def hashed_name(prefix, key, ext):
    """Short, stable cache file name for a long URL."""
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{safe_name(key.split('://', 1)[-1])[-90:]}_{h}{ext}"


HOST_DELAY = {"wayback.archive-it.org": 3.0}   # Archive-It answered 429 at 1 request/second (2026-11-03)
MAX_HOST_DELAY = 60.0


class Fetcher:
    """Polite cached HTTP fetcher (bytes). A 429 answer is retried after a long pause (Retry-After,
    else 60, 120, 240, 480 s) and doubles that host's spacing between requests (up to 60 s; it eases
    back by a quarter after 50 answers in a row without a refusal); a host that still refuses after
    the pauses raises Refused."""

    def __init__(self, cache, delay=1.0, retries=4, timeout=120, log=print):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay, self.retries, self.timeout, self.log = delay, retries, timeout, log
        self._next = 0.0
        self.fetched = self.cached = 0
        self.refusals = 0
        self._host_delay, self._ok = {}, {}
        self.final_url = {}

    def get(self, url, name, headers=None, cache=True):
        p = self.cache / name
        if cache and p.exists() and p.stat().st_size > 0:
            self.cached += 1
            meta = p.with_name(p.name + ".url")
            if meta.exists():
                self.final_url[url] = meta.read_text(encoding="utf-8").strip()
            return p.read_bytes()
        last, refused = None, 0
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in range(self.retries + 1):
            delay = max(self.delay, self._host_delay.get(host, HOST_DELAY.get(host, 0)))
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + delay
            h = {"User-Agent": UA}
            h.update(headers or {})
            try:
                r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=self.timeout)
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                self.fetched += 1
                self._ok[host] = self._ok.get(host, 0) + 1
                if self._ok[host] >= 50 and host in self._host_delay:          # ease back
                    self._host_delay[host] = max(HOST_DELAY.get(host, self.delay), self._host_delay[host] * 0.75)
                    self._ok[host] = 0
                if cache:
                    tmp = p.with_name(p.name + ".part")
                    tmp.write_bytes(raw)
                    os.replace(tmp, p)
                    if r.geturl() != url and "Signature=" not in r.geturl():   # an archive redirect
                        p.with_name(p.name + ".url").write_text(r.geturl(), encoding="utf-8")
                self.final_url[url] = r.geturl()
                return raw
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404:
                    raise Missing(f"HTTP 404 for {url}")
                if e.code in (403, 429):
                    refused += 1
                    self.refusals += 1
                    self._ok[host] = 0
                    cur = self._host_delay.get(host, HOST_DELAY.get(host, self.delay))
                    self._host_delay[host] = min(MAX_HOST_DELAY, max(cur * 2, 2.0))
                    if attempt < self.retries:
                        ra = e.headers.get("Retry-After") if e.headers else None
                        pause = int(ra) if ra and ra.isdigit() else 60 * 2 ** attempt
                        self.log(f"    {host} answered {e.code}; pausing {min(pause, 900)} s, then 1 request per "
                                 f"{self._host_delay[host]:.0f} s")
                        time.sleep(min(pause, 900))
                    continue
                elif e.code < 500:
                    raise Missing(f"HTTP {e.code} for {url}")
            except Exception as e:                       # network error: retry
                last = e
            if attempt < self.retries:
                time.sleep(2 ** (attempt + 1))
        if refused:
            raise Refused(f"{url}: refused/rate-limited ({last})")
        raise Missing(f"{url}: {last}")


# ---------------------------------------------------------------------------
#  SURT keys and HTTP payload decoding
# ---------------------------------------------------------------------------
def surt(url):
    """'https://www.fda.gov/a/B?y=2&x=1' -> 'gov,fda)/a/b?x=1&y=2' (pywb style)."""
    u = urllib.parse.urlsplit(url.strip())
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (u.path or "/").lower()
    q = "&".join(sorted(u.query.lower().split("&"))) if u.query else ""
    return ",".join(reversed(host.split("."))) + ")" + path + ("?" + q if q else "")


def _dechunk(b):
    out, i = bytearray(), 0
    while True:
        j = b.find(b"\r\n", i)
        if j < 0:
            raise ValueError("bad chunk")
        n = int(b[i:j].split(b";")[0].strip() or b"0", 16)
        if n == 0:
            return bytes(out)
        out += b[j + 2:j + 2 + n]
        i = j + 2 + n + 2


def http_payload(rec):
    """Raw WARC 'response' record bytes -> (warc headers dict, http status, http headers dict, body)."""
    a = rec.find(b"\r\n\r\n")
    wh = {}
    for ln in rec[:a].decode("latin-1").split("\r\n")[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            wh[k.strip().lower()] = v.strip()
    http = rec[a + 4:]
    b = http.find(b"\r\n\r\n")
    head = http[:b].decode("latin-1").split("\r\n")
    status = int(head[0].split()[1]) if len(head[0].split()) > 1 else None
    hh = {}
    for ln in head[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hh[k.strip().lower()] = v.strip()
    body = http[b + 4:]
    if "content-length" in wh:                 # drop trailing record separator
        body = body[:max(0, int(wh["content-length"]) - (b + 4))]
    if "chunked" in hh.get("transfer-encoding", "").lower():
        try:
            body = _dechunk(body)
        except Exception:
            pass                               # already de-chunked by the crawler
    enc = hh.get("content-encoding", "").lower()
    if enc in ("gzip", "x-gzip"):
        try:
            body = gzip.decompress(body)
        except Exception:
            pass                               # stored decoded
    elif enc == "deflate":
        try:
            body = zlib.decompress(body)
        except Exception:
            try:
                body = zlib.decompress(body, -15)
            except Exception:
                pass
    elif enc == "br":
        try:
            import brotli                      # not installed on the build VM; stored decoded so far
            body = brotli.decompress(body)
        except Exception:
            pass
    return wh, status, hh, body


# ---------------------------------------------------------------------------
#  Pagefreezer (FDA's current archive host)
# ---------------------------------------------------------------------------
PF_API = "https://pageapi-prod-wa-usw2-wa.pagefreezer.com/public/api/v1"
PF_FDA_TASK = "456c38cb-745a-47a8-b856-45444fd4b29d"        # the "FDA" crawl task FDA's links resolve to
_PF_LINK = re.compile(r"https?://public\d*\.pagefreezer\.com/browse/FDA/(\d{2})-(\d{2})-(\d{4})T(\d{2}):(\d{2})/"
                      r"(https?:/+)(.*)$")


def parse_pf_link(link):
    """FDA's 'public4.pagefreezer.com/browse/FDA/01-08-2024T13:49/https:/www.fda.gov/x' ->
    ('2024-08-01T13:49:00Z', 'https://www.fda.gov/x')  (None if not such a link)."""
    m = _PF_LINK.match((link or "").strip())
    if not m:
        return None
    d, mo, y, hh, mm, scheme, rest = m.groups()
    return f"{y}-{mo}-{d}T{hh}:{mm}:00Z", scheme.split(":")[0] + "://" + rest


def pf_link(ts, original):
    """Stable public link to a Pagefreezer copy, in the form fda.gov uses."""
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    return f"https://public4.pagefreezer.com/browse/FDA/{dt:%d-%m-%Y}T{dt:%H:%M}/{original}"


class Pagefreezer:
    def __init__(self, fetcher, task=PF_FDA_TASK, log=print):
        self.f, self.task, self.log = fetcher, task, log
        self._caps, self._idx, self._cd = {}, {}, {}

    # capture and its data package (signed URLs; refreshed when they expire)
    def capture(self, ts):
        if ts not in self._caps:
            q = urllib.parse.urlencode({"value": ts, "type": "START"})
            raw = self.f.get(f"{PF_API}/task-definitions/{self.task}/captures/find-by-timestamp?{q}",
                             f"pf_capture_{safe_name(ts)}.json")
            self._caps[ts] = json.loads(raw)
        return self._caps[ts]

    def package(self, cap_id):
        name = f"pf_datapackage_{cap_id}.json"
        p = self.f.cache / name
        if p.exists():
            dp = json.loads(p.read_text(encoding="utf-8"))
            exp = re.search(r"Expires=(\d+)", dp["resources"][0]["path"])
            if exp and int(exp.group(1)) > time.time() + 6 * 3600:
                return dp
        signed = json.loads(self.f.get(f"{PF_API}/captures/{cap_id}/url", "", cache=False))
        dp = json.loads(self.f.get(signed, "", cache=False))
        p.write_text(json.dumps(dp), encoding="utf-8")
        return dp

    def _index(self, cap_id):
        if cap_id not in self._idx:
            dp = self.package(cap_id)
            raw = self.f.get(dp["indexes"]["idx"], f"pf_{cap_id}_index.idx").decode("utf-8")
            keys, blocks = [], []
            for ln in raw.splitlines():
                if not ln or ln.startswith("!"):
                    continue
                k, ts, js = ln.split(" ", 2)
                j = json.loads(js)
                keys.append(f"{k} {ts}")
                blocks.append((j["offset"], j["length"]))
            self._idx[cap_id] = (keys, blocks)
        return self._idx[cap_id]

    def cdx(self, cap_id, url):
        """All CDXJ entries (dicts) of `url` in the capture."""
        key = surt(url)
        keys, blocks = self._index(cap_id)
        dp = self.package(cap_id)
        lo = max(0, bisect.bisect_left(keys, key + " ") - 1)
        hi = bisect.bisect_right(keys, key + " ~")
        out = []
        for i in range(lo, min(hi + 1, len(blocks))):
            off, ln = blocks[i]
            raw = self.f.get(dp["indexes"]["cdx"], f"pf_{cap_id}_cdx_{off}.gz",
                             headers={"Range": f"bytes={off}-{off + ln - 1}"})
            for line in gzip.decompress(raw).decode("utf-8", errors="replace").splitlines():
                k, ts, js = line.split(" ", 2)
                if k == key:
                    j = json.loads(js)
                    j["timestamp"] = ts
                    out.append(j)
        return out

    def _members(self, cap_id):
        """zip member name -> data offset inside the WACZ (stored members only)."""
        if cap_id in self._cd:
            return self._cd[cap_id]
        p = self.f.cache / f"pf_{cap_id}_members.json"
        if p.exists():
            self._cd[cap_id] = json.loads(p.read_text(encoding="utf-8"))
            return self._cd[cap_id]
        dp = self.package(cap_id)
        wurl, size = dp["resources"][0]["path"], int(dp["resources"][0]["bytes"])
        tail_len = min(size, 1 << 16)
        tail = self.f.get(wurl, f"pf_{cap_id}_tail.bin", headers={"Range": f"bytes={size - tail_len}-{size - 1}"})
        e = tail.rfind(b"PK\x05\x06")
        cd_size, cd_off = struct.unpack("<II", tail[e + 12:e + 20])
        loc = tail.rfind(b"PK\x06\x07")
        if loc >= 0:                                            # zip64
            z64 = struct.unpack("<Q", tail[loc + 8:loc + 16])[0]
            rec = tail[z64 - (size - tail_len):] if z64 >= size - tail_len else \
                self.f.get(wurl, f"pf_{cap_id}_z64.bin", headers={"Range": f"bytes={z64}-{z64 + 55}"})
            cd_size, cd_off = struct.unpack("<QQ", rec[40:56])
        cd = tail[cd_off - (size - tail_len):cd_off - (size - tail_len) + cd_size] if cd_off >= size - tail_len else \
            self.f.get(wurl, f"pf_{cap_id}_cd.bin", headers={"Range": f"bytes={cd_off}-{cd_off + cd_size - 1}"})
        members, i = {}, 0
        while cd[i:i + 4] == b"PK\x01\x02":
            (method,) = struct.unpack("<H", cd[i + 10:i + 12])
            csize, usize = struct.unpack("<II", cd[i + 20:i + 28])
            nlen, xlen, clen = struct.unpack("<HHH", cd[i + 28:i + 34])
            (lho,) = struct.unpack("<I", cd[i + 42:i + 46])
            name = cd[i + 46:i + 46 + nlen].decode("utf-8")
            extra = cd[i + 46 + nlen:i + 46 + nlen + xlen]
            j = 0
            while j + 4 <= len(extra):
                hid, hl = struct.unpack("<HH", extra[j:j + 4])
                if hid == 1:                                    # zip64 extended info
                    vals, k = [], j + 4
                    while k + 8 <= j + 4 + hl:
                        vals.append(struct.unpack("<Q", extra[k:k + 8])[0])
                        k += 8
                    for fld in ("u", "c", "o"):
                        cur = {"u": usize, "c": csize, "o": lho}[fld]
                        if cur == 0xFFFFFFFF and vals:
                            v = vals.pop(0)
                            usize, csize, lho = (v, csize, lho) if fld == "u" else (usize, v, lho) if fld == "c" else (usize, csize, v)
                j += 4 + hl
            if name.endswith((".warc.gz", ".warc")):
                members[name] = {"method": method, "header": lho}
            i += 46 + nlen + xlen + clen
        p.write_text(json.dumps(members), encoding="utf-8")
        self._cd[cap_id] = members
        return members

    def _data_offset(self, cap_id, name):
        """Offset of a stored member's data: local header + 30 + name + extra (read once, cached)."""
        members = self._members(cap_id)
        m = members.get(name) or members.get("archive/" + name)
        if not m or m.get("method") != 0:
            raise Missing(f"WARC {name} not stored in the WACZ of capture {cap_id}")
        if "data" not in m:
            dp = self.package(cap_id)
            lh = self.f.get(dp["resources"][0]["path"], f"pf_{cap_id}_lh_{m['header']}.bin",
                            headers={"Range": f"bytes={m['header']}-{m['header'] + 29}"})
            nlen, xlen = struct.unpack("<HH", lh[26:30])
            m["data"] = m["header"] + 30 + nlen + xlen
            (self.f.cache / f"pf_{cap_id}_members.json").write_text(json.dumps(members), encoding="utf-8")
        return m["data"]

    def get(self, url, ts):
        """-> dict(body=str, capture_ts, capture_id, archive_url, original_url) for an HTML page of the
        capture nearest `ts`. Raises Missing when that capture holds no 200 copy of the page."""
        cap = self.capture(ts)
        # The capture's index lists the page's records across neighboring daily crawls; incremental
        # crawls store only "revisit" records (payload unchanged). As the viewer does, replay the newest
        # full copy at or before the linked time (else the earliest after it); its WARC file name names
        # the crawl (capture) whose WACZ holds it.
        want = re.sub(r"\D", "", ts)[:14]
        cands = sorted((c for c in self.cdx(cap["id"], url) if str(c.get("status", "200")) == "200"
                        and "revisit" not in (c.get("mime") or "") and "html" in (c.get("mime") or "html")),
                       key=lambda c: c["timestamp"])
        if not cands:
            raise Missing(f"Pagefreezer capture {cap['id']} holds no copy of {url}")
        before = [c for c in cands if c["timestamp"] <= want]
        c = before[-1] if before else cands[0]
        fn = c["filename"]
        fm = re.match(r"^\d{4}-\d\d-\d\dT\d\d_\d\d_\d\dZ-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-", fn)
        if not fm:
            raise Missing(f"unexpected WARC name {fn}")
        cid = fm.group(1)
        dp = self.package(cid)
        start = self._data_offset(cid, fn) + int(c["offset"])
        raw = self.f.get(dp["resources"][0]["path"], f"pf_{cid}_rec_{safe_name(fn)}_{c['offset']}.warc.gz",
                         headers={"Range": f"bytes={start}-{start + int(c['length']) - 1}"})
        rec = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        wh, status, hh, body = http_payload(rec)
        if status != 200:
            raise Missing(f"archived copy of {url} has HTTP {status}")
        cts = datetime.strptime(c["timestamp"], "%Y%m%d%H%M%S").strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"body": body.decode("utf-8", errors="replace"), "capture_ts": cts, "capture_id": cid,
                "archive_url": pf_link(ts, url), "original_url": url}


# ---------------------------------------------------------------------------
#  Wayback-style archives (web.archive.org, Archive-It collection 7993)
# ---------------------------------------------------------------------------
class Wayback:
    BASE = "https://web.archive.org/web/"
    LABEL = "web.archive.org"

    def __init__(self, fetcher):
        self.f = fetcher

    def raw_url(self, ts, url):
        return f"{self.BASE}{ts}id_/{url}"

    def view_url(self, ts, url):
        return f"{self.BASE}{ts}/{url}"

    def get(self, url, ts, name=None):
        """-> dict(body, capture_ts, archive_url, original_url) of the snapshot at (or the archive's
        redirect nearest to) timestamp `ts`."""
        ru = self.raw_url(ts, url)
        raw = self.f.get(ru, name or hashed_name("wb", f"{ts}/{url}", ".html"))
        final = self.f.final_url.get(ru, ru)
        m = re.search(r"/(\d{14})id_/", final)
        cts = m.group(1) if m else ts
        return {"body": raw.decode("utf-8", errors="replace"), "capture_ts": cts,
                "archive_url": self.view_url(cts, url), "original_url": url}

    def cdx(self, url, name, **params):
        q = {"url": url, "output": "json"}
        q.update(params)
        raw = self.f.get(self.CDX + "?" + urllib.parse.urlencode(q), name)
        rows = json.loads(raw or b"[]")
        return [dict(zip(rows[0], r)) for r in rows[1:]] if rows else []

    CDX = "https://web.archive.org/cdx/search/cdx"


class ArchiveIt(Wayback):
    BASE = "https://wayback.archive-it.org/7993/"
    LABEL = "Archive-It collection 7993 (FDA.gov Web Archive)"
    CDX = "https://wayback.archive-it.org/7993/timemap/cdx"

    def cdx(self, url, name, **params):
        q = {"url": url}
        q.update(params)
        raw = self.f.get(self.CDX + "?" + urllib.parse.urlencode(q), name).decode("utf-8", errors="replace")
        fl = (params.get("fl") or "urlkey,timestamp,original,mimetype,statuscode,digest,length").split(",")
        return [dict(zip(fl, ln.split(" "))) for ln in raw.splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
#  One reader for the three archives (used by the source modules)
# ---------------------------------------------------------------------------
_WB_LINK = re.compile(r"(?i)^https?://(wayback\.archive-it\.org/7993|web\.archive\.org/web)/(\d{14})(?:id_)?/(https?:/+.*)$")
_LABEL = {"pf": "Pagefreezer", "ai": "Archive-It collection 7993", "wb": "web.archive.org"}


def fda_url(u, site="https://www.fda.gov"):
    """Normalize an fda.gov URL found in an archive ('http://www.fda.gov:80/x', '/x') -> 'https://www.fda.gov/x'."""
    import html as _h
    u = _h.unescape(u or "").strip()
    if u.startswith("/"):
        return site + u.split("#", 1)[0]
    m = re.match(r"(?i)^https?:/+(?:www\.)?fda\.gov(?::\d+)?(/.*)?$", u)
    return (site + (m.group(1) or "/")).split("#", 1)[0] if m else None


def ref_of(href, base=None):
    """A link on an fda.gov or archived page -> (kind, timestamp, original fda.gov url), or None.
    kind: 'pf' (Pagefreezer), 'ai' (Archive-It 7993), 'wb' (web.archive.org); base = (kind, ts) of the
    archived page holding a relative or fda.gov link, which is then read from the same archive and time."""
    import html as _h
    h = _h.unescape(href or "").strip()
    p = parse_pf_link(h)
    if p:
        return ("pf", p[0], fda_url(p[1]) or p[1])
    m = _WB_LINK.match(h)
    if m:
        orig = re.sub(r"^(https?):/+", r"\1://", m.group(3))
        return ("ai" if "archive-it" in m.group(1) else "wb", m.group(2), fda_url(orig) or orig)
    u = fda_url(h)
    if u and base:
        return (base[0], base[1], u)
    return None


def view_url(ref):
    kind, ts, orig = ref
    if kind == "pf":
        return pf_link(ts, orig)
    return (ArchiveIt.BASE if kind == "ai" else Wayback.BASE) + f"{ts}/{orig}"


def note(kind, capture=None, extra=""):
    when = ""
    if capture:
        c = re.sub(r"\D", "", capture)
        when = f", captured {c[:4]}-{c[4:6]}-{c[6:8]}"
    return ARCHIVE_NOTE.format(_LABEL.get(kind, kind) + when + extra)


def _last_segment(url):
    return url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]


class Reader:
    """Reads pages from FDA's archives through one polite Fetcher; each body and its metadata
    (archive URL, capture time) is kept under <dir>/pages/ so transform() works offline."""

    def __init__(self, cache_dir, delay=1.0, log=print):
        self.dir = Path(cache_dir)
        self.f = Fetcher(self.dir, delay=delay, log=log)
        self.pf, self.ai, self.wb = Pagefreezer(self.f, log=log), ArchiveIt(self.f), Wayback(self.f)
        (self.dir / "pages").mkdir(parents=True, exist_ok=True)

    def name(self, ref):
        kind, ts, orig = ref
        return f"{kind}_{safe_name(_last_segment(orig))[:120]}_" + hashlib.sha1(f"{kind}|{ts}|{orig}".encode()).hexdigest()[:10]

    def load(self, name):
        p, mp = self.dir / "pages" / f"{name}.html", self.dir / "pages" / f"{name}.json"
        if p.exists() and mp.exists():
            return json.loads(mp.read_text(encoding="utf-8")), p.read_text(encoding="utf-8", errors="replace")
        return None

    def get(self, ref):
        kind, ts, orig = ref
        name = self.name(ref)
        got = self.load(name)
        if got:
            return got
        r = self.pf.get(orig, ts) if kind == "pf" else (self.ai if kind == "ai" else self.wb).get(orig, ts)
        meta = {"kind": kind, "ts": ts, "original_url": orig, "archive_url": r["archive_url"],
                "capture_ts": r["capture_ts"], "page": name}
        (self.dir / "pages" / f"{name}.html").write_text(r["body"], encoding="utf-8")
        (self.dir / "pages" / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")
        return meta, r["body"]


def _selftest():
    assert surt("https://www.fda.gov/Medical-Devices/x?b=2&a=1") == "gov,fda)/medical-devices/x?a=1&b=2"
    assert parse_pf_link("https://public4.pagefreezer.com/browse/FDA/01-08-2024T13:49/https:/www.fda.gov/a/b") == \
        ("2024-08-01T13:49:00Z", "https://www.fda.gov/a/b")
    assert pf_link("2024-08-01T13:49:00Z", "https://www.fda.gov/a") == \
        "https://public4.pagefreezer.com/browse/FDA/01-08-2024T13:49/https://www.fda.gov/a"
    http = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n"
    rec = b"WARC/1.1\r\nWARC-Type: response\r\nContent-Length: %d\r\n\r\n" % len(http) + http
    wh, st, hh, body = http_payload(rec + b"\r\n\r\n")
    assert st == 200 and body == b"hello", (st, body)
    print("_archive.py self-test PASSED")


if __name__ == "__main__":
    _selftest()
