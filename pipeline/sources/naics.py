#!/usr/bin/env python3
"""
naics.py -- FDA product code -> NAICS 2022 crosswalk, and manufacturer NAICS -> RDF.

This is an INFERRED layer, not FDA data: FDA does not assign NAICS codes. Every
assignment is a reified ex:ProductCodeNAICSMapping that carries how it was made
(mappingMethod), how sure it is (mappingConfidence), why (mappingEvidence) and that
it is our crosswalk (mappingSource), so a query can always separate it from FDA facts.

Inputs (already downloaded and validated by other sources):
  downloads/classification       openFDA device/classification (every product code)
  downloads/registrationlisting  openFDA device/registrationlisting (who lists what)
  downloads/naics/legacy_naics.tsv  optional: the 1,589 reviewed ("approved") legacy
                                 assignments, product_code<TAB>naics<TAB>confidence<TAB>reason

Method, in order:
  1. keyword_rule      ordered keyword rules over device name + definition  (0.85)
  2. specialty_default the product code's review panel / specialty default     (0.60)
  3. legacy_reviewed   where a reviewed legacy assignment exists it is kept; if the
                       rules agree, confidence 0.95, otherwise the legacy code stays
                       and the disagreeing rule is recorded in mappingEvidence.
Manufacturer NAICS: every NAICS code mapped from the product codes an establishment
lists is attached to its ex:Manufacturer (FEI) node, both as ex:hasNAICSClassification
and as sudokn:hasNAICSClassifier, so sudokn joins work without our vocabulary.
"""
import collections
import datetime as dt
import os
import re
import sys
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "naics"
DATASET = "derived/naics-crosswalk"
SUDOKN = "http://asu.edu/semantics/SUDOKN/"
VERSION = "2022"
MAPPING_SOURCE = "internal_crosswalk (rule-based; not FDA data)"

LABELS = {
    "325412": "Pharmaceutical Preparation Manufacturing",
    "325413": "In-Vitro Diagnostic Substance Manufacturing",
    "325414": "Biological Product (except Diagnostic) Manufacturing",
    "332721": "Precision Turned Product Manufacturing",
    "334510": "Electromedical and Electrotherapeutic Apparatus Manufacturing",
    "334516": "Analytical Laboratory Instrument Manufacturing",
    "334517": "Irradiation Apparatus Manufacturing",
    "334519": "Other Measuring and Controlling Device Manufacturing",
    "339112": "Surgical and Medical Instrument Manufacturing",
    "339113": "Surgical Appliance and Supplies Manufacturing",
    "339114": "Dental Equipment and Supplies Manufacturing",
    "339115": "Ophthalmic Goods Manufacturing",
    "339116": "Dental Laboratories",
}

# Ordered: the first rule whose keyword appears wins.
RULES = [
    ("339114", ["dental", "orthodontic", "denture", "tooth", "endodontic", "periodontal", "dentin", "enamel"]),
    ("339115", ["contact lens", "intraocular lens", "spectacle", "eyeglass", "ophthalmic lens", "sunglass"]),
    ("334517", ["x-ray", "radiograph", "computed tomography", "fluoroscop", "linear accelerator",
                "radiation therapy", "radionuclide", "gamma camera", "mammograph", "brachytherapy",
                "positron emission", "radiologic"]),
    ("325413", ["reagent", "assay", "antisera", "antiserum", "antigen", "antibod", "calibrator",
                "control material", "culture medi", "immunoassay", "test system", "nucleic acid",
                "stain", "test strip", "test kit"]),
    ("334516", ["analyzer", "chromatograph", "spectrophotometer", "mass spectromet", "cell counter",
                "flow cytomet", "photometer", "densitometer", "centrifuge", "microscope"]),
    ("334510", ["electrocardiograph", "defibrillator", "pacemaker", "electroencephalograph",
                "stimulator", "magnetic resonance", "ultrasonic", "ultrasound", "hearing aid",
                "electrosurgical", "diathermy", "laser", "monitor", "electromyograph", "evoked response",
                "cochlear implant", "electrode", "pulse oximeter", "telemetry", "electrical"]),
    ("339113", ["wheelchair", "crutch", "orthosis", "brace", "splint", "bandage", "dressing", "glove",
                "gown", "drape", "surgical mask", "respirator", "cast ", "prosthesis", "hearing protector",
                "condom", "walker", "bed", "stocking", "sling", "support"]),
]
LAB_PANELS = {"CH", "HE", "IM", "MI", "PA", "TX"}
PANEL_DEFAULT = {"DE": "339114", "OP": "339115", "RA": "334510"}


def _has(text, kw):
    return re.search(r"(?<![a-z])" + re.escape(kw), text) is not None


def classify(name, definition, panel):
    """-> (naics, method, confidence, evidence)."""
    text = " ".join(x for x in (name, definition) if x).lower()
    for code, kws in RULES:
        hit = [k for k in kws if _has(text, k)]
        if hit:
            return code, "keyword_rule", 0.85, f"keywords {hit[:3]} in FDA device name/definition"
    p = (panel or "").upper()
    if p in LAB_PANELS:
        return "325413", "specialty_default", 0.60, f"laboratory review panel {p}"
    if p in PANEL_DEFAULT:
        return PANEL_DEFAULT[p], "specialty_default", 0.60, f"review panel {p}"
    return "339112", "specialty_default", 0.60, f"no keyword; default for review panel {p or 'unknown'}"


def fetch(dest):
    Path(dest).mkdir(parents=True, exist_ok=True)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])


def _legacy(src):
    p = Path(src) / "legacy_naics.tsv"
    out = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            a = ln.split("\t")
            if len(a) >= 2 and a[1].strip() in LABELS:
                out[a[0].strip().upper()] = (a[1].strip(), a[2].strip() if len(a) > 2 else "", a[3].strip() if len(a) > 3 else "")
    return out


def transform(src, out, retrieved_at=None, downloads=None):
    src = Path(src)
    downloads = Path(downloads) if downloads else src.parent
    today = retrieved_at or dt.date.today().isoformat()
    legacy = _legacy(src)
    st = base.Stats(source=NAME)
    pc_naics, used = {}, collections.Counter()
    with base.TurtleWriter(out, extra={"sudokn": SUDOKN}) as w:
        for r in base.iter_openfda_results(downloads / "classification"):
            pc = str(r.get("product_code") or "").strip().upper()
            if not pc or pc in pc_naics:
                continue
            code, method, conf, ev = classify(r.get("device_name"), r.get("definition"),
                                              r.get("review_panel") or r.get("medical_specialty"))
            status = "candidate"
            if pc in legacy:
                lcode, lconf, lreason = legacy[pc]
                status = "accepted"
                if lcode == code:
                    method, conf, ev = "legacy_reviewed+keyword_rule", 0.95, f"{ev}; agrees with reviewed legacy assignment"
                else:
                    ev = f"reviewed legacy assignment kept ({lreason or 'no reason recorded'}); rule suggested {code} ({ev})"
                    code, method = lcode, "legacy_reviewed"
                    conf = float(lconf) if re.fullmatch(r"[0-9.]+", lconf or "") else 0.75
            pc_naics[pc] = code
            used[code] += 1
            m = iri.naics_mapping(pc)
            w.block(m, [("a", "ex:ProductCodeNAICSMapping"),
                        ("rdfs:label", common.lit(f"{pc} -> NAICS {code} (inferred)")),
                        ("ex:mappingSubject", f"<{iri.product_code(pc)}>"),
                        ("ex:assignedNAICS", f"<{iri.naics(code)}>"),
                        ("ex:mappingMethod", common.lit(method)),
                        ("ex:mappingConfidence", f'"{conf:.2f}"^^xsd:decimal'),
                        ("ex:mappingSource", common.lit(MAPPING_SOURCE)),
                        ("ex:mappingStatus", common.lit(status)),
                        ("ex:mappingEvidence", common.lit(ev)),
                        ("ex:mappingDate", common.date_lit(today[:10]))])
            w.block(iri.product_code(pc), [("ex:hasNAICSMapping", f"<{m}>"),
                                           ("ex:hasNAICSClassification", f"<{iri.naics(code)}>"),
                                           ("sudokn:hasNAICSClassifier", f"<{iri.naics(code)}>")])
            st.records += 1
        # manufacturers: union of NAICS over listed product codes
        mfr = collections.defaultdict(set)
        for r in base.iter_openfda_results(downloads / "registrationlisting"):
            reg = r.get("registration") or {}
            fei = str(reg.get("fei_number") or "").strip()
            if not fei:
                continue
            for prod in (r.get("products") or []):
                code = pc_naics.get(str(prod.get("product_code") or "").strip().upper())
                if code:
                    mfr[fei].add(code)
        for fei in sorted(mfr):
            pairs = []
            for code in sorted(mfr[fei]):
                pairs += [("ex:hasNAICSClassification", f"<{iri.naics(code)}>"),
                          ("sudokn:hasNAICSClassifier", f"<{iri.naics(code)}>")]
            w.block(iri.manufacturer(fei=fei), pairs)
        for code in sorted(set(used) | {c for s in mfr.values() for c in s}):
            w.block(iri.naics(code), [("a", "ex:NAICSClassification"), ("a", "sudokn:NAICSClassifier"),
                                      ("rdfs:label", common.lit(LABELS[code])),
                                      ("ex:naicsCode", common.lit(code)),
                                      ("ex:naicsVersion", common.lit(VERSION))])
    st.triples = w.triples
    print(f"naics: {st.records} product codes mapped; {len(mfr)} manufacturers; by code {dict(used.most_common())}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import json
    import tempfile
    assert classify("Hip prosthesis, cemented", "", "OR")[0] == "339113"
    assert classify("Analyzer, chemistry", "", "CH")[0] == "334516"
    assert classify("Glucose test system", "", "CH")[0] == "325413"
    assert classify("Unit, x-ray, mobile", "", "RA")[0] == "334517"
    assert classify("Bracket, orthodontic", "", "DE")[0] == "339114"
    assert classify("Catheter, intravascular", "", "CV") == ("339112", "specialty_default", 0.60,
                                                            "no keyword; default for review panel CV")
    assert classify("Stain, Gram", "", "MI")[0] == "325413" and classify("Prostain dish", "", "")[0] == "339112"
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for sub in ("classification", "registrationlisting", "naics"):
            (d / sub).mkdir()
        (d / "classification" / "c.json").write_text(json.dumps({"results": [
            {"product_code": "LAN", "device_name": "Radioimmunoassay, Digoxin", "review_panel": "TX"},
            {"product_code": "INB", "device_name": "Wheelchair, Mechanical", "review_panel": "PM"},
            {"product_code": "XYZ", "device_name": "Catheter", "review_panel": "CV"}]}))
        (d / "registrationlisting" / "r.json").write_text(json.dumps({"results": [
            {"registration": {"fei_number": "3001"}, "products": [{"product_code": "LAN"}, {"product_code": "INB"}]}]}))
        (d / "naics" / "legacy_naics.tsv").write_text("LAN\t325413\t0.75\tassay\nINB\t339112\t0.65\tx\n", encoding="utf-8")
        st = transform(d / "naics", d / "n.ttl", retrieved_at="2026-10-10")
        ttl = (d / "n.ttl").read_text(encoding="utf-8")
    assert st.records == 3, st
    lan = ttl.split(f"<{iri.naics_mapping('LAN')}>\n", 1)[1].split("\n\n")[0]
    assert 'ex:mappingMethod "legacy_reviewed+keyword_rule"' in lan and '"0.95"^^xsd:decimal' in lan
    inb = ttl.split(f"<{iri.naics_mapping('INB')}>\n", 1)[1].split("\n\n")[0]
    assert f"<{iri.naics('339112')}>" in inb and "rule suggested 339113" in inb
    m = ttl.split(f"<{iri.manufacturer(fei='3001')}>\n", 1)[1].split("\n\n")[0]
    assert m.count("sudokn:hasNAICSClassifier") == 2
    assert "a sudokn:NAICSClassifier" in ttl and "@prefix sudokn:" in ttl
    print(f"naics.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
