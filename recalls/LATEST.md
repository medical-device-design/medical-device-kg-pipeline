# Recall monitor, 2026-10-26

Status: **ATTENTION**

| | FDA (openFDA) | In the graph |
|---|---|---|
| Medical Device Recall database | 59,552 (updated 2026-10-22) | 59,552 |
| Enforcement reports | 40,300 (updated 2026-10-14) | 40,300 |
| **Distinct recalls** | | **59,555** |

Recall class known for 59,545 (100.0%): Class I 5,019, Class II 51,937, Class III 2,588, Not Yet Classified 1.
Source of the class: FDA recall database web page (cfRES) 19,245, enforcement report 40,300.

Since the previous run (2026-10-19): **58 new recalls**, 12 class changes (lists in new_recalls.tsv and class_changes.tsv).

## Warnings

- 10 recalls still have no class (FDA may not have classified them yet)
- 21 implausible initiation dates: Z-0002-2014 0012-12-13, Z-0139-2014 0012-11-30, Z-0493-2014 0013-11-26, Z-0536-2014 0013-11-25, Z-0660-2013 0012-12-06

## Validation

```
recalls.ttl: riot OK, Triples = 2,060,888
recall_db.ttl: riot OK, Triples = 2,896,790
recall_class.ttl: riot OK, Triples = 192,450
recalls.ttl vs postmarket.shacl.ttl: conforms
recalls.ttl vs identity.shacl.ttl: conforms
recall_class.ttl vs postmarket.shacl.ttl: conforms
recall_class.ttl vs identity.shacl.ttl: conforms
```
