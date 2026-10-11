#!/usr/bin/env python3
"""
clia.py -- CLIA test complexity categorizations (FDA + CDC bulk files) -> RDF.

Sources (FDA "CLIA Download Data" page):
  clia_detail.zip  clia_detail.txt   categorizations by FDA since 31 Jan 2000
      DOCUMENT_NUMBER|TEST_SYSTEM_ID|TEST_SYSTEM_NAME|QUALIFIER1|QUALIFIER2|ANALYTE_ID|
      ANALYTE_NAME|SPECIALTY_ID|COMPLEXITY|DATE_EFFECTIVE
  clia_cdc.zip     clia_cdc.txt      categorizations by CDC before 2000
      ID|Document_Number|Test_System_ID|Test_System_Name|Qualifier1|Qualifier2|Analyte_ID|
      Analyte_Name|Specialty_ID|Complexity|Date_Effective

The legacy graph held only the 152 names on the "currently waived analytes" page.
This module keeps every categorization row (WAIVED, MODERATE and HIGH):

  ex:CliaCategorization   one row: complexity, effective date, specialty id, qualifiers,
                          document number (linked to the 510(k)/PMA node where it is one),
                          categorized by FDA or CDC
  ex:CliaTestSystem       TEST_SYSTEM_ID + name
  ex:CliaAnalyte          ANALYTE_ID + name; also typed ex:CliaWaivedAnalyte (the legacy
                          class) when at least one test system for it is categorized WAIVED
"""
import os
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

NAME = "clia"
DATASET = "cdrh/clia"
URLS = {"fda": "https://www.accessdata.fda.gov/premarket/ftparea/clia_detail.zip",
        "cdc": "https://www.accessdata.fda.gov/premarket/ftparea/clia_cdc.zip"}
FDA_COLS = ["DOCUMENT_NUMBER", "TEST_SYSTEM_ID", "TEST_SYSTEM_NAME", "QUALIFIER1", "QUALIFIER2",
            "ANALYTE_ID", "ANALYTE_NAME", "SPECIALTY_ID", "COMPLEXITY", "DATE_EFFECTIVE"]


def fetch(dest):
    files = [base.bulk_download(u, dest) for u in URLS.values()]
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def _norm(header):
    return [h.strip().upper() for h in header]


def _read(text, source):
    lines = text.splitlines()
    hdr = _norm(lines[0].split("|"))
    need = set(FDA_COLS)
    if not need <= set(hdr):
        raise RuntimeError(f"CLIA {source} header changed: {hdr}")
    for ln in lines[1:]:
        if ln.strip():
            cells = [c.strip() for c in ln.split("|")]
            yield {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(hdr)}


def _rows(src):
    src = Path(src)
    for p in sorted(src.glob("*.zip")):
        source = "cdc" if "cdc" in p.name.lower() else "fda"
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.lower().endswith(".txt"):
                    for r in _read(z.read(n).decode("latin-1"), source):
                        yield source, r
    for p in sorted(src.glob("*.txt")):
        source = "cdc" if "cdc" in p.name.lower() else "fda"
        for r in _read(p.read_text(encoding="latin-1"), source):
            yield source, r


def _date(v):
    v = (v or "").strip()[:10].replace("/", "-")
    return common.date_lit(v) if v else None


def _doc_link(doc):
    u = (doc or "").upper()
    try:
        if u.startswith("K") or u.startswith("DEN"):
            return ("ex:relatedK510Record", f"<{iri.k510(doc)}>")
        if u.startswith("P"):
            return ("ex:relatedPMARecord", f"<{iri.pma(doc.split('/')[0])}>")
    except ValueError:
        return None
    return None


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    systems, analytes, waived, used = {}, {}, set(), set()
    with base.TurtleWriter(out) as w:
        for source, r in _rows(src):
            ts, an, doc = r["TEST_SYSTEM_ID"], r["ANALYTE_ID"], r["DOCUMENT_NUMBER"]
            if not ts or not an:
                st.skipped += 1
                continue
            base_key = f"{doc or 'nodoc'}-{ts}-{an}"
            key, n = base_key, 1
            while iri.slug(f"{source} {key}") in used:
                n += 1
                key = f"{base_key}-{n}"
            used.add(iri.slug(f"{source} {key}"))
            cx = (r["COMPLEXITY"] or "").upper()
            pairs = [
                ("a", "ex:CliaCategorization"),
                ("ex:complexity", common.lit(cx)),
                ("ex:dateEffective", _date(r["DATE_EFFECTIVE"])),
                ("ex:specialtyId", common.lit(r["SPECIALTY_ID"])),
                ("ex:qualifier", common.lit(r["QUALIFIER1"])),
                ("ex:qualifier", common.lit(r["QUALIFIER2"])),
                ("ex:documentNumber", common.lit(doc)),
                ("ex:categorizedBy", common.lit(source.upper())),
                ("ex:hasTestSystem", f"<{iri.clia_test_system(ts)}>"),
                ("ex:hasAnalyte", f"<{iri.clia_analyte(an)}>"),
            ]
            link = _doc_link(doc)
            if link:
                pairs.append(link)
            pairs += common.provenance(DATASET, f"{source}:{key}", source_url=URLS[source],
                                       retrieved_at=retrieved_at)
            w.block(iri.clia_categorization(source, key), pairs)
            st.records += 1
            systems.setdefault(ts, r["TEST_SYSTEM_NAME"])
            analytes.setdefault(an, r["ANALYTE_NAME"])
            if cx == "WAIVED":
                waived.add(an)
        for ts in sorted(systems, key=lambda x: (len(x), x)):
            w.block(iri.clia_test_system(ts), [("a", "ex:CliaTestSystem"),
                                               ("rdfs:label", common.lit(systems[ts])),
                                               ("ex:testSystemId", common.lit(ts))])
        for an in sorted(analytes, key=lambda x: (len(x), x)):
            pairs = [("a", "ex:CliaAnalyte"), ("rdfs:label", common.lit(analytes[an])),
                     ("ex:analyteName", common.lit(analytes[an])), ("ex:analyteId", common.lit(an))]
            if an in waived:
                pairs.insert(1, ("a", "ex:CliaWaivedAnalyte"))
            w.block(iri.clia_analyte(an), pairs)
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


def _selftest():
    import tempfile
    fda = ("|".join(FDA_COLS) + "\n"
           "X140005|21528|Carolina Liquid Chemistries Corp., Biolis 24i|||12|Alanine aminotransferase (ALT) (SGPT)|2|MODERATE|2014/03/18 00:00:00.000\n"
           "K140003|22432|RightBio Metrics, RightSpot Infant pH Indicator|||335|Gastric pH|2|WAIVED|2014/03/18 00:00:00.000\n"
           "K140003|22432|RightBio Metrics, RightSpot Infant pH Indicator|lot B||335|Gastric pH|2|WAIVED|2015/01/02 00:00:00.000\n")
    cdc = ("ID|Document_Number|Test_System_ID|Test_System_Name|Qualifier1|Qualifier2|Analyte_ID|Analyte_Name|Specialty_ID|Complexity|Date_Effective\n"
           "1||43053|Nichols 1,25-Dihydroxyvitamin D Assay Kit|||104|1,25-Dihydroxyvitamin D|2|HIGH|1993-07-26 00:00:00\n")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "clia_detail.txt").write_text(fda, encoding="latin-1")
        (d / "clia_cdc.txt").write_text(cdc, encoding="latin-1")
        st = transform(d, d / "c.ttl", retrieved_at="2026-10-10")
        ttl = (d / "c.ttl").read_text(encoding="utf-8")
    assert st.records == 4, st
    assert ttl.count("a ex:CliaCategorization") == 4
    assert f"<{iri.clia_categorization('fda', 'K140003-22432-335-2')}>" in ttl     # second row kept, not merged
    assert 'ex:complexity "WAIVED"' in ttl and 'ex:complexity "HIGH"' in ttl
    assert '"2014-03-18"^^xsd:date' in ttl and '"1993-07-26"^^xsd:date' in ttl
    assert f"ex:relatedK510Record <{iri.k510('K140003')}>" in ttl
    assert 'ex:categorizedBy "CDC"' in ttl
    blk = ttl.split(f"<{iri.clia_analyte('335')}>\n", 1)[1].split("\n\n")[0]
    assert "a ex:CliaWaivedAnalyte" in blk
    blk12 = ttl.split(f"<{iri.clia_analyte('12')}>\n", 1)[1].split("\n\n")[0]
    assert "ex:CliaWaivedAnalyte" not in blk12
    print(f"clia.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
