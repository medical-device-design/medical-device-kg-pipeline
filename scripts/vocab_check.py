#!/usr/bin/env python3
"""
vocab_check.py -- ontology/data agreement gate (rebuild-strategy Stage 3, closes D2/D3).

Two checks:
  A. every ex: term a converter can EMIT is DECLARED in ontology/ontology.ttl
     (static scan of pipeline/**/*.py for "ex:Term" tokens)
  B. optionally, every class DECLARED is POPULATED in a built output directory:
        python scripts/vocab_check.py --out out/

Exit 1 if check A finds undeclared terms (or B finds empty classes with --strict).
"""
import re
import sys
import argparse
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NS = "https://bmedesign.org/medical-device-kg/ns/"
TOKEN = re.compile(r"""["'(]ex:([A-Za-z][A-Za-z0-9]*)""")


def declared():
    import rdflib
    g = rdflib.Graph()
    for f in sorted((REPO / "ontology").glob("*.ttl")):
        if "CURRENT_from_lakefs" in f.name:       # legacy snapshot, reference only
            continue
        g.parse(f, format="turtle")
    terms = {str(s)[len(NS):] for s in g.subjects() if str(s).startswith(NS)}
    classes = {str(s)[len(NS):] for s in g.subjects(rdflib.RDF.type, rdflib.OWL.Class) if str(s).startswith(NS)}
    return terms, classes


def emitted():
    used = {}
    for p in sorted((REPO / "pipeline").rglob("*.py")):
        if p.name.startswith("_"):
            continue
        code = p.read_text(encoding="utf-8").split("def _selftest", 1)[0]   # test fixtures don't count
        for m in TOKEN.finditer(code):
            used.setdefault(m.group(1), set()).add(p.relative_to(REPO).as_posix())
    return used


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="built TTL dir to check class population")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args(argv)
    terms, classes = declared()
    used = emitted()
    missing = {t: f for t, f in used.items() if t not in terms}
    print(f"declared terms: {len(terms)}  (classes {len(classes)});  emitted by code: {len(used)}")
    if missing:
        print(f"\nUNDECLARED ({len(missing)}): emitted but not in ontology.ttl")
        for t in sorted(missing):
            print(f"  ex:{t:<32} {', '.join(sorted(missing[t]))}")
    else:
        print("A. every emitted term is declared.  OK")
    bad = bool(missing)
    if a.out:
        # Stream the TTL text instead of loading it into rdflib: the pipeline writer
        # always emits types as "    a ex:Class ;" and a 5.6M-triple file does not
        # fit in rdflib on a 3.8 GB VM.
        type_line = re.compile(r"^\s+a ex:([A-Za-z][A-Za-z0-9]*)\s*[;.]")
        seen = set()
        for ttl in Path(a.out).glob("*.ttl"):
            with open(ttl, encoding="utf-8") as fh:
                for line in fh:
                    m = type_line.match(line)
                    if m:
                        seen.add(m.group(1))
        empty = sorted(classes - seen)
        print(f"\nB. declared classes with no instances in {a.out}: {len(empty)}")
        for c in empty:
            print("  ex:" + c)
        bad = bad or (a.strict and bool(empty))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
