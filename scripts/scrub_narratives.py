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


def mask(s):
    return re.sub(r"\d", "#", s)


def run(src, dst, report=None, examples=25):
    opener = gzip.open if str(dst).endswith(".gz") else open
    hits, n_lit, n_changed = collections.Counter(), 0, 0
    sample, rnd = [], random.Random(7)
    with open(src, encoding="utf-8", errors="strict") as fi, \
            opener(dst, "wt", encoding="utf-8", **({"compresslevel": 3} if opener is gzip.open else {})) as fo:
        for ln in fi:
            m = LINE.match(ln.rstrip("\n"))
            if not m:
                fo.write(ln)
                continue
            n_lit += 1
            raw = unescape(m.group(2))
            clean, h = scrub.scrub_text(raw)
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
    lines = [f"# Narrative scrub report", "", f"- narrative literals: {n_lit:,}",
             f"- literals changed: {n_changed:,} ({100 * n_changed / max(n_lit, 1):.3f}%)",
             "- removals by rule: " + ", ".join(f"{k} {v:,}" for k, v in hits.most_common()), "",
             "## Examples (digits masked, trimmed to 300 characters)", ""]
    for raw, clean in sample:
        lines += [f"- before: {mask(raw)[:300]}", f"  after:  {mask(clean)[:300]}", ""]
    text = "\n".join(lines) + "\n"
    if report:
        Path(report).write_text(text, encoding="utf-8")
    print(text[:1500])
    return n_lit, n_changed, hits


if __name__ == "__main__":
    a = sys.argv[1:]
    rep = None
    if "--report" in a:
        i = a.index("--report")
        rep = a[i + 1]
        del a[i:i + 2]
    run(a[0], a[1], rep)
