# Recall monitor, 2026-11-16

Status: **ATTENTION**

| | FDA (openFDA) | In the graph |
|---|---|---|
| Medical Device Recall database | 59,705 (updated 2026-11-12) | 59,705 |
| Enforcement reports | 40,481 (updated 2026-11-04) | 40,481 |
| **Distinct recalls** | | **59,708** |

Recall class known for 59,700 (100.0%): Class I 5,035, Class II 52,076, Class III 2,588, Not Yet Classified 1.
Source of the class: FDA recall database web page (cfRES) 19,219, enforcement report 40,481.

Since the previous run (2026-11-09): **2 new recalls**, 0 class changes (lists in new_recalls.tsv and class_changes.tsv).

## Warnings

- 8 recalls still have no class (FDA may not have classified them yet)
- 21 implausible initiation dates: Z-0002-2014 0012-12-13, Z-0139-2014 0012-11-30, Z-0493-2014 0013-11-26, Z-0536-2014 0013-11-25, Z-0660-2013 0012-12-06

## Validation

```
recalls.ttl: riot OK, Triples = 2,079,108
recall_db.ttl: riot OK, Triples = 2,910,990
recall_class.ttl: riot OK, Triples = 192,190
recalls.ttl vs postmarket.shacl.ttl: conforms
recalls.ttl vs identity.shacl.ttl: conforms
recall_class.ttl vs postmarket.shacl.ttl: conforms
recall_class.ttl vs identity.shacl.ttl: conforms
```
