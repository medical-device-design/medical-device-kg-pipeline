# Daily Plan — 9 to 25 September 2026 (rev. 2)

Revised after the RENCI format email of 8 September. Two hard external dates were
missing from rev. 1.

---

## What changed

| | rev. 1 | rev. 2 |
|---|---|---|
| Deliverables for 9/25 | one deck | **two**: 5-min lightning talk + 10-min demo |
| Lightning slides due | not known | **end of day Friday 18 September** |
| Dry-run opportunity | none | **Friday 11 September, 11am ET PI call** — must reply to Jasmine to claim |
| 18 Sep | pipeline dry run | slides — pipeline dry run moves to 17 Sep |
| Release protocol | our cron tags | **FRINK bot creates `stable_*`, a human tags** |

## Four rules that keep early rebuilding safe

1. **`main` and all tags are frozen until after 25 September.** Work goes to the lakeFS
   branch `rebuild`. FRINK keeps serving `v0.0.4`.
2. **No tags before October.** The tag is the deployment trigger. Say this to Prabhjot
   in plain words — an accidental tag publishes an unfinished graph mid-meeting-prep.
3. **Development in WSL2 on the laptop.** The VM is a deployment target. This keeps
   .83 off the critical path.
4. **Every day ends with something committed.**

## Goal by 25 September

Not the finished rebuild. Eleven working days, two talks to prepare, one trip.

- Foundation reviewed: `iri.py`, `common.py`, `ontology.ttl`, SHACL shapes
- Module contract proven on **three** sources end to end (was four — the slide
  deadline costs a day)
- One dry run into `rebuild`, validated, never tagged
- Both talks rehearsed, demo clip recorded
- Every operational blocker resolved or escalated with a name and a date

---

# Week 1

## Wed 9 Sep — Claim the dry run, start the foundation

| Who | Task | Done when |
|---|---|---|
| **Roche** | **Reply to Jasmine Snipe to claim a slot at Friday's 11am ET PI call.** Two-line email. Do this first — slots will fill | Sent |
| Roche | Corrected email to Prabhjot and Mustafa: TTLs are safe in lakeFS, the asks have changed, **no tags until October** | Sent |
| Roche | Ask Prabhjot for .83 status and credentials on a **service account**, not a student login | Sent |
| Claude | `pipeline/iri.py` — every IRI, one function per entity type, new namespace | Unit tests pass |
| Claude | `pipeline/common.py` — typed literals, ISO dates, provenance block | Reviewed |
| Mustafa | Source inventory: bulk-download URL, record count, update frequency per source | Table committed |

## Thu 10 Sep — The ontology, and the lightning talk

| Who | Task | Done when |
|---|---|---|
| **Roche** | Review the new `ontology.ttl`. **Protect this hour.** Every other file inherits this decision; changing it in week 3 regenerates everything | Approved or marked up |
| Claude | Lightning talk draft, 5 slides, recall narrative | Draft ready |
| Prabhjot | Confirm .83 reachable. If not, open the IT ticket today | Answer either way |
| Mustafa | Verify coverage against FDA published totals, all 18 sources | Numbers committed |

Mustafa's numbers do double duty: they drive the rebuild *and* they are what you say
when someone in DC asks how complete the graph is.

## Fri 11 Sep — Dry run at the PI call

| Who | Task | Done when |
|---|---|---|
| **Roche** | **11am ET — deliver the lightning talk on the PI call.** Rehearsal in front of the actual audience, two weeks early, for free | Delivered |
| Roche | Write down every question asked. Those are the questions you get on the 25th | Noted |
| Claude | SHACL shapes for defects D1–D6 + ontology/data agreement + ±20% count diff | Clean against a fixture |
| Prabhjot | Create lakeFS branch `rebuild` from `main`. Nothing else | Branch exists |

## Sat 12 – Sun 13 Sep — Off

Optional: reread the ontology with fresh eyes.

---

# Week 2

## Mon 14 Sep — Reference module

| Who | Task | Done when |
|---|---|---|
| Prabhjot + Claude | `sources/product_classification.py` — the reference implementation | Valid TTL, passes SHACL |
| | Chosen first because it is small (5.7 MB) and defines `ProductCode`, the hub everything links to | |
| Roche | Revise the lightning talk from Friday's questions | v2 ready |

The first module is the expensive one. The rest are pattern-matching.

## Tue 15 Sep — Prove the joins

| Who | Task | Done when |
|---|---|---|
| Prabhjot | `sources/k510.py` — 217 MB, most inbound links of any source | Valid TTL |
| Claude | Verify `k510` → `product-code` IRIs actually resolve under the new minting rules | Join count matches |

A join that silently misses produces isolated nodes — defect D1 all over again. Catch
it here, not at the end.

## Wed 16 Sep — Recalls, dates, and the demo clip

| Who | Task | Done when |
|---|---|---|
| Prabhjot | `sources/recalls.py` — 410 MB, date-heavy, and the source the talk is built on | All dates `xsd:date` |
| Claude | Range-query test: recalls between two dates return the right rows | Passes |
| **Prabhjot** | **Record the 60-second demo clip** — one live query, screen capture. RENCI explicitly recommends pre-recording | File exists |

The clip removes any dependence on conference wifi. This is the single highest-value
insurance policy for the 25th.

## Thu 17 Sep — Pipeline dry run

| Who | Task | Done when |
|---|---|---|
| Prabhjot | Run all three modules, upload to `rebuild`, commit. **No tag** | Branch has three TTLs |
| Claude | Full validation suite against the branch | Report produced |
| Roche | Read the report, decide October scope | Decision recorded |

## Fri 18 Sep — SLIDES DUE

| Who | Task | Done when |
|---|---|---|
| **Roche** | **Submit lightning talk slides to the RENCI link. End of day. Hard deadline** | Submitted |
| Roche | Demo deck v2 finalised — restructured around the recall story, 6–7 min | Final |
| All | No pipeline work today. Protect the deadline | — |

## Sat 19 – Sun 20 Sep — Off

---

# Week 3

## Mon 21 Sep — Fix what the dry run exposed

| Who | Task | Done when |
|---|---|---|
| All | Work Thursday's defect list. No new sources | List closed |
| Prabhjot | If .83 is up: Python, lakectl, `~/.lakectl.yaml` at mode 600, 80 GB free confirmed | Verified or escalated |

## Tue 22 Sep — Comparison and rehearsal

| Who | Task | Done when |
|---|---|---|
| Claude | Run the demo's competency questions against `rebuild` and against live. Results must agree except where a defect fix changes them **and we can explain why** | Comparison table |
| **All three** | Full rehearsal: 5-min talk, then 10-min demo with the clip. Time it | Both under time |
| Roche | Decide who monitors Zoom chat during rotations — **you cannot present and watch chat** | Assigned |

## Wed 23 Sep — Freeze for travel

| Who | Task | Done when |
|---|---|---|
| All | Commit everything. Nothing half-finished on a laptop | Branch clean |
| **Roche** | **Verify the live endpoint still serves `v0.0.4` and answers all five demo queries** | Confirmed |
| Roche | One-page status: rebuilt / remaining / October dates | Written |
| Roche | Pack: **audio headset** (RENCI asked), laptop, offline copy of the clip and both decks | Packed |

Five minutes on that endpoint check. Do it even though nothing should have changed.

## Thu 24 Sep — Travel to DC

No technical work.

## Fri 25 Sep — OKN Showcase

**Morning — lightning talk, 5 minutes.** One job: make people want to come to the
afternoon station.

**Afternoon — demo, 10 minutes per rotation, 6–7 min presenting.** The audience rotates,
so you give the same demo several times. It gets sharper each round; make sure the
collaboration ask is in every single one, including the last.

Three things to raise while you are in the room:

1. **Peter Rose (SDSC)** — namespace migration is coming in October, IRIs change,
   crosswalks need rebuilding. Say it in person as well as by email.
2. **the FRINK data team** — can the Fabric accept ~277M triples when UDI lands?
3. **Coverage** — MAUDE at 0.059% and standards at 3.5% were an API ceiling, not an
   effort problem. Bulk downloads fix both. Say it before someone else asks.

---

## Standing risks

| Risk | Handling |
|---|---|
| .83 stays unreachable | Development is on the laptop. Deployment slips to October, nothing else does |
| Repo transfer stays blocked | Registry `homepage:` fix waits. Does not block the rebuild |
| Ontology changes after week 2 | Regenerates everything built so far. This is why 10 Sep matters |
| Accidental tag on `rebuild` | Publishes an unfinished graph to FRINK. One sentence to Prabhjot prevents it |
| Roche runs out of hours | Days 21–22 compress. **10 Sep, 11 Sep and 18 Sep do not** |
| Conference wifi fails | The recorded clip. Recorded 16 Sep, carried offline |
