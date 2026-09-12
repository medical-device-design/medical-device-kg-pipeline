# Medical Device Design Knowledge Graph

An RDF knowledge graph unifying United States FDA medical device data — premarket
clearances and approvals, product classification, regulations, recognized standards,
recalls, adverse events, postmarket studies, manufacturer registrations, and facility
records — so that device designers can query across systems FDA publishes separately.

Published on the NSF Proto-OKN Fabric as **`medical-device-kg`**.

| | |
|---|---|
| SPARQL endpoint | `https://apps.okn.us/medical-device-kg/sparql` |
| Registry entry | https://registry.okn.us/registry/kgs/medical-device-kg/ |
| Persistent identifier | https://purl.org/okn/frink/kg/medical-device-kg |
| Triple Pattern Fragments | https://apps.okn.us/ldf/medical-device-kg |
| Scale | ~17.9M triples (17,862,535) · 887K subjects · 30 populated classes · incl. 9,120 geocoded facilities |

## Support

- **NSF** Proto-OKN subaward 5136002 under prime award
  [#2535091](https://www.nsf.gov/awardsearch/show-award/?AWD_ID=2535091) (UNC-Chapel Hill)
- **NIH/NIBIB** R25EB035522 — the education platform at https://www.bmedesign.org

Hofstra University · PI: Roche C. de Guzman

---

## Namespaces

```
https://bmedesign.org/medical-device-kg/ns/    classes and properties
https://bmedesign.org/medical-device-kg/id/    instances
```

Both are on a domain the project owns. The exact form is load-bearing — `https`, no `www`,
trailing slash — because IRIs are compared as exact strings.

> **Migration in progress.** The currently published graph still uses
> `http://medicaldevice.com/ontology/`, a domain the project does **not** own. Replacing it
> is scheduled for the first rebuild after 25 September 2026. See
> `scripts/migrate_namespace.sh`. Queries written against the live endpoint today must still
> use the old prefix.

---

## Data sources

All from FDA, via the [openFDA API](https://open.fda.gov/) except where noted.

**Premarket** — 510(k), PMA, PMA supplements and design changes, De Novo, HDE,
Devices@FDA catalog
**Classification and regulation** — Product Classification (21 CFR 862–892),
Recognized Consensus Standards
**Postmarket safety** — MAUDE adverse events, MedSun, Device Recalls, Section 522
postmarket surveillance studies, TPLC (scraped)
**Identification** — AccessGUDID / UDI
**Establishments and facilities** — Registration & Listing, Device Listing, MQSA certified
mammography facilities, X-ray assemblers
**Laboratory** — CLIA test systems, CLIA waived analytes, IVD home-use (OTC) tests

### A note on the skip ceiling

openFDA caps pagination at 25,000 records. Any query exceeding it is **silently truncated**.
The pipeline detects this and falls back to date-range chunking, escalating
yearly → quarterly → monthly → weekly → daily until each window fits. Any extraction that
does not do this is incomplete regardless of what it reports.

---

## Repository layout

```
pipeline/
  iri.py                  the single source of every IRI (namespace migration = 2-line edit)
  common.py               typed literals, provenance, Turtle serialization
  distribution_parser.py  turns FDA's free-text recall distribution into structured keys
  udi/                    UDI (AccessGUDID) fetch + RDF conversion
ontology/
  ontology.ttl            vocabulary (T-Box), reconciled against the live data
  shapes/                 SHACL constraint shapes
docs/                     rebuild strategy, VM workflow, schema, worked demos
scripts/                  publish_to_lakefs.sh, migrate_namespace.sh
```

`iri.py`, `common.py`, and each source module carry self-tests — run the file directly
(`python3 pipeline/iri.py`) to verify it before building.

Generated Turtle is **not** stored here. It lives in lakeFS, which is version-controlled and
built for data at this scale. See [PUBLISHING.md](PUBLISHING.md) and, for how the team works
on the shared VM, [docs/VM_WORKFLOW.md](docs/VM_WORKFLOW.md).

## Rebuild in progress

The **live** published graph is the original build (old namespace, the gaps listed below).
This repository also contains the **rebuild** that corrects them — foundation modules
(`iri.py`, `common.py`, reconciled `ontology.ttl`, SHACL shapes) are complete and self-tested;
the regenerated graph deploys after 25 September 2026. See
[docs/rebuild-strategy.md](docs/rebuild-strategy.md).

---

## Publishing

Read [PUBLISHING.md](PUBLISHING.md) before attempting to publish. Three things there are
not obvious and each has cost someone time:

1. GitHub is not in the upload path — data goes to lakeFS at `repository.okn.us`
2. A lakeFS upload does not exist until you **commit** it in the UI
3. The **tag** is what deploys; merging to `main` only triggers conversion

---

## Reproducibility

Every published graph should be traceable to the code that built it. The pipeline stamps
`ex:extractionVersion` with the git commit SHA of this repository, alongside `ex:extractedAt`,
`ex:retrievedAt`, `ex:sourceRecordId`, and `ex:sourceUrl` on each record.

Given any triple, you can therefore recover both the FDA record it came from and the code
state that produced it.

---

## Using the graph responsibly

These apply to any analysis built on this graph and are not optional caveats.

- **Report counts are not failure rates.** MAUDE is passive surveillance. Counts reflect how
  widely a device is used, how long it has been marketed, and reporting behaviour. The graph
  holds no utilization denominators, so a count can never be presented as a rate.
- **FDA does not verify causation.** Say "reports associated with", not "failures caused by".
- **Absence of reports is not evidence of safety.**
- **Derived scores are ours, not FDA's.** Any risk index computed here is a project-defined
  educational measure carrying no regulatory weight.
- **Not regulatory or medical advice.**

---

## Known gaps

Tracked in the project's data-quality audit against the
[Proto-OKN graph construction guidelines](https://registry.okn.us/book/best-practices/).
Two columns: what is still true of the **live** graph, and what the **rebuild** in this
repo already addresses (deploying after 25 September 2026).

**Live graph, still outstanding:**

- Namespace on a third-party domain (migration above)
- Dates stored as `YYYYMMDD` strings rather than `xsd:date`, so no date arithmetic
- Class-name drift between ontology and data (e.g. `XRayAssemblerRecord` vs `XrayAssemblerRecord`)
- ~18 declared-but-unpopulated classes; two populated classes (`CFRTitle21Section`, `CfrPart`) undeclared
- No SHACL shapes
- No `owl:sameAs` / `skos:exactMatch` mappings to external vocabularies (NCIt, Wikidata, GMDN)
- Manufacturer names not normalized ("A Biomed" vs "A Biomed Inc.")

**Fixed in the rebuild (this repo):**

- Dates typed `xsd:date`, booleans `xsd:boolean`, counts `xsd:integer` (`common.py`, validated)
- Class names reconciled to the data; unpopulated classes removed; `CFRTitle21Section`/`CfrPart`
  declared; `Recall` vs `RecallRecord` documented (`ontology.ttl`)
- `ProductCode` made an explicit hub with a label
- SHACL shapes enforcing the above (`ontology/shapes/`)
- Every IRI minted from one module, making namespace migration a two-line change (`iri.py`)

**Still open after the rebuild:** external-vocabulary mappings, manufacturer-name resolution
(Manufacturer↔Labeler is a scored `skos:closeMatch` problem, never `owl:sameAs`), and growing
competency questions toward the 10–20 the guidelines ask for.

Listing these is deliberate. A graph whose limitations are written down is more usable than
one whose limitations are discovered.

---

## License

Data derived from US FDA public records, which are not subject to copyright. This
repository's code and the ontology are released under CC BY 4.0 — see [LICENSE](LICENSE).

FDA does not endorse this project. The FDA logo is not used.

## Citation

See [CITATION.cff](CITATION.cff).
