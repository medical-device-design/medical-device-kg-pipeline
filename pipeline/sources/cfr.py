#!/usr/bin/env python3
"""
cfr.py -- 21 CFR (every part of title 21) -> RDF. Device and radiological-health parts
(800-898, 1000-1050) carry ex:deviceRegulationPart true, so device queries can stay scoped.

Source: the govinfo eCFR BULK XML for title 21, regenerated daily
    https://www.govinfo.gov/bulkdata/ECFR/title-21/ECFR-title21.xml   (~22 MB)
(The eCFR "full" API answered 406 for every Accept header on 2026-10-09; the
bulk file is the documented, rate-limit-free route anyway.)

Why it matters (action plan A4): the device-classification parts 862-892 state,
for every regulation, the device IDENTIFICATION and its CLASSIFICATION ("Class II
(special controls)"). Linking ex:RegulationNumber -> ex:CFRTitle21Section turns
"product code FRN -> regulation 880.5725" into the legal definition and class of
the device, which is the bridge from a regulatory record to a design requirement.

Emits:
    ex:CfrPart              iri.cfr_part('878')        label = part heading
    ex:CFRTitle21Section    iri.cfr_section('878.3530')
        ex:sectionNumber, rdfs:label, ex:inCfrPart, ex:identificationText,
        ex:regulatoryClass ("1"/"2"/"3", may repeat), ex:regulationText, ex:cfrCitation
    ex:RegulationNumber     iri.regulation('878.3530')  ex:hasCFRSection -> section
"""
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "cfr"
DATASET = "ecfr/title-21"
BULK_URL = "https://www.govinfo.gov/bulkdata/ECFR/title-21/ECFR-title21.xml"
SECTION_URL = "https://www.ecfr.gov/current/title-21/section-{}"
PART_RANGES = ((800, 898), (1000, 1050))      # the device and radiological-health parts
ALL_PARTS = True          # PI decision 2026-10-10: ingest every part of title 21; device parts are flagged
_SEC_NUM = re.compile(r"(\d+\.\d+[a-z]?)")
_CLASS = re.compile(r"\bClass\s+(III|II|I)\b")
_ROMAN = {"I": "1", "II": "2", "III": "3"}
MAX_TEXT = None           # full regulation text (PI decision 2026-10-09: no truncation)


def is_device_part(part):
    try:
        p = int(part)
    except (TypeError, ValueError):
        return False
    return any(lo <= p <= hi for lo, hi in PART_RANGES)


def in_scope(part):
    if ALL_PARTS:
        return bool(str(part or "").strip())
    return is_device_part(part)


def fetch(dest):
    f = base.bulk_download(BULK_URL, dest)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[f])


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def parse_section(div8):
    """-> dict for one DIV8 SECTION element, or None if it is not a numbered section."""
    head = div8.find("HEAD")
    head_txt = _text(head) if head is not None else ""
    m = _SEC_NUM.search(div8.get("N", "") or head_txt)
    if not m:
        return None
    num = m.group(1)
    title = re.sub(r"^§+\s*" + re.escape(num) + r"\s*", "", head_txt).strip().rstrip(".")
    paras = [_text(p) for p in div8.findall("P")]
    ident = next((p for p in paras if re.match(r"\(a\)\s*Identification", p)), None)
    cls_par = next((p for p in paras if re.match(r"\(b\)\s*Classification", p)), None)
    classes = sorted({_ROMAN[c] for c in _CLASS.findall(cls_par or "")})
    cita = div8.find("CITA")
    return {
        "number": num,
        "title": title,
        "identification": re.sub(r"^\(a\)\s*Identification\.?\s*[—-]?\s*", "", ident) if ident else None,
        "classes": classes,
        "classification_text": cls_par,
        "text": ("\n".join(paras)[:MAX_TEXT] if MAX_TEXT else "\n".join(paras)) if paras else None,
        "citation": _text(cita) if cita is not None else None,
        "reserved": "[Reserved]" in head_txt,
    }


def iter_parts(xml_path):
    """Stream (part_number, part_heading, [section dicts]) for in-scope parts only."""
    for _, el in ET.iterparse(str(xml_path), events=("end",)):
        if el.tag != "DIV5":
            continue
        part = (el.get("N") or "").strip()
        if in_scope(part):
            head = el.find("HEAD")
            secs = [s for s in (parse_section(d) for d in el.iter("DIV8")) if s]
            yield part, (_text(head) if head is not None else ""), secs
        el.clear()


def transform(src, out, retrieved_at=None):
    src = Path(src)
    man = src / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    xml_path = next(iter(sorted(src.glob("*.xml"))))
    st = base.Stats(source=NAME)
    with base.TurtleWriter(out) as w:
        for part, heading, secs in iter_parts(xml_path):
            dev = '"true"^^xsd:boolean' if is_device_part(part) else '"false"^^xsd:boolean'
            w.block(iri.cfr_part(part), [
                ("a", "ex:CfrPart"),
                ("rdfs:label", common.lit(re.sub(r"^PART\s+\d+\s*[—-]*\s*", "", heading, flags=re.I) or heading)),
                ("ex:cfrPartNumber", common.lit(part)),
                ("ex:deviceRegulationPart", dev),
            ])
            for s in secs:
                if s["reserved"]:
                    st.skipped += 1
                    continue
                num = s["number"]
                pairs = [
                    ("a", "ex:CFRTitle21Section"),
                    ("rdfs:label", common.lit(s["title"])),
                    ("ex:sectionNumber", common.lit(num)),
                    ("ex:inCfrPart", f"<{iri.cfr_part(part)}>"),
                    ("ex:identificationText", common.lit(s["identification"])),
                    ("ex:classificationText", common.lit(s["classification_text"])),
                    ("ex:regulationText", common.lit(s["text"])),
                    ("ex:cfrCitation", common.lit(s["citation"])),
                ]
                pairs += [("ex:regulatoryClass", common.lit(c)) for c in s["classes"]]
                pairs += common.provenance(DATASET, num, source_url=SECTION_URL.format(num),
                                           retrieved_at=retrieved_at)
                w.block(iri.cfr_section(num), pairs)
                # the bridge: product codes already point at RegulationNumber nodes
                if s["identification"] or s["classes"]:
                    w.block(iri.regulation(num), [
                        ("a", "ex:RegulationNumber"),
                        ("ex:regulationNumber", common.lit(num)),
                        ("rdfs:label", common.lit(s["title"])),
                        ("ex:hasCFRSection", f"<{iri.cfr_section(num)}>"),
                    ])
                st.records += 1
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    xml = """<?xml version="1.0" encoding="UTF-8" ?><DLPSTEXTCLASS><TEXT><BODY><ECFRBRWS>
<DIV5 N="878" TYPE="PART"><HEAD>PART 878—GENERAL AND PLASTIC SURGERY DEVICES</HEAD>
<DIV8 N="§ 878.3530" TYPE="SECTION">
<HEAD>§ 878.3530   Silicone inflatable breast prosthesis.</HEAD>
<P>(a) <I>Identification.</I> A silicone inflatable breast prosthesis is a silicone rubber shell.</P>
<P>(b) <I>Classification.</I> Class III. </P>
<CITA TYPE="N">[53 FR 23872, June 24, 1988]</CITA></DIV8>
<DIV8 N="§ 878.4040" TYPE="SECTION"><HEAD>§ 878.4040   Surgical apparel.</HEAD>
<P>(a) <I>Identification.</I> Surgical apparel are devices worn in the operating room.</P>
<P>(b) <I>Classification.</I> (1) Class II (special controls) for gowns. (2) Class I (general controls) for others.</P></DIV8>
<DIV8 N="§ 878.9" TYPE="SECTION"><HEAD>§ 878.9   [Reserved]</HEAD></DIV8>
</DIV5>
<DIV5 N="101" TYPE="PART"><HEAD>PART 101—FOOD LABELING</HEAD>
<DIV8 N="§ 101.1" TYPE="SECTION"><HEAD>§ 101.1 Principal display panel.</HEAD><P>x</P></DIV8></DIV5>
</ECFRBRWS></BODY></TEXT></DLPSTEXTCLASS>"""
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "ECFR-title21.xml").write_text(xml, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-09", files=[]).write(d / "manifest.json")
        st = transform(d, d / "cfr.ttl")
        ttl = (d / "cfr.ttl").read_text(encoding="utf-8")
    assert st.records == 3 and st.skipped == 1, st                  # reserved skipped; food part now kept
    assert f"<{iri.cfr_section('878.3530')}>" in ttl and f"<{iri.cfr_section('101.1')}>" in ttl
    food = ttl.split(f"<{iri.cfr_part('101')}>\n", 1)[1].split("\n\n")[0]
    dev = ttl.split(f"<{iri.cfr_part('878')}>\n", 1)[1].split("\n\n")[0]
    assert 'ex:deviceRegulationPart "false"^^xsd:boolean' in food and '"true"^^xsd:boolean' in dev
    assert f"<{iri.regulation('101.1')}>" not in ttl                 # no RegulationNumber without identification/class
    assert 'ex:regulatoryClass "3"' in ttl
    assert 'ex:regulatoryClass "1"' in ttl and 'ex:regulatoryClass "2"' in ttl   # mixed-class section
    assert f"ex:hasCFRSection <{iri.cfr_section('878.4040')}>" in ttl
    assert 'rdfs:label "Silicone inflatable breast prosthesis"' in ttl
    assert 'ex:identificationText "A silicone inflatable breast prosthesis is a silicone rubber shell."' in ttl
    assert 'rdfs:label "GENERAL AND PLASTIC SURGERY DEVICES"' in ttl
    print(f"cfr.py self-test PASSED ({st.records} sections, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
