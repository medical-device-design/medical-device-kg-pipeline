# Saved queries

The first four SPARQL queries are published outside this repository: they are the saved queries for
medical-device-kg in the OKN registry (frink-okn/okn-registry pull request #551) and they are used by
the ENGG 143G Bioengineering Capstone Design course (prior art, standards, hazards and regulatory pathway).
The fifth protects the terms that the mcp-okn federation crosswalks join on.

| File | Answers |
|---|---|
| `device-profile-by-product-code.rq` | device class, regulation number and title, 510(k) and recall counts |
| `510k-records-by-product-code.rq` | 510(k) records per product code, newest first |
| `recognized-standards-by-product-code.rq` | FDA-recognized consensus standards, by product code or regulation |
| `maude-device-problems-by-product-code.rq` | MAUDE device problems by report count, with the passive-surveillance caveat |
| `federation-crosswalk-terms.rq` | sample rows for every mcp-okn crosswalk join key: ZIP, company name, coordinates, MAUDE patient problem, country codes, NAICS |

The `ex:` namespace and every `ex:` class and property used here are a promise to those users:
they do not change between releases. CI enforces it: `scripts/query_contract.py` fails the build if a
query uses a term that is no longer declared in `ontology/` or no longer emitted by a converter, and
Jena `qparse` checks the syntax. If a term ever has to change, update these files, the registry copies
and the course materials in the same release.

## Federation crosswalks (mcp-okn)

The mcp-okn federation server (sbl-sdsc/mcp-okn) links medical-device-kg to other OKN graphs through
crosswalks that depend on the terms in `federation-crosswalk-terms.rq`. They were moved to the
v0.0.5 `https://bmedesign.org/medical-device-kg/ns/` namespace in
[sbl-sdsc/mcp-okn#43](https://github.com/sbl-sdsc/mcp-okn/pull/43) and
[sbl-sdsc/mcp-okn#45](https://github.com/sbl-sdsc/mcp-okn/pull/45)
(closing [sbl-sdsc/mcp-okn#15](https://github.com/sbl-sdsc/mcp-okn/issues/15)).

| Crosswalk | Partner graph | Join terms |
|---|---|---|
| J7-J11 ZIP5 | spoke-okn, scales, sudokn, ruralkg, dreamkg | `ex:postalCode` (US records: `ex:country` absent, "US", "United States" or "PR") on `ex:RegistrationListingRecord`, `ex:MammographyFacility`, `ex:XrayAssemblerRecord`, `ex:K510Record`, `ex:PMARecord`, `ex:HumanitarianDeviceExemption`, `ex:RecallRecord` |
| I3, I8 company name | sudokn, securechainkg | `ex:name` on `ex:RegistrationListingRecord`, confirmed by `ex:postalCode` / `ex:country` |
| MD1 S2 cell | spatialkg | `wgs:lat`, `wgs:long` (W3C WGS84, `xsd:decimal`) on geocoded `ex:RegistrationListingRecord` and `ex:MammographyFacility` sites, with `ex:geocodingSource`, `ex:geocodingConfidence`, `ex:state`, `geo:hasGeometry` / `geo:asWKT` |
| MD2, MD3 patient problem | oard-kg, rdkg | `ex:patientProblem` (plain-string FDA term, matched to HP labels) on `ex:MAUDEReport`, scoped by `ex:productCode` |
| MD4 country code | climatemodelskg | `ex:deviceManufacturerCountry`, `ex:reporterCountry` on `ex:MAUDEReport` (FIPS 10-4 codes); `ex:country` on addresses (ISO 3166-1 alpha-2 or a name) |
| I5, I6 NAICS | fiokg, securechainkg | `sudokn:hasNAICSClassifier` / `ex:hasNAICSClassification` from `ex:Manufacturer` to a classifier node with `ex:naicsCode` (bare 6-digit code) |

The crosswalks also depend on the values, not only the names: ZIP stored as ZIP5 or ZIP+4 text, the
MAUDE country fields as FIPS 10-4 codes, coordinates as `xsd:decimal`, patient problems as the FDA term
text. A change to any of these is a breaking change for the federation, even if the term stays.
`query_contract.py` checks the `ex:` terms only; the `wgs:`, `geo:` and `sudokn:` terms are emitted by
`pipeline/sources/geospatial.py` and `pipeline/sources/naics.py`.
