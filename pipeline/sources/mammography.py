#!/usr/bin/env python3
"""
mammography.py -- FDA MQSA certified mammography facilities (premarket/ftparea/public.zip).

FDA replaces public.zip weekly. public.txt is pipe-delimited with no header:
    Facility Name|Address 1|Address 2|Address 3|City|State|Zip Code|Phone|Fax
(layout published on fda.gov "Search for a Certified Facility"). The legacy graph
held 975 facilities from a capped scrape of the search page; the bulk file has
every certified facility (9,135 on 2026-10-10). Veterans Health Administration
facilities are not in FDA's list.

FDA gives no facility identifier, so the IRI key is the facility name + 5-digit ZIP
(+ first address line when two facilities share both), which is stable as long as
FDA's record is.
"""
import os
import re
import sys
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

NAME = "mammography"
DATASET = "cdrh/mqsa"
BULK_URL = "https://www.accessdata.fda.gov/premarket/ftparea/public.zip"
COLUMNS = ["name", "address1", "address2", "address3", "city", "state", "zip", "phone", "fax"]


def fetch(dest):
    f = base.bulk_download(BULK_URL, dest)
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[f])


def _rows(src):
    src = Path(src)
    texts = []
    for p in sorted(src.glob("*.zip")):
        with zipfile.ZipFile(p) as z:
            texts += [z.read(n).decode("latin-1") for n in z.namelist() if n.lower().endswith(".txt")]
    texts += [p.read_text(encoding="latin-1") for p in sorted(src.glob("*.txt"))]
    for t in texts:
        for ln in t.splitlines():
            if not ln.strip():
                continue
            cells = [c.strip() for c in ln.split("|")]
            if len(cells) < 7:
                raise RuntimeError(f"public.txt layout changed: {ln[:120]!r}")
            yield {c: (cells[i] if i < len(cells) else "") for i, c in enumerate(COLUMNS)}


def iter_facilities(src, st=None):
    """(IRI key, row) per facility -- shared with geospatial.py so both mint the same IRI."""
    used = set()
    for r in _rows(src):
        if not r["name"]:
            if st:
                st.skipped += 1
            continue
        zip5 = re.sub(r"\D", "", r["zip"])[:5]
        key = f"{r['name']} {zip5}"
        if iri.slug(key) in used:
            key = f"{key} {r['address1']}"
        if iri.slug(key) in used:
            if st:
                st.skipped += 1                       # exact duplicate line
            continue
        used.add(iri.slug(key))
        yield key, r


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    with base.TurtleWriter(out) as w:
        for key, r in iter_facilities(src, st):
            addr = ", ".join(x for x in (r["address1"], r["address2"], r["address3"]) if x)
            pairs = [
                ("a", "ex:MammographyFacility"),
                ("rdfs:label", common.lit(r["name"])),
                ("ex:facilityName", common.lit(r["name"])),
                ("ex:address", common.lit(addr)),
                ("ex:city", common.lit(r["city"])),
                ("ex:state", common.lit(r["state"])),
                ("ex:postalCode", common.lit(r["zip"])),
                ("ex:phone", common.lit(r["phone"])),
                ("ex:fax", common.lit(r["fax"])),
                ("ex:mqsaCertified", '"true"^^xsd:boolean'),
            ]
            if "mobile" in r["name"].lower():
                pairs.append(("ex:mobileUnit", '"true"^^xsd:boolean'))
            pairs += common.provenance(DATASET, key, source_url=BULK_URL, retrieved_at=retrieved_at)
            w.block(iri.mammography_facility(key), pairs)
            st.records += 1
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import tempfile
    txt = ("1505 EDGEMONT MEDICAL OFFICES|1505 N. Edgemont|||Los Angeles|CA|90027|3237836956|3237837397\n"
           "Main Imaging|1 First St|||Boston|MA|02110|111|222\n"
           "Main Imaging|9 Second St|||Boston|MA|02110-1234|333|\n"            # same name+zip, other site
           "Mobile Mammo Unit (Mobile)|PO Box 1|||Austin|TX|78701|444|\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "public.txt").write_text(txt, encoding="latin-1")
        st = transform(d, d / "m.ttl", retrieved_at="2026-10-10")
        ttl = (d / "m.ttl").read_text(encoding="utf-8")
    assert st.records == 4 and st.skipped == 0, st
    assert ttl.count("a ex:MammographyFacility") == 4
    assert f"<{iri.mammography_facility('Main Imaging 02110')}>" in ttl
    assert f"<{iri.mammography_facility('Main Imaging 02110 9 Second St')}>" in ttl
    assert 'ex:mobileUnit "true"^^xsd:boolean' in ttl and 'ex:fax "3237837397"' in ttl
    print(f"mammography.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
