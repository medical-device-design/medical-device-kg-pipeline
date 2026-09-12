# Structuring the recall distribution field — worked demonstration

**What this shows:** the free-text field FDA uses to record where a recalled
device went can be turned into structured, joinable geography — entirely within
our own data, with no dependency on any other graph. This is the honest,
provable half of the slide-4 cross-graph story. The *join* to a partner graph
(rural-kg, neighborhood-information-kg, soc-kg) is the collaboration ask; this is
the piece we can already do.

Run live against `apps.okn.us/medical-device-kg/sparql` on 2026-09-10 with
`pipeline/distribution_parser.py`.

---

## Step 1 — Identify (live from medical-device-kg)

    recall        Z-0001-2025   (Class I)
    firm          Medtronic MiniMed, Inc.
    quantity      24,595
    distribution  (as FDA publishes it — free text, unqueryable):
      "US: CT, MI, PA, WA, IA, NY, ND, AZ, TX, OH, NC, AL, MN, IN, NJ, KY, UT,
       CA, FL, VA, MS, NM, NV, TN, GA, MA, NH, OK, VT, IL, ME, SC, LA, WY, RI,
       SD, KS, WI, MD, CO, DE, AR, AK, ID, MO, NE, WV, MT, OR, DC, HI, VI, PR.
       OUS: Worldwide"

## Step 2 — Structure

`distribution_parser.parse_distribution()` returns:

    us_states (53):  AK AL AR AZ CA CO CT DC DE FL GA HI IA ID IL IN KS KY LA
                     MA MD ME MI MN MO MS MT NC ND NE NH NJ NM NV NY OH OK OR
                     PA PR RI SC SD TN TX UT VA VI VT WA WI WV WY
    outside US:      Worldwide
    flags:           nationwide=False  us_none=False  worldwide=True

## Step 3 — Join-ready RDF (each state is now an explicit edge = a join key)

```turtle
@prefix ex:  <https://bmedesign.org/medical-device-kg/ns/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<https://bmedesign.org/medical-device-kg/id/recall/z-0001-2025>
    ex:distributedToState "AK" ;
    ex:distributedToState "AL" ;
    ex:distributedToState "AR" ;
    ...                             # 53 state edges total
    ex:distributedToCountry "Worldwide" ;
    ex:distributionPattern "US: CT, MI, PA, ... PR. OUS: Worldwide" .   # original kept as provenance
```

---

## What it proves — and what it deliberately does NOT claim

**Proves:** the field FDA calls unqueryable becomes 53 structured state keys.
Any OKN graph keyed on US state can now join to this recall. We *structure* the
field; we never *delete* it — the original text stays as `ex:distributionPattern`
so the transformation is auditable.

**Does not claim:** that we have located these devices in another graph. That
join is the ask on slide 4, and it needs a partner graph that keys on US state
(and, ideally, finer than state). MiniMed Z-0001-2025 in particular shipped to
53 of 55 US jurisdictions — essentially nationwide — so it is honest proof of
*structuring*, not a compelling *pinpoint*. A narrowly distributed recall (e.g.
"Domestic distribution to NJ and WI only") is the better device for a pinpoint
story; the same parser handles it (`us_states -> ["NJ","WI"]`).

## Why state-level, and the honest limitation

FDA records recall distribution at **US-state granularity** in this field, so the
join keys we can extract are states, not ZIP or tract. That joins cleanly to
graphs keyed on state, but a graph keyed on census tract would see "went to the
whole state." Finer destination geography is not in FDA's data and is not
something we can manufacture. Structuring free text into 53 correct state keys is
real and useful; inventing tract-level destinations would not be.
