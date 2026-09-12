# GraphDB retirement — evidence

**Decision:** retire the local GraphDB on VM `.83`. It is not in the publish path
(that is lakeFS → FRINK), and it is not needed to test or query the graph. This
document records the run that proves it.

**Run date:** 2026-09-12, on `.83` (`build-vm`), Apache Jena 5.2.0
CLI only (`riot`, `arq`, `shacl`) — **no GraphDB, no triplestore loaded**.

---

## Part 1 — the students' current scraped data

Directory `/srv/medical-device-kg/nstest/` — 13 per-source Turtle files, old
`http://medicaldevice.com/ontology/` namespace. Run with
`scripts/verify_current_graph.sh`.

| Check | Result | vs. GraphDB |
|---|---|---|
| Turtle syntax (`riot --validate`) | PASS, 13/13 files | matches GraphDB's import check |
| Triple count (`riot --count`) | **650,428** | recreates `SELECT (COUNT(*))` |
| Instances per class (`arq`) | full breakdown (below) | recreates class browsing |
| **D1 — orphan `MedicalDevice`** | **4,099** nodes with `rdf:type` + label and no other edge | GraphDB imported these silently |
| **D4 — untyped date literals** | **38,947** `YYYYMMDD`/`YYYY-MM-DD` strings not typed `xsd:date` | GraphDB never flagged this |

Per-class counts (top): MAUDEReport 15,284; DeviceFDARecord 12,386;
Manufacturer(+IOF) 10,432; Facility 9,120; ProductClassificationRecord 7,071;
Manufacturer 5,326; MedicalDevice 4,099; CFRTitle21Section 2,842;
HumanitarianDeviceExemption 1,947; ProductCodeNAICSMapping 1,589;
MammographyFacility 975; DeNovoRecord 483; ProductCode 475;
Postmarket522Study 353; CliaWaivedAnalyte 152; CfrPart 57; IVDHomeUseTest 53;
RecognizedConsensusStandard 49; NAICSClassification 13.

(This `nstest` set does not contain the recall/510(k)/PMA/TPLC sources, so the
recall competency query returned empty — a property of the subset, not a failure.)

---

## Part 2 — the new pipeline's output

New UDI converter output, new `https://bmedesign.org/medical-device-kg/ns/`
namespace.

- **Full scale:** `out/udi-pilot.ttl` — **4,918,375 triples** — `riot --validate`
  **PASS** (streamed, memory-safe; no store needed).
- **Rebuild gate** (`scripts/validate.sh`) on a UDI sample → **VALIDATE: OK**,
  all four layers:
  1. syntax **PASS**
  2. SHACL shapes (`ontology/shapes/udi.shacl.ttl`) **PASS**
  3. join check — only cross-source `k510/*` references unresolved (expected: the
     510(k) source is not in a UDI-only directory)
  4. competency questions:
     - `cq07` orphan-device guard → **0 rows** (no orphans)
     - `cq08` missing-provenance guard → **0 rows** (provenance on every record)
     - `cq04` device → 510(k) clearance join → **real rows** (e.g. FIREBIRD SFS →
       `k510/k180179`), the join nothing else in the graph provides
     - `cq06` implantable × MRI-safety → real counts (MR Conditional 11, MR Safe 4,
       MR Unsafe 3, no-info 77)
     - `cq09` class counts → UDIIdentifier 660, UDIDeviceRecord 500, GMDNTerm 230,
       Labeler 200, StorageCondition 85

---

## Conclusion

The Jena CLI gate **recreates** everything GraphDB gave the team — well-formed
check, triple count, class browsing, live SPARQL — and **exceeds** it: it
quantifies the D1 orphan (4,099) and D4 untyped-date (38,947) defects GraphDB
imported without complaint, enforces SHACL shapes, and enforces zero-orphan and
zero-missing-provenance guards. It runs headless, streams for syntax at any size,
and is disk-friendly. GraphDB is therefore not required for testing or querying.

## Querying going forward (the GraphDB replacement)

- Scripted / CI functional tests: `arq --data <ttl> --query competency/*.rq`,
  or the whole gate via `scripts/validate.sh <dir>`.
- Interactive exploration: load once into Jena TDB2 and serve with Fuseki —
  `tdb2.tdbloader --loc <db> out/*.ttl` then `fuseki-server --loc <db> /mdkg`.
  TDB2 is disk-based, so it holds the full graph (17.9M now, ~277M after UDI)
  without the RAM limits that constrained the GraphDB VM.

## Reproduce

```bash
export PATH=/srv/medical-device-kg/tools/apache-jena-5.2.0/bin:$PATH
# Part 1 (current data):
bash /srv/medical-device-kg/repo/scripts/verify_current_graph.sh /srv/medical-device-kg/nstest
# Part 2 (new pipeline output):
riot --validate /srv/medical-device-kg/out/udi-pilot.ttl
mkdir -p /tmp/vtest && cp /srv/medical-device-kg/out/udi-demo.ttl /tmp/vtest/
bash /srv/medical-device-kg/repo/scripts/validate.sh /tmp/vtest
```
