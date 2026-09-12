# Ingesting AccessGUDID (UDI)

5,182,695 records · 1,895 MB compressed · 52 partitions · openFDA `device/udi`

## Why this source, and why first

UDI is the only FDA database that describes **how marketed devices are physically
built**. Everything else tells you about paperwork; this tells you about the device.

```
is_sterile + sterilization method        is_single_use        is_kit
is_combination_product                   mri_safety           is_labeled_as_nrl (latex)
storage[] with typed temperature ranges  device_count_in_base_package
has_lot_or_batch_number / serial / expiration
```

*"What sterilization method do competing devices in my product code use, and how has
that changed?"* is answerable here and nowhere else — including by web search, which
returns vendor marketing rather than the labelled attribute.

It is also the **only endpoint carrying GMDN terms, FDA product codes, premarket
submission numbers and a DUNS number at once**, which makes it the bridge between the
regulatory records already in the graph and the physical device.

It was listed as a harvested source in the pipeline README and declared in
`ontology.ttl` as `AccessGUDIDRecord` — and the published graph contains **zero**
instances. This ingest closes that gap.

---

## Running it

```bash
# 1. Smoke test first -- two partitions, 500 records. Takes a minute.
python3 pipeline/udi/fetch_udi.py --output downloads/udi --max-partitions 2
python3 pipeline/udi/udi_to_rdf.py --input downloads/udi --output out/udi-smoke.ttl --limit 500

riot --validate out/udi-smoke.ttl
riot --count    out/udi-smoke.ttl

# 2. Full run
python3 pipeline/udi/fetch_udi.py --output downloads/udi          # ~1.9 GB, 30-60 min
python3 pipeline/udi/udi_to_rdf.py --input downloads/udi --output out/udi.ttl

riot --validate out/udi.ttl

# 3. Publish (see PUBLISHING.md -- the tag is what deploys)
./scripts/publish_to_lakefs.sh out/
```

**Do the smoke test.** It exercises every code path in the converter for the cost of a
minute, and it will surface a schema drift on FDA's side before you have spent an hour
downloading.

### Measured scale — smoke test run 2026-09-07

Executed on WSL2 (Ubuntu, Python 3.10.12), partition 1 of 52, first 500 records:

```
records          500
triples          26,684          -> 53.4 triples per record
GMDN terms       233
labelers (DUNS)  200
output           1.6 MB          -> 3.28 KB per record
```

Extrapolated to the full 5,182,695 records:

| | |
|---|---|
| Download | 1,895 MB compressed, 52 partitions |
| **Triples** | **~277 million** |
| **Turtle** | **~17 GB** |

That is roughly **16× the current 17.7M-triple graph** — higher than the initial estimate,
because UDI records are richer than assumed. Still far short of the 43× a full MAUDE ingest
would cost, and unlike MAUDE every triple here answers a design question.

**Confirm with Yaphet Kebede that the Fabric can accept ~277M triples before uploading.**
FRINK's automatic HDT conversion has probably not been handed a graph this size from this
project before.

### Smoke test findings

**One real bug caught.** `sterilization.sterilization_methods` arrives as a JSON **list**,
not the semicolon-delimited string the FDA documentation implies. The converter now
normalises either shape via `as_list()`. This is exactly why the smoke test exists — the
failure would otherwise have surfaced an hour into a full run.

**Joins verified against the live endpoint.** Five IRIs minted by the converter were
queried against `apps.okn.us` and all five resolve to existing nodes:

| Minted IRI | Resolves to |
|---|---|
| `k510/k033394`, `k510/k131407`, `k510/k180179` | `ex:K510Record` |
| `product-code/jds`, `product-code/hsb` | `ex:ProductCode` |

In 500 records the converter produced **158 `relatedK510Record`** and **4
`relatedPMARecord`** edges — roughly a third of UDI devices link to a clearance record.
Across the full set that is on the order of **1.6M device-to-clearance edges**, none of
which exist in the graph today.

**Typing confirmed correct.** 8,733 `xsd:boolean` and 1,000 `xsd:date` literals in the
sample — no untyped date strings, no `"true"` strings.

---

## Design decisions worth knowing

### The joins are the point

Three IRI patterns are minted deliberately to match nodes **already in the graph**,
verified against the live endpoint on 2026-09-07:

| Join | IRI pattern | Existing example |
|---|---|---|
| Product code | `id:product-code/<lowercase>` | `.../ontology/product-code/brt` |
| 510(k) / De Novo | `id:k510/<lowercase>` | `.../ontology/k510/den000001` |
| PMA | `id:pma/<lowercase>` | `.../ontology/pma/n10389/supplement/s001` |

`relatedK510Record` and `relatedPMARecord` are the highest-value edges this ingest adds:
they connect a **physical marketed device** to the **regulatory record that authorised
it**. Nothing in the graph does that today.

> **If you run this before the namespace migration**, change `NS_BASE` and `ID_BASE` at
> the top of `udi_to_rdf.py` to `http://medicaldevice.com/ontology/`, or the joins will
> silently miss and you will get isolated UDI nodes.

### Labeler is NOT Manufacturer, and that is intentional

UDI identifies the labeler by **D-U-N-S**. The graph's existing `Manufacturer` nodes are
keyed on FEI or on a slugified name. **FDA publishes no FEI-to-DUNS crosswalk.**

So UDI labelers are minted as a separate `ex:Labeler` class at `id:labeler/<duns>`.
Merging them into `Manufacturer` would be a guess dressed up as a fact.

Resolving the two is real work — name normalisation plus address blocking — and the
result should be `skos:closeMatch` with a confidence score, never `owl:sameAs`. This is
the single largest entity-resolution problem in the FDA estate and it deserves its own
work item.

### Booleans arrive as strings

openFDA serialises every UDI boolean as the *string* `"true"` / `"false"`. The converter
emits real `xsd:boolean`, so `FILTER(?sterile = true)` works as expected.

### Dates are already ISO 8601

`publish_date` and `public_version_date` come through as `2016-09-12`. The converter
emits `xsd:date`. This is a deliberate departure from the rest of the graph, which
stores untyped `YYYYMMDD` strings — for most endpoints openFDA supplies clean ISO dates
and the existing converter is flattening them. UDI does it correctly; the other
converters should be brought into line.

### GMDN is the strategic asset

GMDN is the **only external controlled vocabulary anywhere in FDA device data**. Every
other identifier in the graph — product codes, regulation numbers, FEI — is FDA-specific
and appears in no other graph on the Proto-OKN Fabric.

GMDN terms are emitted as shared nodes at `id:gmdn/<code>` carrying name, definition,
implantable flag and status. That makes them the natural anchor for future
`skos:exactMatch` links outward, and the most likely route to the biomedical half of the
Fabric.

---

## Verification queries

Run after ingest. All should return non-zero.

```sparql
# Devices by sterilisation status for one product code
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?sterile (COUNT(*) AS ?n) WHERE {
  ?d a ex:UDIDeviceRecord ;
     ex:hasProductCode <https://bmedesign.org/medical-device-kg/id/product-code/brt> ;
     ex:isSterile ?sterile .
} GROUP BY ?sterile
```

```sparql
# THE JOIN THAT MATTERS -- physical device linked to its clearance record
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?brand ?k ?applicant WHERE {
  ?d a ex:UDIDeviceRecord ;
     ex:brandName ?brand ;
     ex:relatedK510Record ?k .
  ?k ex:applicant ?applicant .
} LIMIT 20
```

```sparql
# Implantable devices with MRI safety labelling -- a real design question
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?mri (COUNT(*) AS ?n) WHERE {
  ?d a ex:UDIDeviceRecord ;
     ex:hasGMDNTerm ?g ;
     ex:mriSafetyStatus ?mri .
  ?g ex:gmdnImplantable true .
} GROUP BY ?mri ORDER BY DESC(?n)
```

```sparql
# Latex labelling by medical specialty -- joins UDI to classification
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?specialty (COUNT(*) AS ?n) WHERE {
  ?d a ex:UDIDeviceRecord ;
     ex:isLabeledAsNRL true ;
     ex:hasProductCode ?pc .
  ?cls ex:hasProductCode ?pc ; ex:medicalSpecialtyDescription ?specialty .
} GROUP BY ?specialty ORDER BY DESC(?n)
```

If the second query returns zero rows, the namespace constants are wrong for the graph
you loaded into — see the warning above.

---

## Known limitations

- **No storage or premarket_submissions on many records.** Both are optional in GUDID.
  The converter handles absence; do not read a missing value as a negative.
- **`sterilization_methods` is a semicolon-delimited string** in the source. The
  converter splits it into repeated `ex:sterilizationMethod` triples, which is right for
  querying but means a device can carry several.
- **GMDN codes are not IRIs anywhere else.** We mint `id:gmdn/<code>`. If GMDN Agency or
  another OKN graph later publishes canonical IRIs, add `skos:exactMatch` rather than
  re-minting.
- **`is_labeled_as_nrl` and `is_labeled_as_no_nrl` are independent fields.** Both false
  means "not stated", not "no latex". Do not collapse them into one boolean.
