# v0.0.7: the rest of FDA's Medical Device Databases page

Status: in progress (started October 13, 2026; last updated November 4, 2026). Not uploaded to lakeFS. Per the PI, v0.0.7 is uploaded only once every database on FDA's page is in the graph.

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

The rows above are the state of October 28. The Data Dashboard, warning letters 2005 to April 2019, the archived safety communications and discontinuances, and EUA numbers were added on November 3 and 4 (below). The remaining gaps are listed at the end of this file.

## Pre-upload fixes (October 29, 2026)

From the full-size v0.0.7 test load and query battery. Outputs in `work/out-2026-10-29/`.

- TPLC year labels (`pipeline/derived_views.py`): every TPLC year node has exactly one `rdfs:label`. Years already labelled by `tplc.ttl` (MDR report years) get no second label; years with only premarket or recall counts are labelled "`<code>` premarket and recall counts `<year>`".
- Refreshed on one date (October 29): recalls (enforcement reports), recall database, 510(k), PMA, 21 CFR (eCFR) and recall class (`refresh.sh`), so recall, premarket and regulation links are taken from the same day.
- Reference fallbacks (`scripts/reference_fallback.py`, new): every 510(k), PMA, HDE, De Novo or regulation IRI that a record links to but no input file labels gets one minimal node with its type, one `rdfs:label` naming the number and the kind of record that cites it, and an `ex:dataQualityNote` that FDA's data lacks it. PMA supplements cited without FDA's "S" prefix go to a separate file. Regulation numbers whose 21 CFR section exists link to that section. Added November 3: cited 21 CFR sections and parts missing from the current eCFR (mostly Quality System regulation sections retired when the QMSR took effect on February 2, 2026: 820.30, 820.100, 820.198 ...) get a fallback `ex:CFRTitle21Section` / `ex:CfrPart` node, linked to its part when the part exists.
- X-ray assembler placeholders (`pipeline/common.py`, `xray_assembler.py`): values made only of "no value" tokens (N/A, N/A=NOT APPLICABLE and its fixed-width cuts, 0000 N/A, NA, N.A., NONE, UNKNOWN, NO-PHONE, runs of zeros) write no triple; real text that merely contains such letters is kept whole.
- Lower-case MAUDE product codes (`scripts/patch_event_ttl.py`): three-letter codes typed in lower case ("psv") are upper-cased on the report and on hub blocks, and `ex:hasProductCode` uses the canonical `iri.product_code` IRI.
- GUDID PMA supplements (`udi.py`): GUDID writes supplement numbers as digits only ("014"); they are prefixed with "S" so the UDI device links to the supplement node `pma.py` mints. Supplement "000" (the original approval) links to the base PMA only.

## New sources (November 3 and 4, 2026)

Every output passes `riot --validate`; triple counts from the transforms.

| Source | Module | How it is fetched | Records | Triples |
|---|---|---|---:|---:|
| Medical Device Development Tools (qualified MDDTs) | `mddt.py` | fda.gov MDDT table + each tool's Summary of Evidence and Basis of Qualification PDF | 21 tools (20 with summary text) | 294 |
| ASCA accredited testing laboratories and accreditation bodies | `asca.py` | cfStandards ASCA lists (current and withdrawn laboratories, accreditation bodies) + detail pages | 98 laboratories, 23 withdrawn, 5 bodies; 2,616 scope entries | 44,353 |
| 510(k) Third Party Review organizations | `third_party_review.py` | cfThirdParty organization pages, eligible devices by panel, fda.gov accreditation status table | 8 (7 recognized, 1 withdrawn); 644 regulations, 1,556 product codes | 13,472 |
| Data Dashboard: inspection classifications | `dd_inspections.py` | Data Dashboard API, ProductType Devices | 33,190 inspections (50,601 rows) | 839,212 |
| Data Dashboard: inspection citations | `dd_citations.py` | Data Dashboard API, Program Area Devices and Radiologic Health | 52,439 citations; 784 citation references | 845,858 |
| Data Dashboard: compliance actions | `dd_compliance_actions.py` | Data Dashboard API, ProductType Devices | 1,844 (1,819 warning letters, 20 injunctions, 5 seizures) | 42,787 |
| Data Dashboard: import refusals | `dd_import_refusals.py` | Data Dashboard API, Devices and Radiological Health + FDA's public charge list | 37,491 refusals; 287 charges | 1,117,096 |

- IRIs come from new `pipeline/iri.py` minters: `mddt`, `asca_testing_laboratory`, `asca_accreditation_body`, `asca_scope`, `third_party_review_organization`, `fda_inspection`, `fda_inspection_citation`, `fda_citation_reference`, `fda_compliance_action`, `import_refusal`, `import_refusal_charge`. Moving the modules from local `iri._instance` calls to these minters left every output byte-identical (sha256).
- New classes and properties are declared in the "November 4, 2026" block of `ontology/ontology.ttl`. `ex:productArea`, `ex:fdaCenter`, `ex:relatedPageUrl` and `ex:summaryUrl` are now shared and lost their `rdfs:domain`.
- The Data Dashboard API needs a user name and key; the PI's key is read from an environment file on the build VM at request time and is never logged, cached or written to an output. Requests are 1 per second.
- Individuals: firms whose name reads as a person (degree or honorific; for Bioresearch Monitoring inspections, any personal-name shape) keep no name, street address, city, ZIP, FEI or profile URL, in all four Data Dashboard sources. MDDT, ASCA and third party review contact persons are not read.
- Joins: inspections, citations, compliance actions and import refusals link to `iri.manufacturer(fei=...)`, the node registration/listing and the recall database use; citations link to 21 CFR sections and parts; import refusals link to the device product code only when FDA's import code is 2 digits plus a CDRH product code (37,169 of 37,491); compliance actions of type warning letter link to `ex:WarningLetter` nodes by MARCS-CMS number (`ex:relatedWarningLetter`): 427 of 1,819 (377 with the same issue date, 50 by number only), against the warning-letter file with the archive years.

## FDA web archive (PI approval, November 3, 2026)

For FDA records that fda.gov no longer serves, the pipeline now reads FDA's official web archive (Pagefreezer, and Archive-It collection 7993) and web.archive.org snapshots that fda.gov itself links (`pipeline/sources/_archive.py`: cached, at least 3 s between Archive-It requests, backoff on throttling). No proxies or other networks are used to get around a block. An archive-derived record has `ex:sourceUrl` = the archive URL, `ex:archivedFrom` = the original fda.gov URL, and an `ex:dataQualityNote`.

| Source | Added from the archive | Now |
|---|---|---|
| Warning letters | 2005 to April 2019 (Archive-It year listings and letter pages): 1,769 letters | 2,045 device letters, 53,224 triples |
| Safety communications | safety communications 2011-2022 and letters to health care providers 2016-2022 (Pagefreezer and Archive-It): 271 records | 328 records |
| Device discontinuances | 2020-2024 rows (the web.archive.org snapshot fda.gov links) | 69 archived rows |
| Device EUA letters | the 8 letters (2009 H1N1 EUAs) FDA links only on Archive-It | all 646 letters read |

## EUA numbers (November 3 and 4, 2026)

`device_eua.py fetch-letters` reads each EUA row's letter once (letter of authorization, else its first revision or revocation letter) and keeps only the EUA number(s) and the letter date (`eua_letters.tsv`); the letter text is never stored. `ex:euaNumber` is on 371 of the 648 rows with a letter; `ex:documentDate` on 642 letters. The other letters state no EUA number in their text (including the 2009 H1N1 letters read from the archive) or have no text layer. A date on a line of its own before the salutation is taken as the letter date, so an archived page's notice ("As of June 23, 2010, the EUAs have been terminated") is not read as one.

## 510(k) summary PDF links (in progress until November 5, 2026)

`devices_fda.py fetch-k510` checks each 510(k) summary or statement PDF with an HTTP HEAD request at 1 request per second; `ex:summaryUrl` is written only where FDA answered 200 with a PDF, and `ex:statementOrSummary` comes from the 510(k) records. The run ends about November 5, 2026 14:10; `devices_fda.ttl` is then rebuilt and validated.

## MAUDE scrubbed narratives (November 3 and 4, 2026)

MAUDE event narratives are released only as `event_text_scrubbed.ttl` (59,190,550 narratives, 32.6 GB; `scripts/scrub_maude_text.py`), never the unscrubbed `event_text.ttl`. The scrub rules were extended (v2, `pipeline/scrub.py`, `pipeline/scrub_words.txt`, `pipeline/scrub_verbs_1.txt` to `_4.txt`) after a leak check of the first scrubbed file: any date after DOB / date-of-birth wording, US-phone- and SSN-shaped numbers not tied to a device, part or report label, and a word after a title (DR, MR, MRS, NURSE ...) unless it is a common word or a dictionary verb form. The independent leak check (its own patterns, not the scrub rules) over the v2 file found 7 matches outside the benign categories; all 7 are dictionary words after a title (POST-PROCEDURE, RE-MADE, REBROKE), judged benign, and v2 was installed. `riot --validate` passed.

## Remaining gaps

| Gap | Reason |
|---|---|
| Radiation-emitting product corrective actions (`rh_res.cfm`) | Every search still returns HTTP 503. The derived view (`rh_actions_derived.ttl`) stands in. |
| Warning letters May 2019 to 2020 | FDA's current listing starts in 2021; the archived listing of those months is a JavaScript table whose data the archive did not capture. |
| Warning letters before 2005 | The archived 1996-2004 year pages are redirect records only. |
