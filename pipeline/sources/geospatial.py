#!/usr/bin/env python3
"""
geospatial.py -- geocode US FDA-registered establishments and MQSA mammography
facilities with the US Census Geocoder batch service -> GeoSPARQL RDF.

Inputs: downloads/registrationlisting (US establishments with a street address) and
downloads/mammography (public.zip). fetch() writes 10,000-address input files and posts
each to the Census batch endpoint (benchmark Public_AR_Current), one at a time, saving
the answer next to it; finished batches are skipped, so a rerun resumes.

transform() reads the Census answers. Only "Match" rows produce triples:
  subject  a geo:Feature (establishments are also ex:Facility)
           wgs:lat / wgs:long, geo:hasGeometry / geo:defaultGeometry -> geometry node,
           ex:normalizedAddress (the Census matched address),
           ex:geocodingConfidence 1.0 for an Exact match, 0.7 for Non_Exact
           (the legacy layer's convention), ex:geocodingSource
  geometry a geo:Geometry ; geo:asWKT "POINT(long lat)"^^geo:wktLiteral
No_Match and Tie rows are counted, never placed. Non-US addresses are not sent
(the Census service covers US addresses only).
"""
import csv
import io
import os
import re
import sys
import time
import urllib.request
import uuid
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "geospatial"
DATASET = "derived/census-geocoder"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK = "Public_AR_Current"
SOURCE_LABEL = "US Census Geocoder batch (Public_AR_Current)"
UA = "medical-device-kg/rebuild (Hofstra University)"
BATCH = 10000
GEO = "http://www.opengis.net/ont/geosparql#"
WGS = "http://www.w3.org/2003/01/geo/wgs84_pos#"


def _clean(v):
    return re.sub(r"[\r\n,\"]+", " ", str(v or "")).strip()


def addresses(downloads):
    """Yield (id, street, city, state, zip). id = '<kind>|<key>'."""
    downloads = Path(downloads)
    seen = set()
    if (downloads / "registrationlisting").exists():
        for r in base.iter_openfda_results(downloads / "registrationlisting"):
            reg = r.get("registration") or {}
            regno = str(reg.get("registration_number") or reg.get("fei_number") or "").strip()
            a = reg if (reg.get("address_line_1") or reg.get("city")) else (reg.get("contact_address") or {})
            if not regno or regno in seen or (a.get("iso_country_code") or reg.get("iso_country_code")) != "US":
                continue
            street = a.get("address_line_1")
            if not street or not (a.get("zip_code") or a.get("postal_code") or a.get("city")):
                continue
            seen.add(regno)
            yield (f"establishment|{regno}", _clean(street), _clean(a.get("city")), _clean(a.get("state_code")),
                   _clean(a.get("zip_code") or a.get("postal_code"))[:5])
    if (downloads / "mammography").exists():
        import mammography
        for key, r in mammography.iter_facilities(downloads / "mammography"):
            if r["address1"]:
                yield (f"mammography|{key}", _clean(r["address1"]), _clean(r["city"]), _clean(r["state"]),
                       re.sub(r"\D", "", r["zip"])[:5])


def _post_batch(path):
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"benchmark\"\r\n\r\n{BENCHMARK}\r\n".encode())
    body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"addressFile\"; filename=\"{path.name}\"\r\n"
               f"Content-Type: text/csv\r\n\r\n".encode())
    body.write(path.read_bytes())
    body.write(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(CENSUS_URL, data=body.getvalue(), headers={
        "User-Agent": UA, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return urllib.request.urlopen(req, timeout=1800).read()


def fetch(dest, downloads=None, retries=3):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    downloads = Path(downloads) if downloads else dest.parent
    rows = list(addresses(downloads))
    files = []
    for n in range(0, len(rows), BATCH):
        inp = dest / f"in_{n // BATCH:03d}.csv"
        outp = dest / f"out_{n // BATCH:03d}.csv"
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows[n:n + BATCH])
        if not inp.exists() or inp.read_text(encoding="utf-8") != buf.getvalue():
            inp.write_text(buf.getvalue(), encoding="utf-8")
            if outp.exists():
                outp.unlink()                     # input changed: answer is stale
        if not outp.exists():
            for attempt in range(retries):
                try:
                    outp.write_bytes(_post_batch(inp))
                    break
                except Exception as e:            # service hiccup: wait and retry
                    print(f"  batch {inp.name} attempt {attempt + 1} failed: {e}")
                    time.sleep(60)
            else:
                raise RuntimeError(f"Census batch {inp.name} failed {retries} times")
            print(f"  geocoded {inp.name}")
        files.append({"path": outp.name, "url": CENSUS_URL})
    print(f"geospatial: {len(rows)} addresses in {len(files)} batches")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def _subject(rid):
    kind, key = rid.split("|", 1)
    if kind == "establishment":
        return kind, key, iri.establishment(key)
    return kind, key, iri.mammography_facility(key)


def transform(src, out, retrieved_at=None):
    src = Path(src)
    st = base.Stats(source=NAME)
    status = {}
    with base.TurtleWriter(out, extra={"geo": GEO, "wgs": WGS}) as w:
        for p in sorted(src.glob("out_*.csv")):
            for row in csv.reader(io.StringIO(p.read_text(encoding="utf-8", errors="replace"))):
                if len(row) < 3:
                    continue
                status[row[2]] = status.get(row[2], 0) + 1
                if row[2] != "Match" or len(row) < 6 or "," not in row[5]:
                    st.skipped += 1
                    continue
                lon, lat = (x.strip() for x in row[5].split(",", 1))
                kind, key, subj = _subject(row[0])
                geom = iri.geometry(kind, key)
                pairs = [("a", "geo:Feature")]
                if kind == "establishment":
                    pairs.insert(0, ("a", "ex:Facility"))
                pairs += [("wgs:lat", f'"{float(lat):.6f}"^^xsd:decimal'),
                          ("wgs:long", f'"{float(lon):.6f}"^^xsd:decimal'),
                          ("geo:hasGeometry", f"<{geom}>"), ("geo:defaultGeometry", f"<{geom}>"),
                          ("ex:normalizedAddress", common.lit(row[4])),
                          ("ex:geocodingConfidence", '"1.0"^^xsd:decimal' if row[3] == "Exact" else '"0.7"^^xsd:decimal'),
                          ("ex:geocodingSource", common.lit(SOURCE_LABEL))]
                if retrieved_at:
                    pairs.append(("ex:retrievedAt", common.date_lit(retrieved_at[:10])))
                w.block(subj, pairs)
                w.block(geom, [("a", "geo:Geometry"),
                               ("geo:asWKT", f'"POINT({float(lon):.6f} {float(lat):.6f})"^^geo:wktLiteral')])
                st.records += 1
    print(f"geospatial: match status {status}")
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=None)


def _selftest():
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "registrationlisting").mkdir(); (d / "mammography").mkdir(); (d / "geospatial").mkdir()
        (d / "registrationlisting" / "r.json").write_text(json.dumps({"results": [
            {"registration": {"registration_number": "1000517406", "address_line_1": "125 W 1000 S",
                              "city": "Smithfield", "state_code": "UT", "zip_code": "84335", "iso_country_code": "US"}},
            {"registration": {"registration_number": "3002", "address_line_1": "1 Rue", "city": "Paris",
                              "iso_country_code": "FR"}}]}))
        (d / "mammography" / "public.txt").write_text("Main Imaging|1 First St, Ste 2|||Boston|MA|02110|111|\n", encoding="latin-1")
        rows = list(addresses(d))
        assert rows == [("establishment|1000517406", "125 W 1000 S", "Smithfield", "UT", "84335"),
                        ("mammography|Main Imaging 02110", "1 First St  Ste 2", "Boston", "MA", "02110")], rows
        (d / "geospatial" / "out_000.csv").write_text(
            '"establishment|1000517406","125 W 1000 S, Smithfield, UT, 84335","Match","Exact",'
            '"125 W 1000 S, SMITHFIELD, UT, 84335","-111.837185,41.816032","123","L"\n'
            '"mammography|Main Imaging 02110","1 First St, Boston, MA, 02110","Match","Non_Exact",'
            '"1 FIRST ST, BOSTON, MA, 02110","-71.05,42.36","9","R"\n'
            '"establishment|999","x","No_Match"\n', encoding="utf-8")
        st = transform(d / "geospatial", d / "g.ttl", retrieved_at="2026-10-10")
        ttl = (d / "g.ttl").read_text(encoding="utf-8")
    assert st.records == 2 and st.skipped == 1, st
    est = ttl.split(f"<{iri.establishment('1000517406')}>\n", 1)[1].split("\n\n")[0]
    assert "a ex:Facility" in est and "a geo:Feature" in est and '"41.816032"^^xsd:decimal' in est
    assert '"1.0"^^xsd:decimal' in est
    assert 'geo:asWKT "POINT(-111.837185 41.816032)"^^geo:wktLiteral' in ttl
    mam = ttl.split(f"<{iri.mammography_facility('Main Imaging 02110')}>\n", 1)[1].split("\n\n")[0]
    assert "ex:Facility" not in mam and '"0.7"^^xsd:decimal' in mam
    assert "@prefix geo: <http://www.opengis.net/ont/geosparql#>" in ttl
    print(f"geospatial.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
