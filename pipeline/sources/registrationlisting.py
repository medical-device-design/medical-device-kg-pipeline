#!/usr/bin/env python3
"""
registrationlisting.py -- establishment registration & listing
(openFDA device/registrationlisting) -> RDF, on the base.py contract.

Each record -> ex:RegistrationListingRecord (identity + address = the geographic
layer), linked to a deduped ex:Manufacturer node (keyed on FEI) and to the
ex:ProductCode hub for every product the establishment lists. Records are nested
(registration / owner_operator / contact_address / products), handled defensively.
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

NAME = "registrationlisting"
DATASET = "device/registrationlisting"
DOWNLOAD_INDEX = "https://api.fda.gov/download.json"
SOURCE_URL = "https://open.fda.gov/apis/device/registrationlisting/"


def fetch(dest):
    """Bulk-download every device/registrationlisting partition (no paging)."""
    dest = Path(dest)
    with urllib.request.urlopen(DOWNLOAD_INDEX, timeout=120) as r:
        idx = json.loads(r.read())
    partitions = idx["results"]["device"]["registrationlisting"]["partitions"]
    files = [base.bulk_download(p["file"], dest) for p in partitions]
    return base.Manifest(source=NAME, dataset=DATASET,
                         retrieved_at=base.today(), files=files)


def _iter_records(src):
    """Stream records one at a time (base.iter_openfda_results; ijson if installed)."""
    yield from base.iter_openfda_results(src)


def transform(src, out, retrieved_at=None):
    man = Path(src) / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None

    st = base.Stats(source=NAME)
    manufacturers = {}   # fei -> firm_name
    hub = {}             # product_code -> label
    establishments = {}  # regno -> (seen pairs, ordered pairs)
    listings = 0
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            reg = r.get("registration") or {}
            regno = str(reg.get("registration_number") or reg.get("fei_number") or "").strip()
            if not regno:
                st.skipped += 1
                continue
            # openFDA puts the establishment address directly on "registration"
            # (address_line_1, city, state_code, zip_code, iso_country_code); older
            # layouts nested it in contact_address. Read both so no address is lost.
            addr = reg if (reg.get("address_line_1") or reg.get("city")) else (reg.get("contact_address") or {})
            owner = reg.get("owner_operator") or {}
            fei = str(reg.get("fei_number") or "").strip()
            street = common.norm_address(addr.get("address_line_1"), addr.get("address_line_2"))

            pairs = [
                ("a", "ex:RegistrationListingRecord"),
                # the establishment name is its label (also shown for its geocoded point)
                ("rdfs:label", common.lit(common.norm_text(reg.get("name")))),
                ("ex:registrationNumber", common.lit(reg.get("registration_number"))),
                ("ex:feiNumber", common.lit(fei) if fei else None),
                ("ex:name", common.lit(reg.get("name"))),
                ("ex:statusCode", common.lit(reg.get("status_code"))),
                ("ex:initialImporterFlag", common.bool_lit(reg.get("initial_importer_flag"))),
                ("ex:registrationExpiryYear", common.lit(reg.get("reg_expiry_date_year"))),
                ("ex:address", common.lit(street)),
                ("ex:city", common.lit(addr.get("city"))),
                ("ex:state", common.lit(addr.get("state_code") or addr.get("state_province"))),
                ("ex:postalCode", common.lit(addr.get("zip_code") or addr.get("postal_code"))),
                ("ex:country", common.lit(addr.get("iso_country_code"))),
                # owner/operator FIRM only; official correspondent and US agent are people -> not ingested
                ("ex:ownerOperatorNumber", common.lit(owner.get("owner_operator_number"))),
                ("ex:ownerOperatorName", common.lit(owner.get("firm_name"))),
                ("ex:usAgentBusinessName", common.lit((reg.get("us_agent") or {}).get("business_name"))),
            ]
            for et in common.as_list(r.get("establishment_type")):
                pairs.append(("ex:establishmentType", common.lit(et)))
            for pn in common.as_list(r.get("proprietary_name")):
                pairs.append(("ex:proprietaryName", common.lit(pn)))
            for k in common.as_list(r.get("k_number")):
                k = str(k or "").strip()
                if k:
                    pairs.append(("ex:relatedK510Record", f"<{iri.k510(k)}>"))
            for p in common.as_list(r.get("pma_number")):
                p = str(p or "").strip()
                if p:
                    pairs.append(("ex:relatedPMARecord", f"<{iri.pma(p.split('/')[0][:7])}>"))

            if fei:
                pairs.append(("ex:hasManufacturer", f"<{iri.manufacturer(fei=fei)}>"))
                manufacturers.setdefault(fei, owner.get("firm_name") or reg.get("name"))

            for prod in (r.get("products") or []):
                pc = common.product_code(prod.get("product_code"))
                if not pc:
                    continue
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                of = prod.get("openfda") or {}
                hub.setdefault(pc, (common.as_list(of.get("device_name")) or [None])[0])

            for pred, obj in common.provenance(DATASET, regno,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            # openFDA has one record per LISTING (~13 per establishment); merge them so
            # each establishment is written once with the union of its listings.
            acc = establishments.setdefault(regno, ({}, []))
            for p in pairs:
                if p[1] is not None and p not in acc[0]:
                    acc[0][p] = True
                    acc[1].append(p)
            listings += 1

        for regno, (_, pairs) in establishments.items():
            w.block(iri.establishment(regno), pairs)
            st.records += 1
        print(f"  registrationlisting: {listings} listing records -> {st.records} establishments")

        for fei in sorted(manufacturers):
            w.block(iri.manufacturer(fei=fei), [
                ("a", "ex:Manufacturer"),
                ("rdfs:label", common.lit(manufacturers[fei])),
                ("ex:feiNumber", common.lit(fei)),
            ])
        for pc in sorted(hub):
            w.block(iri.product_code(pc), [
                ("a", "ex:ProductCode"),
                ("skos:altLabel", common.lit(hub[pc])),      # rdfs:label comes from classification
                ("ex:productCode", common.lit(pc)),
            ])

    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=None)


# ===========================================================================
def _selftest():
    import tempfile
    sample = {"results": [
        {"registration": {"registration_number": "3001234567", "fei_number": "3009999999",
                          "name": "Acme Devices Inc", "status_code": "1",
                          "initial_importer_flag": "N",
                          "owner_operator": {"firm_name": "Acme Holdings"},
                          "contact_address": {"address_line_1": "1 Main St", "city": "Boston",
                                              "state_code": "MA", "postal_code": "02110",
                                              "iso_country_code": "US"}},
         "establishment_type": ["Manufacture Medical Device"],
         "proprietary_name": ["WidgetPro"],
         "products": [{"product_code": "FRN", "openfda": {"device_name": ["Pump, Infusion"]}},
                      {"product_code": "OYC"}]},
        {"registration": {"name": "no reg number"}},   # skipped
        # current openFDA layout: address fields directly on "registration"
        {"registration": {"registration_number": "1000517406", "fei_number": "1000517406",
                          "name": "Flat Layout Co", "address_line_1": "125 W 1000 S", "address_line_2": "Suite 2",
                          "city": "Smithfield", "state_code": "UT", "zip_code": "84335",
                          "iso_country_code": "US", "reg_expiry_date_year": "2026",
                          "owner_operator": {"firm_name": "Flat Parent", "owner_operator_number": "9001",
                                             "official_correspondent": {"name": "A Person"}}},
         "k_number": "K190001", "pma_number": "",
         "products": [{"product_code": "FRN"}]},
        # a second listing for the same establishment -> merged into one block
        {"registration": {"registration_number": "1000517406", "fei_number": "1000517406",
                          "name": "Flat Layout Co", "address_line_1": "125 W 1000 S", "address_line_2": "Suite 2",
                          "city": "Smithfield", "state_code": "UT", "zip_code": "84335", "iso_country_code": "US"},
         "proprietary_name": ["SecondPump"], "products": [{"product_code": "MEA"}]},
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "registrationlisting.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 2 and st.skipped == 1, (st.records, st.skipped)
    assert text.count(f"<{iri.establishment('1000517406')}>\n") == 1          # merged, written once
    assert 'ex:proprietaryName "SecondPump"' in text and f"<{iri.product_code('MEA')}>" in text
    assert text.count('ex:city "Smithfield"') == 1
    assert 'ex:city "Smithfield"' in text and 'ex:postalCode "84335"' in text and 'ex:country "US"' in text
    assert 'ex:address "125 W 1000 S, Suite 2"' in text and 'ex:registrationExpiryYear "2026"' in text
    assert 'ex:ownerOperatorName "Flat Parent"' in text and "A Person" not in text
    assert f"ex:relatedK510Record <{iri.k510('K190001')}>" in text
    assert f"<{iri.establishment('3001234567')}>" in text and "ex:RegistrationListingRecord" in text
    assert 'ex:city "Boston"' in text and 'ex:state "MA"' in text
    assert f"<{iri.manufacturer(fei='3009999999')}>" in text and "ex:Manufacturer" in text
    assert 'rdfs:label "Acme Holdings"' in text
    assert f"<{iri.product_code('FRN')}>" in text and f"<{iri.product_code('OYC')}>" in text
    assert 'ex:establishmentType "Manufacture Medical Device"' in text
    assert 'rdfs:label "Flat Layout Co"' in text and text.count('rdfs:label "Flat Layout Co"') == 1
    assert 'skos:altLabel "Pump, Infusion"' in text
    assert "ex:sourceDataset \"device/registrationlisting\"" in text
    print(f"registrationlisting.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
