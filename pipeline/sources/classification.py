#!/usr/bin/env python3
"""
classification.py -- FDA product classification (openFDA device/classification) -> RDF.

This is the AUTHORITATIVE source for the ProductCode hub (rebuild-strategy 1b):
each record both creates an ex:ProductClassificationRecord and defines the
ex:ProductCode node it is keyed on (label, device class, regulation, specialty,
implant / life-sustaining flags). Regulation numbers become ex:RegulationNumber
nodes. On the base.py contract.
"""
import os
import sys
import json
import zipfile
import urllib.request
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "classification"
DATASET = "device/classification"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/classification/"


def fetch(dest):
    """Bulk-download every device/classification partition (no paging)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["classification"]["partitions"]
    files = [base.bulk_download(p["file"], dest) for p in partitions]
    return base.Manifest(source=NAME, dataset=DATASET,
                         retrieved_at=base.today(), files=files)


def _iter_records(src):
    src = Path(src)
    paths = sorted(set(src.glob("*.json")) | set(src.glob("*.zip")) | set(src.glob("*.json.zip")))
    for p in paths:
        if p.name.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.endswith(".json"):
                        for rec in json.loads(z.read(n)).get("results", []):
                            yield rec
        else:
            for rec in json.loads(p.read_text(encoding="utf-8")).get("results", []):
                yield rec


def transform(src, out, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    regs = set()
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            pc = str(r.get("product_code") or "").strip()
            if not pc:
                st.skipped += 1
                continue
            reg = r.get("regulation_number")

            # 1) the classification record
            rec_pairs = [
                ("a", "ex:ProductClassificationRecord"),
                ("ex:productCode", common.lit(pc)),
                ("ex:deviceName", common.lit(r.get("device_name"))),
                ("ex:deviceClass", common.lit(r.get("device_class"))),
                ("ex:medicalSpecialtyDescription", common.lit(r.get("medical_specialty_description"))),
                ("ex:reviewPanel", common.lit(r.get("review_panel"))),
                ("ex:definition", common.lit(r.get("definition"))),
                ("ex:submissionTypeId", common.lit(r.get("submission_type_id"))),
                ("ex:reviewCode", common.lit(r.get("review_code"))),
                ("ex:gmpExemptFlag", common.bool_lit(r.get("gmp_exempt_flag"))),
                ("ex:implantFlag", common.bool_lit(r.get("implant_flag"))),
                ("ex:lifeSustainSupportFlag", common.bool_lit(r.get("life_sustain_support_flag"))),
                ("ex:thirdPartyFlag", common.bool_lit(r.get("third_party_flag"))),
                ("ex:hasProductCode", f"<{iri.product_code(pc)}>"),
            ]
            if reg:
                rec_pairs.append(("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>"))
            for pred, obj in common.provenance(DATASET, pc,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                rec_pairs.append((pred, obj))
            w.block(iri.classification(pc), rec_pairs)

            # 2) the authoritative ProductCode hub node
            hub_pairs = [
                ("a", "ex:ProductCode"),
                ("rdfs:label", common.lit(r.get("device_name"))),
                ("ex:productCode", common.lit(pc)),
                ("ex:deviceClass", common.lit(r.get("device_class"))),
                ("ex:medicalSpecialtyDescription", common.lit(r.get("medical_specialty_description"))),
                ("ex:implantable", common.bool_lit(r.get("implant_flag"))),
                ("ex:lifeSustaining", common.bool_lit(r.get("life_sustain_support_flag"))),
                ("ex:hasClassification", f"<{iri.classification(pc)}>"),
            ]
            if reg:
                hub_pairs.append(("ex:hasRegulationNumber", f"<{iri.regulation(reg)}>"))
            w.block(iri.product_code(pc), hub_pairs)

            if reg:
                regs.add(str(reg))
            st.records += 1

        # 3) regulation nodes, deduped + sorted
        for reg in sorted(regs):
            w.block(iri.regulation(reg), [
                ("a", "ex:RegulationNumber"),
                ("rdfs:label", common.lit(reg)),
                ("ex:regulationNumber", common.lit(reg)),
            ])

    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=None)


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {"product_code": "FRN", "device_name": "Pump, Infusion", "device_class": "2",
         "regulation_number": "880.5725", "medical_specialty_description": "General Hospital",
         "review_panel": "HO", "implant_flag": "N", "life_sustain_support_flag": "N",
         "gmp_exempt_flag": "N", "definition": "An infusion pump is a device..."},
        {"product_code": "LWR", "device_name": "Valve, Replacement Heart", "device_class": "3",
         "regulation_number": "870.3925", "implant_flag": "Y", "life_sustain_support_flag": "Y"},
        {"device_name": "no code"},   # missing product_code -> skipped
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "classification.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 2 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.classification('FRN')}>" in text and "ex:ProductClassificationRecord" in text
    assert f"<{iri.product_code('FRN')}>" in text and 'rdfs:label "Pump, Infusion"' in text
    assert "ex:implantable \"true\"^^xsd:boolean" in text          # LWR implant_flag Y
    assert "ex:hasClassification" in text
    assert f"<{iri.regulation('880.5725')}>" in text and "ex:RegulationNumber" in text
    assert text.count("a ex:RegulationNumber") == 2               # two reg nodes, deduped
    assert "ex:sourceDataset \"device/classification\"" in text
    print(f"classification.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
