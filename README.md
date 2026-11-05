# Medical Device Design Knowledge Graph

An RDF knowledge graph unifying United States FDA medical device data — premarket
clearances and approvals, product classification, regulations, recognized standards,
device identifiers, recalls, adverse events, postmarket studies, manufacturer registrations,
and facility records — so that device designers can query across systems FDA publishes separately.

Published on the NSF Proto-OKN Fabric as **`medical-device-kg`**.

| | |
|---|---|
| SPARQL endpoint | `https://apps.okn.us/medical-device-kg/sparql` |
| Registry entry | https://registry.okn.us/registry/kgs/medical-device-kg/ |
| Persistent identifier | https://purl.org/okn/frink/kg/medical-device-kg |
| Triple Pattern Fragments | https://apps.okn.us/kgf/medical-device-kg/latest/tpf |
| Release | v0.0.5 (October 2026), built by this repository |
| Scale | about 1.31 billion triples: MAUDE 26.1 M reports, UDI 5.18 M devices, 59,471 recalls, 235,500 Devices@FDA documents |

## Support

- **NSF** Proto-OKN subaward 5136002 under prime award
  [#2535091](https://www.nsf.gov/awardsearch/show-award/?AWD_ID=2535091) (UNC-Chapel Hill)
- **NIH/NIBIB** R25EB035522 — the education platform at https://www.bmedesign.org

PI: Roche C. de Guzman, Ph.D. (Hofstra University)

---

## Namespaces

```
https://bmedesign.org/medical-device-kg/ns/    classes and properties
https://bmedesign.org/medical-device-kg/id/    instances
```

Both are on a domain the project owns. The exact form is load-bearing — `https`, no `www`,
trailing slash — because IRIs are compared as exact strings.

> **Namespace changed in v0.0.5.** Releases up to v0.0.4 used
> `http://medicaldevice.com/ontology/`, a domain the project does not own. From v0.0.5 every
> term and instance is under `bmedesign.org`. Queries written for v0.0.4 must change their
> prefix; class and property names are listed in `ontology/ontology.ttl`.

Example queries for the okn.us query interface are in [`queries/`](queries/).

---

## Data sources

All from FDA, via the [openFDA API](https://open.fda.gov/) or FDA's own bulk files and
pages, except where noted. Each source is one module in `pipeline/sources/`.

**Premarket** — 510(k) and De Novo, PMA and supplements, HDE, Devices@FDA decision documents
**Classification and regulation** — Product Classification, all of 21 CFR (device parts
flagged), Recognized Consensus Standards
**Postmarket safety** — MAUDE adverse events (full history; narratives scrubbed of personal
details), recall enforcement reports and the recall database, recall class from FDA recall
pages, TPLC summaries rebuilt from MAUDE, Section 522 postmarket studies
**Identification** — AccessGUDID / UDI, with GS1 device identifiers quoted in recall code text
linked to their UDI records
**Establishments and facilities** — Registration & Listing, MQSA certified mammography
facilities, X-ray assembler reports, US Census geocodes, NAICS crosswalk (inferred, labelled)
**Laboratory** — CLIA categorizations, test systems and analytes (FDA and CDC), IVD home-use (OTC) tests

Contact persons are not ingested from any source.

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
  scrub.py                removes personal details from free text (MAUDE narratives)
  distribution_parser.py  turns FDA's free-text recall distribution into structured keys
  sources/                one module per FDA source: fetch, transform, validate, self-test
ontology/
  ontology.ttl            vocabulary (T-Box)
  shapes/                 SHACL constraint shapes
queries/                  example SPARQL queries (.rq) for the okn.us query interface
docs/                     change logs, comparisons, rebuild strategy, VM workflow
scripts/                  build, validation, weekly recall monitor, lakeFS release watch
```

`iri.py`, `common.py`, and each source module carry self-tests — run the file directly
(`python3 pipeline/iri.py`) to verify it before building.

Generated Turtle is **not** stored here. It lives in lakeFS, which is version-controlled and
built for data at this scale. See [PUBLISHING.md](PUBLISHING.md) and, for how the team works
on the shared VM, [docs/VM_WORKFLOW.md](docs/VM_WORKFLOW.md).

## Release history

- **v0.0.5 (October 2026):** full rebuild from FDA sources on the `bmedesign.org` namespace.
  What changed and why: [docs/CHANGES_2026-10-09.md](docs/CHANGES_2026-10-09.md),
  [docs/CHANGES_2026-10-10.md](docs/CHANGES_2026-10-10.md). Class-by-class comparison with
  v0.0.4: [docs/OLD_VS_NEW_2026-10-10.md](docs/OLD_VS_NEW_2026-10-10.md).
- **v0.0.1–v0.0.4 (2026):** original build, 17.9 M triples, old namespace.

---

## Publishing

Read [PUBLISHING.md](PUBLISHING.md) before attempting to publish. Three things there are
not obvious and each has cost someone time:

1. GitHub is not in the upload path — data goes to lakeFS at `repository.okn.us`
2. Upload to the `develop` branch and **commit**; then merge `develop` into `main`
3. Merging into `main` only triggers conversion to a `stable_vX_Y_Z` branch; the **tag** is
   what deploys

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
- **Derived scores and mappings are ours, not FDA's.** The NAICS crosswalk and any risk index
  computed here are project-defined and carry no regulatory weight.
- **Not regulatory or medical advice.**

---

## Known gaps

Tracked against the
[Proto-OKN graph construction guidelines](https://registry.okn.us/book/best-practices/).

**Fixed in v0.0.5:**

- Namespace moved to a project-owned domain
- Dates typed `xsd:date`, booleans `xsd:boolean`, counts `xsd:integer`
- Class names reconciled; unpopulated classes removed; every emitted term declared (CI gate)
- `ProductCode` made an explicit hub with a label
- SHACL shapes for premarket, postmarket, establishment, UDI, TPLC and identity records
- Every IRI minted from one module (`iri.py`)
- Manufacturers keyed by FEI; name variants kept as `FirmName` with identity mappings

**Still open:**

- External-vocabulary mappings (NCIt, Wikidata, GMDN)
- Manufacturer↔Labeler resolution (a scored `skos:closeMatch` problem, never `owl:sameAs`)
- Recall distribution parsed for US states only, not foreign countries
- Growing competency questions toward the 10–20 the guidelines ask for

Listing these is deliberate. A graph whose limitations are written down is more usable than
one whose limitations are discovered.

---

## License

The graph, ontology, code and transformations are dedicated to the public domain under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/), as provided in the statement of
work of NSF Proto-OKN subaward 5136002. Anyone may use them, including commercially, without
permission; citation is appreciated (see [CITATION.cff](CITATION.cff)). The underlying FDA
records are U.S. government works and not subject to copyright. Third-party terminology (GMDN
term text in UDI records) is excluded from the dedication. See [LICENSE](LICENSE).

FDA does not endorse this project. The FDA logo is not used.

## Citation

See [CITATION.cff](CITATION.cff).
