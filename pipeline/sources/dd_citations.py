#!/usr/bin/env python3
"""
dd_citations.py -- FDA Data Dashboard inspection citations (devices) -> RDF.

SOURCE
------
POST https://api-datadashboard.fda.gov/v1/inspections_citations (client and credential handling in
_ddapi.py). Field definitions: https://datadashboard.fda.gov/oii/api/api-definitions-citations.htm.
The endpoint has no ProductType field; its ProgramArea names the regulated area of the cited CFR/Act
provision. Scope filter: ProgramArea "Devices" (51,831 rows on 2026-11-04) plus "Radiologic Health"
(608 rows, radiation-emitting products, 21 CFR 1000-1050), fetched as two queries.

TWO NODE KINDS
--------------
  ex:InspectionCitation  one per (InspectionID, CitationID): the citation as recorded for one inspection,
                         -> ex:partOfInspection the dd_inspections node (iri: fda-inspection/<id>),
                         -> ex:citationReference the coded reference, end date, fiscal year, program area,
                         FEI (-> iri.manufacturer(fei=...)), long description as worded here.
  ex:CitationReference   one per CitationID: FDA's coded citation (Act/CFR number and short
                         description) -> ex:citesCfrSection iri.cfr_section('820.100') and ex:citesCfrPart
                         iri.cfr_part('820') when the number is a 21 CFR provision (paragraph letters such
                         as "(b)" are dropped for the link; the verbatim number stays in ex:actCfrNumber).
Individuals: the same rule as dd_inspections (_ddapi.person_context reads the cached inspection pages):
the strict name test applies to Bioresearch Monitoring inspections and to citations of 21 CFR 50/54/56/
58/812, and any FEI judged to be an individual there is withheld here too.
The long description is kept on the ex:InspectionCitation, because one CitationID carries several
wordings (434 of 784 ids on 2026-11-04, e.g. "Procedures for corrective and preventive action have not
been [adequately] established"); the Act/CFR number and short description never vary within an id.
It is FDA's wording about firm practices. It is run through scrub.scrub_text
anyway: text with no personal-data pattern stays in the main file; otherwise only the scrubbed copy is
written, to dd_citations_text.ttl.

Firms whose name reads as a person (_ddapi.is_person) keep no FEI or firm link on their citations.

Minters (iri.py, aliased in _ddapi): fda_inspection_citation(iid, cid) -> .../id/fda-inspection/<iid>/citation/<cid>;
                              fda_citation_reference(cid) -> .../id/fda-citation/<cid>
"""
import json
import os
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
import _ddapi as dd   # noqa: E402

NAME = "dd_citations"
DATASET = "fda-data-dashboard/inspections_citations"
ENDPOINT = "inspections_citations"
QUERIES = [("devices", {"ProgramArea": ["Devices"]}), ("radiologic", {"ProgramArea": ["Radiologic Health"]})]
SOURCE_URL = "https://datadashboard.fda.gov/oii/cd/inspections.htm"


def fetch(dest, log=print, client=None):
    return dd.fetch_endpoint(dest, ENDPOINT, QUERIES, NAME, DATASET, client=client, log=log)


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    retrieved_at = dd.retrieved_at_of(src, retrieved_at)
    st = base.Stats(source=NAME)
    refs, seen = {}, set()
    bimo, person_feis, ctx = dd.person_context(src)
    counts = {"rows": 0, "duplicate_rows": 0, "person_withheld": 0, "with_fei": 0, "person_context": ctx,
              "long_main": 0, "long_text": 0, "distinct_long_texts": 0}
    text_out = out.with_name(out.stem + "_text.ttl")
    scrubbed_cache = {}
    with base.TurtleWriter(out) as w, base.TurtleWriter(text_out) as wt:
        for _, r in dd.iter_rows(src):
            counts["rows"] += 1
            iid, cid = dd.s(r.get("InspectionID")), dd.s(r.get("CitationID"))
            if not iid or not cid:
                st.skipped += 1
                continue
            if (iid, cid) in seen:
                counts["duplicate_rows"] += 1
                continue
            seen.add((iid, cid))
            ref = refs.setdefault(cid, {"act": [], "short": []})
            for k, f in (("act", "ActCFRNumber"), ("short", "ShortDescription")):
                v = common.norm_text(r.get(f))
                if v and v not in ref[k]:
                    ref[k].append(v)
            strict = iid in bimo or bool(set(dd.cfr_refs(r.get("ActCFRNumber"))[1]) & dd.BIMO_PARTS)
            fei = dd.fei_of(r.get("FEINumber"))
            person = dd.is_person(r.get("LegalName"), strict) or fei in person_feis
            fei = None if person else fei
            counts["person_withheld"] += person
            counts["with_fei"] += bool(fei)
            act = common.norm_text(r.get("ActCFRNumber"))
            end = dd.s(r.get("InspectionEndDate"))
            pairs = [("a", "ex:InspectionCitation"),
                     ("rdfs:label", common.lit(f"Citation {cid}" + (f" ({act})" if act else "") +
                                               f" in inspection {iid}")),
                     ("ex:citationId", common.lit(cid)),
                     ("ex:inspectionId", common.lit(iid)),
                     ("ex:partOfInspection", f"<{dd.fda_inspection(iid)}>"),
                     ("ex:citationReference", f"<{dd.fda_citation_reference(cid)}>"),
                     ("ex:inspectionEndDate", common.date_lit(end)),
                     ("ex:fiscalYear", common.int_lit(dd.s(r.get("FiscalYear")))),
                     ("ex:programArea", common.lit(dd.s(r.get("ProgramArea")))),
                     ("ex:feiNumber", common.lit(fei)),
                     ("ex:hasManufacturer", f"<{iri.manufacturer(fei=fei)}>" if fei else None)]
            if person:
                pairs.append(("ex:dataQualityNote", common.lit(
                    "firm name reads as an individual's name; the FEI and firm link are not ingested")))
            # the long description is the wording recorded for THIS inspection (one CitationID carries
            # several wordings, e.g. "... not been established" / "... not been adequately established")
            long = common.norm_text(r.get("LongDescription"))
            if long:
                if long not in scrubbed_cache:
                    scrubbed_cache[long] = dd.split_text(long)
                main, scrubbed, _ = scrubbed_cache[long]
                if main:
                    pairs.append(("ex:citationLongDescription", common.lit(main)))
                    counts["long_main"] += 1
                elif scrubbed:
                    wt.block(dd.fda_inspection_citation(iid, cid), [("ex:citationLongDescription", common.lit(scrubbed))])
                    counts["long_text"] += 1
            pairs += common.provenance(DATASET, f"{iid}/{cid}", source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(dd.fda_inspection_citation(iid, cid), pairs)
            st.records += 1
        counts.update(references=len(refs), ref_multi_text=0, distinct_long_texts=len(scrubbed_cache),
                      distinct_long_texts_with_pattern=sum(1 for v in scrubbed_cache.values() if v[1]),
                      refs_with_cfr_section=0, refs_with_cfr_part_only=0, refs_not_21cfr=0)
        sections, parts = set(), set()
        for cid in sorted(refs, key=lambda k: (len(k), k)):
            ref = refs[cid]
            if any(len(v) > 1 for v in ref.values()):
                counts["ref_multi_text"] += 1
            pairs = [("a", "ex:CitationReference"),
                     ("rdfs:label", common.label(f"Citation {cid}", ref["act"][0] if ref["act"] else None,
                                                 ref["short"][0] if ref["short"] else None)),
                     ("ex:citationId", common.lit(cid))]
            secs, pts = [], []
            for a in ref["act"]:
                pairs.append(("ex:actCfrNumber", common.lit(a)))
                s_, p_ = dd.cfr_refs(a)
                secs += [x for x in s_ if x not in secs]
                pts += [x for x in p_ if x not in pts]
            for x in secs:
                pairs.append(("ex:citesCfrSection", f"<{iri.cfr_section(x)}>"))
            for x in pts:
                pairs.append(("ex:citesCfrPart", f"<{iri.cfr_part(x)}>"))
            sections |= set(secs)
            parts |= set(pts)
            counts["refs_with_cfr_section" if secs else "refs_with_cfr_part_only" if pts else "refs_not_21cfr"] += 1
            pairs += [("ex:citationShortDescription", common.lit(x)) for x in ref["short"]]
            pairs += common.provenance(DATASET, cid, source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(dd.fda_citation_reference(cid), pairs)
    st.triples = w.triples
    counts.update(records=st.records, triples=st.triples, text_triples=wt.triples,
                  distinct_cfr_sections=len(sections), distinct_cfr_parts=len(parts))
    (out.with_suffix(".counts.json")).write_text(json.dumps(counts, indent=1), encoding="utf-8")
    (out.with_suffix(".cfr_sections.txt")).write_text("\n".join(sorted(sections)) + "\n", encoding="utf-8")
    print(f"  {NAME}: {counts}")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
# Verbatim API rows (2026-11-04): two device citations of inspection 1227688 (firm fields as published
# for that program area are represented by a constructed firm), one Radiologic Health citation, a
# duplicate row, and a constructed clinical-investigator row (a person).
def _row(iid, fei, name, cid, act, short, long, area="Devices", date="2023-05-04", fy="2023"):
    return {"InspectionID": iid, "FEINumber": fei, "LegalName": name, "AddressLine1": "1 Main St",
            "AddressLine2": None, "City": "Anytown", "StateCode": "MN", "State": "Minnesota", "ZipCode": "55001",
            "CountryCode": "US", "CountryName": "United States", "InspectionEndDate": date, "FiscalYear": fy,
            "ProgramArea": area, "CitationID": cid, "ActCFRNumber": act, "ShortDescription": short,
            "LongDescription": long,
            "FirmProfile": f"https://datadashboard.fda.gov/oii/firmprofile.htm?FEIi={fei}"}


_ROWS = [
    _row("1227688", "3003456789", "Acme Medical Inc.", "3696", "21 CFR 820.100(b)", "Documentation",
         "Corrective and preventive action activities and/or results have not been [adequately] documented."),
    _row("1227688", "3003456789", "Acme Medical Inc.", "14713", "21 CFR 820.198(a)", "Lack of or inadequate complaint procedures",
         "Procedures for receiving, reviewing, and evaluating complaints by a formally designated unit have not been [adequately] established."),
    _row("1227688", "3003456789", "Acme Medical Inc.", "3696", "21 CFR 820.100(b)", "Documentation",
         "Corrective and preventive action activities and/or results have not been [adequately] documented."),
    _row("666784", "3001111111", "Laser Works LLC", "5034", "21 CFR 1010.2(c)", "Certification label",
         "The certification label was not legible.", area="Radiologic Health"),
    _row("1100001", "3009999999", "Jane Q. Doe, M.D.", "999", "FD&C Act 520(g)", "IDE",
         "CALL JANE DOE AT 555-123-4567 ABOUT THE STUDY."),
    _row("1100002", "3008888888", "Richard Roe", "998", "21 CFR 812.100", "General responsibilities",
         "Failure to ensure that an investigation is conducted according to the signed agreement."),
]


def _selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / "devices_s0000001.json").write_text(json.dumps({"result": _ROWS[:3] + _ROWS[4:]}), encoding="utf-8")
        (t / "pages" / "radiologic_s0000001.json").write_text(json.dumps({"result": _ROWS[3:4]}), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-04", files=[]).write(t / "manifest.json")
        st = transform(t, t / "dd_citations.ttl")
        ttl = (t / "dd_citations.ttl").read_text(encoding="utf-8")
        txt = (t / "dd_citations_text.ttl").read_text(encoding="utf-8")
        counts = json.loads((t / "dd_citations.counts.json").read_text(encoding="utf-8"))
    assert st.records == 5 and counts["duplicate_rows"] == 1, (st, counts)
    assert "3008888888" not in ttl and counts["person_withheld"] == 2
    c = ttl.split(f"<{dd.fda_inspection_citation('1227688', '3696')}>")[1].split("\n\n")[0]
    assert f"ex:partOfInspection <{dd.fda_inspection('1227688')}>" in c
    assert f"ex:citationReference <{dd.fda_citation_reference('3696')}>" in c
    assert f"ex:hasManufacturer <{iri.manufacturer(fei='3003456789')}>" in c
    assert 'rdfs:label "Citation 3696 (21 CFR 820.100(b)) in inspection 1227688"' in c
    ref = ttl.split(f"<{dd.fda_citation_reference('3696')}>\n")[1].split("\n\n")[0]
    assert "a ex:CitationReference" in ref and f"ex:citesCfrSection <{iri.cfr_section('820.100')}>" in ref
    assert f"ex:citesCfrPart <{iri.cfr_part('820')}>" in ref and 'ex:actCfrNumber "21 CFR 820.100(b)"' in ref
    assert "ex:citationLongDescription \"Corrective and preventive action" in c and "LongDescription" not in ref
    rad = ttl.split(f"<{dd.fda_citation_reference('5034')}>\n")[1].split("\n\n")[0]
    assert f"<{iri.cfr_section('1010.2')}>" in rad
    fd = ttl.split(f"<{dd.fda_citation_reference('999')}>\n")[1].split("\n\n")[0]
    assert "citesCfr" not in fd and "citationLongDescription" not in fd
    assert "[NAME REMOVED]" in txt and "[PHONE REMOVED]" in txt
    for x in ("Doe", "DOE", "555-123", "3009999999"):
        assert x not in ttl and x not in txt, x
    assert counts["long_text"] == 1 and counts["refs_not_21cfr"] == 1
    print(f"dd_citations.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    dd._selftest()
    _selftest()
