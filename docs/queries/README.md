# Saved queries

These four SPARQL queries are published outside this repository: they are the saved queries for
medical-device-kg in the OKN registry (frink-okn/okn-registry pull request #551) and they are used by
the ENGG 143G Bioengineering Capstone Design course (prior art, standards, hazards and regulatory pathway).

| File | Answers |
|---|---|
| `device-profile-by-product-code.rq` | device class, regulation number and title, 510(k) and recall counts |
| `510k-records-by-product-code.rq` | 510(k) records per product code, newest first |
| `recognized-standards-by-product-code.rq` | FDA-recognized consensus standards, by product code or regulation |
| `maude-device-problems-by-product-code.rq` | MAUDE device problems by report count, with the passive-surveillance caveat |

The `ex:` namespace and every `ex:` class and property used here are a promise to those users:
they do not change between releases. CI enforces it: `scripts/query_contract.py` fails the build if a
query uses a term that is no longer declared in `ontology/` or no longer emitted by a converter, and
Jena `qparse` checks the syntax. If a term ever has to change, update these files, the registry copies
and the course materials in the same release.
