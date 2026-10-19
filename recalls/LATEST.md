# Recall monitor, 2026-10-19

Status: **ATTENTION**

| | FDA (openFDA) | In the graph |
|---|---|---|
| Medical Device Recall database | 59,494 (updated 2026-10-15) | 59,494 |
| Enforcement reports | 40,249 (updated 2026-10-07) | 40,249 |
| **Distinct recalls** | | **59,497** |

Recall class known for 59,475 (100.0%): Class I 5,006, Class II 51,881, Class III 2,587, Not Yet Classified 1.
Source of the class: FDA recall database web page (cfRES) 19,226, enforcement report 40,249.

Since the previous run (2026-10-12): **26 new recalls**, 1 class changes (lists in new_recalls.tsv and class_changes.tsv).

## Warnings

- 22 recalls still have no class (FDA may not have classified them yet)
- 21 implausible initiation dates: Z-0002-2014 0012-12-13, Z-0139-2014 0012-11-30, Z-0493-2014 0013-11-26, Z-0536-2014 0013-11-25, Z-0660-2013 0012-12-06

## Validation

```
recalls.ttl: riot OK, Triples = 2,058,940
recall_db.ttl: riot OK, Triples = 2,890,346
recall_class.ttl: riot OK, Triples = 192,260
recalls.ttl vs postmarket.shacl.ttl: conforms
recalls.ttl vs identity.shacl.ttl: conforms
recall_class.ttl vs postmarket.shacl.ttl: conforms
recall_class.ttl vs identity.shacl.ttl: conforms
```
