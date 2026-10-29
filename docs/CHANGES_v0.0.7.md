# v0.0.7: the rest of FDA's Medical Device Databases page

Status: in progress (October 13, 2026). Not uploaded to lakeFS. Per the PI, v0.0.7 is uploaded only once every database on FDA's page is in the graph.

## New sources

| FDA database (fda.gov "Medical Device Databases") | Module | How it is fetched | Records |
|---|---|---|---:|
| MDR (Medical Device Reporting), 1984-1997 | `legacy_mdr.py` | MAUDE ftparea `mdr84`-`mdr97.zip` + `disclaim.zip` | 620,198 |
| MAUDE Alternative Summary Reports, 1999-2019 (same FDA page as MAUDE data files; not in MAUDE or openFDA) | `asr.py` | ftparea `ASR_1999`-`ASR_2019.zip`, `ASR_PPC.zip`, term files | 6,066,980 |
| Post-Approval Studies (PAS) | `pas.py` | `pma_pas_Excel.cfm` export | 354 |
| PMA Summary Review Memos for 180-Day Design Changes | `pma_memo.py` | `pmamemos.cfm` list | 325 |
| De Novo (detail pages; previously only DEN numbers from the 510(k) file) | `denovo.py` | `denovo.cfm` list + detail pages | 495 |
| Product Classification detail fields not in `foiclass` | `classification_detail.py` | `classification.cfm` per product code | 7,094 codes |
| Radiation-emitting Electronic Product Codes | `rh_codes.py` | `cfPCD_rh` advanced-search code list + detail pages | 646 |
| CDRH Export Certificate Validation (CECV) | `cecv.py` | `rl_cert.cfm` by expiration-date windows + detail pages | 21,030 |
| Advisory Committee/Panel Meetings (archive to 2009) | `advisory.py` | `cfAdvisory` per-panel searches + detail pages | 660 |
| MedSun Reports | `medsun.py` | `searchreport.cfm` by date windows + detail pages | 79,433 ids (fetch running) |

## Not yet available from FDA

| Database | Problem (checked October 12 and 13, 2026) |
|---|---|
| Total Product Life Cycle (`cfTPLC/tplc.cfm`) | Redirects to fda.gov/medical-devices from the Hofstra network (VM and PI workstation). Our MAUDE-derived TPLC aggregates remain in the graph. |
| Radiation Emitting Product Corrective Actions and Recalls (`rh_res.cfm`) | Every search returns HTTP 503 "currently unavailable". The module records the refusal and writes no records. |

FDA's Office of Industry and Consumer Education replied on October 20, 2026: FDA is aware of the TPLC access problem (also seen outside Hofstra) and is working on it with no restoration date; it stated the corrective-action database works as of October 20, but searches still returned HTTP 503 on October 21, 2026, both from the build VM and in a browser. Neither database has a public bulk export. Research access and the TPLC license question were referred to uig@fda.hhs.gov.

## Stopgap derived views (pipeline/derived_views.py)

Built October 21, 2026 from FDA records already in the graph and labeled as derived on their `ex:SourceDataset` nodes. They are replaced or checked against FDA's own data when the two databases return.

| View | Output | Result |
|---|---|---|
| Radiation-emitting product corrective actions: recalls whose product code is a radiation-emitting product code, typed `ex:RadiationCorrectiveAction` on the same recall IRI, linked to the radiation product code record, with 21 CFR 1000-1050 citations extracted from the recall text | `rh_actions_derived.ttl` | 8,099 recalls (of 59,483), 24,760 triples |
| TPLC premarket and recall layer: 510(k), PMA (original and supplement), De Novo and HDE decisions and recalls by class, per product code and per year, on the existing TPLC nodes | `tplc_premarket.ttl` | 5,582 product codes, 331,479 triples |

## Fixes from the v0.0.6 tests

- One `rdfs:label` per vocabulary term: duplicate labels removed from `ontology/udi_extension.ttl` (test V1).
- Product codes cited by records but missing from FDA's current classification file get a name from `scripts/product_code_fallback.py` (test V4; 299 codes in v0.0.6).
- X-ray assembler addresses that were only "N/A" are dropped (test V6; 62 records).
- `ex:hasRegulationNumber` no longer has `rdfs:domain ex:ProductCode`, so De Novo, classification and radiation records are not mis-typed under RDFS inference. `ex:reportedProductCodeText` likewise lost its MAUDE-only domain.

## Modeling notes

- ASR rows are summaries, not individual events; supplements repeat initial reports; FDA notes that ASR plus companion MAUDE reports double-count. This is on the dataset node (`ex:usageNote`). Per-row provenance is the dataset and record id only (file and line), with the download page and date on `ex:SourceDataset`, to fit 6 M rows on the VM.
- Narratives (legacy MDR event descriptions and closeout text, MedSun descriptions and manufacturer responses, advisory agenda and procedure text) are in separate `*_text.ttl` files after `scrub.scrub_text`. Contact persons are never ingested (De Novo contacts, advisory staff contacts, MedSun reporters).
- Legacy MDR access numbers that repeat (29,070; mostly a preliminary and a final report) take an ordinal suffix and `ex:sameAccessNumberAs` the first row.

## Six more FDA device sources (October 28, 2026)

Built from cached FDA pages on October 28, 2026; every output passes `riot --validate`. Triple counts are from `riot --count`.

| Source | Module | How it is fetched | Records | Triples |
|---|---|---|---:|---:|
| AI-Enabled Medical Devices list | `ai_devices.py` | FDA's CSV download of the list | 1,614 (1,553 510(k), 40 De Novo, 21 PMA) | 27,016 |
| Device Emergency Use Authorizations | `device_eua.py` | EUA HTML tables (COVID-19, mpox, historical and revoked, main EUA page) | 1,097 EUA rows; 3,472 linked documents | 39,517 |
| Warning letters (medical devices) | `warning_letters.py` | warning letter index (JSON behind the listing page) + letter pages | 276 | 8,993 |
| Safety communications and letters to health care providers | `safety_comms.py` | fda.gov listings + detail pages | 58 | 1,123 |
| Guidance documents (CDRH, and CBER device guidances) | `guidance.py` | guidance search JSON + guidance pages | 605 | 15,039 |
| Medical Device Shortages List and Discontinuance List | `shortages.py` | one fda.gov page, daily snapshots | 230 (6 on the shortage list, 1 removed, 223 discontinuances) | 4,183 |

- IRIs come from new `pipeline/iri.py` minters: `ai_enabled_device`, `device_eua`, `eua_document`, `warning_letter`, `safety_communication`, `guidance_document`, `device_shortage`, `device_discontinuance`.
- Warning letters and guidance documents share `ex:citesCfrSection` / `ex:citesCfrPart` (guidance previously used `ex:mentionsCfrSection` / `ex:mentionsCfrPart`).
- New classes and properties are declared in the "six more FDA device sources" block of `ontology/ontology.ttl`; `ex:GuidanceDocument` now covers FDA's guidance listing as well as guidances linked from radiation product code pages.
- Contact persons, letter bodies, recipient names, signatories and PDF contents are not ingested.

### Known gaps

| Gap | Reason |
|---|---|
| FDA Data Dashboard | Not ingested: its data downloads work only through JavaScript in a browser. |
| Warning letters before 2021 | FDA's warning letter index covers 2021 onward. |
| Safety communications 2011-2022 and discontinuances 2020-2024 | Published only on FDA's archive host, which this pipeline does not read. Not ingested. |
| Device EUA numbers | Not on FDA's EUA pages (only inside the PDF letters, which are linked, not read). EUA rows are keyed on a hash of page, section, holder and product. |
