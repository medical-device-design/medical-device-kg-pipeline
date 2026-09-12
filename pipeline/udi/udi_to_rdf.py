#!/usr/bin/env python3
"""
udi_to_rdf.py -- convert openFDA AccessGUDID (UDI) bulk partitions to Turtle.

    python3 udi_to_rdf.py --input downloads/udi --output out/udi.ttl
    python3 udi_to_rdf.py --input downloads/udi --output out/smoke.ttl --limit 500

Reads .json.zip partitions in place (no separate decompression step) and streams
Turtle out, so peak memory is one partition rather than the whole 5.2M-record set.

WHY UDI MATTERS
---------------
This is the only FDA source describing how marketed devices are physically built:
sterilisation method, single-use, MRI safety, latex, storage temperature, kit and
combination-product status. It is also the only endpoint that simultaneously carries
GMDN terms, FDA product codes, premarket submission numbers and a DUNS number, which
makes it the bridge between the regulatory records and the physical device.

IRIs
----
Every IRI here is minted by iri.py -- the single source of truth. This converter
does NOT build IRI strings itself. That is what guarantees the joins below land on
the same nodes the other source converters mint:

    device -> product code    iri.product_code(...)
    device -> 510(k)/De Novo  iri.k510(...)     (relatedK510Record)
    device -> PMA             iri.pma(...)       (relatedPMARecord)

To move namespaces, edit iri.py -- never this file.
"""

import argparse
import json
import os
import sys
import zipfile
from datetime import datetime

# iri.py and common.py live one directory up, in pipeline/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import iri  # noqa: E402  -- the ONLY source of IRIs
from common import (  # noqa: E402  -- shared literal / provenance / serialization
    lit, bool_lit, date_lit, int_lit, as_list, write_block, provenance,
)

PREFIXES = f"""\
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix ex:   <{iri.NS}> .
"""

SOURCE_DATASET = "openFDA device/udi (AccessGUDID)"
SOURCE_URL_BASE = "https://accessgudid.nlm.nih.gov/devices/"

def ref(iri_string):
    """Wrap a full IRI (from iri.py) as a Turtle IRI reference."""
    return f"<{iri_string}>"


# --------------------------------------------------------------------------
# Record conversion
# --------------------------------------------------------------------------
def convert_record(rec, out, seen):
    """Emit all triples for one UDI record. Returns triple count."""
    key = rec.get("public_device_record_key")
    if not key:
        return 0

    dev = ref(iri.udi_device(key))
    n = 0

    # ---- the device record ------------------------------------------------
    pairs = [
        ("rdf:type", "ex:UDIDeviceRecord"),
        ("rdfs:label", lit(rec.get("brand_name"))),
        ("ex:brandName", lit(rec.get("brand_name"))),
        ("ex:companyName", lit(rec.get("company_name"))),
        ("ex:versionModelNumber", lit(rec.get("version_or_model_number"))),
        ("ex:catalogNumber", lit(rec.get("catalog_number"))),
        ("ex:deviceDescription", lit(rec.get("device_description"))),
        ("ex:publicDeviceRecordKey", lit(key)),
        ("ex:devicePublishDate", date_lit(rec.get("publish_date"))),
        ("ex:publicVersionDate", date_lit(rec.get("public_version_date"))),
        ("ex:recordStatus", lit(rec.get("record_status"))),
        ("ex:commercialDistributionStatus",
         lit(rec.get("commercial_distribution_status"))),
        ("ex:deviceCountInBasePackage",
         int_lit(rec.get("device_count_in_base_package"))),

        # --- design-relevant attributes: the reason this source matters -----
        ("ex:isRx", bool_lit(rec.get("is_rx"))),
        ("ex:isOTC", bool_lit(rec.get("is_otc"))),
        ("ex:isSingleUse", bool_lit(rec.get("is_single_use"))),
        ("ex:isKit", bool_lit(rec.get("is_kit"))),
        ("ex:isCombinationProduct", bool_lit(rec.get("is_combination_product"))),
        ("ex:isHCTP", bool_lit(rec.get("is_hct_p"))),
        ("ex:isLabeledAsNRL", bool_lit(rec.get("is_labeled_as_nrl"))),
        ("ex:isLabeledAsNoNRL", bool_lit(rec.get("is_labeled_as_no_nrl"))),
        ("ex:isDirectMarkingExempt", bool_lit(rec.get("is_direct_marking_exempt"))),
        ("ex:isPremarketExempt", bool_lit(rec.get("is_pm_exempt"))),
        ("ex:mriSafetyStatus", lit(rec.get("mri_safety"))),
        ("ex:hasLotOrBatchNumber", bool_lit(rec.get("has_lot_or_batch_number"))),
        ("ex:hasSerialNumber", bool_lit(rec.get("has_serial_number"))),
        ("ex:hasExpirationDate", bool_lit(rec.get("has_expiration_date"))),
        ("ex:hasManufacturingDate", bool_lit(rec.get("has_manufacturing_date"))),
        ("ex:hasDonationIdNumber", bool_lit(rec.get("has_donation_id_number"))),

        # --- provenance (shared helper; retrieved_at is wired in once the fetch
        #     manifest is threaded through -- omitted now so output is unchanged) -
        *provenance(SOURCE_DATASET, key, source_url=f"{SOURCE_URL_BASE}{key}"),
    ]

    # sterilisation is a NESTED object, not top-level flags
    ster = rec.get("sterilization") or {}
    pairs.append(("ex:isSterile", bool_lit(ster.get("is_sterile"))))
    pairs.append(("ex:requiresSterilizationPriorToUse",
                  bool_lit(ster.get("is_sterilization_prior_use"))))
    for method in as_list(ster.get("sterilization_methods")):
        pairs.append(("ex:sterilizationMethod", lit(method)))

    # labeler, identified by DUNS -- a DIFFERENT key space from the FEI-based
    # and name-slug manufacturers already in the graph. Kept separate on purpose;
    # see the entity-resolution note in docs/udi-ingest.md.
    duns = (rec.get("labeler_duns_number") or "").strip()
    if duns:
        pairs.append(("ex:hasLabeler", ref(iri.labeler(duns))))

    # --- cross references --------------------------------------------------
    for pc in rec.get("product_codes") or []:
        code = (pc.get("code") or "").strip()
        if code:
            pairs.append(("ex:hasProductCode", ref(iri.product_code(code))))

    for g in rec.get("gmdn_terms") or []:
        code = (g.get("code") or "").strip()
        if code:
            pairs.append(("ex:hasGMDNTerm", ref(iri.gmdn(code))))

    for sub in rec.get("premarket_submissions") or []:
        num = (sub.get("submission_number") or "").strip()
        if not num:
            continue
        u = num.upper()
        # This is the join that ties a physical device to its clearance record.
        if u.startswith("K") or u.startswith("DEN"):
            pairs.append(("ex:relatedK510Record", ref(iri.k510(num))))
        elif u.startswith("P") or u.startswith("N"):
            pairs.append(("ex:relatedPMARecord", ref(iri.pma(num))))
        else:
            pairs.append(("ex:premarketSubmissionNumber", lit(num)))

    for ident in rec.get("identifiers") or []:
        iid = (ident.get("id") or "").strip()
        agency = (ident.get("issuing_agency") or "unknown").strip()
        if iid:
            pairs.append(("ex:hasIdentifier",
                          ref(iri.udi_identifier(agency, iid))))

    n += write_block(out, dev, pairs)

    # ---- UDI identifier nodes --------------------------------------------
    for ident in rec.get("identifiers") or []:
        iid = (ident.get("id") or "").strip()
        if not iid:
            continue
        agency = (ident.get("issuing_agency") or "unknown").strip()
        node = ref(iri.udi_identifier(agency, iid))
        if node in seen["ident"]:
            continue
        seen["ident"].add(node)
        n += write_block(out, node, [
            ("rdf:type", "ex:UDIIdentifier"),
            ("rdfs:label", lit(iid)),
            ("ex:identifierId", lit(iid)),
            ("ex:identifierType", lit(ident.get("type"))),
            ("ex:issuingAgency", lit(agency)),
            ("ex:packageType", lit(ident.get("package_type"))),
            ("ex:quantityPerUnitOfUse",
             int_lit(ident.get("quantity_per_package"))),
            ("ex:unitOfUseId", lit(ident.get("unit_of_use_id"))),
            ("ex:belongsToDevice", dev),
        ])

    # ---- GMDN terms (shared nodes, emitted once) --------------------------
    for g in rec.get("gmdn_terms") or []:
        code = (g.get("code") or "").strip()
        if not code:
            continue
        node = ref(iri.gmdn(code))
        if node in seen["gmdn"]:
            continue
        seen["gmdn"].add(node)
        n += write_block(out, node, [
            ("rdf:type", "ex:GMDNTerm"),
            ("rdfs:label", lit(g.get("name"))),
            ("ex:gmdnTermCode", lit(code)),
            ("ex:gmdnTermName", lit(g.get("name"))),
            ("ex:gmdnTermDefinition", lit(g.get("definition"))),
            ("ex:gmdnImplantable", bool_lit(g.get("implantable"))),
            ("ex:gmdnCodeStatus", lit(g.get("code_status"))),
        ])

    # ---- labeler nodes ----------------------------------------------------
    if duns:
        node = ref(iri.labeler(duns))
        if node not in seen["labeler"]:
            seen["labeler"].add(node)
            n += write_block(out, node, [
                ("rdf:type", "ex:Labeler"),
                ("rdfs:label", lit(rec.get("company_name"))),
                ("ex:companyName", lit(rec.get("company_name"))),
                ("ex:dunsNumber", lit(duns)),
            ])

    # ---- storage conditions ----------------------------------------------
    for i, st in enumerate(rec.get("storage") or []):
        node = ref(iri.storage_condition(key, i))
        high = st.get("high") or {}
        low = st.get("low") or {}
        n += write_block(out, node, [
            ("rdf:type", "ex:StorageCondition"),
            ("ex:storageType", lit(st.get("type"))),
            ("ex:storageHighValue", lit(high.get("value"))),
            ("ex:storageHighUnit", lit(high.get("unit"))),
            ("ex:storageLowValue", lit(low.get("value"))),
            ("ex:storageLowUnit", lit(low.get("unit"))),
            ("ex:specialConditions", lit(st.get("special_conditions"))),
            ("ex:appliesToDevice", dev),
        ])
        n += write_block(out, dev, [("ex:hasStorageCondition", node)])

    return n


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def iter_records(path):
    """Yield records from a .json.zip or plain .json partition."""
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            name = zf.namelist()[0]
            with zf.open(name) as fh:
                payload = json.load(fh)
    else:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    for rec in payload.get("results", []):
        yield rec


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True,
                    help="directory of .json.zip partitions from fetch_udi.py")
    ap.add_argument("--output", required=True, help="destination .ttl file")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N records (smoke test only -- never for production)")
    args = ap.parse_args()

    parts = sorted(
        os.path.join(args.input, f)
        for f in os.listdir(args.input)
        if f.endswith(".json.zip") or f.endswith(".json")
    )
    if not parts:
        sys.exit(f"error: no partitions found in {args.input}")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)

    seen = {"gmdn": set(), "labeler": set(), "ident": set()}
    records = triples = 0
    started = datetime.now()

    print(f"==> {len(parts)} partition(s) -> {args.output}")

    with open(args.output, "w", encoding="utf-8") as out:
        out.write(PREFIXES)
        out.write(f"\n# Generated {started.isoformat(timespec='seconds')}\n")
        out.write(f"# Source: {SOURCE_DATASET}\n\n")

        for pi, part in enumerate(parts, 1):
            pname = os.path.basename(part)
            pcount = 0
            for rec in iter_records(part):
                triples += convert_record(rec, out, seen)
                records += 1
                pcount += 1
                if records % 100_000 == 0:
                    print(f"    {records:,} records, {triples:,} triples")
                if args.limit and records >= args.limit:
                    break
            print(f"  [{pi}/{len(parts)}] {pname}: {pcount:,} records")
            if args.limit and records >= args.limit:
                print("  (stopped early -- --limit was set)")
                break

    elapsed = (datetime.now() - started).total_seconds()
    size_mb = os.path.getsize(args.output) / 1024 / 1024
    print(f"\n==> done in {elapsed/60:.1f} min")
    print(f"    records          {records:,}")
    print(f"    triples          {triples:,}")
    print(f"    GMDN terms       {len(seen['gmdn']):,}")
    print(f"    labelers (DUNS)  {len(seen['labeler']):,}")
    print(f"    output           {size_mb:,.1f} MB")
    if args.limit:
        print("\n    WARNING: --limit was set. This output is INCOMPLETE.")


if __name__ == "__main__":
    main()
