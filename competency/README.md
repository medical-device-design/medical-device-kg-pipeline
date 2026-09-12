# Competency questions

SPARQL questions the graph must answer. They are the functional half of the
pre-publish gate (see `../scripts/validate.sh`): `riot` + SHACL check that the
graph is *well-formed*; these check that it is *useful* and that the joins land.

## Running

No triplestore needed — Apache Jena's `arq` runs a query straight over Turtle:

    arq --data out/udi.ttl --data out/recalls.ttl --query competency/cq04_device_to_clearance.rq

Or run the whole set as part of the gate:

    ./scripts/validate.sh out/

For interactive exploration, load the TTL into Jena TDB2 and serve with Fuseki:

    tdb2.tdbloader --loc /srv/medical-device-kg/tdb out/*.ttl
    fuseki-server --loc /srv/medical-device-kg/tdb /mdkg      # then http://localhost:3030

TDB2 is disk-based, so it is not RAM-bound the way a default GraphDB load is —
which matters at 17.9M triples now and ~277M after the UDI ingest.

## Namespace

These use the rebuild namespace `https://bmedesign.org/medical-device-kg/ns/`.
To run against the *currently published* graph (still on the old namespace),
replace the `ex:` prefix with `http://medicaldevice.com/ontology/` and the
instance base accordingly. After the October migration, no change is needed.

## The set

| File | Question | Kind |
|---|---|---|
| cq01 | Which record types link to a product code, and how many? | connectivity |
| cq02 | How many recalls in each classification (Class I/II/III)? | coverage |
| cq03 | Which recalls were initiated in 2024? | date typing (D4) |
| cq04 | Which devices link to the 510(k) that cleared them? | the marquee join |
| cq05 | What sterilization methods do devices in a product code use? | design attribute |
| cq06 | MRI-safety statuses among implantable devices? | cross-attribute |
| cq07 | UDI devices with no outbound edge (orphans)? | DEFECT GUARD — expect 0 |
| cq08 | UDI device records missing provenance? | DEFECT GUARD — expect 0 |
| cq09 | Instance count per class | coverage snapshot |
| cq10 | Recalls distributed to a given US state | distribution structuring |

Guards (cq07, cq08) must return **zero rows**. Any row is a defect the SHACL
shapes should also have caught — treat a hit as a build failure.

Grow this toward the 10–20 the Proto-OKN guidelines ask for; good competency
questions double as demo material.
