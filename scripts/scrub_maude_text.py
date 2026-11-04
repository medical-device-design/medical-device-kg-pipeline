#!/usr/bin/env python3
"""
scrub_maude_text.py -- scrubbed MAUDE narrative file for a release, plus independent leak checks.

    python3 scripts/scrub_maude_text.py scrub IN.ttl OUT.ttl [--every-gb 1] [--workers N]
    python3 scripts/scrub_maude_text.py sample IN.ttl OUT.ttl [--dict /usr/share/dict/american-english]
    python3 scripts/scrub_maude_text.py leakcheck FILE.ttl [--examples 5] [--counts-only] [--workers N]
    python3 scripts/scrub_maude_text.py triage FILE.ttl [--workers N] [--max-lines 2000]

IN is an event_text.ttl written by an older event.py (narratives not yet scrubbed). It is
streamed line by line; every narrative literal (ex:eventDescription, ex:manufacturerNarrative,
ex:mdrText, and ex:closeoutText / ex:manufacturerResponse of legacy_mdr_text.ttl and
medsun_text.ttl) is unescaped, passed through scrub.scrub_text, and re-escaped with common.lit.
Every other line (subject IRIs, prefixes, blank lines) is copied unchanged, so the output
attaches the narratives to the same report IRIs, with the same predicates and block pattern.
No text is capped or truncated. OUT is written as OUT.part and renamed when complete. With
--workers N the input is split at report-block boundaries, the ranges are scrubbed in
parallel, and the parts are appended to OUT.part in order (each part removed once appended).

The leak check uses its own regular expressions (not the scrub rules): e-mail, US phone
formats, SSN, DOB / date of birth followed by a date, and Dr./Mr./Mrs./Ms. followed by a
capitalized word. It prints counts per category and at most --examples examples. Examples
are structure only: the match is masked (letters -> x, digits -> #) and of the neighboring
words only common words (scrub._STOP_CORE and label words such as LOT / MODEL / PHONE) are
shown; every other word is reduced to its shape (Xxxx, ###). Use --counts-only on unscrubbed
input. "triage" lists every match in a category that is not judged benign, with the same
structure-only context and with features computed from the real text (for example, why
the scrub rules did not take a title + word); the real text is never printed.
"""
import collections
import difflib
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "pipeline"
sys.path[:0] = [str(ROOT), str(ROOT / "sources")]
import common   # noqa: E402
import scrub    # noqa: E402

_PREDS = r"(?:eventDescription|manufacturerNarrative|mdrText|closeoutText|manufacturerResponse)"
LINE = re.compile(r'^(\s+ex:' + _PREDS + r'\s+)"(.*)"(\s*[;.]\s*)$', re.S)
NARR_PRED = re.compile(r"^\s+ex:" + _PREDS + r"\b")
_UNESC = re.compile(r'\\(["\\nrt])')
_MAP = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
_ODD_ESC = re.compile(r'\\[^"\\nrt]')
DICT = "/usr/share/dict/american-english"


def unescape(s):
    return _UNESC.sub(lambda m: _MAP[m.group(1)], s)


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + msg, flush=True)


def mask(s):
    return re.sub(r"\d", "#", re.sub(r"[A-Za-z]", "x", s))


# ---------------------------------------------------------------------------------------------
#  structure-only context: common words shown, every other word reduced to its shape
# ---------------------------------------------------------------------------------------------
_LABEL_WORDS = set("""
LOT REF CAT CATALOG PART P/N PN MODEL SN S/N SERIAL UDI DI NUMBER NUMBERS NO NOS # CODE ITEM PRODUCT REPORT
MDR COMPLAINT FILE BATCH ORDER CASE PHONE TELEPHONE TEL CELL FAX CALL CALLED DOB D.O.B. DATE BIRTH BORN BIRTHDATE
OF IS WAS ON IN AT AND OR THE A AN TO FOR BY WITH PATIENT PT MFR MANUFACTURER DEVICE DR DR. MR MR. MRS MRS. MS MS.
SSN SOCIAL SECURITY ID REFERENCE K510 PMA IDE CONTROL TRACKING ACCESSION
""".split())
_MARKER = re.compile(r"^\W*\[[A-Z ]+ (?:REMOVED|OVER 89)\]\W*$|^\W*\((?:B|b)\)\(\d\)\W*$")


def _safe_words():
    return set(getattr(scrub, "_STOP_CORE", ())) | _LABEL_WORDS


_SAFE = _safe_words()


def shape_word(w):
    core = re.sub(r"[^A-Za-z/#.']", "", w).upper().strip(".'")
    if _MARKER.match(w) or (core and not re.search(r"\d", w) and (core in _SAFE or core + "." in _SAFE)):
        return w
    return re.sub(r"\d", "#", re.sub(r"[a-z]", "x", re.sub(r"[A-Z]", "X", w)))


def masked_context(text, start, end, before=8, after=6):
    """Structure-only window around text[start:end]; markers like [NAME REMOVED] stay readable."""
    left = re.findall(r"\[[A-Z ]+(?:REMOVED|OVER 89)\]|\S+", text[max(0, start - 200):start])
    right = re.findall(r"\[[A-Z ]+(?:REMOVED|OVER 89)\]|\S+", text[end:end + 200])
    if start - 200 > 0 and left:
        left = left[1:]                                       # first token may be cut
    if end + 200 < len(text) and right:
        right = right[:-1]
    glue_l = "" if start > 0 and not text[start - 1].isspace() else " "
    glue_r = "" if end < len(text) and not text[end].isspace() else " "
    return (" ".join(shape_word(w) for w in left[-before:]) + glue_l + "<<" + mask(text[start:end]) + ">>"
            + glue_r + " ".join(shape_word(w) for w in right[:after])).strip()


# ---------------------------------------------------------------------------------------------
#  splitting a file at report-block boundaries (a line that follows a blank line)
# ---------------------------------------------------------------------------------------------
def _boundaries(src, n):
    size = Path(src).stat().st_size
    cuts = [0]
    with open(src, "rb") as fb:
        for k in range(1, n):
            fb.seek(size * k // n)
            fb.readline()
            while True:
                ln = fb.readline()
                if not ln or ln.strip() == b"":
                    break
            cuts.append(fb.tell())
    cuts.append(size)
    return sorted(set(cuts))


def _lines(src, start, end):
    with open(src, "rb") as fb:
        fb.seek(start)
        pos = start
        for raw_ln in fb:
            if pos >= end:
                break
            pos += len(raw_ln)
            yield pos, raw_ln


def _pmap(fn, jobs):
    if len(jobs) == 1:
        return [fn(jobs[0])]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(len(jobs)) as ex:
        return list(ex.map(fn, jobs))


# ---------------------------------------------------------------------------------------------
def _scrub_range(args):
    src, part, start, end, every_gb, wid, sample_hook = args
    hits, n = collections.Counter(), collections.Counter()
    t0, next_mark, span = time.time(), every_gb * 1e9, max(end - start, 1)
    with open(part, "w", encoding="utf-8", newline="\n") as fo:
        for pos, raw_ln in _lines(src, start, end):
            ln = raw_ln.decode("utf-8")
            m = LINE.match(ln.rstrip("\n"))
            if not m:
                if NARR_PRED.match(ln):
                    n["narrative_lines_unparsed"] += 1         # must stay 0 (checked below)
                elif ln.startswith("<"):
                    n["subjects"] += 1
                fo.write(ln)
            else:
                n["narratives"] += 1
                if _ODD_ESC.search(m.group(2).replace("\\\\", "")):
                    n["odd_escapes"] += 1
                raw = unescape(m.group(2))
                clean, h = scrub.scrub_text(raw)
                if h:
                    n["changed"] += 1
                    hits.update(h)
                    fo.write(f"{m.group(1)}{common.lit(clean)}{m.group(3)}\n")
                    if sample_hook:
                        sample_hook(raw, clean, h)
                else:
                    fo.write(ln)
                n["chars_in"] += len(raw)
            if pos - start >= next_mark:
                el = time.time() - t0
                rate = (pos - start) / el
                log(f"worker {wid}: {(pos - start) / 1e9:,.1f} / {span / 1e9:,.1f} GB ({100 * (pos - start) / span:.1f}%), "
                    f"{n['narratives']:,} narratives, {n['changed']:,} changed, {rate / 1e6:.1f} MB/s, "
                    f"ETA {time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() + (end - pos) / rate))}")
                next_mark += every_gb * 1e9
    return n, hits


def scrub_file(src, dst, every_gb=1.0, sample_hook=None, workers=1):
    size = os.path.getsize(src)
    part = Path(str(dst) + ".part")
    t0 = time.time()
    cuts = _boundaries(src, max(1, workers)) if not sample_hook else [0, size]
    if len(cuts) == 2:
        results = [_scrub_range((src, part, 0, size, every_gb, 0, sample_hook))]
    else:
        parts = [Path(f"{part}{i:02d}") for i in range(len(cuts) - 1)]
        log(f"scrub with {len(parts)} workers over {size:,} bytes")
        results = _pmap(_scrub_range, [(src, p, a, b, every_gb, i, None)
                                       for i, (p, a, b) in enumerate(zip(parts, cuts, cuts[1:]))])
        with open(part, "wb") as out:
            for p in parts:                                   # append in order, drop each part once copied
                with open(p, "rb") as fp:
                    while True:
                        buf = fp.read(1 << 24)
                        if not buf:
                            break
                        out.write(buf)
                p.unlink()
    n, hits = collections.Counter(), collections.Counter()
    for a, b in results:
        n.update(a)
        hits.update(b)
    if n["narrative_lines_unparsed"]:
        raise SystemExit(f"{n['narrative_lines_unparsed']} narrative lines did not parse; {part} left for review")
    part.replace(dst)
    el = time.time() - t0
    log(f"scrub done in {el / 3600:.2f} h: {n['subjects']:,} report blocks, {n['narratives']:,} narratives, "
        f"{n['changed']:,} changed ({100 * n['changed'] / max(n['narratives'], 1):.3f}%), "
        f"odd escapes {n['odd_escapes']}, unparsed narrative lines {n['narrative_lines_unparsed']}")
    log("replacements by rule: " + ", ".join(f"{k} {v:,}" for k, v in hits.most_common()))
    log(f"output {dst} ({os.path.getsize(dst):,} bytes; input {size:,} bytes)")
    return n, hits


# ---------------------------------------------------------------------------------------------
_TITLE_DOT = re.compile(r"\b(DR|MR|MRS|MS)\.\s+([A-Z][A-Za-z'-]*)", re.I)
LEAKS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "phone_us": re.compile(r"(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s*|(?<![\d-])\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])"),
    "ssn": re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
    "dob_date": re.compile(
        r"\b(?:D\.?O\.?B\.?|DATE\s+OF\s+BIRTH|BIRTH\s*DATE)\W{0,8}(?:IS\s+|WAS\s+)?"
        r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|(?:19|20)\d{2}\b|\d{1,2}\s*[A-Z]{3,9}\.?\s*\d{2,4}|"
        r"[A-Z]{3,9}\.?\s+\d{1,2},?\s+\d{4})", re.I),
    "title_capitalized": _TITLE_DOT,
}
_DEVICE_CTX = re.compile(r"\b(LOT|REF|CAT|CATALOG|PART|P/N|PN|MODEL|SN|S/N|SERIAL|UDI|DI|NUMBER|NO|#|K\d*|"
                         r"CODE|ITEM|PRODUCT|REPORT|MDR|COMPLAINT|FILE|BATCH|ORDER|CASE)\W{0,6}$", re.I)
BENIGN = ("common_word", "device_or_report_number_context", "milliseconds_unit", "part_number")


def _dictionary(path):
    try:
        return {w.strip().upper() for w in open(path, encoding="utf-8", errors="ignore") if w[:1].islower()}
    except OSError:
        return set()


class Leaks:
    def __init__(self, examples=5, words=None):
        self.counts = collections.Counter()
        self.examples, self.max_ex = [], examples
        self.words = words or set()

    def classify(self, text):
        for k, rx in LEAKS.items():
            for m in rx.finditer(text):
                cat = k
                before = text[max(0, m.start() - 24):m.start()]
                if k in ("phone_us", "ssn"):
                    if re.search(r"[A-Za-z]$", before):
                        cat = k + ":letter_prefixed_part_number"     # "(D123-45-6789)"
                    elif re.search(r"\b(SSN|SOCIAL\s+SECURITY)\W{0,8}$", before, re.I):
                        cat = k + ":labelled_ssn"
                    elif re.search(r"\b(PHONE|TEL|CELL|FAX|CALL)\w*\W{0,12}$", before, re.I):
                        cat = k + ":phone_label"
                    elif _DEVICE_CTX.search(before):
                        cat = k + ":device_or_report_number_context"
                    else:
                        cat = k + ":unlabelled"
                elif k == "title_capitalized":
                    w = m.group(2).upper().strip("'-")
                    if m.group(1).upper() == "MS" and re.search(r"\d\s*$", before):
                        cat = k + ":milliseconds_unit"               # "2.0 MS. LV ..."
                    else:
                        cat = k + (":common_word" if w in self.words or w in scrub._STOP else ":other")
                yield cat, m

    def check(self, text):
        for cat, m in self.classify(text):
            self.counts[cat] += 1
            if len(self.examples) < self.max_ex and not cat.endswith(BENIGN):
                self.examples.append(f"{cat}: " + masked_context(text, m.start(), m.end()))

    def report(self, label):
        log(f"leak check {label}: " + (", ".join(f"{k} {v:,}" for k, v in sorted(self.counts.items())) or "none found"))
        nb = sum(v for k, v in self.counts.items() if not k.endswith(BENIGN))
        log(f"leak check {label}: {nb:,} matches in categories not judged benign")
        for e in self.examples:
            log("  example (structure only): " + e)


def _leak_range(args):
    path, start, end, examples, dict_path, wid = args
    lk = Leaks(examples, _dictionary(dict_path) if dict_path else set())
    n = 0
    for _, raw_ln in _lines(path, start, end):
        m = LINE.match(raw_ln.decode("utf-8").rstrip("\n"))
        if m:
            n += 1
            lk.check(unescape(m.group(2)))
            if n % 5_000_000 == 0:
                log(f"leak check worker {wid}: {n:,} narratives")
    return n, lk.counts, lk.examples


def leakcheck(path, examples=5, counts_only=False, dict_path=DICT, workers=1):
    t0 = time.time()
    cuts = _boundaries(path, max(1, workers))
    res = _pmap(_leak_range, [(path, a, b, 0 if counts_only else examples, dict_path, i)
                              for i, (a, b) in enumerate(zip(cuts, cuts[1:]))])
    lk = Leaks(examples)
    n = 0
    for k, c, ex in res:
        n += k
        lk.counts.update(c)
        lk.examples += ex
    lk.examples = lk.examples[:examples if not counts_only else 0]
    log(f"leak check over {n:,} narratives in {path} ({(time.time() - t0) / 60:.1f} min)")
    lk.report(Path(path).name)
    return lk


# ---------------------------------------------------------------------------------------------
#  triage: every non-benign match, structure-only, with features computed from the real text
# ---------------------------------------------------------------------------------------------
def _near_dict(w, words):
    """True when w is one edit (delete, insert, replace, swap) away from a dictionary word."""
    abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i in range(len(w) + 1):
        if i < len(w) and w[:i] + w[i + 1:] in words:
            return True
        if i < len(w) - 1 and w[:i] + w[i + 1] + w[i] + w[i + 2:] in words:
            return True
        for c in abc:
            if w[:i] + c + w[i:] in words or (i < len(w) and w[:i] + c + w[i + 1:] in words):
                return True
    return False


_PREFIXES = re.compile(r"^(?:RE-?|UN-?|PRE-?|NON-?|DE-?|DIS|MIS|OVER|UNDER|CO-|SELF-|POST-?|SUB|INTER|AUTO-?)")


def _title_features(text, m, words):
    w = m.group(2).upper().strip("'-")
    f = [f"len{len(w)}"]
    s = m.start()
    if s > 0 and re.match(r"[\w'’/-]", text[s - 1]):
        f.append("char_before_title")
    nxt = text[m.end():m.end() + 1]
    if nxt and not nxt.isspace():
        f.append("token_followed_by_" + ("paren" if nxt == "(" else "digit" if nxt.isdigit() else "punct"))
    if m.group(2)[:1].islower():
        f.append("lowercase_word")
    if len(w) >= 6 and w.endswith(("ED", "ING", "LY")):
        f.append("ed_ing_ly")
    names_like = getattr(scrub, "_NAMES_LIKE_VERBS", set())
    if w in names_like:
        f.append("listed_name")
    pm = _PREFIXES.match(w)
    if pm and len(w) - pm.end() >= 4 and w[pm.end():] in words | scrub._STOP:
        f.append("prefix+dictionary_word")
    elif _near_dict(w, words):
        f.append("one_edit_from_dictionary_word")
    else:
        f.append("not_near_dictionary")
    after = text[m.end():m.end() + 40].split()
    if after and after[0].upper().strip(".,") in ("THE", "A", "AN", "HIS", "HER", "IT", "THEM", "THAT", "TO", "IN", "ON", "OFF", "UP", "OUT"):
        f.append("verb_like_next")
    if "[NAME REMOVED]" in text[m.end():m.end() + 30]:
        f.append("name_marker_after")
    sub = text[max(0, s - 6):m.end() + 40]
    got, _ = scrub.scrub_text(sub)
    f.append("scrub_now_removes" if "[NAME REMOVED]" in got and "[NAME REMOVED]" not in sub else "scrub_now_keeps")
    return f, (w if "prefix+dictionary_word" in f else None)


def _number_features(text, m):
    f = []
    s = m.group(0)
    seps = re.sub(r"[\d()+ ]", "", s)
    f.append("seps:" + (seps or "space"))
    if re.search(r"\d[\d\s,;/&-]*$", text[max(0, m.start() - 6):m.start()]) or \
            re.search(r"\d\s*(?:,|;|/|&|AND|OR|TO|THROUGH|THRU)\s*$", text[max(0, m.start() - 12):m.start()], re.I):
        f.append("follows_another_number")
    if _DEVICE_CTX.search(text[max(0, m.start() - 80):m.start()]):
        f.append("device_word_within_80")
    if re.search(r"(?:19|20)\d{2}\s*$", text[max(0, m.start() - 8):m.start()]) or s[:2] in ("19", "20"):
        f.append("year_like")
    if re.search(r"\b(VOL|PP|PAGES?|J|JOURNAL|DOI|PMID|ET\s+AL)\b", text[max(0, m.start() - 60):m.end() + 30], re.I):
        f.append("citation_words_near")
    if re.search(r"\b(PHONE|TEL|CALL|FAX|CELL|CONTACT|REACH)", text[max(0, m.start() - 60):m.start()], re.I):
        f.append("phone_word_within_60")
    return f


def _triage_range(args):
    path, start, end, dict_path, wid = args
    words = _dictionary(dict_path) if dict_path else set()
    lk = Leaks(0, words)
    rows, feats, verbs = [], collections.Counter(), collections.Counter()
    for _, raw_ln in _lines(path, start, end):
        m = LINE.match(raw_ln.decode("utf-8").rstrip("\n"))
        if not m:
            continue
        text = unescape(m.group(2))
        for cat, mm in lk.classify(text):
            if cat.endswith(BENIGN):
                continue
            if cat.startswith("title"):
                f, v = _title_features(text, mm, words)
                if v:
                    verbs[v] += 1
            elif cat.startswith(("phone", "ssn")):
                f = _number_features(text, mm)
            else:
                f = []
            for x in f:
                feats[(cat, x)] += 1
            rows.append(f"{cat} | {masked_context(text, mm.start(), mm.end())} | {' '.join(f)}")
    return rows, feats, verbs


def triage(path, workers=1, dict_path=DICT, max_lines=2000):
    t0 = time.time()
    cuts = _boundaries(path, max(1, workers))
    res = _pmap(_triage_range, [(path, a, b, dict_path, i) for i, (a, b) in enumerate(zip(cuts, cuts[1:]))])
    rows, feats, verbs = [], collections.Counter(), collections.Counter()
    for r, f, v in res:
        rows += r
        feats.update(f)
        verbs.update(v)
    log(f"triage of {path}: {len(rows):,} matches in categories not judged benign ({(time.time() - t0) / 60:.1f} min)")
    for (cat, x), v in sorted(feats.items()):
        log(f"  feature {cat} {x}: {v:,}")
    log("  prefix + dictionary-word tokens after a title (English words, not names): "
        + ", ".join(f"{k} {v}" for k, v in verbs.most_common()))
    for r in sorted(rows)[:max_lines]:
        print("ROW " + r)
    return rows


# ---------------------------------------------------------------------------------------------
_MARK = re.compile(r"\[(?:NAME|ADDRESS|INITIALS) REMOVED\]|\[AGE OVER 89\]")


def sample(src, dst, dict_path=None):
    """Scrub a sample and report what the name / address / initials / age rules removed: removed
    words that are lowercase dictionary words (possible over-removal) are listed; everything else is
    counted only. At most 5 masked examples."""
    words = _dictionary(dict_path) if dict_path else set()
    removed_dict, removed_other, ex = collections.Counter(), collections.Counter(), []

    def hook(raw, clean, h):
        a, b = raw.split(), clean.split()
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if op == "equal":
                continue
            new = " ".join(b[j1:j2])
            mk = _MARK.search(new)
            if not mk:
                continue
            kind = mk.group(0)
            for w in a[i1:i2]:
                core = re.sub(r"[^A-Za-z']", "", w).upper().strip("'")
                if not core or core in new.upper():
                    continue
                if core in words:
                    removed_dict[(kind, core)] += 1
                else:
                    removed_other[kind] += 1
            if len(ex) < 5 and kind != "[AGE OVER 89]":
                ex.append(f"{kind}: removed {mask(' '.join(a[i1:i2]))!r} -> {new!r}")

    n, hits = scrub_file(src, dst, every_gb=1e9, sample_hook=hook)
    log("removed words that are dictionary words (review for over-removal; common surnames are also words): "
        + ", ".join(f"{k[0][1:5]}:{k[1]} {v}" for k, v in removed_dict.most_common(120)))
    log("removed non-dictionary tokens by marker (names, counts only): " + str(dict(removed_other)))
    for e in ex:
        log("  removal example (masked): " + e)
    return n, hits


if __name__ == "__main__":
    a = sys.argv[1:]
    opts = {}
    for flag in ("--every-gb", "--dict", "--examples", "--workers", "--max-lines"):
        if flag in a:
            i = a.index(flag)
            opts[flag] = a[i + 1]
            del a[i:i + 2]
    counts_only = "--counts-only" in a
    a = [x for x in a if x != "--counts-only"]
    nw = int(opts.get("--workers", 1))
    dct = opts.get("--dict", DICT)
    if not a:
        raise SystemExit(__doc__)
    if a[0] == "scrub":
        scrub_file(a[1], a[2], float(opts.get("--every-gb", 1)), workers=nw)
    elif a[0] == "sample":
        sample(a[1], a[2], dct)
    elif a[0] == "leakcheck":
        leakcheck(a[1], int(opts.get("--examples", 5)), counts_only, dct, nw)
    elif a[0] == "triage":
        triage(a[1], nw, dct, int(opts.get("--max-lines", 2000)))
    else:
        raise SystemExit(__doc__)
