# v0.0.7: the rest of FDA's Medical Device Databases page

Status: in progress (started October 13, 2026; last updated November 11, 2026). Not uploaded to lakeFS. Per the PI, v0.0.7 is uploaded only once every database on FDA's page is in the graph.

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
| Data Dashboard: compliance actions | `dd_compliance_actions.py` | Data Dashboard API, ProductType Devices | 1,844 (1,819 warning letters, 20 injunctions, 5 seizures) | 42,801 |
| Data Dashboard: import refusals | `dd_import_refusals.py` | Data Dashboard API, Devices and Radiological Health + FDA's public charge list | 37,491 refusals; 287 charges | 1,117,096 |

- IRIs come from new `pipeline/iri.py` minters: `mddt`, `asca_testing_laboratory`, `asca_accreditation_body`, `asca_scope`, `third_party_review_organization`, `fda_inspection`, `fda_inspection_citation`, `fda_citation_reference`, `fda_compliance_action`, `import_refusal`, `import_refusal_charge`. Moving the modules from local `iri._instance` calls to these minters left every output byte-identical (sha256).
- New classes and properties are declared in the "November 4, 2026" block of `ontology/ontology.ttl`. `ex:productArea`, `ex:fdaCenter`, `ex:relatedPageUrl` and `ex:summaryUrl` are now shared and lost their `rdfs:domain`.
- The Data Dashboard API needs a user name and key; the PI's key is read from an environment file on the build VM at request time and is never logged, cached or written to an output. Requests are 1 per second.
- Individuals: firms whose name reads as a person (degree or honorific; for Bioresearch Monitoring inspections, any personal-name shape) keep no name, street address, city, ZIP, FEI or profile URL, in all four Data Dashboard sources. MDDT, ASCA and third party review contact persons are not read.
- Joins: inspections, citations, compliance actions and import refusals link to `iri.manufacturer(fei=...)`, the node registration/listing and the recall database use; citations link to 21 CFR sections and parts; import refusals link to the device product code only when FDA's import code is 2 digits plus a CDRH product code (37,169 of 37,491); compliance actions of type warning letter link to `ex:WarningLetter` nodes by MARCS-CMS number (`ex:relatedWarningLetter`): 441 of 1,819 (391 with the same issue date, 50 by number only), against the warning-letter file with the archive years.

## FDA web archive (PI approval, November 3, 2026)

For FDA records that fda.gov no longer serves, the pipeline now reads FDA's official web archive (Pagefreezer, and Archive-It collection 7993) and web.archive.org snapshots that fda.gov itself links (`pipeline/sources/_archive.py`: cached, at least 3 s between Archive-It requests, backoff on throttling). No proxies or other networks are used to get around a block. An archive-derived record has `ex:sourceUrl` = the archive URL, `ex:archivedFrom` = the original fda.gov URL, and an `ex:dataQualityNote`.

| Source | Added from the archive | Now |
|---|---|---|
| Warning letters | 2005 to April 2019 (Archive-It year listings and letter pages): 1,940 letters | 2,216 device letters, 57,745 triples |
| Safety communications | safety communications 2011-2022 and letters to health care providers 2016-2022 (Pagefreezer and Archive-It): 271 records | 328 records |
| Device discontinuances | 2020-2024 rows (the web.archive.org snapshot fda.gov links) | 69 archived rows |
| Device EUA letters | the 8 letters (2009 H1N1 EUAs) FDA links only on Archive-It | all 646 letters read |

Warning letters 2013 (fixed November 4, 2026): the first archive run kept only 11 device letters for 2013. The newest readable capture of the 2013 year listing (Archive-It 20190422151413) links its other pages as `2013/ucm20035841.htm?Page=N` instead of `2013/default.htm?Page=N`, and `fetch_archive` only recognized the second form, so it read page 1 alone (50 of 682 letters). It now follows the pager's own links (`pager_links`, with a self-test on the verbatim 2013 pager); the other 13 pages and the 2013 device letter pages were read (184 new archive requests, none failed), giving 182 device letters for 2013 (the Data Dashboard lists 189 device warning letters with a 2013 action date). The other years read every page their pager lists, and in 2011 to 2015 every listed letter's issue date falls in its listing year. Re-running `dd_compliance_actions` raised its warning-letter links from 427 to 441 (2013: 2 to 16; most 2013 letter pages state no MARCS-CMS number, as in 2009 to 2012).

## EUA numbers (November 3 and 4, 2026)

`device_eua.py fetch-letters` reads each EUA row's letter once (letter of authorization, else its first revision or revocation letter) and keeps only the EUA number(s) and the letter date (`eua_letters.tsv`); the letter text is never stored. `ex:euaNumber` is on 371 of the 648 rows with a letter; `ex:documentDate` on 642 letters. The other letters state no EUA number in their text (including the 2009 H1N1 letters read from the archive) or have no text layer. A date on a line of its own before the salutation is taken as the letter date, so an archived page's notice ("As of June 23, 2010, the EUAs have been terminated") is not read as one.

## 510(k) summary PDF links (in progress until November 5, 2026)

`devices_fda.py fetch-k510` checks each 510(k) summary or statement PDF with an HTTP HEAD request at 1 request per second; `ex:summaryUrl` is written only where FDA answered 200 with a PDF, and `ex:statementOrSummary` comes from the 510(k) records. The run ended on November 5, 2026 at 14:25: 171,620 numbers checked, 88,369 PDFs found, none refused; `devices_fda.ttl` is then rebuilt and validated.

## MAUDE scrubbed narratives (November 3 and 4, 2026)

MAUDE event narratives are released only as `event_text_scrubbed.ttl` (59,190,550 narratives, 32.6 GB; `scripts/scrub_maude_text.py`), never the unscrubbed `event_text.ttl`. The scrub rules were extended (v2, `pipeline/scrub.py`, `pipeline/scrub_words.txt`, `pipeline/scrub_verbs_1.txt` to `_4.txt`) after a leak check of the first scrubbed file: any date after DOB / date-of-birth wording, US-phone- and SSN-shaped numbers not tied to a device, part or report label, and a word after a title (DR, MR, MRS, NURSE ...) unless it is a common word or a dictionary verb form. The independent leak check (its own patterns, not the scrub rules) over the v2 file found 7 matches outside the benign categories; all 7 are dictionary words after a title (POST-PROCEDURE, RE-MADE, REBROKE), judged benign, and v2 was installed. `riot --validate` passed.

## More FDA device sources (November 5, 2026)

| Source | Module | How it is fetched | Records | Triples |
|---|---|---|---:|---:|
| CDRH FOIA Electronic Reading Room | `foia_reading_room.py` (new) | `readingroom.cfm` listing, 500 rows per page (1 request); listing metadata and document links only | 177 | 2,300 |
| Safety communications before 2011 | `safety_comms.py` (`fetch_archive`) | FDA's archived page "Safety Communications Issued Prior to 2011" (Archive-It collection 7993) and the sections it links | 164 added (492 in the file) | 6,928 (whole file) |
| PMA and supplement document PDFs | `devices_fda.py fetch-pma` | one HTTP HEAD per candidate PDF at 1 request per second, resumable (`pma_pdf_heads.tsv`) | 57,373 PMA and supplement numbers to check (run started November 5, 2026 14:26) | in `devices_fda.ttl` |

- FOIA reading room: one record per posted document (`ex:FOIAReadingRoomRecord`, `iri.foia_reading_room_record`, keyed on FDA's media id or the PDF file name): title, document type and reading-room category as published, file size, date posted, document URL (`ex:url`; the document itself is never downloaded or stored), 510(k)/De Novo and PMA numbers (`ex:mentionsK510Record` 123 of 126 distinct numbers join to `k510.ttl`, `ex:mentionsPMARecord` 2 of 2 to `pma.ttl`), the Section 522 order number linked to its study node in `postmarket_522.ttl` through `ex:orderNumber` (`ex:mentionsPostmarket522Study`), and a firm named in a letter's title (`ex:namesFirm`, 10 records, 8 firm-name nodes). The listing gives no product codes or FEI numbers. Two FDA staff presentations are titled with the presenter's surname; the name is dropped and the record carries an `ex:dataQualityNote`.
- Safety communications before 2011: the archived page lists FDA's archived sections; "Archived Medical Device Safety Communications" (`ex:communicationType "Safety Communication"`), "Archived Public Health Notifications" ("Public Health Notification", published until 2009) and "Archived Patient Alerts (Medical Devices)" ("Patient Alert", until 2010) are read with the same fields and rules as the 2011-2022 archive part (archive URL in `ex:url`/`ex:sourceUrl`, `ex:archivedFrom`, `ex:dataQualityNote`). Old-site tables give two-digit years ("9/2/83"); the cell's `data-value="19830902"` is used. Not read: "Archived Tips and Articles on Device Safety" (consumer articles) and "Information About Heparin" (a topic page). Rows read: 11 safety communications (2001-2009), 132 Public Health Notifications (1983-2009) and 21 Patient Alerts (1999-2010), 164 records, every page read from Archive-It (166 archive requests, none failed; Archive-It throttled 3 times and the reader slowed down as designed); issue years 1983-2010 (2 rows give no date on the listing). One listed Public Health Advisory links to a CDER page and is kept as listed. The safety communications file now holds 492 records.
- PMA documents: the pma.cfm detail pages link `cdrh_docs/<folder>/<number><supplement><letter>.pdf` (A approval order, B Summary of Safety and Effectiveness, C labeling, D labeling part 2; folder rule as for 510(k)s; checked on 23 detail pages). Every PMA and supplement number gets one HEAD for its B file and, where it exists, one each for A, C and D. `ex:summaryUrl` (B) with `ex:summaryDocumentType "Summary of Safety and Effectiveness"`, `ex:approvalOrderUrl` (A) and `ex:labelingUrl` (C, D) are written on the `ex:DeviceFDARecord` nodes. Fix: the older verified URL list held approval orders (A files) for some PMAs, which `devices_fda.ttl` wrote as `ex:summaryUrl`; they are now `ex:approvalOrderUrl`. The run checks newest first, originals and supplements other than 30-day notices (26,496) before the 30,877 30-day notices, and is expected to end about November 6, 2026 08:00; `devices_fda.ttl` is then rebuilt and validated. A transform over the first 846 answers wrote 61 SSED links, 129 approval orders (including the older list's) and 91 labeling links, and passed `riot --validate`.
- New terms (ontology, "November 5, 2026" block): `ex:FOIAReadingRoomRecord`, `ex:readingRoomCategory`, `ex:fileSize`, `ex:namesFirm`, `ex:mentionsPostmarket522Study`, `ex:labelingUrl`, `ex:summaryDocumentType`. New minter: `iri.foia_reading_room_record`.

## MAUDE report IRIs keyed on the MDR report key; e-mail scrub of all MAUDE text (November 8, 2026)

**Breaking IRI change for MAUDE reports (vs v0.0.5).** MAUDE report IRIs were minted from the report number (`id/report/3003442380-2026-010367`). FDA gives one report number to several MDR records, so distinct records were merged into one node: on the 2026-11-04 snapshot, 26,557,759 MDR report keys sat on 25,810,958 report nodes (about 746,800 records folded into another record's node; for example one report number carried 8 report keys, two event types, 6 event dates and 7 lot numbers). From v0.0.7 every MDR record is its own `ex:MAUDEReport`, minted by `iri.report()` from FDA's `MDR_REPORT_KEY`: `id/report/<mdr report key>` (for example `id/report/50835592`). Queries that hard-code v0.0.5 report IRIs must look the report up by `ex:reportNumber` instead. Counts of "MAUDE reports" are now counts of MDR records; counting distinct `ex:reportNumber` values gives report numbers.

- The report number is kept as the literal `ex:reportNumber` (existing predicate) and in the `rdfs:label` ("MAUDE <report number> (<event type>)"); the key is `ex:mdrReportKey` and `ex:sourceRecordId`. Records without a report key are skipped (counted in the transform's `skipped`).
- The narratives (`event_text_scrubbed.ttl`) sit on the same key-based IRIs: `event.py` writes each record's `mdr_text` (openFDA device/event) under the subject of that same record, so the structured parts and the narratives join one to one. Device, patient, problem and premarket facts are literals or links inside each report's own block. No other module links to a MAUDE report IRI (UDI-DI is a literal on the report; TPLC aggregates and derived views do not reference report IRIs; the saved queries in `docs/queries` and the capstone queries do not hard-code report IRIs).
- Ontology comments on `ex:MAUDEReport`, `ex:mdrReportKey` and `ex:reportNumber` updated; `iri.py` self-test pins the new minter.

**E-mail addresses removed from every MAUDE text literal.** The v0.0.7 check found 74 lines with e-mail addresses in short structured MAUDE fields (manufacturer name, catalog and model number, generic and device name, removal/correction number), outside the narrative scrub. `scrub.scrub_email()` (new) replaces any address with `[EMAIL REMOVED]`; its pattern covers both the narrative rule and the release check's pattern. `event.py` applies it to every MAUDE text value, `scrub.scrub_text()` starts with it, and `scripts/scrub_maude_text.py scrub` applies it to every literal line, not only narratives. `scripts/scrub_maude_text.py emailcheck` (new) counts addresses in every literal of any Turtle file.

**Other fixes before release.**
- Import refusals: shipment IDs that differ only in characters the slug drops (`GJ8-0223708-2/12/1]`, `^`, `[`, `\`) had been merged into one node. `iri.import_refusal()` keeps the slug for IDs in FDA's usual layout and appends the first 8 hex digits of the SHA-1 of the exact ID otherwise, so every distinct shipment ID is its own node.
- Registration and listing: one `rdfs:label` per establishment (the first listing's name). FDA lists establishment 3008849319 under two names ("Canady Biotechnology LLC" and "US MEDICAL INNOVATIONS, LLC", checked in the 2026-11-04 download); both stay as `ex:name`.
- Ontology: the five properties labelled in both `ontology.ttl` and `udi_extension.ttl` (`ex:recordStatus`, `ex:dunsNumber`, `ex:identifierId`, `ex:publicDeviceRecordKey`, `ex:issuingAgency`) keep only the `ontology.ttl` label.

**Rebuilt and checked on November 8, 2026** (same 2026-11-04 downloads, copied back from the archive and MD5-checked; `scripts/v007d/ft7d/v7d_regen.sh`, checks `v7d_verify.sh`, results in `work/fulltest-v007d/verify/`):
- MAUDE: 26,557,775 records -> 995,136,784 triples (0 skipped; the 16 records skipped before had report numbers that were not URI-safe and now get a node from their key). 26,557,775 distinct report IRIs = 26,557,775 distinct `ex:mdrReportKey` values = report blocks, and every IRI equals its block's key; 25,810,973 distinct report numbers. The old example report number 3003442380-2026-010367 is now on 8 report nodes. Parts: 12 files, 43 GB, `riot --validate` OK on each.
- Narratives: 59,637,673 narratives in 25,868,869 blocks, one block per record; every narrative subject is an existing report IRI (0 orphans); leak check unchanged from November 4 (7 non-benign matches, all dictionary words); `riot --validate` OK.
- E-mail: 74 addresses removed from structured MAUDE fields (`ex:deviceManufacturerName` 23, `ex:genericName` 18, `ex:modelNumber` 11, `ex:catalogNumber` 9, `ex:deviceName` 7, `ex:removalCorrectionNumber` 5, `ex:lotNumber` 1). 0 e-mail addresses left in any literal of the MAUDE parts and narratives, and in the ASR, legacy MDR and MedSun files (two patterns by grep, plus `emailcheck`).
- Import refusals: 37,491 shipment IDs -> 37,491 distinct IRIs. Establishment 3008849319: one `rdfs:label`, both names as `ex:name`.
- Self-tests (`run.py --selftest`, iri, scrub, event, registrationlisting, dd_import_refusals, patch_event_ttl), `scripts/query_contract.py` and `scripts/structure_lint.sh` pass.

## Warning letters 1996-2004 and 2019-2020 from FDA's web archives; radiation corrective actions and HUD designations re-checked (November 11, 2026)

New file **`warning_letters_archive.ttl`** (`warning_letters.transform(only_via=("newsite", "foi"))`): 2,741 device warning letters that
`warning_letters.ttl` (current index from 2021, old-site year listings 2005 - April 2019) does not have. It is a separate file because
the 2026-11-04 download of the current index is no longer on the VM, so `warning_letters.ttl` itself is kept exactly as loaded and tested.

- **May 2019 - December 2020** (`fetch_archive_newsite`): FDA's listing for this period is a JavaScript table whose data no archive kept,
  but the letter pages are listed in the CDX index of FDA's official archive (Archive-It collection 7993, captures September 2019 -
  December 2020): 1,104 letter URLs dated 2019-2020 (all FDA centers). fda.gov still serves 1,100 of them (read from fda.gov, 1 request
  per second; the other 4 from the archive copy). The device filter (Issuing Office / Product) keeps 75; 10 are already in
  `warning_letters.ttl` (the January - April 2019 letters of the old-site listing, matched on issue date and company), so **65** are new
  (25 issued in 2019, 40 in 2020; 4 of them are January - April 2019 letters missing from the last capture of the 2019 old-site listing, 61 are from May 2019 - December 2020). Same IRI rule as current letters (`warning-letter/<CMS>_<MMDDYYYY>`), MARCS-CMS number, Issuing
  Office, Product, firm address, FEI, cited 510(k)/PMA numbers, product codes and 21 CFR citations, as for current letters; no
  ex:letterSubject (these pages show none). 64 of the 65 have a Data Dashboard compliance action with the same case number
  (`dd_compliance_actions.ttl`); FDA reuses a case number for later letters of the same case, so two of them share a number with an older
  letter in `warning_letters.ttl` (different dates, different letters). `dd_compliance_actions.transform` now also reads
  `warning_letters_archive.ttl`, so the next regeneration links those actions with `ex:relatedWarningLetter`; the release file was not
  regenerated (its 2026-11-04 download is gone).
- **November 1996 - December 2004** (`fetch_archive_foi`): FDA's Electronic FOI Reading Room month index
  (`www.accessdata.fda.gov/scripts/wlcfm/date.cfm` and `date_archive.cfm`, linked from `www.fda.gov/foi/warning.htm`), read from
  web.archive.org (fda.gov links its archive.org copy from the FDA.gov Archive page; Archive-It 7993 has none of these pages): all 98 months,
  7,584 listed letters, 2,676 device letters (same filter). Company, issue date, issuing office, subject and the letter file are from
  the index; letters posted after June 25, 2001 also have an HTML page, read from web.archive.org for identifiers only (861 of 862
  read); 1,814 letters exist only as PDFs and keep the index metadata with a note. IRIs from the file name
  (`warning-letter/g4618d_htm`, as for old-site `ucm` pages). Letters to persons (degree, "Last, First" with no organization word, clinical
  investigator subject, salutation): 140 records with no name, address, FEI, firm link or URL.
- No body text, recipient names or titles, signatories or e-mail addresses are stored (0 e-mail addresses in the file).
- `reference_fallback_wl_archive.ttl` (new, 52 nodes): the cited references no release file names (510(k) K022830, K033765 and K171343, and 49 cited 21 CFR parts or sections that the current eCFR lacks: retired or non-device parts such as 1270, and typos in the letters themselves such as "21 CFR 820.901" or "21 CFR 5900.12", kept as cited), made by
  `scripts/reference_fallback.py` and keeping only IRIs that `reference_fallback.ttl` does not already name.
- Checked: self-tests (`warning_letters.py`, `dd_compliance_actions.py`, `rh_actions.py`, `run.py --selftest`), `riot --validate`,
  `structure_lint.sh`, `query_contract.py`; one rdfs:label per letter; provenance on every letter; scratch TDB2 store with the new file and
  the release files it links to (`k510`, `pma`, `classification`, `product_code_fallback`, `cfr`, `reference_fallback`, `registrationlisting`,
  `dd_inspections`, `devices_fda`, `warning_letters`, `dd_compliance_actions`): one rdfs:label on each of the 2,741 letters; 1,147 / 1,147 citesCfrPart, 3,027 / 3,027 citesCfrSection, 53 / 53 mentionsK510Record, 6 / 6 mentionsPMARecord, 6 / 6 hasProductCode and 2,558 / 2,558 hasRecipientFirm links reach a typed, labelled node; 3 of 42 FEI targets of ex:hasManufacturer have a node, as for the release's own warning letters (40 of 89; FEI IRIs are identifier hubs); 64 of the 65 letters of 2019-2020 share the case number of a Data Dashboard compliance action (78 device warning-letter action nodes May 2019 - Dec 2020).
- `_us_date` reads two-digit years 70-99 as 19xx (the FOI index writes 3/02/98).

**Radiation-emitting product corrective actions** (`rh_res.cfm`): retried 2026-11-11 22:44 UTC after FDA support reported it working on
October 20. The form page loads (with Akamai Bot Manager cookies); every search (five variants, also with the form's cookies and Referer)
still answers HTTP 503 "currently unavailable" from AkamaiNetStorage; a request from a second network also got a server error. Not
worked around; `rh_actions_derived.ttl` stays the stand-in (module docstring updated).

**HUD designations**: re-checked 2026-11-11: the HUD Designation Program page (content current 12/15/2023), its FAQ, laws/guidances and
education pages, and their 2010-2019 versions under `ForIndustry/DevelopingProductsforRareDiseasesConditions/DesignatingHumanitarianUseDevicesHUDS/`
(web.archive.org and Archive-It CDX) contain no list of designations; the OOPD searchable designation database is for drugs and biologics;
21 CFR 814 subpart H does not require FDA to publish designations (unlike 21 CFR 316.28 for orphan drugs). No public list exists; nothing added.

## Remaining gaps

| Gap | Reason |
|---|---|
| Humanitarian Use Device (HUD) designations | FDA publishes no list of HUD designations. The HUD Designation Program pages on fda.gov (program page, FAQs, laws and guidances, education resources) and their archived versions (Archive-It collection 7993) link none, the guidance describes only how to request a designation, and FDA's searchable OOPD database covers orphan drug designations only. Approved HDEs (whose devices hold HUD designations) are in `hde.ttl`. Re-checked November 11, 2026 (program, FAQ and education pages and their 2010-2019 versions; OOPD database; 21 CFR 814 subpart H): still no public list. |
| Radiation-emitting product corrective actions (`rh_res.cfm`) | Every search still returns HTTP 503 (retried November 11, 2026, after FDA support reported it working on October 20; the form page loads, every search variant gets AkamaiNetStorage's "currently unavailable" page). The derived view (`rh_actions_derived.ttl`) stands in. |
| Warning letters May 2019 to 2020 | Closed November 11, 2026: 65 letters in `warning_letters_archive.ttl` (letter pages found through FDA's Archive-It index; see above). |
| Warning letters before 2005 | Closed November 11, 2026 for November 1996 - December 2004: 2,676 letters in `warning_letters_archive.ttl` (FOI reading-room month index on web.archive.org; see above). Letters before November 1996 are on no index found. |
