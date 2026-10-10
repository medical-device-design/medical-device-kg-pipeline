#!/usr/bin/env python3
"""
client_readiness.py -- can the rebuilt graph answer the recall questions a
regulatory-intelligence client asks? Measures coverage and runs worked examples.

    python3 scripts/client_readiness.py --out <build dir> --recall-out <recall layer dir> \
        --db <scratch TDB2 dir> --report <report.md>

Steps
  1. Load the recall-centred core (recalls, recall_db, recall_class, classification,
     k510, pma, hde, tplc, registrationlisting, naics, geospatial, devices_fda) into a
     scratch TDB2 store (skipped if --db already holds it).
  2. Run the SPARQL battery below; each answer goes into the report with its run time.
  3. Stream udi.ttl and event.ttl (too large for the scratch store) and measure how many
     device identifiers and adverse-event reports join to a recall.
Nothing is written outside --db and --report.
"""
import argparse
import collections
import re
import subprocess
import time
from pathlib import Path

NS = "https://bmedesign.org/medical-device-kg/ns/"
ID = "https://bmedesign.org/medical-device-kg/id/"
PFX = f"PREFIX ex: <{NS}>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n" \
      "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\nPREFIX wgs: <http://www.w3.org/2003/01/geo/wgs84_pos#>\n"
CORE = ["recalls", "recall_db", "classification", "k510", "pma", "hde", "tplc",
        "registrationlisting", "naics", "geospatial", "devices_fda"]

QUERIES = [
 ("Recall coverage: how many recall records, and how many carry each field a client filters on", """
SELECT (COUNT(DISTINCT ?r) AS ?recalls)
  (COUNT(DISTINCT ?rc) AS ?with_class) (COUNT(DISTINCT ?rp) AS ?with_product_code)
  (COUNT(DISTINCT ?rk) AS ?with_510k_or_pma) (COUNT(DISTINCT ?rf) AS ?with_fei_manufacturer)
  (COUNT(DISTINCT ?rr) AS ?with_root_cause) (COUNT(DISTINCT ?rl) AS ?with_lot_serial_text)
  (COUNT(DISTINCT ?rs) AS ?with_us_states) (COUNT(DISTINCT ?rn) AS ?pending_number)
WHERE { ?r a ex:RecallRecord .
  OPTIONAL { ?r ex:recallClassification ?c . BIND(?r AS ?rc) }
  OPTIONAL { ?r ex:hasProductCode ?p . BIND(?r AS ?rp) }
  OPTIONAL { { ?r ex:relatedK510Record ?k } UNION { ?r ex:relatedPMARecord ?k } BIND(?r AS ?rk) }
  OPTIONAL { ?r ex:hasRecallingManufacturer ?m . BIND(?r AS ?rf) }
  OPTIONAL { ?r ex:rootCauseDescription ?rt . BIND(?r AS ?rr) }
  OPTIONAL { ?r ex:codeInfo ?ci . BIND(?r AS ?rl) }
  OPTIONAL { ?r ex:distributedToState ?st . BIND(?r AS ?rs) }
  OPTIONAL { ?r ex:recallNumberStatus ?ns . BIND(?r AS ?rn) } }"""),
 ("Recalls by class", """
SELECT ?class (COUNT(DISTINCT ?r) AS ?n) WHERE { ?r a ex:RecallRecord ; ex:recallClassification ?class }
GROUP BY ?class ORDER BY ?class"""),
 ("Open (not terminated) recalls by class", """
SELECT ?class (COUNT(DISTINCT ?r) AS ?open) WHERE { ?r a ex:RecallRecord ; ex:recallClassification ?class .
  FILTER NOT EXISTS { ?r ex:terminationDate ?t } FILTER NOT EXISTS { ?r ex:recallStatus "Terminated" } }
GROUP BY ?class ORDER BY ?class"""),
 ("Class I recalls per year (initiation date), last 10 years", """
SELECT ?year (COUNT(DISTINCT ?r) AS ?class_I) WHERE { ?r a ex:RecallRecord ; ex:recallClassification "Class I" ;
  ex:recallInitiationDate ?d . BIND(YEAR(?d) AS ?year) FILTER(?year >= 2016) } GROUP BY ?year ORDER BY ?year"""),
 ("Top 15 FDA root-cause categories (recall database)", """
SELECT ?cause (COUNT(DISTINCT ?r) AS ?n) WHERE { ?r ex:rootCauseDescription ?cause } GROUP BY ?cause ORDER BY DESC(?n) LIMIT 15"""),
 ("Top 15 recalling firms since 2020 (Class I count alongside)", """
SELECT (SAMPLE(?name) AS ?firm) (COUNT(DISTINCT ?r) AS ?recalls) (COUNT(DISTINCT ?r1) AS ?class_I) WHERE {
  ?r a ex:RecallRecord ; ex:hasRecallingFirm ?f ; ex:recallInitiationDate ?d . ?f rdfs:label ?name .
  FILTER(?d >= "2020-01-01"^^xsd:date)
  OPTIONAL { ?r ex:recallClassification "Class I" . BIND(?r AS ?r1) } }
GROUP BY ?f ORDER BY DESC(?recalls) LIMIT 15"""),
 ("Product codes with the most Class I recalls, with device name, device class and MAUDE death/injury totals (TPLC)", """
SELECT ?code ?name ?cls (COUNT(DISTINCT ?r) AS ?class_I) (MAX(?deaths) AS ?mdr_deaths) (MAX(?inj) AS ?mdr_injuries) WHERE {
  ?r a ex:RecallRecord ; ex:recallClassification "Class I" ; ex:hasProductCode ?pc . ?pc ex:productCode ?code .
  OPTIONAL { ?pc rdfs:label ?name } OPTIONAL { ?pc ex:deviceClass ?cls }
  OPTIONAL { ?t a ex:TPLCRecord ; ex:hasProductCode ?pc ; ex:deathReportCount ?deaths ; ex:injuryReportCount ?inj } }
GROUP BY ?code ?name ?cls ORDER BY DESC(?class_I) LIMIT 15"""),
 ("Recall -> 510(k): mean years from clearance to recall start, by top product codes", """
SELECT ?code (COUNT(DISTINCT ?r) AS ?recalls_linked) (AVG(?yrs) AS ?mean_years) WHERE {
  ?r a ex:RecallRecord ; ex:relatedK510Record ?k ; ex:recallInitiationDate ?d ; ex:hasProductCode ?pc .
  ?pc ex:productCode ?code . ?k ex:decisionDate ?dd .
  BIND((YEAR(?d) - YEAR(?dd)) AS ?yrs) FILTER(?yrs >= 0) }
GROUP BY ?code ORDER BY DESC(?recalls_linked) LIMIT 10"""),
 ("Recalling manufacturers (FEI) that resolve to a geocoded registered establishment", """
SELECT (COUNT(DISTINCT ?m) AS ?recalling_manufacturers) (COUNT(DISTINCT ?mg) AS ?with_geocoded_site)
       (COUNT(DISTINCT ?r) AS ?recalls) (COUNT(DISTINCT ?rg) AS ?recalls_with_site_location) WHERE {
  ?r ex:hasRecallingManufacturer ?m .
  OPTIONAL { ?e ex:hasManufacturer ?m ; wgs:lat ?lat . }
  BIND(IF(BOUND(?e), ?m, ?unbound) AS ?mg) BIND(IF(BOUND(?e), ?r, ?unbound) AS ?rg) }"""),
 ("Class I recalls by state of the recalling manufacturer's geocoded site (top 10)", """
SELECT ?state (COUNT(DISTINCT ?r) AS ?class_I) WHERE {
  ?r ex:recallClassification "Class I" ; ex:hasRecallingManufacturer ?m . ?e ex:hasManufacturer ?m ; wgs:lat ?lat ; ex:state ?state }
GROUP BY ?state ORDER BY DESC(?class_I) LIMIT 10"""),
 ("Class I recalls by NAICS industry of the product code (inferred crosswalk)", """
SELECT ?naics ?label (COUNT(DISTINCT ?r) AS ?class_I) WHERE {
  ?r ex:recallClassification "Class I" ; ex:hasProductCode ?pc . ?pc ex:hasNAICSClassification ?n .
  ?n ex:naicsCode ?naics ; rdfs:label ?label } GROUP BY ?naics ?label ORDER BY DESC(?class_I)"""),
 ("Days from recall start to FDA classification (Class I vs II vs III)", """
SELECT ?class (COUNT(?r) AS ?n) (AVG(?days) AS ?mean_days) WHERE {
  ?r ex:recallClassification ?class ; ex:recallInitiationDate ?a ; ex:centerClassificationDate ?b .
  BIND((YEAR(?b)-YEAR(?a))*365 + (MONTH(?b)-MONTH(?a))*30 + (DAY(?b)-DAY(?a)) AS ?days) FILTER(?days >= 0) }
GROUP BY ?class ORDER BY ?class"""),
 ("Software- or cybersecurity-related recalls by year and class (FDA root cause or reason text)", """
SELECT ?year ?class (COUNT(DISTINCT ?r) AS ?n) WHERE {
  ?r a ex:RecallRecord ; ex:recallInitiationDate ?d . OPTIONAL { ?r ex:recallClassification ?c0 }
  BIND(COALESCE(?c0, "unclassified") AS ?class) BIND(YEAR(?d) AS ?year) FILTER(?year >= 2018)
  { ?r ex:rootCauseDescription ?t FILTER(CONTAINS(LCASE(?t), "software")) } UNION
  { ?r ex:reasonForRecall ?t FILTER(REGEX(?t, "software|cyber|firmware", "i")) } }
GROUP BY ?year ?class ORDER BY ?year ?class"""),
 ("Lot / serial lookup: recalls whose published code information contains a given lot string (example: first lot token of a 2025 Class I recall)", """
SELECT ?rn ?class ?firm (SUBSTR(?ci, 1, 160) AS ?code_info_start) WHERE {
  { SELECT (SAMPLE(?tok) AS ?lot) WHERE { ?x ex:recallClassification "Class I" ; ex:recallInitiationDate ?d ; ex:codeInfo ?c .
      FILTER(YEAR(?d) = 2025) BIND(REPLACE(?c, "^.*?(Lot|LOT|lot)[^A-Za-z0-9]*([A-Za-z0-9-]{5,}).*$", "$2", "s") AS ?tok)
      FILTER(?tok != ?c) } }
  ?r ex:codeInfo ?ci ; ex:recallNumber ?rn . FILTER(CONTAINS(?ci, ?lot))
  OPTIONAL { ?r ex:recallClassification ?class } OPTIONAL { ?r ex:recallingFirm ?firm } } LIMIT 10"""),
 ("Worked example -- infusion pumps (product code FRN): recalls by class, 510(k)s, listing establishments, MAUDE totals", """
SELECT ?what ?n WHERE {
  { SELECT ("recalls Class I" AS ?what) (COUNT(DISTINCT ?r) AS ?n) WHERE { ?r ex:hasProductCode ?pc ; ex:recallClassification "Class I" . ?pc ex:productCode "FRN" } }
  UNION { SELECT ("recalls Class II" AS ?what) (COUNT(DISTINCT ?r) AS ?n) WHERE { ?r ex:hasProductCode ?pc ; ex:recallClassification "Class II" . ?pc ex:productCode "FRN" } }
  UNION { SELECT ("510(k) records" AS ?what) (COUNT(DISTINCT ?k) AS ?n) WHERE { ?k a ex:K510Record ; ex:productCode "FRN" } }
  UNION { SELECT ("registered establishments listing FRN" AS ?what) (COUNT(DISTINCT ?e) AS ?n) WHERE { ?e a ex:RegistrationListingRecord ; ex:hasProductCode ?pc . ?pc ex:productCode "FRN" } }
  UNION { SELECT ("MAUDE reports (TPLC)" AS ?what) (MAX(?c) AS ?n) WHERE { ?t a ex:TPLCRecord ; ex:productCode "FRN" ; ex:reportCount ?c } }
  UNION { SELECT ("MAUDE death reports (TPLC)" AS ?what) (MAX(?c) AS ?n) WHERE { ?t a ex:TPLCRecord ; ex:productCode "FRN" ; ex:deathReportCount ?c } } }"""),
]


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def load(db, out, recall_out, jena):
    if (Path(db) / "Data-0001").exists():
        return "store present (reused)"
    files = [f"{out}/{n}.ttl" for n in CORE if Path(f"{out}/{n}.ttl").exists()]
    rc = Path(recall_out) / "recall_class.ttl"
    if rc.exists():
        files.append(str(rc))
    t = time.time()
    r = sh(f"{jena}/tdb2.tdbloader --loc {db} " + " ".join(files))
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])
    return f"loaded {len(files)} files in {time.time() - t:.0f} s"


def query(db, q, jena):
    p = Path(db).parent / "q.rq"
    p.write_text(PFX + q, encoding="utf-8")
    t = time.time()
    r = sh(f"{jena}/tdb2.tdbquery --loc {db} --results=TSV --query {p}")
    return r.stdout.strip() + (("\nERROR: " + r.stderr[-800:]) if r.returncode else ""), time.time() - t


def recall_keys(db, jena):
    out, _ = query(db, """SELECT DISTINCT ?x WHERE { ?r a ex:RecallRecord .
        { ?r ex:relatedK510Record ?x } UNION { ?r ex:relatedPMARecord ?x } UNION { ?r ex:hasProductCode ?x } }""", jena)
    return {ln.strip().strip("<>") for ln in out.splitlines()[1:] if ln.startswith("<")}


def stream(path, keys, want, typ):
    """Walk a TurtleWriter file block by block; for blocks typed `typ`, count the ones
    whose links hit `keys` (premarket records or product codes that have a recall)."""
    c = collections.Counter()
    subj, links, flags = None, set(), set()

    def flush():
        if subj is None or f"a {typ}" not in flags:
            return
        c["records"] += 1
        if "ex:commercialDistributionStatus \"In Commercial" in flags and {x for x in links if "/k510/" in x or "/pma/" in x} & keys:
            c["in_commercial_distribution_and_premarket_record_recalled"] += 1
        for f in flags:
            c[f] += 1
        k5 = {x for x in links if "/k510/" in x or "/pma/" in x}
        pcs = {x for x in links if "/product-code/" in x}
        if k5:
            c["with_premarket_link"] += 1
        if k5 & keys:
            c["premarket_link_has_recall"] += 1
        if pcs & keys:
            c["product_code_has_recall"] += 1
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.startswith("<"):
                flush()
                subj, links, flags = None, set(), set()
                if want(ln):
                    subj = ln
            elif subj is not None and ln.startswith("    "):
                for m in re.finditer(r"<([^>]+)>", ln):
                    links.add(m.group(1))
                for f in ("ex:udiDI", "ex:lotNumber", "ex:hasProductCode", "ex:commercialDistributionStatus \"In Commercial",
                          f"a {typ}"):
                    if ln.lstrip().startswith(f):
                        flags.add(f)
        flush()
    c.pop(f"a {typ}", None)
    return c


GTIN = re.compile(r"(?<!\d)(\d{14})(?!\d)")


def gtin_join(out):
    """How many recalls quote a device identifier (14-digit GS1 DI) in their published
    lot/code text that matches a DI in the UDI layer? That is the barcode -> recall path."""
    by_recall = collections.defaultdict(set)
    for f in ("recalls.ttl", "recall_db.ttl"):
        subj = None
        with open(Path(out) / f, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if ln.startswith("<"):
                    subj = ln.strip()
                elif subj and ln.lstrip().startswith("ex:codeInfo "):
                    by_recall[subj].update(GTIN.findall(ln))
    wanted = set().union(*by_recall.values()) if by_recall else set()
    found = set()
    with open(Path(out) / "udi.ttl", encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if ln.startswith("    ex:identifierId "):
                m = GTIN.search(ln)
                if m and m.group(1) in wanted:
                    found.add(m.group(1))
    quoting = sum(1 for v in by_recall.values() if v)
    matched = sum(1 for v in by_recall.values() if v & found)
    return {"recalls_with_code_text": len(by_recall), "recalls_quoting_14_digit_DI": quoting,
            "distinct_DIs_quoted": len(wanted), "DIs_found_in_UDI_layer": len(found),
            "recalls_with_a_DI_in_UDI_layer": matched}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--recall-out", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--jena", default="/srv/medical-device-kg/tools/apache-jena-5.2.0/bin")
    ap.add_argument("--skip-stream", action="store_true")
    ap.add_argument("--skip-event", action="store_true", help="stream UDI only (MAUDE pass takes ~25 min)")
    ap.add_argument("--add", nargs="*", default=[], help="extra TTL files to load into an existing store")
    a = ap.parse_args()
    lines = ["# Client-readiness test -- recall intelligence", ""]
    lines.append(f"Store: {load(a.db, a.out, a.recall_out, a.jena)}")
    for f in a.add:                      # e.g. a recall_class.ttl that finished after the first load
        r = sh(f"{a.jena}/tdb2.tdbloader --loc {a.db} {f}")
        lines.append(f"Added {f}: {'ok' if r.returncode == 0 else r.stderr[-300:]}")
    for title, q in QUERIES:
        res, secs = query(a.db, q, a.jena)
        lines += ["", f"## {title}", f"_({secs:.1f} s)_", "", "```", res, "```"]
        print(f"done: {title} ({secs:.1f} s)", flush=True)
    if not a.skip_stream:
        t = time.time()
        g = gtin_join(a.out)
        lines += ["", f"## Barcode path: device identifiers quoted in recall code text ({time.time() - t:.0f} s)", "```"]
        lines += [f"{k}\t{v}" for k, v in g.items()] + ["```"]
        print("done: gtin join", g, flush=True)
        keys = recall_keys(a.db, a.jena)
        lines += ["", f"## Streaming joins (recall-linked keys: {len(keys)})"]
        streams = [("UDI device records", "udi.ttl", lambda s: s.startswith(f"<{ID}udi/"), "ex:UDIDeviceRecord")]
        if not a.skip_event:
            streams.append(("MAUDE adverse-event reports", "event.ttl", lambda s: s.startswith(f"<{ID}report/"), "ex:MAUDEReport"))
        for name, f, want, typ in streams:
            t = time.time()
            c = stream(Path(a.out) / f, keys, want, typ)
            lines += ["", f"### {name} ({time.time() - t:.0f} s)", "```"] + [f"{k}\t{v}" for k, v in sorted(c.items())] + ["```"]
            print(f"done: stream {f}", flush=True)
    Path(a.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("report:", a.report)


if __name__ == "__main__":
    main()
