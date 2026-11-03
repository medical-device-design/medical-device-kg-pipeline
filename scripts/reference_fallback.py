#!/usr/bin/env python3
"""
reference_fallback.py -- give every referenced 510(k), PMA, HDE, De Novo, regulation and cited
21 CFR section/part node a name (v0.0.7 full test N5-N7, re-test P5z/P6z), modeled on
product_code_fallback.py.

Records link to submission and regulation nodes that FDA's own downloads do not contain:
De Novo decisions cite 510(k) numbers outside the openFDA 510(k) file (K033607, BR220737),
post-approval studies cite PMAs newer than the PMA download, and De Novo decisions cite
regulations newer than the 21 CFR snapshot. Those IRIs had no label (or no triples at all).
This writes reference_fallback.ttl with ONE minimal node per unresolved IRI: its type, one
rdfs:label that says what it is, which kind of record cites it and that FDA's data lacks it,
and an ex:dataQualityNote.

    python3 scripts/reference_fallback.py --out <dir> [--date YYYY-MM-DD] [--workers N] <file or dir> ...

Links checked (object IRIs): ex:hasRegulationNumber, ex:relatedK510Record, ex:relatedPMARecord,
ex:relatedHDE (PAS, 522, UDI, MAUDE), ex:mentionsK510Record, ex:mentionsPMARecord (warning
letters, safety communications), ex:listedSubmission (AI-enabled device list) and
ex:citesCfrSection, ex:citesCfrPart (warning letters, guidance documents). A cited 21 CFR section
or part that is not in the current eCFR (for example a Quality System regulation section retired
when the QMSR took effect on February 2, 2026, still cited by older letters and guidance) gets a
node typed ex:CFRTitle21Section / ex:CfrPart with its number and a label that says so.
An IRI is resolved when any input file writes a block for it that has an rdfs:label. All inputs
are streamed (any size; files over 2 GB are split into byte ranges and read in parallel).
Files named *_text.ttl and earlier fallback outputs are not read.
"""
import argparse
import collections
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "pipeline"))
sys.path.insert(0, str(HERE.parent / "pipeline" / "sources"))
import iri      # noqa: E402
import common   # noqa: E402
import base     # noqa: E402

PREDICATES = ("hasRegulationNumber", "relatedK510Record", "relatedPMARecord", "relatedHDE",
              "mentionsK510Record", "mentionsPMARecord", "listedSubmission", "citesCfrSection", "citesCfrPart")
KINDS = ("k510", "pma", "hde", "de-novo", "regulation", "cfr-section", "cfr-part")
_PREFIX = {k: (iri.ID + k + "/").encode() for k in KINDS}
# the Quality System regulation sections of 21 CFR part 820 (1996-2026); the QMSR, effective
# February 2, 2026, replaced them, so the current eCFR no longer has most of these numbers
QS_REG_SECTIONS = {"820." + s for s in (
    "1 3 5 20 22 25 30 40 50 60 65 70 72 75 80 86 90 100 120 130 140 150 160 170 180 181 184 186 "
    "198 200 250").split()}
QMSR_DATE = "February 2, 2026"
_REF = re.compile(rb"^\s+ex:(" + b"|".join(p.encode() for p in PREDICATES) + rb") <([^>]+)>")
_SUBJ = re.compile(rb"^<([^>]+)>")
CHUNK = 2_000_000_000
OUT_NAME = "reference_fallback.ttl"
OUT_FORMAT_NAME = "reference_fallback_supplement_format.ttl"

# which kind of record cites the reference (by input file name)
SOURCE_PHRASE = [
    ("denovo", "FDA De Novo record"), ("pas", "FDA post-approval study"), ("pma_memo", "FDA PMA 180-day memo"),
    ("postmarket_522", "FDA 522 postmarket study"), ("udi", "GUDID device record"), ("event", "MAUDE report"),
    ("recall_db", "FDA recall record"), ("recalls", "FDA recall record"), ("recall_class", "FDA recall record"),
    ("k510", "FDA 510(k) record"), ("pma", "FDA PMA record"), ("hde", "FDA HDE record"),
    ("classification_detail", "FDA product classification record"),
    ("classification", "FDA product classification record"), ("registrationlisting", "FDA device listing"),
    ("clia", "FDA CLIA record"), ("ivd_home_use", "FDA home-use IVD record"),
    ("rh_codes", "FDA radiation product code record"), ("fda_tplc", "FDA TPLC page"),
    ("ai_devices", "FDA AI-enabled device list entry"), ("warning_letters", "FDA warning letter"),
    ("safety_comms", "FDA safety communication"), ("devices_fda", "Devices@FDA record"),
    ("standards", "FDA recognized standard"), ("legacy_mdr", "FDA legacy MDR report"),
    ("medsun", "MedSun report"), ("asr", "FDA alternative summary report"),
    ("guidance", "FDA guidance document"), ("device_eua", "FDA emergency use authorization record"),
    ("shortages", "FDA device shortage list entry"),
]
# what each kind is and which FDA data lacks it
KIND_INFO = {
    "k510": ("ex:K510Record", "510(k)", "not in FDA 510(k) data", "openFDA device/510k download"),
    "pma": ("ex:PMARecord", "PMA", "not in FDA PMA data", "openFDA device/pma download"),
    "hde": ("ex:HumanitarianDeviceExemption", "HDE", "not in FDA HDE data", "FDA HDE database"),
    "de-novo": ("ex:DeNovoRecord", "De Novo", "not in FDA De Novo data", "FDA De Novo database"),
    "regulation": ("ex:RegulationNumber", "21 CFR", "not in the current 21 CFR", "eCFR title 21 bulk XML"),
    "cfr-section": ("ex:CFRTitle21Section", "21 CFR", "not in the current eCFR", "eCFR title 21 bulk XML"),
    "cfr-part": ("ex:CfrPart", "21 CFR part", "not in the current eCFR", "eCFR title 21 bulk XML"),
}


def source_phrase(stem):
    for key, phrase in SOURCE_PHRASE:
        if stem == key or stem.startswith(key + "_") or (key == "event" and stem.startswith("event")):
            return phrase
    return f"{stem} record"


def kind_of(iri_text):
    for k in KINDS:
        if iri_text.startswith(iri.ID + k + "/"):
            return k
    return None


def _scan(job):
    """(path, start, end) -> (labelled subjects, defined subjects, {target: {stem}}, Counter)."""
    path, start, end = job
    stem = Path(path).stem
    labelled, defined = set(), set()
    refs = collections.defaultdict(set)
    preds = collections.Counter()
    cur = None
    with open(path, "rb") as fh:
        if start:
            # the block that is open at the range start: its subject is the first line after the
            # last blank line before `start` (the previous range reads that block's own lines)
            pre = max(0, start - (1 << 22))
            fh.seek(pre)
            head = fh.read(start - pre)
            cut = head.rfind(b"\n\n")
            if cut >= 0 or pre == 0:
                bstart = pre + cut + 2 if cut >= 0 else 0
                if bstart < start:
                    fh.seek(bstart)
                    m = _SUBJ.match(fh.readline())           # the whole subject line, even if cut
                    s = m.group(1) if m else b""
                    cur = s if any(s.startswith(p) for p in _PREFIX.values()) else None
            fh.seek(start - 1)
            if fh.read(1) != b"\n":
                fh.readline()                       # finish the line that straddles the range start
        pos = fh.tell()
        for line in fh:
            if pos >= end:
                break
            pos += len(line)
            if line[:1] == b"<":
                m = _SUBJ.match(line)
                s = m.group(1) if m else b""
                cur = s if any(s.startswith(p) for p in _PREFIX.values()) else None
                if cur:
                    defined.add(cur.decode())
                continue
            if not line.strip():
                cur = None
                continue
            if cur is not None and line.lstrip().startswith(b"rdfs:label "):
                labelled.add(cur.decode())
            if b" ex:" in line[:8] and b"<" in line:
                m = _REF.match(line)
                if m:
                    t = m.group(2).decode()
                    if kind_of(t):
                        refs[t].add(stem)
                        preds[m.group(1).decode()] += 1
    return labelled, defined, dict(refs), preds


def _jobs(files):
    jobs = []
    for f in files:
        size = os.path.getsize(f)
        if size <= CHUNK:
            jobs.append((str(f), 0, size + 1))
        else:
            for s in range(0, size, CHUNK):
                jobs.append((str(f), s, min(size, s + CHUNK)))
    return jobs


def number_of(kind, t):
    """IRI -> the number as FDA writes it ('.../k510/k033607' -> 'K033607',
    '.../pma/p123/supplement/s001' -> 'P123 supplement S001', '.../regulation/862_1354' -> '862.1354')."""
    rest = t[len(iri.ID + kind + "/"):]
    if kind in ("regulation", "cfr-section"):
        return rest.replace("_", ".")
    if kind == "cfr-part":
        return rest
    parts = rest.split("/")
    if len(parts) == 3 and parts[1] == "supplement":
        return f"{parts[0].upper()} supplement {parts[2].upper()}"
    return rest.upper()


def _join(phrases):
    phrases = sorted(phrases)
    if len(phrases) <= 2:
        return " and ".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def build(inputs, out_dir, date=None, workers=4):
    files = []
    for t in map(Path, inputs):
        files += sorted(t.glob("*.ttl")) if t.is_dir() else [t]
    files = [f for f in files if f.name not in (OUT_NAME, OUT_FORMAT_NAME, "product_code_fallback.ttl")
             and not f.name.endswith("_text.ttl") and "event_text" not in f.name]
    labelled, defined = set(), set()
    refs = collections.defaultdict(set)
    preds = collections.Counter()
    jobs = _jobs(files)
    with ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        for lab, dfn, rf, pc in ex.map(_scan, jobs):
            labelled |= lab
            defined |= dfn
            for t, stems in rf.items():
                refs[t] |= stems
            preds.update(pc)
    missing = sorted(t for t in refs if t not in labelled)
    counts = collections.Counter()
    no_triples = collections.Counter()
    out = Path(out_dir) / OUT_NAME
    out_fmt = Path(out_dir) / OUT_FORMAT_NAME
    bases_with_supplements = {d.split("/supplement/")[0] for d in defined if "/supplement/" in d}
    with base.TurtleWriter(out) as w, base.TurtleWriter(out_fmt) as wf:
        for t in missing:
            kind = kind_of(t)
            cls, what, lacking, data = KIND_INFO[kind]
            num = number_of(kind, t)
            if kind == "k510" and num.startswith("DEN"):
                what = "De Novo"                               # DEN numbers sit in FDA's 510(k) data
            by = _join({source_phrase(s) for s in refs[t]})
            note = (f"Cited by {by} data, but {data}"
                    f"{' of ' + date if date else ''} has no record with this number. "
                    "This node only names the reference; it carries no FDA record content.")
            pairs = [("a", cls)]
            writer, key, verb = w, kind, "referenced by"
            m = re.search(r"/supplement/(\d+)$", t)
            fda_form = t[:m.start(1)] + "s" + m.group(1) if m else None
            if kind in ("pma", "hde") and "/supplement/" not in t and t in bases_with_supplements:
                lacking = lacking.replace("not in", "original approval not in")
            elif fda_form and fda_form in labelled:
                # the citing file writes the supplement number without FDA's "S" ("014" for "S014");
                # kept in a separate file so it can be dropped once the citing module is fixed
                n = num.split()[-1]
                lacking = f"FDA {what} data numbers this supplement S{n}"
                note = (f"Cited by {by} data with the supplement number {n}; FDA {what} data has this "
                        f"supplement as S{n} ({fda_form}). This node only names the reference.")
                writer, key = wf, kind + " supplement-number format"
            elif kind == "regulation":
                sec = iri.cfr_section(num)
                if sec in labelled:
                    lacking = "a 21 CFR section without a device identification or class"
                    note = (f"Cited by {by} data as a regulation number. The {data}"
                            f"{' of ' + date if date else ''} has this section but no device identification "
                            "or classification paragraph in it, so no regulation-number record is built from it.")
                    pairs.append(("ex:hasCFRSection", f"<{sec}>"))
                    key = "regulation (CFR section exists)"
            elif kind in ("cfr-section", "cfr-part"):
                verb = "cited by"
                where = (f"the {data}{' of ' + date if date else ''} has no "
                         f"{'section' if kind == 'cfr-section' else 'part'} with this number")
                note = f"Cited by {by} data, but {where}."
                if kind == "cfr-section":
                    pairs.append(("ex:sectionNumber", common.lit(num)))
                    part = iri.cfr_part(num.split(".")[0])
                    if part in labelled:
                        pairs.append(("ex:inCfrPart", f"<{part}>"))
                    if num in QS_REG_SECTIONS:
                        lacking += f": a Quality System regulation section retired with the QMSR on {QMSR_DATE}"
                        note += (f" 21 CFR {num} is a section of the Quality System regulation, which the Quality "
                                 f"Management System Regulation (QMSR) replaced on {QMSR_DATE}; documents issued "
                                 "before then cite it.")
                        key = "cfr-section (Quality System regulation)"
                else:
                    pairs.append(("ex:cfrPartNumber", common.lit(num)))
                note += " This node only names the citation; it carries no regulation text."
            writer.block(t, pairs + [
                ("rdfs:label", common.lit(f"{what} {num} ({verb} {by}; {lacking})")),
                ("ex:dataQualityNote", common.lit(note)),
            ])
            counts[key] += 1
            if t not in defined:
                no_triples[key] += 1
    return {"files": len(files), "jobs": len(jobs), "links": dict(preds), "referenced": len(refs),
            "fallback": dict(counts), "fallback_without_any_block": dict(no_triples),
            "out": [str(out), str(out_fmt)]}


def _selftest():
    import tempfile
    P = "@prefix ex: <" + iri.NS + "> .\n@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n\n"
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "k510.ttl").write_text(P + f"<{iri.k510('K111111')}>\n    a ex:K510Record ;\n"
                                    '    rdfs:label "K111111: Widget" .\n\n', encoding="utf-8")
        (d / "pma.ttl").write_text(P + f"<{iri.pma('P200001', 'S001')}>\n    a ex:PMARecord ;\n"
                                   '    rdfs:label "P200001/S001" ;\n'
                                   f"    ex:supplementOf <{iri.pma('P200001')}> .\n\n", encoding="utf-8")
        (d / "cfr.ttl").write_text(P + f"<{iri.regulation('880.5725')}>\n    a ex:RegulationNumber ;\n"
                                   '    rdfs:label "Infusion pump" .\n\n'
                                   f"<{iri.cfr_section('610.40')}>\n    a ex:CFRTitle21Section ;\n"
                                   '    rdfs:label "Test requirements" .\n\n'
                                   f"<{iri.cfr_part('820')}>\n    a ex:CfrPart ;\n"
                                   '    rdfs:label "QUALITY MANAGEMENT SYSTEM REGULATION" .\n\n', encoding="utf-8")
        # warning letter and guidance citations: a QS regulation section retired with the QMSR (820.30),
        # a section and a part the eCFR does not have, a resolved section/part, a PMA-shaped number
        (d / "warning_letters.ttl").write_text(P + f"<{iri.ID}warning-letter/1>\n    a ex:WarningLetter ;\n"
                                               '    rdfs:label "Warning letter 1" ;\n'
                                               f"    ex:mentionsK510Record <{iri.k510('K111111')}> ;\n"
                                               f"    ex:mentionsPMARecord <{iri.pma('P017333')}> ;\n"
                                               f"    ex:citesCfrSection <{iri.cfr_section('820.30')}> ;\n"
                                               f"    ex:citesCfrSection <{iri.cfr_section('610.40')}> ;\n"
                                               f"    ex:citesCfrPart <{iri.cfr_part('820')}> ;\n"
                                               f"    ex:citesCfrPart <{iri.cfr_part('90')}> .\n\n", encoding="utf-8")
        (d / "guidance.ttl").write_text(P + f"<{iri.ID}guidance-document/g>\n    a ex:GuidanceDocument ;\n"
                                        '    rdfs:label "Guidance G" ;\n'
                                        f"    ex:citesCfrSection <{iri.cfr_section('820.30')}> ;\n"
                                        f"    ex:citesCfrSection <{iri.cfr_section('5.37')}> .\n\n"
                                        f"<{iri.cfr_section('820.30')}>\n    a ex:CFRTitle21Section ;\n"
                                        '    ex:sectionNumber "820.30" .\n\n', encoding="utf-8")
        (d / "classification.ttl").write_text(P + f"<{iri.ID}classification/x>\n    a ex:ProductClassificationRecord ;\n"
                                              '    rdfs:label "X" ;\n'
                                              f"    ex:hasRegulationNumber <{iri.regulation('610.40')}> .\n\n",
                                              encoding="utf-8")
        (d / "denovo.ttl").write_text(P + f"<{iri.de_novo('DEN200001')}>\n    a ex:DeNovoRecord ;\n"
                                      '    rdfs:label "DEN200001: Thing" ;\n'
                                      f"    ex:hasRegulationNumber <{iri.regulation('880.5725')}> ;\n"
                                      f"    ex:hasRegulationNumber <{iri.regulation('862.1354')}> ;\n"
                                      f"    ex:relatedK510Record <{iri.k510('K111111')}> ;\n"
                                      f"    ex:relatedK510Record <{iri.k510('K033607')}> .\n\n"
                                      f"<{iri.regulation('862.1354')}>\n    a ex:RegulationNumber ;\n"
                                      '    ex:regulationNumber "862.1354" .\n\n', encoding="utf-8")
        (d / "pas.ttl").write_text(P + f"<{iri.ID}pas/1>\n    a ex:PostApprovalStudy ;\n"
                                   '    rdfs:label "PAS 1" ;\n'
                                   f"    ex:relatedPMARecord <{iri.pma('P260007')}> ;\n"
                                   f"    ex:relatedPMARecord <{iri.pma('P200001')}> ;\n"
                                   f"    ex:relatedHDE <{iri.hde('H990001')}> .\n\n", encoding="utf-8")
        (d / "udi.ttl").write_text(P + f"<{iri.ID}udi/1>\n    a ex:Device ;\n"
                                   f"    ex:relatedK510Record <{iri.k510('K033607')}> ;\n"
                                   f"    ex:relatedPMARecord <{iri.pma('P200001', '001')}> ;\n"
                                   f"    ex:relatedK510Record <{iri.k510('DEN0210006')}> .\n\n", encoding="utf-8")
        (d / "denovo_text.ttl").write_text(P + f"<{iri.ID}x>\n    ex:relatedK510Record <{iri.k510('K999999')}> .\n\n",
                                           encoding="utf-8")
        global CHUNK
        old, CHUNK = CHUNK, 64                                      # exercise the byte-range split too
        try:
            st = build([d], d, date="2026-10-29", workers=2)
        finally:
            CHUNK = old
        ttl = (d / OUT_NAME).read_text(encoding="utf-8")
        fmt = (d / OUT_FORMAT_NAME).read_text(encoding="utf-8")
    assert st["fallback"] == {"k510": 2, "pma": 3, "hde": 1, "regulation": 1, "regulation (CFR section exists)": 1,
                              "pma supplement-number format": 1, "cfr-section (Quality System regulation)": 1,
                              "cfr-section": 1, "cfr-part": 1}, st
    assert st["links"]["citesCfrSection"] == 4 and st["links"]["citesCfrPart"] == 2, st
    assert st["links"]["mentionsK510Record"] == 1 and st["links"]["mentionsPMARecord"] == 1, st
    # 21 CFR citations the current eCFR does not have
    sec = ttl.split(f"<{iri.cfr_section('820.30')}>\n", 1)[1].split("\n\n")[0]
    assert sec.startswith("    a ex:CFRTitle21Section ;") and 'ex:sectionNumber "820.30"' in sec, sec
    assert f"ex:inCfrPart <{iri.cfr_part('820')}>" in sec, sec
    assert ('rdfs:label "21 CFR 820.30 (cited by FDA guidance document and FDA warning letter; not in the current '
            'eCFR: a Quality System regulation section retired with the QMSR on February 2, 2026)"') in sec, sec
    assert "Quality Management System Regulation (QMSR) replaced on February 2, 2026" in sec, sec
    assert ('rdfs:label "21 CFR 5.37 (cited by FDA guidance document; not in the current eCFR)"' in ttl
            and "ex:inCfrPart" not in ttl.split(f"<{iri.cfr_section('5.37')}>\n", 1)[1].split("\n\n")[0])
    part = ttl.split(f"<{iri.cfr_part('90')}>\n", 1)[1].split("\n\n")[0]
    assert part.startswith("    a ex:CfrPart ;") and 'ex:cfrPartNumber "90"' in part, part
    assert 'rdfs:label "21 CFR part 90 (cited by FDA warning letter; not in the current eCFR)"' in part, part
    assert "eCFR title 21 bulk XML of 2026-10-29 has no part with this number" in part, part
    assert f"\n<{iri.cfr_section('610.40')}>\n" not in ttl and f"\n<{iri.cfr_part('820')}>\n" not in ttl  # resolved
    # a PMA-shaped number in a warning letter that FDA PMA data lacks is named, not dropped
    assert 'rdfs:label "PMA P017333 (referenced by FDA warning letter; not in FDA PMA data)"' in ttl, ttl
    # a supplement cited as "001" while FDA PMA data has "S001": separate file, says so
    assert f"<{iri.pma('P200001', '001')}>" in fmt and f"<{iri.pma('P200001', '001')}>" not in ttl, fmt
    assert ('rdfs:label "PMA P200001 supplement 001 (referenced by GUDID device record; '
            'FDA PMA data numbers this supplement S001)"') in fmt, fmt
    # a regulation number whose 21 CFR section exists but builds no regulation record
    assert ('rdfs:label "21 CFR 610.40 (referenced by FDA product classification record; '
            'a 21 CFR section without a device identification or class)"') in ttl, ttl
    assert f"ex:hasCFRSection <{iri.cfr_section('610.40')}>" in ttl
    assert 'rdfs:label "De Novo DEN0210006 (referenced by GUDID device record; not in FDA 510(k) data)"' in ttl
    assert ('rdfs:label "510(k) K033607 (referenced by FDA De Novo record and GUDID device record; '
            'not in FDA 510(k) data)"') in ttl, ttl
    assert 'rdfs:label "21 CFR 862.1354 (referenced by FDA De Novo record; not in the current 21 CFR)"' in ttl
    assert 'rdfs:label "PMA P260007 (referenced by FDA post-approval study; not in FDA PMA data)"' in ttl
    assert ('rdfs:label "PMA P200001 (referenced by FDA post-approval study; original approval not in FDA PMA data)"'
            in ttl)
    assert 'rdfs:label "HDE H990001 (referenced by FDA post-approval study; not in FDA HDE data)"' in ttl
    assert "K111111" not in ttl and "880_5725" not in ttl and "K999999" not in ttl     # resolved / text file
    blocks = [b for b in ttl.split("\n\n") if b.startswith("<")]
    blocks += [b for b in fmt.split("\n\n") if b.startswith("<")]
    assert len(blocks) == 12 and all(b.count("rdfs:label") == 1 and "ex:dataQualityNote" in b for b in blocks)
    assert all(b.split("ex:dataQualityNote ", 1)[1].startswith('"Cited by ') for b in blocks)
    assert f"<{iri.k510('K033607')}>\n    a ex:K510Record ;" in ttl
    assert st["fallback_without_any_block"] == {"k510": 2, "pma": 3, "hde": 1, "regulation (CFR section exists)": 1,
                                                "pma supplement-number format": 1, "cfr-section": 1,
                                                "cfr-part": 1}, st
    print(f"reference_fallback.py self-test PASSED ({st}).")


if __name__ == "__main__":
    if len(sys.argv) == 1 or sys.argv[1:] == ["--selftest"]:
        _selftest()
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--date")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("inputs", nargs="+")
    a = ap.parse_args()
    st = build(a.inputs, a.out, date=a.date, workers=a.workers)
    for k, v in st.items():
        print(f"{k}: {v}")
