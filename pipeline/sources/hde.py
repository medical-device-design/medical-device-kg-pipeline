#!/usr/bin/env python3
"""
hde.py -- Humanitarian Device Exemptions (CDRH ftparea hde.zip) -> RDF.

HDE has no openFDA endpoint. FDA publishes a pipe-delimited flat file, refreshed
weekly (Last-Modified 2026-10-05 when checked 2026-10-09):
    https://www.accessdata.fda.gov/premarket/ftparea/hde.zip  -> hde.txt
Header (verified 2026-10-09):
    HDENUMBER|SUPPLEMENTNUMBER|APPLICANT|STREET_1|STREET_2|CITY|STATE|ZIP|ZIP_EXT|
    TRADENAME|GENERICNAME|PRODUCTCODE|ADVISORYCOMMITTEE|SUPPLEMENTTYPE|
    SUPPLEMENTREASON|REVIEWGRANTEDYN|DATERECEIVED|DECISIONDATE|DOCKETNUMBER|
    DECISIONCODE|FEDREGNOTICEDATE|AOSTATEMENT

Modeled exactly like PMA: the original HDE is iri.hde(number); each supplement
nests under it as iri.hde(number, supplement) with ex:supplementOf. Dates arrive
MM/DD/YYYY and are typed by common.date_lit.
"""
import os
import sys
import csv
import io
import zipfile
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
from identity import normalize_org   # noqa: E402

NAME = "hde"
DATASET = "cdrh/hde"
BULK_URL = "https://www.accessdata.fda.gov/premarket/ftparea/hde.zip"
SOURCE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfHDE/hde.cfm"
EXPECTED = ["HDENUMBER", "SUPPLEMENTNUMBER", "APPLICANT", "PRODUCTCODE", "DECISIONDATE"]


def fetch(dest):
    f = base.bulk_download(BULK_URL, dest)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[f])


def _rows(src):
    src = Path(src)
    for p in sorted(src.glob("*.zip")) + sorted(src.glob("*.txt")):
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    if n.lower().endswith(".txt"):
                        yield from _parse(z.read(n).decode("latin-1"))
        else:
            yield from _parse(p.read_text(encoding="latin-1"))


def _parse(text):
    rdr = csv.DictReader(io.StringIO(text), delimiter="|", quoting=csv.QUOTE_NONE)
    missing = [c for c in EXPECTED if c not in (rdr.fieldnames or [])]
    if missing:
        # schema drift must stop the build, not silently produce empty triples
        raise ValueError(f"hde.txt header changed; missing {missing}")
    for r in rdr:
        yield {k: (v or "").strip() for k, v in r.items() if k}


def transform(src, out, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    rows = list(_rows(src))
    # deterministic: originals first, then supplements, sorted
    rows.sort(key=lambda r: (r.get("HDENUMBER", ""), r.get("SUPPLEMENTNUMBER", "")))
    bases_written, firms, hub_codes = set(), {}, set()
    with base.TurtleWriter(out) as w:
        for r in rows:
            num, supp = r.get("HDENUMBER"), r.get("SUPPLEMENTNUMBER")
            if not num:
                st.skipped += 1
                continue
            pc = common.product_code(r.get("PRODUCTCODE"))
            if pc:
                hub_codes.add(pc)
            nf = normalize_org(r.get("APPLICANT"))
            pairs = [
                ("a", "ex:HumanitarianDeviceExemption"),
                ("ex:hdeNumber", common.lit(num)),
                ("ex:supplementNumber", common.lit(supp) if supp else None),
                ("ex:applicant", common.lit(r.get("APPLICANT"))),
                ("ex:tradeName", common.lit(r.get("TRADENAME"))),
                ("rdfs:label", common.lit(r.get("TRADENAME") or r.get("GENERICNAME"))),
                ("ex:genericName", common.lit(r.get("GENERICNAME"))),
                ("ex:productCode", common.lit(pc)),
                ("ex:advisoryCommittee", common.lit(r.get("ADVISORYCOMMITTEE"))),
                ("ex:supplementType", common.lit(r.get("SUPPLEMENTTYPE"))),
                ("ex:supplementReason", common.lit(r.get("SUPPLEMENTREASON"))),
                ("ex:reviewGranted", common.bool_lit(r.get("REVIEWGRANTEDYN"))),
                ("ex:dateReceived", common.date_lit(r.get("DATERECEIVED"))),
                ("ex:decisionDate", common.date_lit(r.get("DECISIONDATE"))),
                ("ex:decisionCode", common.lit(r.get("DECISIONCODE"))),
                ("ex:docketNumber", common.lit(r.get("DOCKETNUMBER"))),
                ("ex:fedRegNoticeDate", common.date_lit(r.get("FEDREGNOTICEDATE"))),
                ("ex:approvalOrderStatement", common.lit(r.get("AOSTATEMENT"))),
                ("ex:city", common.lit(r.get("CITY"))),
                ("ex:state", common.lit(r.get("STATE"))),
                ("ex:postalCode", common.lit(r.get("ZIP"))),
            ]
            if pc:
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
            if nf:
                pairs.append(("ex:hasApplicantFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, r.get("APPLICANT"))
            if supp:
                pairs.append(("ex:supplementOf", f"<{iri.hde(num)}>"))
                subj = iri.hde(num, supp)
                rec_id = f"{num}/{supp}"
            else:
                subj = iri.hde(num)
                rec_id = num
                bases_written.add(num)
            pairs += common.provenance(DATASET, rec_id, source_url=SOURCE_URL,
                                       retrieved_at=retrieved_at)
            w.block(subj, pairs)
            st.records += 1

        # an HDE that appears only through supplements still needs its base node,
        # or ex:supplementOf would point at a node with no type (an orphan)
        for num in sorted({r["HDENUMBER"] for r in rows if r.get("HDENUMBER")} - bases_written):
            w.block(iri.hde(num), [("a", "ex:HumanitarianDeviceExemption"),
                                   ("rdfs:label", common.lit(f"HDE {num}")),
                                   ("ex:hdeNumber", common.lit(num))])

        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        # minimal typed hub nodes so product-code links never dangle
        for pc in sorted(hub_codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    hdr = ("HDENUMBER|SUPPLEMENTNUMBER|APPLICANT|STREET_1|STREET_2|CITY|STATE|ZIP|ZIP_EXT|"
           "TRADENAME|GENERICNAME|PRODUCTCODE|ADVISORYCOMMITTEE|SUPPLEMENTTYPE|"
           "SUPPLEMENTREASON|REVIEWGRANTEDYN|DATERECEIVED|DECISIONDATE|DOCKETNUMBER|"
           "DECISIONCODE|FEDREGNOTICEDATE|AOSTATEMENT")
    rows = [  # first line verbatim from hde.txt, 2026-10-09
        "H000001|S014|Abbott Vascular Devices|26531 Ynez Rd.||Temecula|CA|92591||GRAFT MASTER RX "
        "CORONARY STENT GRAFT SYSTM|coronary covered stent|NIV|CV|Normal 75 Day Track|Labeling Change"
        " - Indications/instructions/shelf life/tradename|N|12/06/2016|02/17/2017||APPR||Approval for "
        "updates to the device label",
        "H120005||Kaneka Pharma America, LLC|546 Fifth Ave.||New York|NY|10036||KANEKA LIPOSORBER|"
        "Apheresis|PBN|GU|||N|01/02/2012|10/10/2013||APPR||Approval",
        "|S001|No number|||||||||||||||||||",
    ]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "hde.txt").write_text("\n".join([hdr] + rows), encoding="latin-1")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-09", files=[]).write(
            Path(d) / "manifest.json")
        out = Path(d) / "hde.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")
    assert st.records == 2 and st.skipped == 1, st
    assert f"<{iri.hde('H000001', 'S014')}>" in text and "ex:supplementOf" in text
    assert f"<{iri.hde('H000001')}>" in text                       # base node backfilled
    assert '"2017-02-17"^^xsd:date' in text                          # MM/DD/YYYY typed
    assert f"<{iri.product_code('NIV')}>" in text
    assert f"<{iri.firm_name('KANEKA PHARMA AMERICA')}>" in text
    assert 'ex:reviewGranted "false"^^xsd:boolean' in text
    assert 'rdfs:label "HDE H000001"' in text                        # backfilled base is labelled
    assert f"<{iri.product_code('NIV')}>\n    a ex:ProductCode" in text  # hub stub
    try:
        list(_parse("A|B\n1|2"))
    except ValueError:
        pass
    else:
        raise AssertionError("schema drift not detected")
    print(f"hde.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
