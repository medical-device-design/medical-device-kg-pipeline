# Client-readiness test: recall intelligence on the rebuilt graph (10 October 2026)

**Question tested.** Can the rebuilt FDA graph answer what a regulatory-intelligence client asks of a recall database, when it runs offline inside the client's own secured network? The target is the deployment discussed with Insilica: a packaged graph (BioBricks brick or Docker/QLever bundle) refreshed monthly.

**How.** `scripts/client_readiness.py` loads the recall-centred layers into a scratch Apache Jena TDB2 store on the build VM: recalls, the recall database, recall classes, classification, 510(k), PMA, HDE, TPLC, registration and listing, NAICS, geospatial, and the Devices@FDA catalog, about 22 million triples. It then:

- runs fifteen SPARQL questions;
- streams the two files too large for the scratch store, UDI (15 GB) and MAUDE (42 GB), to measure how they join to recalls.

The test needs no network.

Every answer came back in under 30 seconds on the 4 GB build VM. Most took 1 to 5 seconds.

## 1. Coverage: are the fields a client filters on populated?

| Field on a recall | Recalls with it | Share |
|---|---:|---:|
| Any recall record (recall database plus enforcement, merged) | 59,471 | 100% |
| FDA recall class (I / II / III) | 59,117 | 99.4% |
| Product code (links to device type, TPLC and MAUDE) | 58,788 | 98.9% |
| 510(k) or PMA number (links to the cleared device and its applicant) | 41,517 | 69.8% |
| Recalling firm's FEI (links to the registered manufacturer) | 59,370 | 99.8% |
| FDA root-cause category | 59,455 | 99.97% |
| Lot / serial / code text, complete | 59,471 | 100% |
| Distribution states parsed from FDA's free text | 30,530 | 51.3% |

**Recall class.** Class comes from the enforcement reports for 40,113 recalls. Another 19,004 get it from FDA's own recall pages, fetched at one request per second (19,350 pages, finished at 12:30). The remaining 354 pages do not state a class; the weekly job re-checks them.

**Distribution states.** States appear only where FDA's text names them. "Nationwide" is recorded as a flag rather than as fifty states.

## 2. Example answers

**Recalls by class.**

| Class | All recalls | Still open |
|---|---:|---:|
| Class I | 4,973 | 1,811 |
| Class II | 51,580 | 12,719 |
| Class III | 2,563 | 251 |

One more recall is "Not Yet Classified". The 1,314 Class I recalls added by the recall pages are almost all from before 2016, which is why the per-year figures below barely moved.

**Class I recalls per year.**

| 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to Oct) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 112 | 120 | 98 | 368 | 159 | 171 | 177 | 584 | 314 | 377 | 254 |

**Top FDA root causes.**

| Root cause | Recalls |
|---|---:|
| Other | 8,928 |
| Device Design | 7,676 |
| Under Investigation by firm | 6,967 |
| Process control | 6,557 |
| Nonconforming Material/Component | 5,965 |
| Software design | 3,519 |

**Product codes with the most Class I recalls,** joined to MAUDE totals from TPLC:

| Product code | Device | Class I recalls | MAUDE death reports | MAUDE injury reports |
|---|---|---:|---:|---:|
| DQY | percutaneous catheter | 229 | 906 | 9,475 |
| LRO | general surgery tray | 205 | 2 | 1,543 |
| BTR | tracheal tube | 184 | 256 | 3,815 |
| FRN | infusion pump | 183 | 1,757 | 24,255 |
| CBK | facility ventilator | 171 | 2,259 | 14,404 |
| FOZ | short-term therapeutic IV catheter | 150 | 141 | 6,464 |
| LWS | ICD (non-CRT) | 89 | 10,102 | 227,220 |

**Software- and cybersecurity-related recalls,** matched on FDA root cause "Software design" or on reason text mentioning software, firmware or cyber:

- 2018 to 2024: 147 to 282 a year;
- 2025: 348, of which 63 were Class I, the highest in the window.

**Lot lookup.** Given a lot number taken from a 2025 Class I recall (Medline, lot EP250618), the store returned every recall that quotes it, in 6 seconds. The published code text carries the UDI-DIs alongside the lots.

**Worked example: infusion pumps (FRN).**

- 183 Class I and 526 Class II recall records.
- 849 cleared 510(k)s.
- 149 registered establishments list the code.
- 1,831,109 MAUDE reports, 1,757 of them deaths.

**Clearance-to-recall interval.** For each recalled 510(k), the store gives the mean years from clearance to recall start. Examples: FRN 7.9 years; FPA, IV administration sets, 16.7 years.

**Time to FDA classification.** Mean days from recall start to classification, from enforcement reports, which carry the classification date: Class I 76, Class III 161, Class II 508. The Class II mean is inflated by old recalls entered late. A median by year would be more informative and is a next step.

**Recalls by manufacturer location.**

- 1,318 of the 3,352 recalling manufacturers (by FEI) resolve to a geocoded US establishment, covering 36,569 recalls (61%).
- Top states for Class I recalls by site: MN 479, MA 436, PR 412, CA 328, NC 284.

**Recalls by industry.** Class I recalls by NAICS sector (inferred crosswalk): surgical and medical instruments 3,393; electromedical 811; in-vitro diagnostics 430.

## 3. Joins to the device and adverse-event layers (streamed)

**UDI devices (5,182,695):**

| | Devices | Share |
|---|---:|---:|
| With a 510(k)/PMA link | 1,625,898 | 31% |
| Whose 510(k)/PMA has at least one recall | 353,492 | 6.8% |
| Of those, still in commercial distribution | 308,808 | |
| Whose product code has any recall (broad) | 4,622,822 | 89% |

Most UDI records with no premarket link are 510(k)-exempt devices.

**Barcode path.** Recall code text often quotes the 14-digit device identifier (GS1 GTIN):

- 12,897 recalls quote at least one, 136,602 distinct DIs in all;
- 98,773 of those DIs (72%) are in the UDI layer;
- 11,586 recalls can therefore be reached from a scanned barcode, with no product-code guesswork.

**MAUDE reports (26,136,873):**

| | Reports | Share |
|---|---:|---:|
| With a product code | 26,120,089 | 99.9% |
| With a 510(k)/PMA link | 19,450,482 | 74% |
| Whose 510(k)/PMA has a recall | 13,729,061 | 53% |
| With a UDI-DI | 13,209,762 | 51% |
| With a lot number | 12,263,285 | 47% |

## 4. What this means for a client deployment

**What it can do now:**

- Answer recall questions by device type, by firm (FEI), by cleared device (510(k)/PMA), by lot text, by class, by root cause and by year, all offline in seconds.
- Join each answer to adverse-event volume and to the manufacturer's registered sites.
- Keep every published lot list in full (no truncation), and keep the source URL and retrieval date on every record.

**Gaps a client would notice, and the planned fix for each:**

1. **Barcode → recall is text-based today.** The 136,602 device identifiers in recall code text are matched by pattern. Writing them as explicit links (recall → UDI identifier) at build time would make "scan a barcode, get its recalls" a one-hop query for 11,586 recalls.
2. **Firm names vary across FDA sources** (for example "Baxter Healthcare Corp." and "Baxter Healthcare Corporation"). Grouping by the normalized firm node resolves most of this. Divisions remain separate: "MEDLINE INDUSTRIES, LP - Northfield" is listed apart from "Medline Industries, LP". The FEI join gives the true manufacturer.
   - **One recall can hold the lot text twice** (enforcement and recall database, with different line breaks), so a lot lookup can return the same recall twice. Queries should use DISTINCT.
3. **About 30% of recalls carry no 510(k)/PMA number.** They are mostly exempt Class I devices; these still join by product code and firm.
4. **Distribution text is parsed for US states only.** Foreign countries are a next step.
5. **Size for offline packaging.** The recall-centred core is about 22 M triples. The full graph is about 1.3 B triples (88 GB of Turtle), dominated by MAUDE narratives and UDI. A client bundle can ship the core plus UDI and leave narratives as an optional add-on.

## 5. Reproducing

```
python3 scripts/client_readiness.py --out <build dir> --recall-out <recall layer dir> \
    --db <scratch TDB2 dir> --report report.md [--skip-event] [--add recall_class.ttl]
```
