#!/usr/bin/env python3
"""
_cfweb.py -- shared plumbing for the accessdata.fda.gov ColdFusion (cfdocs) page scrapers
(rh_codes, rh_actions, cecv, advisory). Not a source module (the leading underscore keeps
it out of base.source_names()).

  Fetcher   polite, cached page fetcher:
              * User-Agent medical-device-kg/rebuild (Hofstra University; 1 request per second)
              * at most one request per `delay` seconds (default 1.0)
              * retries with exponential backoff (2, 4, 8, 16 s) on network errors and 5xx
              * every page cached under <dest>/pages/<name>.html, written atomically;
                a cached page is reused unless it is older than `max_age_days`
              * FDA's "Accessdata Error" page (HTTP 503 "The page you are requesting is
                currently unavailable") raises Unavailable after the retries, so a refused
                page is reported, never cached as data
  text()    HTML fragment -> plain text (tags removed, entities decoded, whitespace collapsed)
"""
import html as htmllib
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
BASE = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/"
ERROR_MARK = "FDA Internet Application Site (Accessdata) Error"


class Unavailable(Exception):
    """FDA answered with its Accessdata Error page (or kept failing) for this URL."""


def is_error_page(body):
    return ERROR_MARK in body or "<title>Accessdata Error" in body


def text(fragment):
    if fragment is None:
        return None
    t = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", fragment)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = htmllib.unescape(t).replace("\xa0", " ")
    lines = [" ".join(ln.split()) for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln) or None


def one_line(fragment):
    t = text(fragment)
    return " ".join(t.split()) if t else None


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_")[:180] or "page"


class Fetcher:
    def __init__(self, dest, delay=1.0, retries=4, timeout=90, log=print):
        self.pages = Path(dest) / "pages"
        self.pages.mkdir(parents=True, exist_ok=True)
        self.delay, self.retries, self.timeout, self.log = delay, retries, timeout, log
        self._next = 0.0
        self.fetched = self.cached = self.errors = 0

    def path(self, name):
        return self.pages / f"{safe_name(name)}.html"

    def _fresh(self, p, max_age_days):
        if not p.exists() or p.stat().st_size < 200:
            return False
        if max_age_days is None:
            return True
        return (time.time() - p.stat().st_mtime) < max_age_days * 86400

    def get(self, url, name, data=None, max_age_days=None):
        """Return the page body (str). `data` (dict) makes it a form POST."""
        p = self.path(name)
        if self._fresh(p, max_age_days):
            self.cached += 1
            return p.read_text(encoding="utf-8", errors="replace")
        body_data = urllib.parse.urlencode(data).encode("ascii") if data is not None else None
        last = None
        for attempt in range(self.retries + 1):
            wait = self._next - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next = time.monotonic() + self.delay
            req = urllib.request.Request(url, data=body_data, headers={"User-Agent": UA})
            try:
                raw = urllib.request.urlopen(req, timeout=self.timeout).read()
                body = raw.decode("utf-8", errors="replace")
                if is_error_page(body):
                    last = Unavailable(f"Accessdata Error page for {url}")
                else:
                    tmp = p.with_suffix(".part")
                    tmp.write_bytes(raw)
                    os.replace(tmp, p)
                    self.fetched += 1
                    return body
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace") if e.fp else ""
                last = Unavailable(f"HTTP {e.code} for {url}" + (" (Accessdata Error page)" if is_error_page(body) else ""))
                if e.code < 500 and e.code != 429:
                    break
            except Exception as e:                     # network error: retry
                last = e
            if attempt < self.retries:
                time.sleep(2 ** (attempt + 1))
        self.errors += 1
        raise last if isinstance(last, Unavailable) else Unavailable(f"{url}: {last}")


def _selftest():
    assert text("<td>A&amp;B<br>  C </td>") == "A&B\nC"
    assert one_line("<td>A&amp;B<br>  C </td>") == "A&B C"
    assert is_error_page("<h1>FDA Internet Application Site (Accessdata) Error</h1>")
    assert safe_name("rid=3179-12-2021&x y") == "rid_3179-12-2021_x_y"
    print("_cfweb.py self-test PASSED")


if __name__ == "__main__":
    _selftest()
