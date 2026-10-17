#!/usr/bin/env python3
"""
product_code_fallback.py -- give every referenced product code a name (v0.0.7, query test V4).

Product-code hub nodes get their rdfs:label from classification.ttl. Records also cite codes
that FDA has retired or never put in the current classification file (KOG, LOS, LDQ, ...),
so those hubs had no name. This writes product_code_fallback.ttl with one rdfs:label per
such code, saying plainly that the code is not in FDA's current classification file.

    python3 scripts/product_code_fallback.py <out_dir> [more dirs or .ttl files ...]

Scans every *.ttl (streaming, any size) for .../id/product-code/<code> references, takes
the set labelled by classification.ttl, and writes the difference.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
sys.path.insert(0, str(HERE.parent / "pipeline" / "sources"))
import iri      # noqa: E402
import common   # noqa: E402
import base     # noqa: E402

REF = re.compile(rb"/id/product-code/([a-z]{3})>")


def referenced(paths):
    codes = set()
    for p in paths:
        with open(p, "rb") as fh:
            for line in fh:
                if b"/id/product-code/" in line:
                    codes.update(m.decode() for m in REF.findall(line))
    return codes


def classified(classification_ttl):
    """Codes whose hub node gets a label from classification.ttl."""
    codes, cur = set(), None
    with open(classification_ttl, "rb") as fh:
        for line in fh:
            m = REF.search(line)
            if line.startswith(b"<") and m:
                cur = m.group(1).decode()
            elif not line.startswith(b" "):
                cur = None
            if cur and line.lstrip().startswith(b"rdfs:label"):
                codes.add(cur)
    return codes


def main(argv):
    targets = [Path(a) for a in argv]
    files = []
    for t in targets:
        files += sorted(t.glob("*.ttl")) if t.is_dir() else [t]
    files = [f for f in files if f.name != "product_code_fallback.ttl" and not f.name.endswith("_text.ttl")]
    cls = next((f for f in files if f.name == "classification.ttl"), None)
    if cls is None:
        raise SystemExit("classification.ttl not among the inputs")
    have = classified(cls)
    refs = referenced(files)
    missing = sorted(refs - have)
    out = Path(targets[0] if targets[0].is_dir() else targets[0].parent) / "product_code_fallback.ttl"
    with base.TurtleWriter(out) as w:
        for c in missing:
            code = c.upper()
            w.block(iri.product_code(code), [
                ("a", "ex:ProductCode"),
                ("rdfs:label", common.lit(f"Product code {code} (not in FDA's current classification file)")),
                ("ex:productCode", common.lit(code)),
                ("ex:dataQualityNote", common.lit("Cited by FDA records but absent from FDA's current product classification file (retired or never listed).")),
            ])
    print(f"{len(refs):,} codes referenced; {len(have):,} named by classification; {len(missing):,} fallback names -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
