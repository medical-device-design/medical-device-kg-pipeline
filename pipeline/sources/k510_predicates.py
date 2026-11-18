#!/usr/bin/env python3
"""
k510_predicates.py -- predicate and reference devices named in 510(k) summary PDFs -> RDF.

FDA publishes no structured predicate list for 510(k)s. The summary (or statement) PDF that
FDA files for each 510(k) usually names the predicate device(s) with their submission
numbers, and since about 2014 often a "Reference Device" as well. This module reads those
PDFs.

fetch(dest, devices_ttl=...)
    Downloads every 510(k) summary PDF listed in Devices@FDA (ex:summaryUrl on the
    ex:DeviceFDARecord nodes of devices_fda_with_pma.ttl; only URLs that FDA answered 200
    with a PDF to a HEAD request are listed there). Polite and resumable:
      * at most 1 request per second, User-Agent
        "medical-device-kg/rebuild (Hofstra University; 1 request per second)";
      * a file already present with size > 0 is never requested again; a 404 recorded in
        fetch_log.tsv is final and not asked again;
      * refusals (403, 429, 5xx, timeouts, connection errors) are retried after 30 s, 2 min
        and 10 min; the run stops after 10 numbers in a row are refused after all retries;
      * an answer that is not a PDF (HTML error page) is logged "notpdf" and not stored;
      * one line per file in fetch_log.tsv (number, url, status, bytes, sha256, date,
        attempts); progress.json is rewritten every 25 files (done, rate, ETA);
      * the run stops when free disk space on the download volume falls below
        min_free_gb (default 105 GB, i.e. the VM's 100 GB floor plus a margin).
    Layout: <dest>/pdf/<first 3 characters>/<number>.pdf, <dest>/urls.tsv,
    <dest>/fetch_log.tsv, <dest>/progress.json, <dest>/manifest.json.

transform(src, out, known_dir=None, workers=4, ocr_dirs=None)
    pdftotext (poppler) on each PDF (text cached under <src>/text/), then
    extract_links() on the text. A PDF whose text layer has fewer than 100 non-space
    characters is image-only. For such a PDF text_of() looks for OCR text made elsewhere
    (no OCR engine is installed on the build VM; the OCR runs on Star HPC, see
    scripts/star/ocr_batch.sh): a file <stem>.txt (the PDF's name with .txt, e.g.
    K981234.txt, H970005A.txt) under one of the OCR batch folders given by `ocr_dirs` or,
    when that is None, by the environment variable MDKG_OCR_DIR (folders separated by ":",
    shell wildcards allowed, e.g. /srv/medical-device-kg/work/ocr-*). In each folder the
    text is looked for under out/txt/, else txt/, else the folder itself, at any depth
    (ocr_batch.sh writes out/txt/<kind>/pdf/K98/K981234.txt, the first batch
    out/txt/k510/K981234.txt); a later folder in the list wins over an earlier one. OCR
    text with at least 100 non-space characters is used and the result is marked:
    ex:extractionMethod "ocrmypdf/tesseract+regex v1" and the confidence one level lower
    than the same reading from a text layer (high -> medium, medium -> low, low stays low),
    because OCR misreads digits. A PDF that still has no usable text is listed in
    <out stem>.ocr_backlog.tsv and yields no links.

    Extraction method "pdftotext+regex v1":
      numbers   K + 6 digits, DEN + 6 digits, P + 6 digits (optionally /Snnn); for K and
                DEN also "K 123456", "K-123456", "K12345 6" and the letter O read for a
                zero ("KO12997"), with at least 4 real digits. The submission's own number
                is never a link.
      sections  a heading at the start of a line or followed by a colon:
                "Predicate Device(s)", "Legally marketed device(s) [to which equivalence is
                claimed]", "Primary/Secondary/Additional Predicate", "Identification of the
                predicate" -> predicate section; "Reference Device(s)" -> reference section;
                any other common summary heading (Device Description, Intended Use,
                Indications for Use, Technological Characteristics, Performance Data,
                Conclusion, Submitter, Classification, ...) ends a section. A section also
                ends 2,500 characters after its heading.
      confidence
                high   -- the number is inside a predicate (or reference) section;
                medium -- outside such a section, but within the same passage (350
                          characters before, 80 after) as "substantially equivalent",
                          "substantial equivalence", "legally marketed" or "predicate"
                          (reference: "reference device" within 200 characters before);
                low    -- the number appears elsewhere in the summary (written as a
                          predicate link with confidence "low"; filter on ex:confidence).
      one reading per cited number per summary: the most confident occurrence, and among equally
      confident ones the first in the text. A number followed by "(Reference)" / "used as a
      reference device", or preceded in the same sentence by "reference device", is a reference
      device even inside a predicate section; a low-confidence number one digit away from the
      summary's own number is dropped (a misread own number). A P number is kept only with high or
      medium confidence and not right after "Model", "Part Number", "Catalog" or "SKU" (part and
      model numbers of the form P + 6 digits are common in device tables).

    Model (one annotated statement per link, see ontology.ttl):
      <citing K510Record> ex:predicateDevice <K510Record | PMARecord | DeNovoRecord>
      <citing K510Record> ex:referenceDevice <...>
      <link node> a ex:PredicateDeviceLink | ex:ReferenceDeviceLink ;
          rdfs:label ; rdf:subject ; rdf:predicate ; rdf:object ; ex:citedNumber ;
          ex:citedAs (only when read OCR-tolerantly or with a separator) ;
          ex:extractionSection ; ex:extractionMethod ; ex:confidence ;
          ex:sourceDataset ; ex:sourceRecordId ; ex:sourceUrl (the PDF) ; ex:retrievedAt.
    Targets: K -> iri.k510, DEN -> iri.de_novo, P -> iri.pma (base PMA; a cited supplement
    is kept in ex:citedNumber). A target (or citing 510(k)) that no file of the release
    defines (k510.ttl, pma.ttl, denovo.ttl, hde.ttl and the reference_fallback*.ttl files in
    known_dir, default: the folder of `out`) gets one fallback node here: its class, one
    rdfs:label that says it is named in a 510(k) summary and missing from FDA's data, its
    number, an ex:dataQualityNote and provenance -- the reference_fallback.py pattern.
    Nodes that other files define get no label here (one label per node).
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "k510_predicates"
DATASET = "cdrh/510k-summary-pdf"
METHOD = "pdftotext+regex v1"
METHOD_OCR = "ocrmypdf/tesseract+regex v1"   # text from OCR (Star HPC), same regex extraction
OCR_ENV = "MDKG_OCR_DIR"
DOWNGRADE = {"high": "medium", "medium": "low", "low": "low"}   # confidence of an OCR-derived reading
USER_AGENT = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
MIN_TEXT_CHARS = 100          # fewer non-space characters than this -> image-only PDF
RETRY_WAITS = (30, 120, 600)  # seconds before retries 1..3 of a refused request
MAX_REFUSED_IN_A_ROW = 10
PDFTOTEXT = shutil.which("pdftotext") or "/usr/bin/pdftotext"


# ===========================================================================
#  FETCH
# ===========================================================================
_SUBJ = re.compile(r"^<([^>]+)>")
_DOCNUM = re.compile(r'^\s+ex:documentNumber "([^"]+)"')
_SURL = re.compile(r'^\s+ex:summaryUrl "([^"]+)"')


def summary_urls(devices_ttl, prefixes=("K",)):
    """(number, url) for every Devices@FDA record whose ex:summaryUrl is a PDF and whose
    document number starts with one of `prefixes` (510(k)s: "K")."""
    out, num, url = {}, None, None

    def flush():
        if num and url and num.upper().startswith(prefixes) and url.lower().endswith(".pdf"):
            out.setdefault(num.upper(), url)

    with open(devices_ttl, encoding="utf-8") as fh:
        for line in fh:
            if _SUBJ.match(line):
                flush()
                num = url = None
                continue
            m = _DOCNUM.match(line)
            if m:
                num = m.group(1).strip()
                continue
            m = _SURL.match(line)
            if m:
                url = m.group(1).strip()
        flush()
    return out


def _year(num):
    y = int(num[1:3]) if num[1:3].isdigit() else 0
    return 1900 + y if y >= 76 else 2000 + y


def pdf_path(dest, number):
    return Path(dest) / "pdf" / number[:3] / f"{number}.pdf"


def _read_log(dest):
    """number -> last log row (dict)."""
    rows = {}
    p = Path(dest) / "fetch_log.tsv"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            f = line.split("\t")
            if len(f) >= 6 and f[0] != "number":
                rows[f[0]] = {"url": f[1], "status": f[2], "bytes": f[3], "sha256": f[4], "date": f[5]}
    return rows


def _free_gb(path):
    """Space available to this user (as df shows it), in GB."""
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def _get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def fetch(dest, devices_ttl=None, numbers=None, limit=None, sample=None, seed=20261115,
          min_free_gb=105.0, sleep=time.sleep, getter=_get):
    """Download summary PDFs into dest (see the module docstring). devices_ttl defaults to
    $V7C_OUT/devices_fda_with_pma.ttl. `numbers` restricts to the given 510(k) numbers,
    `sample` takes a seeded random sample of that size, `limit` stops after that many
    requests. Returns a base.Manifest (files: urls.tsv and fetch_log.tsv)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if devices_ttl is None:
        devices_ttl = Path(os.environ.get("V7C_OUT", ".")) / "devices_fda_with_pma.ttl"
    urls_tsv = dest / "urls.tsv"
    # (re)build the URL list when it is missing or older than the Devices@FDA file (a new snapshot)
    if not urls_tsv.exists() or (Path(devices_ttl).exists()
                                 and Path(devices_ttl).stat().st_mtime > urls_tsv.stat().st_mtime):
        urls = summary_urls(devices_ttl)
        if urls_tsv.exists():
            for l in urls_tsv.read_text(encoding="utf-8").splitlines()[1:]:
                if "\t" in l:
                    urls.setdefault(*l.split("\t", 1))
        tmp = urls_tsv.with_suffix(".part")
        tmp.write_text("number\turl\n" + "".join(f"{n}\t{u}\n" for n, u in sorted(urls.items())),
                       encoding="utf-8")
        tmp.replace(urls_tsv)
    urls = dict(l.split("\t") for l in urls_tsv.read_text(encoding="utf-8").splitlines()[1:] if "\t" in l)
    order = sorted(urls, key=lambda n: (_year(n), n), reverse=True)        # newest first
    if numbers:
        wanted = {n.upper() for n in numbers}
        order = [n for n in order if n in wanted]
    if sample:
        order = sorted(random.Random(seed).sample(order, min(sample, len(order))),
                       key=lambda n: (_year(n), n), reverse=True)
    log = _read_log(dest)
    logf = open(dest / "fetch_log.tsv", "a", encoding="utf-8")
    if (dest / "fetch_log.tsv").stat().st_size == 0:
        logf.write("number\turl\tstatus\tbytes\tsha256\tdate\tattempts\n")
    started = time.time()
    last_req = 0.0
    done = requested = got_bytes = refused_row = 0
    total = len(order)
    stop_reason = "complete"

    def progress(cur):
        el = time.time() - started
        rate = requested / el if el > 0 else 0.0
        have = sum(1 for n in order if pdf_path(dest, n).exists())
        left = total - done
        (dest / "progress.json").write_text(json.dumps({
            "total": total, "processed_this_run": done, "requested_this_run": requested,
            "files_present": have, "bytes_this_run": got_bytes, "rate_per_s": round(rate, 3),
            "eta_hours": round(left / rate / 3600, 1) if rate else None, "last": cur,
            "free_gb": round(_free_gb(dest), 1), "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "stop_reason": stop_reason}, indent=1), encoding="utf-8")

    try:
        for n in order:
            p = pdf_path(dest, n)
            if (p.exists() and p.stat().st_size > 0) or log.get(n, {}).get("status") in ("404", "notpdf"):
                done += 1
                continue
            if limit is not None and requested >= limit:
                stop_reason = f"limit {limit} reached"
                break
            if done % 25 == 0 and _free_gb(dest) < min_free_gb:
                stop_reason = f"free disk below {min_free_gb} GB"
                break
            url = urls[n]
            status, data, attempts = None, None, 0
            for wait in (0,) + RETRY_WAITS:
                if wait:
                    sleep(wait)
                gap = time.time() - last_req
                if gap < 1.0:
                    sleep(1.0 - gap)
                last_req = time.time()
                attempts += 1
                requested += 1
                try:
                    code, data = getter(url)
                    status = str(code)
                    break
                except urllib.error.HTTPError as e:
                    status = str(e.code)
                    if e.code == 404:
                        break
                except Exception as e:      # timeout, reset, DNS
                    status = "error:" + type(e).__name__
                data = None
            sha = nbytes = ""
            if status == "200" and data is not None:
                if not data[:1024].lstrip().startswith(b"%PDF"):
                    status = "notpdf"
                else:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    tmp = p.with_suffix(".part")
                    tmp.write_bytes(data)
                    tmp.replace(p)
                    import hashlib
                    sha, nbytes = hashlib.sha256(data).hexdigest(), str(len(data))
                    got_bytes += len(data)
            if status in ("200", "404", "notpdf"):
                refused_row = 0
            else:
                status = "refused:" + status
                refused_row += 1
            logf.write(f"{n}\t{url}\t{status}\t{nbytes}\t{sha}\t{base.today()}\t{attempts}\n")
            logf.flush()
            done += 1
            if done % 25 == 0:
                progress(n)
            if refused_row >= MAX_REFUSED_IN_A_ROW:
                stop_reason = f"FDA refused {refused_row} numbers in a row; stopped"
                break
    finally:
        logf.close()
        progress("end")
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[
        base.FileRef(url=str(devices_ttl), path="urls.tsv", sha256=base.sha256_file(urls_tsv),
                     bytes=urls_tsv.stat().st_size)])
    man.write(dest / "manifest.json")
    print(f"[{NAME}] fetch: {done}/{total} processed, {requested} requests, "
          f"{got_bytes / 1e6:.1f} MB this run; {stop_reason}", flush=True)
    return man


# ===========================================================================
#  EXTRACTION
# ===========================================================================
_D = r"[0-9OoIl]"          # a digit as read from the PDF text: O/o for 0, I/l for 1 (scanned pages)
_NUM_K = re.compile(r"(?<![A-Za-z0-9])(DEN|K)\s?[-\u2013(]?\s?(" + _D + r"{6}|" + _D + r"{1,5}[ \-\u2013]" + _D
                    + r"{1,5})(?![0-9])")
_NUM_P = re.compile(r"(?<![A-Za-z0-9])P\s?(\d{6})(?:\s?/?\s?(S\d{3}))?(?![0-9])")

_L = r"lega[l1I][l1I]y"   # "legally", also as OCR reads it ("LegaIly")
_PRED_WORDS = (r"(?:" + _L + r"[\s-]+marketed\s+(?:predicate\s+)?devices?(?:\s*\(\s*s\s*\))?"
               r"(?:\s+to\s+which\s+(?:substantial\s+)?equivalence\s+is\s+claimed)?"
               r"|(?:substantial\s+)?equivalence\s+(?:is\s+)?claimed(?:\s+to)?"
               r"|(?:primary\s+|secondary\s+|additional\s+|identification\s+of\s+(?:the\s+)?|name\s+of\s+(?:the\s+)?)?"
               r"predicated?\s*(?:devices?|device\s*\(\s*s\s*\)|510\s*\(k\)s?|products?|information)?)")
_REF_WORDS = r"(?:reference\s+(?:predicate\s+)?devices?(?:\s*\(\s*s\s*\))?)"
_OTHER_WORDS = (r"(?:device\s+description|description\s+of\s+(?:the\s+)?(?:subject\s+)?device|intended\s+use|"
                r"indications?\s+for\s+use|technolog\w*\s+characteristics|comparison|performance|non-?\s?clinical|"
                r"clinical|conclusions?|submitter|applicant|contact|device\s+name|trade\s+name|proprietary\s+name|"
                r"common\s+name|classification|regulation|product\s+code|date\s+(?:prepared|of\s+preparation)|"
                r"summary\s+of|sterilization|biocompatibility|software|testing|discussion|"
                r"safety\s+and\s+effectiveness)")
_SE_WORDS = r"(?:substantial\s+equivalence|statement\s+of\s+substantial\s+equivalence)"
_NUMBERED = r"(?:[0-9IVX]{1,4}[.)]|[A-Ha-h][.)]|\(\w{1,3}\))"
_LEAD = r"(?im)^[ \t]*(?:" + _NUMBERED + r"|[\u2022*\-])?[ \t]*"


_PRED_INLINE = (r"(?:(?:primary\s+|secondary\s+|additional\s+|identification\s+of\s+(?:the\s+)?)?"
                r"predicated?\s*(?:devices?|device\s*\(\s*s\s*\)|510\s*\(k\)s?|products?|information)?"
                r"|" + _L + r"[\s-]+marketed\s+predicate\s+devices?"
                r"|" + _L + r"[\s-]+marketed\s+devices?(?:\s*\(\s*s\s*\))?\s+to\s+which\s+(?:substantial\s+)?equivalence\s+is\s+claimed"
                r"|(?:substantial\s+)?equivalence\s+(?:is\s+)?claimed(?:\s+to)?)")


def _hdr(words, loose=True, inline=None):
    """A heading. Predicate / reference headings: at the start of a line and then a colon, dash, end of
    line or a short line, or anywhere when followed by a colon. Other headings (which end a section)
    must be numbered, followed by a colon, or written in capitals, so that the column headers of a
    comparison table ("510(k) Number", "Intended Use") do not end a predicate section."""
    if loose:
        return re.compile(_LEAD + r"(" + words + r")\b[ \t]*(?:[:\-\u2013.]|$|(?=[^\n]{0,60}$))"
                          r"|(?i:\b(" + (inline or words) + r")\b[ \t]*(?:\[[^\]\n]{0,20}\])?[ \t]*:)")
    return re.compile(r"(?im)^[ \t]*" + _NUMBERED + r"[ \t]*(" + words + r")\b"
                      r"|(?i:\b(" + words + r")\b[ \t]*(?:\[[^\]\n]{0,20}\])?[ \t]*:)"
                      r"|(?m:^[ \t]*(?:" + _NUMBERED + r")?[ \t]*(?-i:[A-Z][A-Z /&()-]{5,60})[ \t]*$)")


_H_PRED, _H_REF, _H_SE = _hdr(_PRED_WORDS, inline=_PRED_INLINE), _hdr(_REF_WORDS), _hdr(_SE_WORDS)
_H_OTHER = _hdr(_OTHER_WORDS, loose=False)
_CUE_PRED = re.compile(r"predicate", re.I)
_CUE_SE = re.compile(r"substantial(?:ly)?\s+equivalen|" + _L + r"\s+marketed|equivalence\s+(?:is\s+)?claimed", re.I)
_CUE_REF = re.compile(r"reference\s+(?:predicate\s+)?devices?", re.I)
_AFTER_REF = re.compile(r"^[^.;:]{0,90}?(?:\(\s*reference|\b(?:used\s+)?as\s+(?:a|the)\s+reference\s+device|"
                        r"\b(?:is|are)\s+(?:considered|used\s+as)\s+(?:a\s+|the\s+)?reference\s+devices?)", re.I)
SECTION_REACH = 2500
_RANK = {"high": 3, "medium": 2, "low": 1}
_P_NOT_PMA = re.compile(r"(?i)\b(?:model|part\s*(?:no\.?|number|#)|catalog(?:ue)?(?:\s*(?:no\.?|number|#))?|cat\.|sku|"
                        r"item\s*(?:no\.?|number|#))[\s.:#-]*\(?\s*$")
_ORDER = {"other": 0, "se": 1, "predicate": 2, "reference": 3}


def _markers(text):
    out = []
    for kind, rx in (("predicate", _H_PRED), ("reference", _H_REF), ("se", _H_SE), ("other", _H_OTHER)):
        for m in rx.finditer(text):
            if kind == "other" and re.search(r"(?i)predicate|reference\s+device|equivalence\s+(?:is\s+)?claimed",
                                             m.group(0)):
                continue          # an all-caps line such as "PREDICATE DEVICES" is not an "other" heading
            out.append((m.start(), m.end(), kind))
    # several headings at one place: the predicate / reference one counts (it sorts last)
    out.sort(key=lambda t: (t[0], _ORDER[t[2]]))
    return out


def normalize_number(prefix, digits):
    d = re.sub(r"[ \-\u2013(]", "", digits).translate(str.maketrans("OoIl", "0011"))
    return prefix.upper() + d


def find_numbers(text):
    """[(start, end, number, raw)] for every K / DEN / P number in the text."""
    found = []
    for m in _NUM_K.finditer(text):
        d = re.sub(r"[ \-\u2013]", "", m.group(2))
        if len(d) != 6 or sum(c.isdigit() for c in d) < 4:
            continue
        found.append((m.start(), m.end(), normalize_number(m.group(1), m.group(2)), m.group(0)))
    for m in _NUM_P.finditer(text):
        found.append((m.start(), m.end(), "P" + m.group(1), m.group(0)))
    found.sort()
    return found


def _one_digit_off(a, b):
    return len(a) == len(b) and a[:1] == b[:1] and sum(x != y for x, y in zip(a, b)) == 1


def extract_links(text, own):
    """Links named in one summary's text. own = the summary's own 510(k) number.
    Returns [{number, kind, confidence, section, raw, snippet}], one per cited number."""
    text = (text or "").replace("\f", "\n")
    own = (own or "").upper()
    marks = _markers(text)
    flat = re.sub(r"\s+", " ", text)
    # positions in `flat` for each position in `text` (for the passage windows)
    pos_map, j, prev_ws = [], 0, False
    for ch in text:
        pos_map.append(j)
        is_ws = ch.isspace()
        if not (is_ws and prev_ws):
            j += 1
        prev_ws = is_ws
    pos_map.append(j)
    nums = find_numbers(text)
    best = {}
    for i, (start, end, num, raw) in enumerate(nums):
        if num == own:
            continue
        # the latest heading that starts before the number and within reach
        sec = None
        for ms, me, kind in marks:
            if ms > start:
                break
            if me <= start + 1:
                sec = (ms, kind)
        fs, fe = pos_map[start], pos_map[end]
        nxt = pos_map[nums[i + 1][0]] if i + 1 < len(nums) else len(flat)
        nxh = next((pos_map[ms] for ms, me, k in marks if ms > start), len(flat))
        after = flat[fe:min(nxt, nxh, fe + 100)]     # up to the next cited number or heading only
        kind, conf, where = "predicate", "low", "elsewhere in the summary"
        if sec and start - sec[0] <= SECTION_REACH and sec[1] in ("predicate", "reference"):
            kind, conf = sec[1], "high"
            where = "Predicate Device(s) section" if kind == "predicate" else "Reference Device(s) section"
        else:
            floor = pos_map[sec[0]] if sec else 0      # a passage never reaches back across a heading
            before = flat[max(floor, fs - 400):fs]
            ref_at = max((m.end() for m in _CUE_REF.finditer(before)), default=-1)
            pred_at = max((m.end() for m in _CUE_PRED.finditer(before)), default=-1)
            if ref_at >= 0 and ref_at > pred_at:
                kind, conf, where = "reference", "medium", "sentence naming a reference device"
            elif ((pred_at >= 0 and pred_at >= len(before) - 350) or _CUE_SE.search(before[-350:]) or _CUE_PRED.search(after)
                  or _CUE_SE.search(after) or (sec and sec[1] == "se" and start - sec[0] <= SECTION_REACH)):
                kind, conf, where = "predicate", "medium", "substantial-equivalence or predicate passage"
        after2 = flat[fe:min(nxt, fe + 100)]          # up to the next cited number (a label may follow)
        near = flat[max(0, fs - 80):fs]
        near = near[near.rfind(". ") + 1:] if ". " in near else near
        if kind == "predicate" and (_AFTER_REF.search(after2) or (
                _CUE_REF.search(near) and not _CUE_PRED.search(near[max((m.end() for m in _CUE_REF.finditer(near))):]))):
            kind, where = "reference", where + "; named as a reference device next to the number"
        if num.startswith("P") and (conf == "low" or _P_NOT_PMA.search(flat[max(0, fs - 40):fs])):
            continue                                   # a part or model number, not a PMA
        if conf == "low" and _one_digit_off(num, own):
            continue                                   # most likely the summary's own number, misread
        snippet = flat[max(0, fs - 110):fe + 40]
        cand = {"number": num, "kind": kind, "confidence": conf, "section": where,
                "raw": re.sub(r"\s+", " ", raw), "snippet": snippet, "_key": (_RANK[conf], -start)}
        # one reading per cited number: the most confident one, and among equals the first in the text
        # (a summary names its predicate and reference devices before it reuses the numbers in tables)
        if num not in best or cand["_key"] > best[num]["_key"]:
            best[num] = cand
    out = []
    for num, c in best.items():
        c = dict(c)
        c.pop("_key")
        out.append(c)
    return out


# ===========================================================================
#  TRANSFORM
# ===========================================================================
def resolve_ocr_dirs(ocr_dirs=None):
    """OCR batch folders: `ocr_dirs` (a path, a ":"-separated string or a list), else $MDKG_OCR_DIR.
    Wildcards are expanded; folders that do not exist are dropped. Returns a tuple of str (order kept)."""
    import glob
    if ocr_dirs is None:
        ocr_dirs = os.environ.get(OCR_ENV, "")
    if isinstance(ocr_dirs, (str, os.PathLike)):
        ocr_dirs = [p for p in str(ocr_dirs).split(os.pathsep) if p.strip()]
    out = []
    for p in ocr_dirs:
        for q in (sorted(glob.glob(str(p))) if glob.has_magic(str(p)) else [str(p)]):
            if os.path.isdir(q) and q not in out:
                out.append(q)
    return tuple(out)


_OCR_INDEX = {}


def _ocr_index(dirs):
    """{PDF stem (upper case): OCR .txt path} over the batch folders; a later folder wins."""
    if dirs not in _OCR_INDEX:
        idx = {}
        for d in dirs:
            d = Path(d)
            base_dir = next((b for b in (d / "out" / "txt", d / "txt") if b.is_dir()), d)
            for t in sorted(base_dir.rglob("*.txt")):
                idx[t.stem.upper()] = t
        _OCR_INDEX[dirs] = idx
    return _OCR_INDEX[dirs]


_OCR_MARK = re.compile(r"(?m)^\s*\[(?:OCR skipped on page\(s\)[^\]]*|skipped page)\]\s*$")


def ocr_text(pdf, ocr_dirs):
    """OCR sidecar text for one PDF from the batch folders (see transform), or None."""
    dirs = resolve_ocr_dirs(ocr_dirs) if not isinstance(ocr_dirs, tuple) else ocr_dirs
    if not dirs:
        return None
    t = _ocr_index(dirs).get(Path(pdf).stem.upper())
    if t is None:
        return None
    return _OCR_MARK.sub("", t.read_text(encoding="utf-8", errors="replace"))


def _chars(text):
    return len(re.sub(r"\s+", "", text or ""))


def text_of(pdf, text_dir, ocr_dirs=()):
    """Text of one PDF. Returns (text, error, method).
    pdftotext output (cached under text_dir) -> method METHOD. When it has fewer than MIN_TEXT_CHARS
    non-space characters (or pdftotext fails) and OCR text with at least MIN_TEXT_CHARS is found in the
    OCR batch folders (`ocr_dirs`: tuple/list/str; None = $MDKG_OCR_DIR; () = no OCR) -> that text, no
    error, method METHOD_OCR. Otherwise the (short) pdftotext text, its error and METHOD."""
    pdf = Path(pdf)
    t = Path(text_dir) / pdf.parent.name / (pdf.stem + ".txt")
    text, err = "", None
    if t.exists() and t.stat().st_mtime >= pdf.stat().st_mtime:
        text = t.read_text(encoding="utf-8", errors="replace")
    else:
        t.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run([PDFTOTEXT, "-q", str(pdf), str(t)], capture_output=True, timeout=180)
            if r.returncode != 0 or not t.exists():
                err = f"pdftotext exit {r.returncode}"
            else:
                text = t.read_text(encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            err = "pdftotext timeout"
    if (err or _chars(text) < MIN_TEXT_CHARS) and ocr_dirs != ():
        o = ocr_text(pdf, ocr_dirs)
        if o is not None and _chars(o) >= MIN_TEXT_CHARS:
            return o, None, METHOD_OCR
    return text, err, METHOD


def _work(args):
    pdf, text_dir, own, ocr_dirs = args
    text, err, method = text_of(Path(pdf), text_dir, ocr_dirs)
    chars = _chars(text)
    if err:
        return own, "error", err, chars, [], method
    if chars < MIN_TEXT_CHARS:
        return own, "image-only", "", chars, [], method
    links = extract_links(text, own)
    if method == METHOD_OCR:
        for l in links:
            l["confidence"] = DOWNGRADE[l["confidence"]]
    return own, "text", "", chars, links, method


_SUBJ_B = re.compile(rb"^<([^>]+)>")


def known_subjects(known_dir, names=("k510.ttl", "pma.ttl", "denovo.ttl", "hde.ttl"),
                   kinds=("k510", "pma", "de-novo", "hde")):
    """IRIs of the given kinds that any of the files defines (as a subject), plus every
    subject of reference_fallback*.ttl (already named elsewhere)."""
    known_dir = Path(known_dir)
    files = [known_dir / n for n in names] + sorted(known_dir.glob("reference_fallback*.ttl"))
    prefixes = tuple((iri.ID + k + "/").encode() for k in kinds)
    seen = set()
    for f in files:
        if not f.exists():
            continue
        with open(f, "rb") as fh:
            for line in fh:
                if line[:1] == b"<":
                    m = _SUBJ_B.match(line)
                    if m and m.group(1).startswith(prefixes):
                        seen.add(m.group(1).decode())
    return seen


def target_iri(number):
    n = number.upper()
    if n.startswith("DEN"):
        return iri.de_novo(n), "ex:DeNovoRecord", "De Novo", "ex:deNovoNumber", "FDA De Novo data"
    if n.startswith("P"):
        return iri.pma(n), "ex:PMARecord", "PMA", "ex:pmaNumber", "FDA PMA data"
    return iri.k510(n), "ex:K510Record", "510(k)", "ex:kNumber", "FDA 510(k) data"


def transform(src, out, known_dir=None, workers=4, numbers=None, ocr_dirs=None):
    src, out = Path(src), Path(out)
    known_dir = Path(known_dir) if known_dir else out.parent
    text_dir = src / "text"
    ocr_dirs = resolve_ocr_dirs(ocr_dirs)
    urls = {}
    if (src / "urls.tsv").exists():
        urls = dict(l.split("\t") for l in (src / "urls.tsv").read_text(encoding="utf-8").splitlines()[1:] if "\t" in l)
    log = _read_log(src)
    pdfs = sorted((src / "pdf").glob("*/*.pdf"))
    if numbers:
        wanted = {n.upper() for n in numbers}
        pdfs = [p for p in pdfs if p.stem.upper() in wanted]
    known = known_subjects(known_dir)
    st = base.Stats(source=NAME)
    counts = Counter()
    by_conf = Counter()
    by_method = Counter()
    target_types = Counter()
    ocr, errors, links_rows = [], [], []
    results = []
    jobs = [(str(p), str(text_dir), p.stem.upper(), ocr_dirs) for p in pdfs]
    if workers and workers > 1 and len(jobs) > 50:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_work, jobs, chunksize=64))
    else:
        results = [_work(j) for j in jobs]
    results.sort()
    fallback = {}
    with base.TurtleWriter(out) as w:
        for own, state, err, chars, links, method in results:
            counts["pdfs"] += 1
            url = urls.get(own) or log.get(own, {}).get("url", "")
            date = log.get(own, {}).get("date") or None
            if state == "error":
                errors.append((own, url, err))
                counts["pdf_errors"] += 1
                continue
            if state == "image-only":
                ocr.append((own, url, chars))
                counts["image_only_ocr_backlog"] += 1
                continue
            counts["text_pdfs"] += 1
            if method == METHOD_OCR:
                counts["text_pdfs_from_ocr"] += 1
            if not links:
                counts["text_pdfs_without_numbers"] += 1
                continue
            child = iri.k510(own)
            if child not in known:
                fallback.setdefault(child, (own, "child"))
            preds = [l for l in links if l["kind"] == "predicate"]
            refs = [l for l in links if l["kind"] == "reference"]
            counts["summaries_with_links"] += 1
            if method == METHOD_OCR:
                counts["summaries_with_links_from_ocr"] += 1
            if any(l["confidence"] in ("high", "medium") for l in preds):
                counts["summaries_with_high_or_medium_predicate"] += 1
            pairs = []
            for l in sorted(links, key=lambda x: (x["kind"], x["number"])):
                t_iri, t_cls, what, num_prop, data = target_iri(l["number"])
                prop = "ex:predicateDevice" if l["kind"] == "predicate" else "ex:referenceDevice"
                pairs.append((prop, f"<{t_iri}>"))
                if t_iri not in known:
                    fallback.setdefault(t_iri, (l["number"], "target"))
                target_types[t_cls] += 1
                by_conf[f"{l['kind']}:{l['confidence']}"] += 1
                by_method[method] += 1
                links_rows.append((own, l["kind"], l["number"], l["confidence"], l["section"], l["raw"],
                                   url, l["snippet"], method))
            w.block(child, pairs)
            for l in sorted(links, key=lambda x: (x["kind"], x["number"])):
                t_iri, t_cls, what, num_prop, data = target_iri(l["number"])
                kind = l["kind"]
                cited = l["number"]
                raw_num = re.sub(r"\s+", " ", l["raw"]).strip()
                lp = [("a", "ex:PredicateDeviceLink" if kind == "predicate" else "ex:ReferenceDeviceLink"),
                      ("rdfs:label", common.lit(f"{own} names {cited} as a {kind} device ({l['confidence']} confidence)")),
                      ("rdf:subject", f"<{child}>"),
                      ("rdf:predicate", "ex:predicateDevice" if kind == "predicate" else "ex:referenceDevice"),
                      ("rdf:object", f"<{t_iri}>"),
                      ("ex:citedNumber", common.lit(cited)),
                      ("ex:citedAs", common.lit(raw_num) if raw_num.replace(" ", "") != cited else None),
                      ("ex:extractionSection", common.lit(l["section"])),
                      ("ex:extractionMethod", common.lit(method)),
                      ("ex:confidence", common.lit(l["confidence"]))]
                lp += common.provenance(DATASET, f"{own}/{kind}/{cited}", source_url=url or None,
                                        retrieved_at=date)
                w.block(iri.k510_device_link(own, kind, cited), lp)
                st.records += 1
        for t in sorted(fallback):
            num, role = fallback[t]
            t_iri, t_cls, what, num_prop, data = target_iri(num)
            if role == "child":
                lab = f"510(k) {num} (summary PDF on FDA's site; not in FDA 510(k) data)"
                note = ("Its 510(k) summary PDF is listed by Devices@FDA and was read for predicate devices, "
                        "but the openFDA 510(k) download has no record for this number.")
            else:
                lab = f"{what} {num} (named in a 510(k) summary; not in {data})"
                note = (f"Named as a predicate or reference device in a 510(k) summary PDF, but {data} "
                        "of this release has no record for this number. It may be a typing error in the "
                        "summary, a number read from a scanned page, or a record FDA does not publish.")
            w.block(t_iri, [("a", t_cls), ("rdfs:label", common.lit(lab)), (num_prop, common.lit(num)),
                            ("ex:dataQualityNote", common.lit(note))]
                    + common.provenance(DATASET, num))
            counts[f"fallback:{t_cls}"] += 1
    st.triples = w.triples
    stem = out.with_suffix("")
    with open(f"{stem}.links.tsv", "w", encoding="utf-8") as f:
        f.write("child\tkind\tcited\tconfidence\tsection\tas_written\tsummary_url\tsnippet\textraction_method\n")
        for r in links_rows:
            f.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
    with open(f"{stem}.ocr_backlog.tsv", "w", encoding="utf-8") as f:
        f.write("number\tsummary_url\ttext_chars\n")
        for r in ocr:
            f.write("\t".join(map(str, r)) + "\n")
    with open(f"{stem}.errors.tsv", "w", encoding="utf-8") as f:
        f.write("number\tsummary_url\terror\n")
        for r in errors:
            f.write("\t".join(map(str, r)) + "\n")
    summary = {"method": METHOD, "summary_urls_listed": len(urls), "pdfs_read": counts["pdfs"],
               **{k: v for k, v in counts.items() if k != "pdfs"},
               "links": st.records, "links_by_kind_confidence": dict(sorted(by_conf.items()))}
    if ocr_dirs:
        summary.update({"ocr_method": METHOD_OCR, "ocr_dirs": list(ocr_dirs),
                        "links_by_method": dict(sorted(by_method.items()))})
    summary.update({"link_targets_by_class": dict(sorted(target_types.items())), "triples": st.triples})
    Path(f"{stem}.counts.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"[{NAME}] transform: {json.dumps(summary)}", flush=True)
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    # --- extraction on summary layouts seen in real 510(k) summaries ------------
    t1 = ("510(k) Summary\nK192386\nDevice Name: KeraStat Cream\nClassification: Unclassified\n"
          "Product Code: KGN\nPredicate Device: KeraStat\u00ae Gel KeraNetics Inc. K162759 Cleared on June 2, 2017\n"
          "Reference Device: Xeroform dressing K 123456\nDevice Description: The device ... "
          "Unrelated mention of K999999 in a test-method footnote.\n")
    L = {l["number"]: l for l in extract_links(t1, "K192386")}
    assert set(L) == {"K162759", "K123456", "K999999"}, L
    assert L["K162759"]["kind"] == "predicate" and L["K162759"]["confidence"] == "high"
    assert L["K123456"]["kind"] == "reference" and L["K123456"]["confidence"] == "high"
    assert L["K123456"]["raw"] == "K 123456"
    assert L["K999999"]["confidence"] == "low"
    t2 = ("3. PREDICATE DEVICES\n* Oasis Wound Matrix by Healthpoint (K06171 1)\n"
          "* Collatek Foam (KO12997)\n4. DEVICE DESCRIPTION\nThe dressing ...\n"
          "5. SUBSTANTIAL EQUIVALENCE\nThe device is substantially equivalent to the Collagen ORC "
          "Matrix (K033523) and to a De Novo device DEN170002.\n"
          "This 510(k) (K080949) also cites P100047/S012 in a table and K 5 lost in text.")
    L = {l["number"]: l for l in extract_links(t2, "K080949")}
    assert L["K061711"]["confidence"] == "high" and L["K012997"]["confidence"] == "high", L
    assert L["K033523"]["confidence"] == "medium" and L["DEN170002"]["confidence"] == "medium"
    assert "K080949" not in L                                     # own number excluded
    assert L["P100047"]["kind"] == "predicate"
    assert all(n in ("K061711", "K012997", "K033523", "DEN170002", "P100047") for n in L), L
    t3 = ("Legally marketed device to which equivalence is claimed:\nAcme Pump, K011111\n"
          "Intended Use: infusion\nThe reference device K022222 provides the test method.")
    L = {l["number"]: l for l in extract_links(t3, "K033333")}
    assert L["K011111"]["confidence"] == "high" and L["K022222"]["kind"] == "reference", L
    assert not extract_links("Phone 555 123456, lot P 12 and KOREA K1234", "K000001")
    # v1 gold-sample fixes: table headers do not end a predicate section; "(Reference)" after a number;
    # a number named both ways at equal strength stays a predicate; OCR I/l for 1; own number misread
    t4 = ("3. Predicate Device:\n510(k) Number Trade Name Manufacturer\nK102648 ANT Inflation Device\n"
          "KI 03007 Atrion QL\n4. Device description\nThe device ...")
    L = {l["number"]: l for l in extract_links(t4, "K133795")}
    assert L["K102648"]["confidence"] == "high" and L["K103007"]["confidence"] == "high", L
    t5 = ("Predicate Device K230480, Weck Auto Endo5 (Primary) K133202, Hem-o-lok Clips (Reference)\n"
          "Device Description The clips ...\nPredicate Device: X (K171820)\nReference Device: Y (K171820)\n"
          "Indications for Use: Enclosure Ko83317 statement")
    L = {l["number"]: l for l in extract_links(t5, "K083319")}
    assert L["K230480"]["kind"] == "predicate" and L["K133202"]["kind"] == "reference", L
    assert L["K171820"]["kind"] == "predicate" and "K083317" not in L, L
    t6 = ("6. Substantial Equivalence\nFlareHawk K182114 is also used as a predicate. The following devices are "
          "used as reference devices to show that titanium coatings exist on other legally marketed devices: "
          "SpineFrontier (K142504) and Tyber (K130573). Additionally, TiHawk9, K201367, is used as a reference device.")
    L = {l["number"]: l for l in extract_links(t6, "K202198")}
    assert L["K182114"]["kind"] == "predicate" and L["K142504"]["kind"] == "reference", L
    assert L["K130573"]["kind"] == "reference" and L["K201367"]["kind"] == "reference", L
    # part and model numbers that look like PMA numbers (crawl test, November 15, 2026)
    t7 = ("Predicate Device: Q-Rejuvalight Pro Facewear (Model: P190023) K230042\nDevice Description: masks\n"
          "Individual Bone Screw Part Number P011265 P011266 P011268\n")
    L = {l["number"]: l for l in extract_links(t7, "K261496")}
    assert set(L) == {"K230042"} and L["K230042"]["confidence"] == "high", L

    # --- fetch (no network) + transform end to end ------------------------------
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        dev = d / "devices_fda_with_pma.ttl"
        dev.write_text(
            '<x:a>\n    ex:documentNumber "K192386" ;\n    ex:summaryUrl "https://f/pdf19/K192386.pdf"^^xsd:anyURI .\n\n'
            '<x:b>\n    ex:documentNumber "K000404" ;\n    ex:summaryUrl "https://f/pdf/K000404.pdf"^^xsd:anyURI .\n\n'
            '<x:c>\n    ex:documentNumber "P050001" ;\n    ex:summaryUrl "https://f/pdf5/P050001B.pdf"^^xsd:anyURI .\n',
            encoding="utf-8")
        assert summary_urls(dev) == {"K192386": "https://f/pdf19/K192386.pdf", "K000404": "https://f/pdf/K000404.pdf"}

        def getter(url):
            if "K000404" in url:
                raise urllib.error.HTTPError(url, 404, "nf", {}, None)
            return 200, b"%PDF-1.4 fake"
        dl = d / "dl"
        fetch(dl, devices_ttl=dev, sleep=lambda s: None, getter=getter, min_free_gb=0)
        assert pdf_path(dl, "K192386").read_bytes().startswith(b"%PDF")
        logrows = _read_log(dl)
        assert logrows["K000404"]["status"] == "404" and logrows["K192386"]["status"] == "200"
        calls = []
        fetch(dl, devices_ttl=dev, sleep=lambda s: None, getter=lambda u: calls.append(u) or (200, b"%PDF"),
              min_free_gb=0)
        assert calls == []                                        # resumable: nothing asked twice
        # transform with a cached text (pdftotext is not needed for the fake PDF)
        tdir = dl / "text" / "K19"
        tdir.mkdir(parents=True)
        (tdir / "K192386.txt").write_text(t1 + ("x" * 200), encoding="utf-8")
        os.utime(tdir / "K192386.txt", (time.time() + 5, time.time() + 5))
        out_dir = d / "out"
        out_dir.mkdir()
        (out_dir / "k510.ttl").write_text(f"<{iri.k510('K192386')}>\n    a ex:K510Record .\n"
                                          f"<{iri.k510('K162759')}>\n    a ex:K510Record .\n", encoding="utf-8")
        # two image-only PDFs (cached text layer of a few characters); only K000505 has OCR text
        for n in ("K000505", "K000606"):
            p = pdf_path(dl, n)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF-1.4 scanned")
            tt = dl / "text" / n[:3] / f"{n}.txt"
            tt.parent.mkdir(parents=True, exist_ok=True)
            tt.write_text("\f 510(k) \f", encoding="utf-8")
            os.utime(tt, (time.time() + 5, time.time() + 5))
        ocr1, ocr2 = d / "ocr-a", d / "ocr-b"       # first-batch layout and ocr_batch.sh layout
        (ocr1 / "out" / "txt" / "k510").mkdir(parents=True)
        (ocr1 / "out" / "txt" / "k510" / "K000505.txt").write_text("too short", encoding="utf-8")
        (ocr2 / "out" / "txt" / "k510" / "pdf" / "K00").mkdir(parents=True)
        (ocr2 / "out" / "txt" / "k510" / "pdf" / "K00" / "K000505.txt").write_text(
            "[OCR skipped on page(s) 3]\n510(k) SUMMARY\nPredicate Device: Acme Pump K011111\n"
            "Device Description: an infusion pump " + "z" * 150 + "\nThe device is substantially equivalent to "
            "the Beta Pump (K022222).\n", encoding="utf-8")
        assert resolve_ocr_dirs(f"{d}/ocr-*") == (str(ocr1), str(ocr2))
        assert resolve_ocr_dirs([str(d / "missing")]) == ()
        tx, er, me = text_of(pdf_path(dl, "K000505"), dl / "text", ())
        assert me == METHOD and _chars(tx) < MIN_TEXT_CHARS and er is None
        tx, er, me = text_of(pdf_path(dl, "K000505"), dl / "text", (str(ocr1),))     # OCR text too short
        assert me == METHOD and _chars(tx) < MIN_TEXT_CHARS
        tx, er, me = text_of(pdf_path(dl, "K000505"), dl / "text", (str(ocr1), str(ocr2)))   # later folder wins
        assert me == METHOD_OCR and "K011111" in tx and "OCR skipped" not in tx, tx
        tx, er, me = text_of(pdf_path(dl, "K192386"), dl / "text", (str(ocr1), str(ocr2)))   # text layer kept
        assert me == METHOD and "K162759" in tx
        out = out_dir / "k510_predicates.ttl"
        st = transform(dl, out, workers=1, ocr_dirs=())
        ttl = out.read_text(encoding="utf-8")
        assert st.records == 3, st
        assert len((out_dir / "k510_predicates.ocr_backlog.tsv").read_text().splitlines()) == 3   # header + 2
        assert "ocrmypdf" not in ttl
        assert f"<{iri.k510('K192386')}>\n    ex:predicateDevice <{iri.k510('K162759')}>" in ttl
        assert f"ex:referenceDevice <{iri.k510('K123456')}>" in ttl
        assert f"<{iri.k510_device_link('K192386', 'predicate', 'K162759')}>" in ttl
        assert 'ex:confidence "high"' in ttl and f'ex:extractionMethod "{METHOD}"' in ttl
        assert 'ex:sourceUrl "https://f/pdf19/K192386.pdf"' in ttl and "ex:retrievedAt" in ttl
        # K162759 is defined elsewhere: no label here; K123456 and K999999 get a fallback node
        assert f"<{iri.k510('K162759')}>\n    a " not in ttl
        assert f"<{iri.k510('K123456')}>\n    a ex:K510Record ;\n    rdfs:label \"510(k) K123456 (named" in ttl
        assert ttl.count("rdfs:label") == 3 + 2, ttl.count("rdfs:label")
        c = json.loads((out_dir / "k510_predicates.counts.json").read_text())
        assert c["links"] == 3 and c["summaries_with_links"] == 1 and c["image_only_ocr_backlog"] == 2
        # --- OCR fallback: same PDFs with the OCR folders (via $MDKG_OCR_DIR) ------------
        old_env = os.environ.get(OCR_ENV)
        os.environ[OCR_ENV] = f"{d}/ocr-*"
        try:
            out2 = out_dir / "k510_predicates_ocr.ttl"
            st2 = transform(dl, out2, workers=1, known_dir=out_dir)
        finally:
            if old_env is None:
                os.environ.pop(OCR_ENV, None)
            else:
                os.environ[OCR_ENV] = old_env
        ttl2 = out2.read_text(encoding="utf-8")
        assert st2.records == 5, st2
        lk = iri.k510_device_link("K000505", "predicate", "K011111")
        blk = ttl2[ttl2.index(f"<{lk}>"):].split("\n\n")[0]
        assert f'ex:extractionMethod "{METHOD_OCR}"' in blk and 'ex:confidence "medium"' in blk, blk   # high -> medium
        blk = ttl2[ttl2.index(f"<{iri.k510_device_link('K000505', 'predicate', 'K022222')}>"):].split("\n\n")[0]
        assert 'ex:confidence "low"' in blk and "(low confidence)" in blk, blk                          # medium -> low
        blk = ttl2[ttl2.index(f"<{iri.k510_device_link('K192386', 'predicate', 'K162759')}>"):].split("\n\n")[0]
        assert f'ex:extractionMethod "{METHOD}"' in blk and 'ex:confidence "high"' in blk, blk          # unchanged
        c2 = json.loads((out_dir / "k510_predicates_ocr.counts.json").read_text())
        assert c2["text_pdfs_from_ocr"] == 1 and c2["image_only_ocr_backlog"] == 1, c2
        assert c2["links_by_method"] == {METHOD: 3, METHOD_OCR: 2}, c2
        bl = (out_dir / "k510_predicates_ocr.ocr_backlog.tsv").read_text().splitlines()
        assert len(bl) == 2 and bl[1].startswith("K000606"), bl                     # still without usable text
        rows = (out_dir / "k510_predicates_ocr.links.tsv").read_text().splitlines()
        assert rows[0].endswith("\textraction_method") and sum(r.endswith(METHOD_OCR) for r in rows) == 2
    print(f"k510_predicates.py self-test PASSED ({st.records} links, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
