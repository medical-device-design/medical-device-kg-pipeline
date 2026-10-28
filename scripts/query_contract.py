#!/usr/bin/env python3
"""
query_contract.py -- saved-query gate.

The queries in docs/queries/*.rq are published for outside users (the OKN registry
saved queries and the ENGG 143G capstone course). They promise that the ex: names they
use stay stable between releases. This gate fails the build when a query uses an ex:
term that is
  A. not declared in ontology/*.ttl, or
  B. no longer emitted by any converter in pipeline/ (a rename in code that left the
     old declaration behind).

    python3 scripts/query_contract.py            # check docs/queries/*.rq
    python3 scripts/query_contract.py FILE.rq    # check given files

Syntax is checked separately in CI with Jena qparse.
Exit 1 on any problem.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import vocab_check   # noqa: E402  (declared(), emitted())

EX_PREFIX = re.compile(r"PREFIX\s+ex:\s*<([^>]+)>", re.I)
EX_TERM = re.compile(r"(?<![\w:])ex:([A-Za-z][A-Za-z0-9]*)")


def terms_in(text):
    """ex: local names used in a query, ignoring comment lines."""
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    return sorted(set(EX_TERM.findall(body)))


def check(files, declared, emitted):
    problems = []
    for f in files:
        text = Path(f).read_text(encoding="utf-8")
        m = EX_PREFIX.search(text)
        if not m or m.group(1) != vocab_check.NS:
            problems.append(f"{f}: PREFIX ex: must be <{vocab_check.NS}>")
        used = terms_in(text)
        if not used:
            problems.append(f"{f}: no ex: terms found")
        for t in used:
            if t not in declared:
                problems.append(f"{f}: ex:{t} is not declared in ontology/")
            elif t not in emitted:
                problems.append(f"{f}: ex:{t} is declared but no converter emits it")
        print(f"  {Path(f).name}: {len(used)} ex: terms")
    return problems


def _selftest():
    q = ('# ex:Ignored in a comment\nPREFIX ex: <%s>\nSELECT * WHERE { ?s a ex:K510Record ; ex:kNumber ?k . '
         '?s <%sx> ?o . BIND("ex:notATerm" AS ?x) }' % (vocab_check.NS, vocab_check.NS))
    assert terms_in(q) == ["K510Record", "kNumber", "notATerm"], terms_in(q)
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.rq")
        open(p, "w", encoding="utf-8").write(q)
        probs = check([p], {"K510Record", "kNumber", "notATerm"}, {"K510Record", "kNumber"})
        assert probs == [f"{p}: ex:notATerm is declared but no converter emits it"], probs
        open(p, "w", encoding="utf-8").write(q.replace(vocab_check.NS, "http://example.org/"))
        assert any("PREFIX ex:" in x for x in check([p], {"K510Record", "kNumber", "notATerm"}, {"K510Record", "kNumber", "notATerm"}))
    print("query_contract.py self-test PASSED")


def main(argv):
    if argv and argv[0] == "--selftest":
        _selftest()
        return 0
    files = argv or sorted(str(p) for p in (REPO / "docs" / "queries").glob("*.rq"))
    if not files:
        print("no saved queries found")
        return 1
    declared, _classes = vocab_check.declared()
    emitted = set(vocab_check.emitted())
    print(f"saved queries: {len(files)}")
    problems = check(files, declared, emitted)
    if problems:
        print(f"\nQUERY CONTRACT: FAIL ({len(problems)})")
        for p in problems:
            print("  " + p)
        return 1
    print("QUERY CONTRACT: OK -- every ex: term in the saved queries is declared and emitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
