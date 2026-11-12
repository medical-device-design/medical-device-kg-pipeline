#!/usr/bin/env python3
"""
_ddapi.py -- shared client and helpers for the FDA Data Dashboard (OII) API modules
(dd_inspections, dd_citations, dd_compliance_actions, dd_import_refusals).

API (documented at https://datadashboard.fda.gov/oii/api/index.htm, read 2026-11-04)
------------------------------------------------------------------------------------
    POST https://api-datadashboard.fda.gov/v1/<endpoint>
    headers  Content-Type: application/json, Authorization-User, Authorization-Key
    body     {"start": 1, "rows": 5000, "sort": "", "sortorder": "", "filters": {...},
              "columns": [], "returntotalcount": true}
    answer   {"statuscode": 400, "message": "Success.", "resultcount": n,
              "totalrecordcount": N, "result": [ {...}, ... ]}
FDA's statuscode 400 means SUCCESS and 412 means "No results found"; 401 is "Not Authorized".
Paging: start is 1-based; at most 5000 rows per response. sort "" orders by the table's unique
primary key, which keeps paging stable (sorting on a non-unique column could repeat or skip rows).

CREDENTIALS
-----------
The user and key are read from the environment variables FDA_DD_USER and FDA_DD_KEY at request
time (the run script sources the VM's env file). They are placed only in the request headers of
each call. They are never logged, never written to a cache file, manifest or output, and never
kept on an object. Cached pages hold only the response body plus the request parameters (endpoint,
filters, start, rows); see _page_record(). An HTTP 401/403 or statuscode 401 stops the run at once
(AuthError) without retrying.

POLITENESS
----------
At most 1 request per second, User-Agent "medical-device-kg/rebuild (Hofstra University; 1 request
per second)", retries with exponential backoff on HTTP 5xx / 429 / timeouts / connection errors.
A complete fetch is reused for CACHE_DAYS days (a rerun then sends no request at all); after that
every page is fetched again in one pass, so the cached pages are always one consistent snapshot.

PERSONS
-------
Firms in these datasets include individuals (clinical investigators inspected under Bioresearch
Monitoring, sole proprietors, private importers). is_person() flags a firm name that reads as a
person's name; for such rows the modules drop the name, street address, city, ZIP, FEI, firm link
and firm-profile URL (the FEI identifies the person), keep the rest, and add ex:dataQualityNote.

MINTERS (in iri.py; the names below are kept here as aliases for the dd_* modules)
----------------------------------------------------------------------------------
    fda_inspection('1277550')               -> .../id/fda-inspection/1277550
    fda_inspection_citation('1227688','3696') -> .../id/fda-inspection/1227688/citation/3696
    fda_citation_reference('3696')          -> .../id/fda-citation/3696
    fda_compliance_action('Warning Letter','729208','3002807798')
                                            -> .../id/fda-compliance-action/warning_letter/729208/3002807798
    import_refusal('UPS-0443680-5/1/1')     -> .../id/import-refusal/ups_0443680_5_1_1
    import_refusal_charge('118')            -> .../id/import-refusal-charge/118
"""
import json
import os
import re
import socket
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

API = "https://api-datadashboard.fda.gov/v1/"
DOCS = "https://datadashboard.fda.gov/oii/api/index.htm"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
ROWS = 5000
CACHE_DAYS = 3
_ENV_USER, _ENV_KEY = "FDA_DD_USER", "FDA_DD_KEY"


# ---------------------------------------------------------------------------
#  Minters (iri.py; see module docstring)
# ---------------------------------------------------------------------------
fda_inspection = iri.fda_inspection
fda_inspection_citation = iri.fda_inspection_citation
fda_citation_reference = iri.fda_citation_reference
fda_compliance_action = iri.fda_compliance_action
import_refusal = iri.import_refusal
import_refusal_charge = iri.import_refusal_charge


# ---------------------------------------------------------------------------
#  Client
# ---------------------------------------------------------------------------
class AuthError(RuntimeError):
    """Credentials missing or refused. Never retried."""


class ApiError(RuntimeError):
    """A request the API rejected (bad field name, bad body) or that kept failing."""


def _headers():
    user, key = os.environ.get(_ENV_USER), os.environ.get(_ENV_KEY)
    if not user or not key:
        raise AuthError(f"{_ENV_USER} / {_ENV_KEY} are not set in the environment "
                        "(the run script must source the Data Dashboard env file)")
    return {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA,
            "Authorization-User": user, "Authorization-Key": key}


_ERR_KEYS = ("invalid_columns", "invalid_filters", "invalid_filters_type", "invalid_parameters",
             "fieldnames_with_invalid_values", "missing_parameters", "duplicate_parameters",
             "duplicate_filters", "duplicate_columns")


class Client:
    """Throttled POST client. Holds no credential: headers are built per request."""

    def __init__(self, delay=1.0, timeout=180, log=print, tries=6, opener=None):
        self.delay, self.timeout, self.log, self.tries = delay, timeout, log, tries
        self._open = opener or urllib.request.urlopen
        self._last = 0.0
        self.requests = 0

    def _wait(self):
        dt = time.time() - self._last
        if dt < self.delay:
            time.sleep(self.delay - dt)
        self._last = time.time()

    def post(self, endpoint, body):
        data = json.dumps(body).encode("utf-8")
        pause = 5.0
        last = None
        for attempt in range(1, self.tries + 1):
            self._wait()
            self.requests += 1
            status, payload = None, None
            try:
                req = urllib.request.Request(API + endpoint, data=data, headers=_headers(), method="POST")
                with self._open(req, timeout=self.timeout) as r:
                    status, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                status = e.code
                if status in (401, 403):
                    raise AuthError(f"HTTP {status} from {endpoint}; stopping without retry") from None
                if status >= 500 or status == 429:
                    last = f"HTTP {status}"
                else:
                    try:
                        payload = e.read()
                    except Exception:
                        payload = None
                    if not payload:
                        raise ApiError(f"HTTP {status} from {endpoint}") from None
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
                last = f"{type(e).__name__}: {getattr(e, 'reason', e)}"
            if payload is not None:
                try:
                    d = json.loads(payload)
                except ValueError:
                    last = f"HTTP {status}: response is not JSON"
                    d = None
                if d is not None:
                    sc = d.get("statuscode")
                    if sc == 401:
                        raise AuthError(f"statuscode 401 ({d.get('message')}) from {endpoint}; stopping without retry")
                    if sc in (400, 412):
                        return d
                    detail = {k: d[k] for k in _ERR_KEYS if k in d}
                    raise ApiError(f"{endpoint}: statuscode {sc} {d.get('message')!r} {detail}")
            self.log(f"  dd {endpoint}: attempt {attempt} failed ({last}); retrying in {pause:.0f}s")
            time.sleep(pause)
            pause = min(pause * 3, 300)
        raise ApiError(f"{endpoint}: gave up after {self.tries} attempts ({last})")


def _page_record(endpoint, filters, start, rows, d):
    """What is cached for one page: request PARAMETERS and the response body. No headers."""
    return {"endpoint": endpoint, "filters": filters, "start": start, "rows": rows,
            "statuscode": d.get("statuscode"), "message": d.get("message"),
            "resultcount": d.get("resultcount"), "totalrecordcount": d.get("totalrecordcount"),
            "result": d.get("result") or []}


def fetch_endpoint(dest, endpoint, queries, name, dataset, client=None, log=print,
                   cache_days=CACHE_DAYS, extra_files=()):
    """Fetch every page of every query into dest/pages/<tag>_s<start>.json.
    queries: [(tag, filters_dict), ...]. Returns a base.Manifest."""
    dest = Path(dest)
    pages_dir = dest / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    flog = dest / "fetch_log.json"
    if flog.exists():
        old = json.loads(flog.read_text(encoding="utf-8"))
        age = (time.time() - flog.stat().st_mtime) / 86400
        if old.get("complete") and age < cache_days and \
                [q["tag"] for q in old.get("queries", [])] == [t for t, _ in queries]:
            log(f"  dd {endpoint}: complete fetch from {old.get('date')} reused ({age:.1f} days old)")
            return base.Manifest.read(dest / "manifest.json")
    if flog.exists():
        flog.unlink()                                    # a fetch that stops midway leaves no "complete" log
    client = client or Client(log=log)
    qlog, files = [], []
    for tag, filters in queries:
        for old in pages_dir.glob(f"{tag}_s*.json"):     # one consistent snapshot per run
            old.unlink()
        start, total, got = 1, None, 0
        while True:
            body = {"start": start, "rows": ROWS, "sort": "", "sortorder": "", "filters": filters, "columns": []}
            if total is None:
                body["returntotalcount"] = True
            d = client.post(endpoint, body)
            if total is None:
                total = int(d.get("totalrecordcount") or 0)
            n = len(d.get("result") or [])
            p = pages_dir / f"{tag}_s{start:07d}.json"
            p.write_text(json.dumps(_page_record(endpoint, filters, start, ROWS, d), ensure_ascii=False),
                         encoding="utf-8")
            files.append(base.FileRef(url=API + endpoint, path=str(p.relative_to(dest)),
                                      sha256=base.sha256_file(p), bytes=p.stat().st_size))
            got += n
            log(f"  dd {endpoint} [{tag}]: start {start}: statuscode {d.get('statuscode')}, {n} rows "
                f"({got}/{total})")
            if n < ROWS or got >= total:
                break
            start += n
        qlog.append({"tag": tag, "filters": filters, "totalrecordcount": total, "rows": got,
                     "pages": len(list(pages_dir.glob(f"{tag}_s*.json")))})
        if got != total:
            log(f"  dd {endpoint} [{tag}]: WARNING received {got} rows, API total {total}")
    files += list(extra_files)
    m = base.Manifest(source=name, dataset=dataset, retrieved_at=base.today(), files=files)
    m.write(dest / "manifest.json")
    flog.write_text(json.dumps({"date": base.today(), "endpoint": API + endpoint, "queries": qlog,
                                "requests": client.requests, "complete": True}, indent=1), encoding="utf-8")
    return m


def iter_rows(src, tags=None):
    """Yield (tag, row) from the cached pages in page order."""
    for p in sorted(Path(src, "pages").glob("*_s*.json")):
        tag = p.name.rsplit("_s", 1)[0]
        if tags is not None and tag not in tags:
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        for r in d.get("result") or []:
            yield tag, r


def retrieved_at_of(src, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            return base.Manifest.read(man).retrieved_at
        except Exception:
            return None
    return retrieved_at


# ---------------------------------------------------------------------------
#  Value helpers
# ---------------------------------------------------------------------------
def s(v):
    """Stripped string or None (FDA pads some flags: "No ")."""
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def yes_no(v):
    t = (s(v) or "").lower()
    return {"yes": "true", "no": "false", "y": "true", "n": "false"}.get(t)


def fei_of(v):
    t = re.sub(r"\D", "", s(v) or "")
    return t or None


# a degree after a comma ("Jane Doe, MD") or in dotted form ("Jane Doe M.D."); a bare trailing "MD" without
# a comma is not used (it is also the Maryland state code in firm names)
_DEG = r"(?:MD|DO|PhD|RN|DDS|DMD|DPM|PharmD|MBBS|DVM|MPH|FAC[SPCE]|M\.\s?D|D\.\s?O|Ph\.\s?D|R\.\s?N|D\.\s?D\.\s?S|" \
       r"D\.\s?M\.\s?D|D\.\s?P\.\s?M|Pharm\.\s?D|M\.\s?B\.\s?B\.\s?S|D\.\s?V\.\s?M|M\.\s?P\.\s?H|F\.\s?A\.\s?C\.\s?[SPCE])"
_DEGREE = re.compile(r"(?i)(?:,\s*" + _DEG + r"\.?(?:\s*,\s*" + _DEG + r"\.?)*|\s(?:M\.\s?D|D\.\s?O|Ph\.\s?D|D\.\s?D\.\s?S|"
                     r"D\.\s?M\.\s?D|D\.\s?P\.\s?M|Pharm\.\s?D|D\.\s?V\.\s?M)\.?)\s*$")
_HONORIFIC = re.compile(r"(?i)^(?:Dr|Doctor|Prof|Professor)\.?\s+[^\W\d_]")
_ORG = re.compile(
    r"(?i)(?:^|[^a-z])(inc|incorporated|llc|l\.l\.c|llp|lp|ltd|limited|corp|corporation|company|co|cos|gmbh|mbh|ag|kg|"
    r"s\.?a|sas|sarl|srl|s\.r\.l|spa|s\.p\.a|bv|b\.v|nv|ab|oy|plc|pty|pvt|pte|kk|sdn|bhd|cv|"
    r"hospitals?|universit\w*|univ|college|school|cent(?:er|re)s?|clinics?|institut\w*|foundation|laborator\w*|labs?|"
    r"group|associates|assoc|health\w*|medical|medic\w*|med|pharm\w*|surgical|surgery|dental|dent\w*|devices?|systems?|"
    r"technolog\w*|tech|industr\w*|enterprises?|international|intl|trading|trade|traders|import\w*|export\w*|imp|exp|"
    r"manufactur\w*|mfg|factory|works|products?|services?|solutions?|suppl\w*|stores?|shop|market\w*|mart|"
    r"distribut\w*|holdings?|partners|department|dept|irb|board|committee|network|research|sciences?|scientific|"
    r"bio\w*|optic\w*|vision|imaging|diagnostic\w*|electr\w*|instruments?|equipment|plastics?|rubber|textiles?|"
    r"garments?|cosmetics?|beauty|care|office|county|city|state|national|federal|army|navy|of|the|and|for|usa|us|"
    r"america\w*|china|global|world|direct|online|express|logistics|cargo|freight|wholesale|retail|drugs?|"
    r"orthop\w*|ortho|dba|d/b/a|trust|fund|union|society|council|agency|authority|ministry|program|project|"
    r"sterile|sterilization|packaging|pack|lens|lenses|eyewear|glass\w*|steel|metal\w*|machin\w*|tools?|"
    r"hk|uk|sro|s\.r\.o|oü|zoo|sp|kft|eirl|ltda|e\.?i\.?r\.?l|cia|cía|unlimited|"
    r"studio|salon|spa|lab|house|depot|outlet|mall|plaza|club|consult\w*|management|mgmt|ventures?|capital|"
    r"brands?|import-export|pharmacy|apothe\w*|farm\w*|foods?|nutrition\w*|supplement\w*|wellness|fitness|sports?)"
    r"(?:$|[^a-z])")
_NAME_SHAPE = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’\-])*\.?(?:[\s,]+[^\W\d_](?:[^\W\d_]|['’\-])*\.?){1,4}$")


def is_person(name, strict=False):
    """True when a firm name reads as a person's name.
    Always: a degree suffix ("Jane Doe, M.D.", "John Roe Ph.D.") or an honorific ("Dr. Jane Doe") on a
    name with no organization word.
    strict=True (Bioresearch Monitoring context, where most "firms" are clinical investigators): also
    any name of 2-5 letter-only words with no organization word, no digit and no '&'.
    Outside that context plain two-word names are overwhelmingly businesses ("Abbott Vascular",
    "Philips Burton"), so the shape test is not applied there."""
    n = " ".join((s(name) or "").split())
    if not n:
        return False
    if _DEGREE.search(n):
        return True
    org = bool(_ORG.search(n))
    if _HONORIFIC.match(n) and not org:
        return True
    if not strict or org:
        return False
    if any(c.isdigit() for c in n) or "&" in n or "@" in n or "/" in n or "(" in n:
        return False
    return bool(_NAME_SHAPE.match(n))


PERSON_NOTE = ("firm name reads as an individual's name; the name, street address, city, ZIP code, "
               "FEI and firm profile are not ingested")
BIMO_PARTS = {"50", "54", "56", "58", "812"}     # informed consent, disclosure, IRB, GLP, IDE


def person_context(src, root=None):
    """From the cached inspection pages (sibling folder inspections_classifications, or
    $DD_INSPECTIONS_SRC): (Bioresearch Monitoring inspection ids, FEIs of firms judged to be
    individuals, available?). Lets every module withhold the same individuals."""
    root = Path(root or os.environ.get("DD_INSPECTIONS_SRC") or Path(src).parent / "inspections_classifications")
    if not (root / "pages").exists():
        return set(), set(), False
    first, bimo = {}, set()
    for _, r in iter_rows(root):
        iid = s(r.get("InspectionID"))
        if not iid:
            continue
        first.setdefault(iid, r)
        if "bioresearch" in (s(r.get("ProjectArea")) or "").lower():
            bimo.add(iid)
    feis = set()
    for iid, r in first.items():
        if is_person(r.get("LegalName"), strict=iid in bimo):
            f = fei_of(r.get("FEINumber"))
            if f:
                feis.add(f)
    return bimo, feis, True


def firm_pairs(r, name_key="LegalName", state_code_key="StateCode", state_name_key="State",
               strict=False, person_feis=()):
    """Firm name + address pairs for a row, or (person, []) when the firm is an individual.
    Returns (person: bool, fei or None, pairs)."""
    name = s(r.get(name_key))
    if is_person(name, strict) or fei_of(r.get("FEINumber")) in person_feis:
        return True, None, [("ex:countryCode", common.lit(s(r.get("CountryCode")))),
                            ("ex:country", common.lit(s(r.get("CountryName")))),
                            ("ex:dataQualityNote", common.lit(PERSON_NOTE))]
    fei = fei_of(r.get("FEINumber"))
    sc = s(r.get(state_code_key)) if state_code_key else None
    sn = s(r.get(state_name_key)) if state_name_key else None
    if state_code_key is None:                 # import refusals: one State column (code or name)
        sc, sn = (sn, None) if sn and len(sn) <= 3 else (None, sn)
    pairs = [("ex:companyName", common.lit(common.norm_text(name))),
             ("ex:feiNumber", common.lit(fei)),
             ("ex:hasManufacturer", f"<{iri.manufacturer(fei=fei)}>" if fei else None),
             ("ex:address", common.lit(common.norm_address(r.get("AddressLine1"), r.get("AddressLine2")))),
             ("ex:city", common.lit(s(r.get("City")))),
             ("ex:state", common.lit(sc)),
             ("ex:stateName", common.lit(sn)),
             ("ex:postalCode", common.lit(s(r.get("ZipCode")))),
             ("ex:countryCode", common.lit(s(r.get("CountryCode")))),
             ("ex:country", common.lit(s(r.get("CountryName")))),
             ("ex:firmProfileUrl", common.lit(s(r.get("FirmProfile"))))]
    return False, fei, pairs


def split_text(text):
    """FDA free text -> (text for the main file, scrubbed text for the _text.ttl file, hit count).
    Text in which scrub.scrub_text finds no personal-data pattern stays in the main file unchanged;
    otherwise only the scrubbed copy is written, to the separate text file."""
    import scrub
    t = common.norm_text(text)
    if not t:
        return None, None, 0
    clean, hits = scrub.scrub_text(t)
    n = sum(hits.values())
    return (t, None, 0) if n == 0 else (None, clean, n)


_CFR21 = re.compile(r"(?i)\b21\s*C\.?\s*F\.?\s*R\.?\s*(?:§+\s*|Parts?\s+|Sec(?:tion)?s?\.?\s*)?(\d{1,4})(?:\.(\d{1,4}))?")


def cfr_refs(text):
    """'21 CFR 820.100(b)' -> (['820.100'], ['820']); '21 CFR Part 11' -> ([], ['11'])."""
    secs, parts = [], []
    for m in _CFR21.finditer(text or ""):
        part = str(int(m.group(1)))
        if part == "0":
            continue
        if m.group(2):
            sec = f"{part}.{m.group(2)}"
            if sec not in secs:
                secs.append(sec)
        if part not in parts:
            parts.append(part)
    return secs, parts


def _selftest():
    assert fda_inspection("1277550").endswith("/id/fda-inspection/1277550")
    assert fda_inspection_citation("1227688", "3696").endswith("/id/fda-inspection/1227688/citation/3696")
    assert fda_compliance_action("Warning Letter", "729208", "3002807798").endswith(
        "/id/fda-compliance-action/warning_letter/729208/3002807798")
    assert import_refusal("UPS-0443680-5/1/1").endswith("/id/import-refusal/ups_0443680_5_1_1")
    assert cfr_refs("21 CFR 820.100(b)") == (["820.100"], ["820"])
    assert cfr_refs("21 CFR 1040.10(h)(1)(iii)") == (["1040.10"], ["1040"])
    assert cfr_refs("21 CFR Part 11") == ([], ["11"]) and cfr_refs("FD&C Act 501(h)") == ([], [])
    for n in ("Jane Doe, M.D.", "John Roe Ph.D.", "Maria de la Cruz-Lopez, MD, FACS", "Dr. Adrian Roe"):
        assert is_person(n), n
    for n in ("John Q. Public", "WANG XIAOMING", "Maria de la Cruz-Lopez"):
        assert is_person(n, strict=True) and not is_person(n), n
    for n in ("Foremount Enterprise Co., Ltd.", "Centra Health Inc IRB", "MAVERICK & WOLF", "P&G",
              "NINGBO JIANGBEI GOLDEN MATE IMP", "Respironics, Inc.", "Olympus Winter & IBE Gmbh",
              "Microsulis Medical Ltd.", "3M Company", "Medtronic", "Abbott Vascular", "Philips Burton",
              "Mesa Laboratories, Inc., Bozeman Manufacturing Facility", "Acme Medical Baltimore MD",
              "Dr. Reddy's Laboratories Ltd.", "Denka Seiken Co., Ltd. - Niigata Factory"):
        assert not is_person(n), n
    for n in ("Foremount Enterprise Co., Ltd.", "Centra Health Inc IRB", "Mesa Laboratories, Inc., Bozeman Manufacturing Facility"):
        assert not is_person(n, strict=True), n
    assert fei_of(" 3010402691") == "3010402691" and fei_of(319) == "319" and fei_of(None) is None and fei_of("") is None
    assert yes_no("No ") == "false" and yes_no("Yes") == "true" and yes_no(None) is None

    # client: retries a 503, stops on 401 without retry, and never stores the credentials
    import io
    calls = []

    class R(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(seq):
        def op(req, timeout=None):
            calls.append(dict(req.header_items()))
            kind = seq.pop(0)
            if kind == 503:
                raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
            if kind == 401:
                raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)
            return R(json.dumps({"statuscode": 400, "message": "Success.", "resultcount": 1,
                                 "totalrecordcount": 1, "result": [{"FEINumber": "1"}]}).encode())
        return op
    os.environ[_ENV_USER], os.environ[_ENV_KEY] = "selftest-user", "selftest-key"
    try:
        c = Client(delay=0, opener=opener([503, 200]), log=lambda *_: None)
        c_sleep, time.sleep = time.sleep, (lambda *_: None)
        try:
            d = c.post("inspections_classifications", {"start": 1})
            assert d["statuscode"] == 400 and c.requests == 2
            assert "selftest-key" not in json.dumps(vars(c), default=str)
            c2 = Client(delay=0, opener=opener([401, 200]), log=lambda *_: None)
            try:
                c2.post("inspections_classifications", {"start": 1})
                raise AssertionError("401 must stop")
            except AuthError as e:
                assert c2.requests == 1 and "selftest" not in str(e)
            import tempfile
            with tempfile.TemporaryDirectory() as t:
                c3 = Client(delay=0, opener=opener([200]), log=lambda *_: None)
                fetch_endpoint(t, "inspections_classifications", [("devices", {"ProductType": ["Devices"]})],
                               "x", "y", client=c3, log=lambda *_: None)
                for p in Path(t).rglob("*"):
                    if p.is_file():
                        txt = p.read_text(encoding="utf-8")
                        assert "selftest-user" not in txt and "selftest-key" not in txt and "Authorization" not in txt, p
                assert [r for _, r in iter_rows(t)] == [{"FEINumber": "1"}]
        finally:
            time.sleep = c_sleep
    finally:
        del os.environ[_ENV_USER], os.environ[_ENV_KEY]
    print("_ddapi.py self-test PASSED")


if __name__ == "__main__":
    _selftest()
