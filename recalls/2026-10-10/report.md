# Recall monitor, 2026-10-10

Status: **ATTENTION**

| | FDA (openFDA) | In the graph |
|---|---|---|
| Medical Device Recall database | 59,468 (updated 2026-10-08) | 59,468 |
| Enforcement reports | 40,113 (updated 2026-09-30) | 40,113 |
| **Distinct recalls** | | **59,471** |

Recall class known for 59,117 (99.4%): Class I 4,973, Class II 51,580, Class III 2,563, Not Yet Classified 1.
Source of the class: FDA recall database web page (cfRES) 19,004, enforcement report 40,113.

Since the previous run (none): **0 new recalls**, 0 class changes (lists in new_recalls.tsv and class_changes.tsv).

## Warnings

- 354 recalls still have no class (FDA may not have classified them yet)
- 21 implausible initiation dates: Z-0002-2014 0012-12-13, Z-0139-2014 0012-11-30, Z-0493-2014 0013-11-26, Z-0536-2014 0013-11-25, Z-0660-2013 0012-12-06

## Validation

```
recalls.ttl: riot OK, Triples = 1,638,516
recall_db.ttl: riot OK, Triples = 2,517,055
recall_class.ttl: riot OK, Triples = 190,040
recalls.ttl vs postmarket.shacl.ttl: conforms
recalls.ttl vs identity.shacl.ttl: conforms
recall_class.ttl vs postmarket.shacl.ttl: conforms
recall_class.ttl vs identity.shacl.ttl: conforms
```
