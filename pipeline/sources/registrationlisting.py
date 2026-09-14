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
    manufacturers = {}   # fei -> firm_name
    hub = {}             # product_code -> label
    with base.TurtleWriter(out) as w:
        for r in _iter_records(src):
            reg = r.get("registration") or {}
            regno = str(reg.get("registration_number") or reg.get("fei_number") or "").strip()
            if not regno:
                st.skipped += 1
                continue
            addr = reg.get("contact_address") or {}
            owner = reg.get("owner_operator") or {}
            fei = str(reg.get("fei_number") or "").strip()

            pairs = [
                ("a", "ex:RegistrationListingRecord"),
                ("ex:registrationNumber", common.lit(reg.get("registration_number"))),
                ("ex:feiNumber", common.lit(fei) if fei else None),
                ("ex:name", common.lit(reg.get("name"))),
                ("ex:statusCode", common.lit(reg.get("status_code"))),
                ("ex:initialImporterFlag", common.bool_lit(reg.get("initial_importer_flag"))),
                ("ex:address", common.lit(addr.get("address_line_1"))),
                ("ex:city", common.lit(addr.get("city"))),
                ("ex:state", common.lit(addr.get("state_code") or addr.get("state_province"))),
                ("ex:postalCode", common.lit(addr.get("postal_code"))),
                ("ex:country", common.lit(addr.get("iso_country_code"))),
            ]
            for et in common.as_list(r.get("establishment_type")):
                pairs.append(("ex:establishmentType", common.lit(et)))
            for pn in common.as_list(r.get("proprietary_name")):
                pairs.append(("ex:proprietaryName", common.lit(pn)))

            if fei:
                pairs.append(("ex:hasManufacturer", f"<{iri.manufacturer(fei=fei)}>"))
                manufacturers.setdefault(fei, owner.get("firm_name") or reg.get("name"))

            for prod in (r.get("products") or []):
                pc = str(prod.get("product_code") or "").strip()
                if not pc:
                    continue
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                of = prod.get("openfda") or {}
                hub.setdefault(pc, (common.as_list(of.get("device_name")) or [None])[0])

            for pred, obj in common.provenance(DATASET, regno,
                                               source_url=SOURCE_URL, retrieved_at=retrieved_at):
                pairs.append((pred, obj))

            w.block(iri.establishment(regno), pairs)
            st.records += 1

        for fei in sorted(manufacturers):
            w.block(iri.manufacturer(fei=fei), [
                ("a", "ex:Manufacturer"),
                ("rdfs:label", common.lit(manufacturers[fei])),
                ("ex:feiNumber", common.lit(fei)),
            ])
        for pc in sorted(hub):
            w.block(iri.product_code(pc), [
                ("a", "ex:ProductCode"),
                ("rdfs:label", common.lit(hub[pc])),
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
    ]}
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "sample.json").write_text(json.dumps(sample), encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-02",
                     files=[]).write(Path(d) / "manifest.json")
        out = Path(d) / "registrationlisting.ttl"
        st = transform(d, out)
        text = out.read_text(encoding="utf-8")

    assert st.records == 1 and st.skipped == 1, (st.records, st.skipped)
    assert f"<{iri.establishment('3001234567')}>" in text and "ex:RegistrationListingRecord" in text
    assert 'ex:city "Boston"' in text and 'ex:state "MA"' in text
    assert f"<{iri.manufacturer(fei='3009999999')}>" in text and "ex:Manufacturer" in text
    assert 'rdfs:label "Acme Holdings"' in text
    assert f"<{iri.product_code('FRN')}>" in text and f"<{iri.product_code('OYC')}>" in text
    assert 'ex:establishmentType "Manufacture Medical Device"' in text
    assert "ex:sourceDataset \"device/registrationlisting\"" in text
    print(f"registrationlisting.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
