#!/usr/bin/env python3
"""
scrub_narratives.py -- apply pipeline/scrub.py to an already-built event_text.ttl
without re-transforming MAUDE (a full rebuild needs ~75 GB of scratch space).

    python3 scripts/scrub_narratives.py IN.ttl OUT.ttl[.gz] [--report report.md]

Streams line by line. Each narrative literal (eventDescription, manufacturerNarrative,
mdrText) is unescaped, scrubbed, and re-escaped with common.lit, so the output is the
same Turtle the pipeline now writes directly. Every other line is copied unchanged.
The report lists counts per rule and before/after examples with digits masked.
"""
import collections
import gzip
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "pipeline"
sys.path[:0] = [str(ROOT), str(ROOT / "sources")]
import common   # noqa: E402
import scrub    # noqa: E402

LINE = re.compile(r'^(\s+ex:(?:eventDescription|manufacturerNarrative|mdrText)\s+)"(.*)"(\s*[;.]\s*)$', re.S)
_UNESC = re.compile(r'\\(["\\nrt])')
_MAP = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}


def unescape(s):
    return _UNESC.sub(lambda m: _MAP[m.group(1)], s)


# deliberately LOOSE detectors run on the scrubbed text: they over-match (citations,
# report numbers) so a human can review what is left; they are not removal rules
RESIDUAL = {"dob": re.compile(r"\b(DOB|DATE OF BIRTH)\b\W{0,6}\d", re.I),
            "phone": re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
            "email": re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)}


def mask(s):
    return re.sub(r"\d", "#", s)


def _scrub_range(args):
    """Worker: scrub bytes [start, end) of src (both on block boundaries) into dst."""
    src, dst, start, end, examples = args
    opener = gzip.open if str(dst).endswith(".gz") else open
    hits, n_lit, n_changed = collections.Counter(), 0, 0
    sample, rnd, resid = [], random.Random(start), []
    with open(src, "rb") as fb, \
            opener(dst, "wt", encoding="utf-8", **({"compresslevel": 3} if opener is gzip.open else {})) as fo:
        fb.seek(start)
        pos = start
        for raw_ln in fb:
            if pos >= end:
                break
            pos += len(raw_ln)
            ln = raw_ln.decode("utf-8")
            m = LINE.match(ln.rstrip("\n"))
            if not m:
                fo.write(ln)
                continue
            n_lit += 1
            raw = unescape(m.group(2))
            clean, h = scrub.scrub_text(raw)
            for k, rx in RESIDUAL.items():
                r = rx.search(clean)
                if r:
                    hits["residual_" + k] += 1
                    if len(resid) < 30:
                        resid.append(f"{k}: " + mask(clean[max(0, r.start() - 60):r.end() + 30]))
            if h:
                n_changed += 1
                hits.update(h)
                if len(sample) < examples:
                    sample.append((raw, clean))
                elif rnd.random() < examples / max(n_changed, 1):
                    sample[rnd.randrange(examples)] = (raw, clean)
                fo.write(f"{m.group(1)}{common.lit(clean)}{m.group(3)}\n")
            else:
                fo.write(ln)
    return n_lit, n_changed, hits, sample, resid


def _boundaries(src, n):
    """Split points at the start of a line that follows a blank line (block start)."""
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


def run(src, dst, report=None, examples=25, workers=1):
    from concurrent.futures import ProcessPoolExecutor
    cuts = _boundaries(src, max(1, workers))
    gz = str(dst).endswith(".gz")
    parts = [f"{dst}.part{i:02d}" + (".gz" if gz else "") for i in range(len(cuts) - 1)]
    jobs = [(src, p, a, b, examples) for p, a, b in zip(parts, cuts, cuts[1:])]
    if len(jobs) == 1:
        results = [_scrub_range(jobs[0])]
    else:
        with ProcessPoolExecutor(len(jobs)) as ex:
            results = list(ex.map(_scrub_range, jobs))
    with open(dst, "wb") as out:                 # gzip members / text parts concatenate validly
        for p in parts:
            with open(p, "rb") as fp:
                while True:
                    buf = fp.read(1 << 24)
                    if not buf:
                        break
                    out.write(buf)
            Path(p).unlink()                      # our own scratch part
    hits, n_lit, n_changed, sample, resid = collections.Counter(), 0, 0, [], []
    for nl, nc, h, s, r in results:
        n_lit += nl
        n_changed += nc
        hits.update(h)
        sample += s[: max(1, examples // len(results))]
        resid += r[:12]
    removals = {k: v for k, v in hits.items() if not k.startswith("residual_")}
    residual = {k[9:]: v for k, v in hits.items() if k.startswith("residual_")}
    lines = [f"# Narrative scrub report", "", f"- narrative literals: {n_lit:,}",
             f"- literals changed: {n_changed:,} ({100 * n_changed / max(n_lit, 1):.3f}%)",
             "- removals by rule: " + ", ".join(f"{k} {v:,}" for k, v in sorted(removals.items(), key=lambda x: -x[1])),
             "- residual (loose detectors on the scrubbed text, for review): "
             + (", ".join(f"{k} {v:,}" for k, v in residual.items()) or "none"), "",
             "## Residual examples (digits masked)", ""] + [f"- {x}" for x in resid] + [
             "", "## Examples (digits masked, trimmed to 300 characters)", ""]
    for raw, clean in sample:
        lines += [f"- before: {mask(raw)[:300]}", f"  after:  {mask(clean)[:300]}", ""]
    text = "\n".join(lines) + "\n"
    if report:
        Path(report).write_text(text, encoding="utf-8")
    print(text[:1500])
    return n_lit, n_changed, hits


if __name__ == "__main__":
    a = sys.argv[1:]
    opts = {}
    for flag in ("--report", "--workers"):
        if flag in a:
            i = a.index(flag)
            opts[flag] = a[i + 1]
            del a[i:i + 2]
    run(a[0], a[1], opts.get("--report"), workers=int(opts.get("--workers", 1)))
