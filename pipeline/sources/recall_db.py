#!/usr/bin/env python3
"""
recall_db.py -- FDA Medical Device Recall database (openFDA device/recall) -> RDF.

WHY A SECOND RECALL SOURCE
--------------------------
The graph's RecallRecords came only from device/enforcement (~40K weekly
enforcement-report entries). The recall DATABASE (device/recall, 59,273 records,
verified on api.fda.gov 2026-10-02, last_updated 2026-09-24) is larger, starts
earlier, and carries four things the enforcement feed does not:

  * firm_fei_number      -> an EXACT identity join to FEI-keyed establishments
                            (confidence 1.0, no name matching needed)
  * root_cause_description  FDA's assigned cause category (design, software, ...)
  * event_date_terminated / event_date_posted  -> full recall lifecycle
  * action               -> what the firm told customers to do

Its product_res_number ("Z-0001-04") is the same key as the enforcement feed's
recall_number, so both sources land on the SAME iri.recall() node and enrich it.
Product codes arrive padded ("IZL--"); they are cleaned before minting.
"""
import os
import re
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
from distribution_parser import distribution_pairs   # noqa: E402
from identity import normalize_org                    # noqa: E402

NAME = "recall_db"
DATASET = "device/recall"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
CODE_INFO_CAP = None      # keep the full lot/serial text (PI decision 2026-10-09: no data loss)
SOURCE_URL = "https://open.fda.gov/apis/device/recall/"
_PC = re.compile(r"[A-Z]{3}")


def fetch(dest):
    """Bulk-download every device/recall partition (no paging)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["recall"]["partitions"]
    files = [base.bulk_download(p["file"], dest) for p in partitions]
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def _iter_records(src):
    """Stream records one at a time (base.iter_openfda_results; ijson if installed)."""
    yield from base.iter_openfda_results(src)


def clean_product_code(raw):
    """'IZL--' -> 'IZL'; returns None if no 3-letter code is present."""
    m = _PC.search(str(raw or "").upper())
    return m.group(0) if m else None


def transform(src, out, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    events, firms, hub_codes = {}, {}, set()
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            rn = str(r.get("product_res_number") or "").strip()
            if not rn:
                st.skipped += 1
                continue
            of = r.get("openfda") or {}
            pairs = [
                ("a", "ex:RecallRecord"),
                ("rdfs:label", common.label(f"Recall {rn}", r.get("recalling_firm"))),
                ("ex:recallNumber", common.lit(rn)),
                ("ex:recallStatus", common.lit(r.get("recall_status"))),
                # whitespace-normalized so the text matches the enforcement feed's copy
                ("ex:reasonForRecall", common.lit(common.norm_text(r.get("reason_for_recall")))),
                ("ex:rootCauseDescription", common.lit(r.get("root_cause_description"))),
                ("ex:recallAction", common.lit(common.norm_text(r.get("action")))),
                ("ex:recallingFirm", common.lit(common.norm_text(r.get("recalling_firm")))),
                ("ex:productDescription", common.lit(common.norm_text(r.get("product_description")))),
                ("ex:productQuantity", common.lit(r.get("product_quantity"))),
                ("ex:distributionPattern", common.lit(r.get("distribution_pattern"))),
                # same predicate as recalls.py so one query covers both sources
                ("ex:recallInitiationDate", common.date_lit(r.get("event_date_initiated"))),
                ("ex:eventDatePosted", common.date_lit(r.get("event_date_posted"))),
                ("ex:terminationDate", common.date_lit(r.get("event_date_terminated"))),
                ("ex:firmFEINumber", common.lit(r.get("firm_fei_number"))),
                ("ex:cfresId", common.lit(r.get("cfres_id"))),
                ("ex:city", common.lit(r.get("city"))),
                ("ex:state", common.lit(r.get("state"))),
                ("ex:postalCode", common.lit(r.get("postal_code"))),
                ("ex:country", common.lit(r.get("country"))),
                ("ex:address", common.lit(common.norm_address(r.get("address_1"), r.get("address_2")))),
                ("ex:eventDateCreated", common.date_lit(r.get("event_date_created"))),
                # additional_info_contact names a person -> deliberately not ingested
            ]
            pairs += common.implausible_year_pairs(r.get("event_date_initiated"), "recall initiation date")
            # code_info (lots/serials): kept in full unless CODE_INFO_CAP is set
            pairs += common.capped_pairs("ex:codeInfo", common.norm_text(r.get("code_info")), CODE_INFO_CAP)
            # barcode path: GS1 device identifiers FDA quotes in the lot text -> UDI identifier nodes
            for di in common.gs1_dis(r.get("code_info")):
                pairs.append(("ex:quotedDeviceIdentifier", common.lit(di)))
                pairs.append(("ex:mentionsDeviceIdentifier", f"<{iri.udi_identifier('GS1', di)}>"))
            pairs += distribution_pairs(r.get("distribution_pattern"))

            ev = str(r.get("res_event_number") or "").strip()
            if ev:
                pairs.append(("ex:partOfRecallEvent", f"<{iri.recall_event(ev)}>"))
                events.setdefault(ev, (r.get("event_date_initiated"), r.get("recall_status")))

            # EXACT identity: the FEI joins straight to the establishment node
            fei = re.sub(r"\D", "", str(r.get("firm_fei_number") or ""))
            if fei:
                pairs.append(("ex:hasRecallingManufacturer", f"<{iri.manufacturer(fei=fei)}>"))
            nf = normalize_org(r.get("recalling_firm"))
            if nf:
                pairs.append(("ex:hasRecallingFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, (r.get("recalling_firm"), fei))

            pcs = {clean_product_code(r.get("product_code"))}
            pcs |= {clean_product_code(x) for x in common.as_list(of.get("product_code"))}
            for pc in sorted(p for p in pcs if p):
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                hub_codes.add(pc)
            for kn in common.as_list(r.get("k_numbers")):
                pairs.append(("ex:relatedK510Record", f"<{iri.k510(kn)}>"))
            for pn in common.as_list(r.get("pma_numbers")):
                pairs.append(("ex:relatedPMARecord", f"<{iri.pma(pn)}>"))

            pairs += common.provenance(DATASET, rn, source_url=SOURCE_URL,
                                       retrieved_at=retrieved_at)
            w.block(iri.recall(rn), pairs)
            st.records += 1

        for ev in sorted(events):
            started, status = events[ev]
            w.block(iri.recall_event(ev), [
                ("a", "ex:RecallEvent"),
                ("rdfs:label", common.lit(f"Recall event {ev}")),
                ("ex:recallEventNumber", common.lit(ev)),
                ("ex:recallInitiationDate", common.date_lit(started)),
                ("ex:recallStatus", common.lit(status)),
            ])
        # Name node -> FEI node with an exact, reified identity mapping
        for nf in sorted(firms):
            raw, fei = firms[nf]
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, raw))
            if fei:
                subj, obj = iri.firm_name(nf), iri.manufacturer(fei=fei)
                w.block(iri.identity_mapping(subj, obj, "shared-fei"), [
                    ("a", "ex:IdentityMapping"),
                    ("rdfs:label", common.lit(f"{nf} = FEI {fei}")),
                    ("ex:mappingSubject", f"<{subj}>"),
                    ("ex:mappingObject", f"<{obj}>"),
                    ("ex:mappingMethod", common.lit("shared-fei")),
                    ("ex:mappingConfidence", common.float_lit(1.0)),
                    ("ex:mappingStatus", common.lit("accepted")),
                    ("ex:mappingEvidence", common.lit(f"FEI {fei} on FDA recall record")),
                    ("ex:mappingDate", common.date_lit(retrieved_at)),
                ])
        # minimal typed hub nodes, so every product-code link reaches a node that
        # carries its code (classification adds the name and class where FDA lists it)
        for pc in sorted(hub_codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])

    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(
        os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {   # verbatim shape of the first record returned by api.fda.gov/device/recall
            "cfres_id": "30043", "product_res_number": "Z-0001-04",
            "event_date_initiated": "2003-10-27", "event_date_posted": "2003-10-29",
            "recall_status": "Terminated", "event_date_terminated": "2004-03-24",
            "res_event_number": "27549", "product_code": "IZL--", "k_numbers": ["K020436"],
            "product_description": "Sedecal SP-HF 4.0 Portable X-Ray System",
            "code_info": "serial #PK10058; UDI-DI 10197344133253, lot 12345678901234",
            "firm_fei_number": "1000139935",
            "recalling_firm": "Sedecal USA, Inc", "city": "Arlington Heights",
            "state": "IL", "postal_code": "60004-1551",
            "reason_for_recall": "Non-compliance with performance standard.",
            "root_cause_description": "Other", "action": "Important Safety Notice sent.",
            "product_quantity": "1 unit", "distribution_pattern": "Vermont", "openfda": {}},
        {"reason_for_recall": "no res number"},
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                      files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "recall_db.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 1 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.recall('Z-0001-04')}>" in text            # SAME node as enforcement
    assert f"ex:mentionsDeviceIdentifier <{iri.udi_identifier('GS1', '10197344133253')}>" in text
    assert 'ex:quotedDeviceIdentifier "10197344133253"' in text and "12345678901234\"" not in text.split("codeInfo")[0]
    assert f"<{iri.product_code('IZL')}>" in text              # "IZL--" cleaned
    assert 'ex:distributedToState "VT"' in text                 # full name parsed
    assert '"2004-03-24"^^xsd:date' in text                     # lifecycle typed
    assert 'ex:rootCauseDescription "Other"' in text
    assert f"<{iri.manufacturer(fei='1000139935')}>" in text    # exact identity join
    assert f"<{iri.recall_event('27549')}>" in text and "a ex:RecallEvent" in text
    assert "a ex:IdentityMapping" in text and 'ex:mappingMethod "shared-fei"' in text
    assert "sameAs" not in text
    assert clean_product_code("IZL--") == "IZL" and clean_product_code("") is None
    assert 'rdfs:label "Recall Z-0001-04: Sedecal USA, Inc"' in text
    assert 'rdfs:label "Recall event 27549"' in text
    assert f"<{iri.product_code('IZL')}>\n    a ex:ProductCode" in text   # hub stub, no dangling link
    assert text.count("a ex:FirmName") == 1 and "skos:altLabel" in text
    print(f"recall_db.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
