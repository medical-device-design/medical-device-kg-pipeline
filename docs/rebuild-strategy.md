# Rebuild Strategy

**Decided 2026-09-09.** Execution host: **Hofstra VM 10.22.13.83**, cron-scheduled.
Scope: **full RDF model rebuild**, done as a single regeneration together with the
namespace migration.

---

## The governing decision: one rebuild, not three

Three changes are pending, and each one rewrites every triple in the graph:

| Change | Why it rewrites everything |
|---|---|
| Namespace migration | Every subject, predicate and object IRI changes |
| Model fixes | Class names, date typing, edge topology all change |
| New sources (UDI first) | Regenerated alongside, or they inherit the old model |

Done separately that is three full regenerations of 1.3 GB, three validation passes,
three FRINK republications, and three chances for the crosswalk network at SDSC to
break. **Do them once.**

Nothing regenerates before **September 25** (final Proto-OKN meeting). The graph
currently answers questions correctly and is cited in a live demo; it stays frozen
until then.

---

## Defect register

Everything below was verified against the live endpoint or the lakeFS contents, not
assumed. Fix column says which stage closes it.

| # | Defect | Evidence | Fix |
|---|---|---|---|
| D1 | `MedicalDevice` is an orphan class | 4,099 nodes, `rdf:type` + label only, **zero outgoing edges** | S1 |
| D2 | Class-name drift, ontology vs data | ontology declares `DevicesFDARecord`, `HDERecord`; data emits `DeviceFDARecord`, `HumanitarianDeviceExemption` | S1 |
| D3 | 21 declared classes never populated | 50 declared, 29 populated | S1 |
| D4 | Dates are untyped `YYYYMMDD` strings | no range queries possible; openFDA supplies ISO dates, converter flattens them | S1 |
| D5 | `rdfs:label` missing on several classes | absent on MAUDEReport, MedSun, AccessGUDID, CLIA | S1 |
| D6 | Custom `ex:label` alongside `rdfs:label` | two label predicates for one purpose | S1 |
| D7 | No SHACL shapes | D1–D6 all shipped undetected | S2 |
| D8 | Namespace on a third-party domain | `medicaldevice.com` is owned by someone else and is for sale | S1 |
| D9 | MAUDE at 0.059% coverage | 15,285 of 25,711,469 | S3 |
| D10 | Consensus standards at 3.5% | 49 of ~1,400 | S3 |
| D11 | Converters not reproducible | no way to prove the graph matches the sources | S2 |
| D12 | Publication is manual | someone uploads and tags by hand | S4 |

**D9 and D10 are symptoms of D11, not of effort.** The pipeline acquires by paging the
openFDA API, which refuses `skip` beyond 25,000 records. Bulk downloads
(`https://api.fda.gov/download.json`) have no such ceiling. Changing the acquisition
method closes both coverage gaps as a side effect.

---

## Stage 1 — Fix the model

### 1a. Keep the record classes. They are correct.

The instinct is to call the record-centric shape a bug. It is not. `K510Record`,
`PMARecord`, `RecallRecord` and the rest are faithful to what FDA actually publishes:
regulatory *events*, not devices. Preserving that is what makes every figure traceable
to a source record, which is the graph's main virtue. **Do not collapse records into
devices.**

### 1b. Make `ProductCode` the explicit hub.

Every query in the September 8 demo routed through a product code. That is already the
hub in practice; the model should say so. After the rebuild, `ProductCode` carries
inbound edges from every record class and outbound edges to classification and
regulation:

```turtle
# BEFORE — product code reachable only from record subjects, ad hoc
id:k510/k033394  ns:hasProductCode  id:product-code/frn .

# AFTER — the same edge, plus the hub is navigable in both directions
id:product-code/frn
    a                             ns:ProductCode ;
    rdfs:label                    "Pump, Infusion" ;
    ns:hasClassification          id:classification/frn ;
    ns:hasRegulationNumber        id:regulation/880_5725 ;
    ns:hasCFRSection              id:cfr-section/880_5725 ;
    ns:deviceClass                "2" ;
    ns:medicalSpecialtyDescription "General Hospital" .
```

### 1c. Resolve `MedicalDevice` honestly.

The 4,099 orphan nodes cannot be given edges without inventing identity that FDA does
not publish. Two defensible options, and only one of them is honest:

- **Chosen:** demote `MedicalDevice` until UDI lands. UDI is the only FDA source with a
  genuine device identifier (the DI). When UDI is ingested, `UDIDeviceRecord` keyed on
  DI becomes the real device layer, linked to `ProductCode`, `K510Record` and `PMARecord`
  by edges that already verified against the live endpoint (158 `relatedK510Record` and
  4 `relatedPMARecord` per 500 records in the smoke test).
- **Rejected:** fabricate device identity by name-matching across sources. That produces
  a graph that looks richer and is wrong.

Until then, the orphan nodes are either given their real edges (where a source supports
it) or removed. A class with 4,099 members and no edges is worse than no class.

### 1d. Type everything.

```turtle
# BEFORE
ns:decisionDate  "20040630" .

# AFTER
ns:decisionDate  "2004-06-30"^^xsd:date .
```

Same treatment for booleans (openFDA serialises them as the strings `"true"`/`"false"`)
and for counts. The UDI converter already does this correctly and is the reference
implementation.

### 1e. One label predicate.

`rdfs:label` everywhere. `ex:label` is removed. Every class gets one.

### 1f. Namespace.

```
vocabulary  https://bmedesign.org/medical-device-kg/ns/
instances   https://bmedesign.org/medical-device-kg/id/
```

Domain is owned through 2030-10-30, registrar lock already on. The FRINK shortname
`medical-device-kg` does not change — changing it breaks the registry and the crosswalks.

---

## Stage 2 — Rebuild the pipeline

### Repository layout

```
medical-device-kg/
├── ontology/
│   ├── ontology.ttl              # T-Box, hand-maintained, the only hand-edited TTL
│   └── shapes/                   # SHACL, one file per class family
├── pipeline/
│   ├── iri.py                    # THE ONLY PLACE IRIs ARE MINTED
│   ├── common.py                 # typed literals, provenance, date parsing
│   ├── sources/
│   │   ├── k510.py               # one module per FDA source
│   │   ├── pma.py
│   │   ├── recalls.py
│   │   ├── tplc.py
│   │   ├── udi.py
│   │   └── ...
│   └── run.py                    # orchestrator
├── scripts/
│   ├── fetch_all.sh
│   ├── validate.sh
│   └── publish_to_lakefs.sh
└── docs/
```

### Module contract

Every source module implements the same three functions, and nothing else:

```python
def fetch(dest: Path) -> Manifest:
    """Bulk download only. Never paginate the API.
    Record source URL, HTTP ETag, download date, SHA-256 of each file."""

def transform(src: Path, out: Path) -> Stats:
    """Rows -> RDF. Deterministic: same input, byte-identical output.
    All IRIs from iri.py. All literals typed. Never writes outside `out`."""

def validate(ttl: Path) -> Report:
    """riot --validate, then SHACL. Non-zero exit on any violation."""
```

Three rules that make the rest work:

1. **`iri.py` is the only module that constructs an IRI.** This is what turns a future
   namespace change from a 1.3 GB text substitution into a one-line edit. It is the
   single most valuable file in the repository.
2. **Determinism.** Sort before serialising; no timestamps in output except an explicit
   `retrievedAt` from the manifest. A rerun that produces a different byte stream means
   FDA changed, and that should be visible in the lakeFS diff.
3. **Bulk downloads only.** Closes D9 and D10.

### Provenance, on every record

```turtle
id:recall/94520
    ns:sourceDataset    "device/recall" ;
    ns:sourceUrl        <https://api.fda.gov/download.json> ;
    ns:sourceRecordId   "94520" ;
    ns:retrievedAt      "2026-10-02"^^xsd:date .
```

This already exists in the current graph (678,590 `sourceRecordId` values, verified) and
is one of its genuine strengths. Preserve it exactly.

---

## Stage 3 — SHACL gates

Shapes encode the defect register so no fixed defect can return. Minimum set:

```turtle
:ProductCodeShape a sh:NodeShape ;
    sh:targetClass ns:ProductCode ;
    sh:property [ sh:path rdfs:label ; sh:minCount 1 ] ;
    sh:property [ sh:path ns:deviceClass ; sh:minCount 1 ] .

:NoOrphanClassShape a sh:NodeShape ;
    sh:targetClass ns:MedicalDevice ;
    sh:property [ sh:path [ sh:zeroOrMorePath ns:hasProductCode ] ;
                  sh:minCount 1 ;
                  sh:message "MedicalDevice with no outgoing edges (defect D1)" ] .

:DateShape a sh:NodeShape ;
    sh:targetObjectsOf ns:decisionDate ;
    sh:datatype xsd:date ;
    sh:message "Untyped date string (defect D4)" .
```

Plus two checks that are not SHACL but belong in the same gate:

- **Ontology/data agreement** — every class used in the data is declared, and every
  declared class is populated. Closes D2 and D3 permanently.
- **Triple-count diff** — compare against the previous release. A swing beyond ±20% on
  any source stops the run and requires a human. Catches a truncated FDA download, which
  is the most likely silent failure.

---

## Stage 4 — Automated release to lakeFS

### The protocol

```
cron on .83
  └─ fetch          bulk download, write manifest
  └─ transform      TTL per source
  └─ validate       riot + SHACL + count diff     ── fails here? stop, notify, no upload
  └─ lakectl branch create  refresh-YYYY-MM-DD from main
  └─ lakectl fs upload      the changed TTLs only
  └─ lakectl commit         message includes source, record count, retrieval date
  └─ lakectl merge          into main
  └─ lakectl tag create     v0.MINOR.PATCH        ── the tag is what deploys
```

**The tag is the deployment trigger.** The `_lakefs_actions/` hooks already in the repo
(`lakefs-action-deploy.yaml`, `lakefs-action-hdt.yaml`, `validate-release-tag.yaml`)
handle publication to FRINK. Nothing reaches `main` that failed validation, and nothing
reaches FRINK that was not tagged.

Existing tags run `v0.0.1`–`v0.0.4`; continue that series.

### Cadence, by how fast the source actually changes

| Sources | Cadence | Cron |
|---|---|---|
| recalls, MAUDE, TPLC | monthly | `0 2 1 * *` |
| 510(k), PMA, PMA design changes, De Novo, HDE | quarterly | `0 2 5 1,4,7,10 *` |
| registration & listing, establishments | quarterly | `0 2 8 1,4,7,10 *` |
| classification, CFR 21, product codes | annually | `0 2 15 1 *` |
| consensus standards | annually | `0 2 16 1 *` |
| UDI | quarterly, once loaded | `0 2 12 1,4,7,10 *` |

Stagger the dates. Do not have every source contend for disk and bandwidth on the same
night — the last storage incident is what caused the KG to be deleted from .55.

### Operational preconditions on .83

Before any of this runs, and these are real blockers today:

- **.83 must be reachable.** As of 2026-09-08 it did not answer ping or port 22 over the
  VPN, while .55 did.
- **Working credentials**, on an account that is not a student's personal login. The
  `admin` password supplied for .55 does not authenticate, though password auth is
  enabled on the host.
- **Disk headroom.** 1.3 GB of TTL today; UDI adds roughly 17 GB more, plus downloads,
  plus one previous release retained for the count diff. Budget **80 GB minimum**.
- **lakeFS config** at `~/.lakectl.yaml` on the VM, owned by the service account, mode
  `600`. Not the copy in OneDrive.
- **Failure notification.** A cron job that fails silently is worse than no cron job.
  Mail on non-zero exit, to Roche, not only to a student.

---

## Sequence

| When | Step | Depends on |
|---|---|---|
| Now → Sep 25 | **Freeze.** No regeneration. Graph stays as demonstrated. | — |
| Now → Sep 25 | Write `iri.py`, `common.py`, SHACL shapes. Pure authoring, touches nothing live. | — |
| Now → Sep 25 | Resolve .83 access and disk. Get credentials on a service account. | Prabhjot, Hofstra IT |
| Now → Sep 25 | Complete repo transfer to `medical-device-design`; registry `homepage:` PR. | Prabhjot accepting the org invite |
| Sep 26 | Rewrite source modules against the new contract. Bulk downloads throughout. | `iri.py` |
| Oct | **The one rebuild.** New namespace + fixed model + full-coverage MAUDE and standards, all regenerated together. Tag `v0.1.0`. | all of the above |
| Oct | Notify Peter Rose (SDSC) before publishing — IRIs change, crosswalks must be rebuilt on his side. **Do not surprise him with this.** | — |
| Nov | UDI ingest, ~277M triples. Confirm with Yaphet Kebede that the Fabric accepts a graph this size. Tag `v0.2.0`. | rebuild complete |
| Nov | Enable cron. Run one cycle in dry-run before arming the upload step. | .83 stable |
| Dec+ | New sources, one per release, each one a module that already satisfies the contract. | — |

---

## What this buys

- A graph whose contents can be proven to match FDA's published data on a given date.
- Coverage gaps closed as a consequence of the architecture, not by heroic effort.
- A namespace under institutional control that survives the grant.
- Defects that cannot silently return, because each is now a test.
- New sources that cost a module, not a negotiation.
- No dependency on any individual's laptop or personal account.
