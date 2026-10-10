#!/usr/bin/env python3
"""
identity.py -- the organization-identity backbone (action plan task A1).

WHY
---
FDA names the same company many ways across datasets ("Medtronic MiniMed, Inc.",
"MEDTRONIC MINIMED INC", "Medtronic Minimed") and keys it differently in each:
FEI in registration/listing and the device/recall database, DUNS in UDI, nothing
at all in an enforcement report's `recalling_firm`. A measured naive name match
(Oct 2 2026 analysis) linked 52% of trial sponsors to 510(k) applicants WITH
visible false positives ("Apple Inc." -> "Apple Diagnostic Products"). So:

  1. Prefer shared identifiers (FEI, DUNS) -> confidence 1.0.
  2. Fall back to normalized names, scored -- never a bare string equality.
  3. NEVER assert owl:sameAs. Every link is a reified ex:IdentityMapping with
     method, confidence, status, date and evidence, so a buyer sets the threshold.

Statuses: confidence >= ACCEPT -> "accepted"; >= CANDIDATE -> "candidate" (human
review); below that nothing is emitted.

Usage (library):
    from identity import Org, match, mappings_to_turtle
    maps = match(left_orgs, right_orgs)
Usage (CLI, two CSVs with columns iri,name,fei,duns):
    python pipeline/identity.py left.csv right.csv --date 2026-10-02 --out out/identity.ttl
Run `python pipeline/identity.py --selftest`.
"""
import os
import re
import sys
import csv
import argparse
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
import iri      # noqa: E402
import common   # noqa: E402

ACCEPT = 0.90
CANDIDATE = 0.60

# Legal-form tokens: removed entirely (they never distinguish two companies).
LEGAL = {
    "INC", "INCORPORATED", "LLC", "L", "C", "LTD", "LIMITED", "CORP", "CORPORATION",
    "CO", "COMPANY", "LP", "LLP", "PLC", "GMBH", "AG", "SA", "SAS", "SARL", "BV",
    "NV", "SPA", "SRL", "KK", "PTY", "AB", "AS", "OY", "ULC", "PC", "PA", "PVT",
    "PRIVATE", "DBA", "THE", "AND", "OF", "DE", "CV", "SL", "KG", "SE",
}
# Generic industry words: kept for the full-name score, but carry no identity on
# their own -- this is what stops "Apple Inc." matching "Apple Diagnostic Products".
GENERIC = {
    "MEDICAL", "MEDICALS", "HEALTH", "HEALTHCARE", "SYSTEM", "SYSTEMS", "PRODUCT",
    "PRODUCTS", "DEVICE", "DEVICES", "DIAGNOSTIC", "DIAGNOSTICS", "TECHNOLOGY",
    "TECHNOLOGIES", "INTERNATIONAL", "USA", "US", "AMERICA", "AMERICAS", "NORTH",
    "GROUP", "HOLDING", "HOLDINGS", "SOLUTION", "SOLUTIONS", "LAB", "LABS",
    "LABORATORY", "LABORATORIES", "SURGICAL", "INDUSTRIES", "INDUSTRY",
    "MANUFACTURING", "SCIENCE", "SCIENCES", "BIO", "PHARMA", "CARE", "GLOBAL",
    "ENTERPRISES", "ENTERPRISE", "INSTRUMENT", "INSTRUMENTS", "SUPPLY", "SUPPLIES",
}
_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_SPACE = re.compile(r"\s+")


def normalize_org(name):
    """Upper-case, strip accents and punctuation, drop legal-form tokens.
    'Medtronic MiniMed, Inc.' -> 'MEDTRONIC MINIMED'. Returns '' if nothing left."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.upper().replace("&", " AND ")
    s = _PUNCT.sub(" ", s)
    toks = [t for t in _SPACE.split(s) if t and t not in LEGAL]
    return " ".join(toks)


def _tokens(norm):
    return set(norm.split()) if norm else set()


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def name_score(a, b):
    """Score two RAW names in [0,1]. Exact normalized match = 0.95 (not 1.0: names
    are evidence, identifiers are proof). Otherwise a blend of full-token and
    informative-token Jaccard, which penalizes extra distinguishing words."""
    na, nb = normalize_org(a), normalize_org(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 0.95
    ta, tb = _tokens(na), _tokens(nb)
    ia, ib = ta - GENERIC, tb - GENERIC
    if not (ia & ib):
        return 0.0                      # no shared distinctive token -> no match
    return round(0.6 * _jaccard(ta, tb) + 0.4 * _jaccard(ia, ib), 4)


@dataclass
class Org:
    iri: str
    name: str = ""
    fei: str = ""
    duns: str = ""


@dataclass
class Mapping:
    subject: str
    object: str
    method: str
    confidence: float
    evidence: str = ""

    @property
    def status(self):
        return "accepted" if self.confidence >= ACCEPT else "candidate"


def _clean_id(v):
    return re.sub(r"\D", "", str(v or "")).lstrip("0")


def match(left, right):
    """Return Mapping objects from each left Org to its best right Org.
    Identifier match wins outright; names are blocked by first distinctive token
    so the comparison stays near-linear on 50k+ organizations."""
    by_fei, by_duns, block = {}, {}, {}
    for o in right:
        if _clean_id(o.fei):
            by_fei.setdefault(_clean_id(o.fei), o)
        if _clean_id(o.duns):
            by_duns.setdefault(_clean_id(o.duns), o)
        for t in _tokens(normalize_org(o.name)) - GENERIC:
            block.setdefault(t, []).append(o)

    out = []
    for l in left:
        hit = None
        if _clean_id(l.fei) and _clean_id(l.fei) in by_fei:
            r = by_fei[_clean_id(l.fei)]
            hit = Mapping(l.iri, r.iri, "shared-fei", 1.0, f"FEI {l.fei}")
        elif _clean_id(l.duns) and _clean_id(l.duns) in by_duns:
            r = by_duns[_clean_id(l.duns)]
            hit = Mapping(l.iri, r.iri, "shared-duns", 1.0, f"DUNS {l.duns}")
        else:
            seen, best = set(), (0.0, None)
            for t in _tokens(normalize_org(l.name)) - GENERIC:
                for r in block.get(t, []):
                    if id(r) in seen or r.iri == l.iri:
                        continue
                    seen.add(id(r))
                    s = name_score(l.name, r.name)
                    if s > best[0]:
                        best = (s, r)
            s, r = best
            if r is not None and s >= CANDIDATE:
                method = "normalized-name-exact" if s >= 0.95 else "name-token-score"
                hit = Mapping(l.iri, r.iri, method, s,
                              f"{normalize_org(l.name)} | {normalize_org(r.name)}")
        if hit:
            out.append(hit)
    return out


def mappings_to_turtle(mappings, writer, mapped_on=None):
    """Write each Mapping as a reified ex:IdentityMapping block via a
    base.TurtleWriter (or anything with .block(subject, pairs))."""
    n = 0
    for m in mappings:
        pairs = [
            ("a", "ex:IdentityMapping"),
            ("ex:mappingSubject", f"<{m.subject}>"),
            ("ex:mappingObject", f"<{m.object}>"),
            ("ex:mappingMethod", common.lit(m.method)),
            ("ex:mappingConfidence", common.float_lit(m.confidence)),
            ("ex:mappingStatus", common.lit(m.status)),
            ("ex:mappingEvidence", common.lit(m.evidence)),
            ("ex:mappingDate", common.date_lit(mapped_on)),
        ]
        writer.block(iri.identity_mapping(m.subject, m.object, m.method), pairs)
        n += 1
    return n


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [Org(iri=r["iri"], name=r.get("name", ""), fei=r.get("fei", ""),
                    duns=r.get("duns", "")) for r in csv.DictReader(f)]


def main(argv=None):
    ap = argparse.ArgumentParser(description="organization identity mappings")
    ap.add_argument("left", nargs="?")
    ap.add_argument("right", nargs="?")
    ap.add_argument("--date", help="mapping date YYYY-MM-DD (from the fetch manifest)")
    ap.add_argument("--out", default="out/identity.ttl")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest or not (a.left and a.right):
        _selftest()
        return 0
    from sources import base as srcbase  # local import keeps the selftest light
    maps = match(_read_csv(a.left), _read_csv(a.right))
    with srcbase.TurtleWriter(a.out) as w:
        n = mappings_to_turtle(maps, w, a.date)
    acc = sum(1 for m in maps if m.status == "accepted")
    print(f"{n} mappings ({acc} accepted, {n - acc} candidate) -> {a.out}")
    return 0


# ===========================================================================
def _selftest():
    assert normalize_org("Medtronic MiniMed, Inc.") == "MEDTRONIC MINIMED"
    assert normalize_org("MEDTRONIC MINIMED INC") == "MEDTRONIC MINIMED"
    assert normalize_org("Johnson & Johnson") == "JOHNSON JOHNSON"
    assert normalize_org("Inc.") == ""

    # exact normalized name -> 0.95, accepted
    assert name_score("Medtronic MiniMed, Inc.", "MEDTRONIC MINIMED INC") == 0.95
    # the measured false positive must NOT be accepted
    s_apple = name_score("Apple Inc.", "Apple Diagnostic Products")
    assert CANDIDATE <= s_apple < ACCEPT, s_apple
    # a joint venture is a candidate for review, not an identity
    s_ge = name_score("Wipro GE Healthcare Private Ltd.", "GE Healthcare")
    assert s_ge < ACCEPT, s_ge
    # nothing distinctive in common -> 0
    assert name_score("Acme Medical Devices", "Zenith Medical Devices") == 0.0

    left = [Org("urn:l1", "Sedecal USA, Inc", fei="1000139935"),
            Org("urn:l2", "Medtronic MiniMed, Inc."),
            Org("urn:l3", "Apple Inc."),
            Org("urn:l4", "Totally Unrelated Widgets")]
    right = [Org("urn:r1", "SEDECAL USA INC", fei="0001000139935"),
             Org("urn:r2", "MEDTRONIC MINIMED INC"),
             Org("urn:r3", "Apple Diagnostic Products")]
    maps = {m.subject: m for m in match(left, right)}
    assert maps["urn:l1"].method == "shared-fei" and maps["urn:l1"].confidence == 1.0
    assert maps["urn:l2"].object == "urn:r2" and maps["urn:l2"].status == "accepted"
    assert maps["urn:l3"].status == "candidate"           # flagged, not merged
    assert "urn:l4" not in maps

    class _W:
        def __init__(self):
            self.blocks = []

        def block(self, s, p):
            self.blocks.append((s, p))
    w = _W()
    n = mappings_to_turtle(list(maps.values()), w, "2026-10-02")
    assert n == 3
    flat = [o for _, ps in w.blocks for _, o in ps]
    assert '"2026-10-02"^^xsd:date' in flat
    assert not any("sameAs" in str(p) for _, ps in w.blocks for p, _ in ps)
    print(f"identity.py self-test PASSED (apple={s_apple}, wipro-ge={s_ge}).")


if __name__ == "__main__":
    raise SystemExit(main())
