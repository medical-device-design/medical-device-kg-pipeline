# Recall monitor, 2026-11-09

Status: **ATTENTION**

| | FDA (openFDA) | In the graph |
|---|---|---|
| Medical Device Recall database | 59,703 (updated 2026-11-05) | 59,703 |
| Enforcement reports | 40,450 (updated 2026-10-28) | 40,450 |
| **Distinct recalls** | | **59,706** |

Recall class known for 59,698 (100.0%): Class I 5,035, Class II 52,074, Class III 2,588, Not Yet Classified 1.
Source of the class: FDA recall database web page (cfRES) 19,248, enforcement report 40,450.

Since the previous run (2026-11-02): **57 new recalls**, 0 class changes (lists in new_recalls.tsv and class_changes.tsv).

## Warnings

- 8 recalls still have no class (FDA may not have classified them yet)
- 21 implausible initiation dates: Z-0002-2014 0012-12-13, Z-0139-2014 0012-11-30, Z-0493-2014 0013-11-26, Z-0536-2014 0013-11-25, Z-0660-2013 0012-12-06

## Validation

```
recalls.ttl: riot OK, Triples = 2,076,651
recall_db.ttl: riot OK, Triples = 2,910,915
recall_class.ttl: riot OK, Triples = 192,480
recalls.ttl vs postmarket.shacl.ttl: conforms
recalls.ttl vs identity.shacl.ttl: conforms
recall_class.ttl vs postmarket.shacl.ttl: conforms
recall_class.ttl vs identity.shacl.ttl: conforms
```
