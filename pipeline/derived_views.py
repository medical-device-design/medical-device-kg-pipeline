#!/usr/bin/env python3
"""
derived_views.py -- stopgap views for the two FDA device databases FDA could not serve in
October 2026, computed from FDA source data already in the graph. Not a source module
(run directly); both views are labeled as derived on their dataset nodes.

1. Radiation-emitting product corrective actions (stands in for cfPCD_RH/rh_res.cfm, which
   returned HTTP 503 from October 12, 2026). FDA describes that search as a combination of
   its product code database and its recalls database, plus the performance standard cited
   in the recall. This view types every recall whose product code is a radiation-emitting
   product code (rh_codes.ttl) as ex:RadiationCorrectiveAction on the SAME recall IRI, links
   the radiation product code record, and extracts 21 CFR 1000-1050 citations from the
   recall's reason and action text. Writes rh_actions_derived.ttl.

2. TPLC premarket and recall layer (stands in for cfTPLC/tplc.cfm, redirected by FDA from
   October 12, 2026). FDA's TPLC page per product code combines PMA, HDE, De Novo, 510(k),
   MAUDE and recall data. tplc.ttl already carries the MAUDE part; this adds, on the same
   iri.tplc(code) node and its iri.tplc_year(code, year) nodes, the counts of 510(k)
   decisions, PMA originals and supplements, De Novo decisions, HDE originals and recalls
   (total and by class). Writes tplc_premarket.ttl.

    python3 derived_views.py <out_dir> <tplc.ttl> <k510.ttl> <pma.ttl> <hde.ttl> <denovo.ttl>
            <rh_codes.ttl> <recall file> [<recall file> ...]
"""
import collections
import os
import re
import sys
from pathlib import Path

_PIPELINE = os.path.dirname(os.path.abspath(__file__))          # pipeline/ (not a source module)
_SRC_DIR = os.path.join(_PIPELINE, "sources")
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

PC = re.compile(r"/id/product-code/([a-z]{3})>")
LIT = re.compile(r'^\s+(ex:\w+)\s+"((?:[^"\\]|\\.)*)"')
CFR = re.compile(r"\b(?:21\s*C\.?F\.?R\.?\s*(?:§|sec(?:tion)?\.?|part)?\s*)?(10[0-4]\d|1050)\.(\d{1,3})\b", re.I)
RH_DATASET = iri.ID + "dataset/derived-radiation-corrective-actions"
TPLC_DATASET = iri.ID + "dataset/derived-tplc-premarket-recall"


def blocks(path):
    """Yield (subject IRI, [lines]) for each block our TurtleWriter wrote (blank-line separated)."""
    subj, lines = None, []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("<"):
                subj, lines = line.split(">", 1)[0][1:], []
            elif not line.strip():
                if subj:
                    yield subj, lines
                subj, lines = None, []
            elif subj:
                lines.append(line)
    if subj:
        yield subj, lines


def fields(lines):
    """{predicate: [literal values]} plus product codes found in the block."""
    out = collections.defaultdict(list)
    codes = set()
    for ln in lines:
        m = LIT.match(ln)
        if m:
            out[m.group(1)].append(m.group(2).encode("utf-8").decode("unicode_escape", "ignore")
                                   if "\\" in m.group(2) else m.group(2))
        for c in PC.findall(ln):
            codes.add(c.upper())
    return out, codes


def year_of(vals):
    for v in vals:
        if len(v) >= 4 and v[:4].isdigit():
            return v[:4]
    return None


def cfr_citations(text):
    found = []
    for part, sec in CFR.findall(text or ""):
        c = f"{part}.{sec}"
        if c not in found:
            found.append(c)
    return found


def build(out_dir, tplc, k510, pma, hde, denovo, rh_codes, recall_files, retrieved_at=None):
    out_dir = Path(out_dir)
    # radiation codes
    rh = {}
    for s, ls in blocks(rh_codes):
        f, _ = fields(ls)
        if f.get("ex:productCode"):
            rh[f["ex:productCode"][0]] = f.get("ex:performanceStandardCitation", [])
    # recalls: merge every file on the recall IRI
    rec = {}
    for path in recall_files:
        for s, ls in blocks(path):
            if "/id/recall/" not in s:
                continue
            f, codes = fields(ls)
            r = rec.setdefault(s, {"codes": set(), "cls": None, "year": None, "text": [], "num": None})
            r["codes"] |= codes
            r["cls"] = r["cls"] or (f.get("ex:recallClassification") or [None])[0]
            r["year"] = r["year"] or year_of(f.get("ex:recallInitiationDate", []))
            r["num"] = r["num"] or (f.get("ex:recallNumber") or [None])[0]
            r["text"] += f.get("ex:reasonForRecall", []) + f.get("ex:recallAction", [])

    # ---- 1. radiation corrective actions
    st_rh = base.Stats(source="rh_actions_derived")
    with base.TurtleWriter(out_dir / "rh_actions_derived.ttl") as w:
        w.block(RH_DATASET, [
            ("a", "ex:SourceDataset"),
            ("rdfs:label", common.lit("Radiation-emitting product corrective actions, derived from FDA recalls and radiation product codes")),
            ("ex:usageNote", common.lit(
                "Stopgap for FDA's Radiation Emitting Product Corrective Actions and Recalls search "
                "(cfPCD_RH/rh_res.cfm), which returned HTTP 503 from October 12, 2026. Recalls whose product "
                "code is a radiation-emitting product code; cited 21 CFR 1000-1050 sections are extracted from "
                "the recall text. Corrective actions FDA tracks outside its device recall data are not included.")),
            ("ex:retrievedAt", common.date_lit(retrieved_at)),
        ])
        for s, r in sorted(rec.items()):
            hit = sorted(c for c in r["codes"] if c in rh)
            if not hit:
                continue
            pairs = [("a", "ex:RadiationCorrectiveAction"), ("ex:inDataset", f"<{RH_DATASET}>")]
            for c in hit:
                pairs.append(("ex:radiationProductClassification", f"<{iri.radiation_product_classification(c)}>"))
            for c in cfr_citations(" ".join(r["text"])):
                pairs.append(("ex:citedPerformanceStandard", common.lit(c)))
                if c.split(".")[0] in {"1020", "1030", "1040", "1050"}:
                    pairs.append(("ex:hasPerformanceStandard", f"<{iri.cfr_section(c)}>"))
            w.block(s, pairs)
            st_rh.records += 1
    st_rh.triples = w.triples

    # ---- 2. TPLC premarket and recall layer
    have_tplc = {s for s, _ in blocks(tplc) if "/id/tplc/" in s and s.count("/") == iri.tplc("ABC").count("/")}
    tot = collections.defaultdict(collections.Counter)                       # code -> counter
    yr = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))   # code -> year -> counter

    def add(code, year, key):
        tot[code][key] += 1
        if year:
            yr[code][year][key] += 1

    for s, ls in blocks(k510):
        f, codes = fields(ls)
        for c in codes:
            add(c, year_of(f.get("ex:decisionDate", [])), "k510DecisionCount")
    for s, ls in blocks(pma):
        f, codes = fields(ls)
        key = "pmaSupplementCount" if f.get("ex:supplementNumber") else "pmaOriginalCount"
        for c in codes:
            add(c, year_of(f.get("ex:decisionDate", [])), key)
    for s, ls in blocks(hde):
        f, codes = fields(ls)
        if f.get("ex:supplementNumber"):
            continue
        for c in codes:
            add(c, year_of(f.get("ex:decisionDate", [])), "hdeOriginalCount")
    for s, ls in blocks(denovo):
        f, codes = fields(ls)
        for c in codes:
            add(c, year_of(f.get("ex:decisionDate", [])), "deNovoDecisionCount")
    for s, r in rec.items():
        cls = (r["cls"] or "").replace("Class ", "").strip()
        for c in r["codes"]:
            add(c, r["year"], "recallCount")
            if cls in ("I", "II", "III"):
                add(c, r["year"], f"recallClass{cls}Count")

    st_t = base.Stats(source="tplc_premarket")
    keys = ["k510DecisionCount", "pmaOriginalCount", "pmaSupplementCount", "deNovoDecisionCount",
            "hdeOriginalCount", "recallCount", "recallClassICount", "recallClassIICount", "recallClassIIICount"]
    with base.TurtleWriter(out_dir / "tplc_premarket.ttl") as w:
        w.block(TPLC_DATASET, [
            ("a", "ex:SourceDataset"),
            ("rdfs:label", common.lit("TPLC premarket and recall counts, computed from FDA source databases")),
            ("ex:usageNote", common.lit(
                "Stopgap for FDA's Total Product Life Cycle pages (cfTPLC/tplc.cfm), redirected by FDA from "
                "October 12, 2026. Counts per product code and decision or recall-initiation year from the "
                "510(k), PMA, HDE, De Novo and recall records in this graph; MAUDE counts are on the same TPLC "
                "nodes from tplc.ttl. Not FDA's own page values.")),
            ("ex:retrievedAt", common.date_lit(retrieved_at)),
        ])
        for code in sorted(tot):
            node = iri.tplc(code)
            pairs = []
            if node not in have_tplc:
                pairs += [("a", "ex:TPLCRecord"), ("rdfs:label", common.lit(f"TPLC {code}")),
                          ("ex:productCode", common.lit(code)),
                          ("ex:hasProductCode", f"<{iri.product_code(code)}>")]
            pairs.append(("ex:inDataset", f"<{TPLC_DATASET}>"))
            pairs += [(f"ex:{k}", common.int_lit(tot[code][k])) for k in keys if tot[code][k]]
            for y in sorted(yr[code]):
                pairs.append(("ex:hasYearStatistic", f"<{iri.tplc_year(code, y)}>"))
            w.block(node, pairs)
            st_t.records += 1
            for y in sorted(yr[code]):
                c = yr[code][y]
                w.block(iri.tplc_year(code, y), [("a", "ex:TPLCYearStatistic"),
                                                 ("ex:year", common.gyear_lit(y)),
                                                 ("ex:relatedTPLC", f"<{node}>")]
                        + [(f"ex:{k}", common.int_lit(c[k])) for k in keys if c[k]])
    st_t.triples = w.triples
    return st_rh, st_t, len(rec)


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        P = "@prefix ex: <" + iri.NS + "> .\n\n"
        (d / "rh.ttl").write_text(P + "<" + iri.ID + "radiation-product-classification/jak>\n"
                                  '    a ex:RadiationProductClassification ;\n    ex:productCode "JAK" ;\n'
                                  '    ex:performanceStandardCitation "1020.33" .\n\n', encoding="utf-8")
        (d / "rec.ttl").write_text(P + "<" + iri.ID + "recall/z_0001_2020>\n    a ex:RecallRecord ;\n"
                                   '    ex:recallNumber "Z-0001-2020" ;\n    ex:recallClassification "Class II" ;\n'
                                   '    ex:reasonForRecall "Does not comply with 21 CFR 1020.33(c)." ;\n'
                                   '    ex:recallInitiationDate "2020-03-01"^^xsd:date ;\n'
                                   "    ex:hasProductCode <" + iri.ID + "product-code/jak> .\n\n"
                                   "<" + iri.ID + "recall/z_0002_2020>\n    a ex:RecallRecord ;\n"
                                   "    ex:hasProductCode <" + iri.ID + "product-code/frn> .\n\n",
                                   encoding="utf-8")
        (d / "k.ttl").write_text(P + "<" + iri.ID + "k510/k1>\n    a ex:K510Record ;\n"
                                 '    ex:decisionDate "2019-05-01"^^xsd:date ;\n'
                                 "    ex:hasProductCode <" + iri.ID + "product-code/jak> .\n\n",
                                 encoding="utf-8")
        (d / "p.ttl").write_text(P + "<" + iri.ID + "pma/p1/supplement/s1>\n    a ex:PMARecord ;\n"
                                 '    ex:supplementNumber "S001" ;\n    ex:decisionDate "2019-06-01"^^xsd:date ;\n'
                                 "    ex:hasProductCode <" + iri.ID + "product-code/jak> .\n\n",
                                 encoding="utf-8")
        for n in ("t.ttl", "h.ttl", "dn.ttl"):
            (d / n).write_text(P, encoding="utf-8")
        st_rh, st_t, n = build(d, d / "t.ttl", d / "k.ttl", d / "p.ttl", d / "h.ttl", d / "dn.ttl", d / "rh.ttl",
                               [d / "rec.ttl"], retrieved_at="2026-10-21")
        rhttl = (d / "rh_actions_derived.ttl").read_text(encoding="utf-8")
        tp = (d / "tplc_premarket.ttl").read_text(encoding="utf-8")
        assert st_rh.records == 1 and "z_0001_2020>" in rhttl and "z_0002_2020>" not in rhttl
        assert 'ex:citedPerformanceStandard "1020.33"' in rhttl and "/id/cfr-section/1020_33>" in rhttl
        assert 'ex:k510DecisionCount "1"^^xsd:integer' in tp and 'ex:pmaSupplementCount "1"^^xsd:integer' in tp
        assert 'ex:recallClassIICount "1"^^xsd:integer' in tp and "/tplc/jak/year/2020>" in tp
        assert 'rdfs:label "TPLC JAK"' in tp                                       # code missing from tplc.ttl gets a node
    assert cfr_citations("violates 21 CFR 1040.10 and 1040.11; see 1003.2") == ["1040.10", "1040.11", "1003.2"]
    print("derived_views.py self-test PASSED")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        _selftest()
    else:
        a = sys.argv[1:]
        st_rh, st_t, n = build(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7:], retrieved_at=base.today())
        print(f"recalls read: {n:,}")
        print(st_rh)
        print(st_t)
