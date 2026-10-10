#!/usr/bin/env python3
"""
distribution_parser.py -- turn FDA's free-text recall `distributionPattern`
into structured, joinable geography.

THE PROBLEM (see the lightning talk, slide 3)
---------------------------------------------
FDA records where a recalled device went in ONE free-text field. Example, from
Medtronic MiniMed Class I recall Z-0001-2025 (verbatim):

    "US: CT, MI, PA, WA, IA, NY, ND, AZ, TX, OH, NC, AL, MN, IN, NJ, KY, UT, CA,
     FL, VA, MS, NM, NV, TN, GA, MA, NH, OK, VT, IL, ME, SC, LA, WY, RI, SD, KS,
     WI, MD, CO, DE, AR, AK, ID, MO, NE, WV, MT, OR, DC, HI, VI, PR.  OUS: Worldwide"

No query can filter or join on that. This module parses it into:

    us_states     -> ["AK","AL","AR", ...]   (validated 2-letter codes)
    ous_countries -> ["Worldwide"]           (or a real country list)
    flags          us_nationwide, worldwide, us_none

The us_states list IS the join key: a graph keyed on US state (rural-kg,
neighborhood-information-kg, soc-kg, ...) can now join to a recall. This is the
"structure the field" half of the cross-graph story -- the half that lives
entirely in our own data and needs no one else's cooperation to prove.

Deterministic; no network. Run `python3 distribution_parser.py` for the self-test.

IRIs: the recall subject is minted by iri.py (iri.recall), never built here --
so the structured triples land on the SAME recall node the recalls converter
mints. (Before this was folded in, the local copy kept hyphens -- "z-0001-2025"
-- while iri.recall slugs to "z_0001_2025", so the two would not have joined.)
"""

import os
import re
import sys

# iri.py lives in the same pipeline/ directory; it is the ONLY source of IRIs.
_PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
if _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)
import iri  # noqa: E402

# 50 states + DC + the five inhabited US territories FDA uses in recall data.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}

# Full names, because the device/recall database writes "Vermont" where the
# enforcement feed writes "VT" (verified on api.fda.gov/device/recall, Oct 2026).
STATE_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX",
    "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC", "PUERTO RICO": "PR", "GUAM": "GU",
    "VIRGIN ISLANDS": "VI", "AMERICAN SAMOA": "AS", "NORTHERN MARIANA ISLANDS": "MP",
}
# longest names first so "WEST VIRGINIA" wins over "VIRGINIA", "NEW YORK" etc.
_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(n) for n in STATE_NAMES), key=len, reverse=True)) + r")\b")
# Two-letter codes that are also everyday words; accept them only in a code list
# context (after "US:" or among other codes), never from prose.
_AMBIGUOUS = {"IN", "OR", "ME", "OK", "HI", "DE", "PA", "MA", "AS", "ID", "OH"}

_US_SECTION = re.compile(r"\bUS\s*:\s*(.*?)(?:\bOUS\s*:|$)", re.I | re.S)
_OUS_SECTION = re.compile(r"\bOUS\s*:\s*(.*)$", re.I | re.S)
_TWO_LETTER = re.compile(r"\b([A-Z]{2})\b")
_NATIONWIDE = re.compile(r"nationwide", re.I)
_NO_DIST = re.compile(r"\bno\s+distribution\b", re.I)
_WORLDWIDE = re.compile(r"worldwide", re.I)
_US_WHOLE = re.compile(r"\b(united states|us and us territories|u\.s\.a?\.?|usa)\b|^\s*US\b(?!\s*:)", re.I)
_INTL = re.compile(r"international\s+distribution\s+(?:to|in)\s+the\s+(?:countr(?:y|ies)|states?)\s+of\s*:?\s*(.*)$",
                   re.I | re.S)
_STATE_OF = re.compile(r"\bstates?\s+of\b", re.I)          # "in the US state of MA" -> code-list context
_COUNTRIES_OF = re.compile(r"\bcountr(?:y|ies)\s+of\s*:?\s*", re.I)
_ROW = re.compile(r"\bROW\b|\brest of (?:the )?world\b", re.I)


def parse_distribution(text):
    """
    Parse a recall distributionPattern string into structured geography.

    Returns a dict:
        us_states      sorted list of valid 2-letter US state/territory codes
        ous_countries  list of country strings (or ["Worldwide"])
        us_nationwide  True if the text says "nationwide" (no explicit states)
        us_none        True if the text says "US: No distribution"
        worldwide      True if OUS is "Worldwide"
        raw            the original text (provenance -- never discard the source)
    """
    result = {
        "us_states": [], "ous_countries": [],
        "us_nationwide": False, "us_none": False, "worldwide": False,
        "raw": text,
    }
    if not text or not str(text).strip():
        return result
    s = str(text).strip()

    # If there is no explicit "US:"/"OUS:" split, treat the whole string as US.
    m_us = _US_SECTION.search(s)
    m_ous_at = re.search(r"\bOUS\s*:", s, re.I)
    # No "US:" label: the US part is everything before "OUS:" (or the whole text).
    # (Fixes "US Nationwide distribution. OUS: Canada", which used to lose the US side.)
    us_text = m_us.group(1) if m_us else (s[:m_ous_at.start()] if m_ous_at else s)
    m_ous = _OUS_SECTION.search(s)
    ous_text = m_ous.group(1) if m_ous else ""

    # --- US side ---
    if _NO_DIST.search(us_text):
        result["us_none"] = True
    else:
        up = us_text.upper()
        # 1) full state names ("Vermont", "New York"); remove them so their
        #    letters can't be re-read as codes ("NEW YORK" -> no stray "NE").
        names = [STATE_NAMES[n] for n in _STATE_NAME_RE.findall(up)]
        rest = _STATE_NAME_RE.sub(" ", up)
        # Countries listed in the US part must not be read as state codes.
        mc_us = _COUNTRIES_OF.search(rest)
        if mc_us:
            rest = rest[:mc_us.start()]
        # "...in the US state(s) of MA" without a "US:" label: read codes only
        # AFTER "state(s) of", so the prose word "in" is not taken as Indiana.
        ms = _STATE_OF.search(rest)
        if ms and not m_us:
            rest = rest[ms.end():]
        # 2) two-letter codes. Ambiguous ones ("IN", "OR", "ME"...) count only
        #    when the text is a code list: "US:" prefix or >=2 unambiguous codes.
        raw_codes = [c for c in _TWO_LETTER.findall(rest) if c in US_STATES]
        firm = [c for c in raw_codes if c not in _AMBIGUOUS]
        list_context = bool(m_us) or len(firm) >= 2 or bool(_STATE_OF.search(us_text))
        codes = [c for c in raw_codes if c not in _AMBIGUOUS or list_context]
        # dedupe, keep sorted for determinism
        result["us_states"] = sorted(set(codes) | set(names))
        if not result["us_states"] and _NATIONWIDE.search(us_text):
            result["us_nationwide"] = True

    # Phrasings seen in the live device/recall sample (Oct 2026) that carry no
    # "US:"/"OUS:" labels: "United States.", "US and US Territories.",
    # "International distribution to the countries of Austria, Germany.", "Worldwide".
    if not m_us and not m_ous_at:
        if not result["us_states"] and _US_WHOLE.search(s) and not _INTL.search(s):
            result["us_nationwide"] = True
        mi = _INTL.search(s)
        if mi:
            result["us_states"] = []          # codes inside an international list are not US states
            ous_text = mi.group(1)
        elif _WORLDWIDE.search(s) or _ROW.search(s):
            ous_text = "Worldwide"

    # --- OUS side ---
    if _WORLDWIDE.search(ous_text):
        result["worldwide"] = True
        result["ous_countries"] = ["Worldwide"]
    elif ous_text.strip():
        # split on commas, drop the "only to the countries of:" boilerplate
        cleaned = re.sub(r"only\s+to\s+the\s+countries\s+of\s*:?", "", ous_text, flags=re.I)
        countries = [c.strip(" .").strip() for c in cleaned.split(",")]
        result["ous_countries"] = [c for c in countries if c and not c.isspace()]

    # Unlabeled text that names countries after "countries of" (common in
    # "Worldwide distribution - US Nationwide and the countries of A, B, C").
    if not m_ous_at and not _INTL.search(s):
        mc = _COUNTRIES_OF.search(s)
        if mc:
            tail = re.split(r"[.;]\s", s[mc.end():])[0]
            tail = re.sub(r"\band\b", ",", tail, flags=re.I)
            countries = [c.strip(" .").strip() for c in tail.split(",") if c.strip(" .").strip()]
            keep = ["Worldwide"] if result["worldwide"] else []
            result["ous_countries"] = keep + countries
    # FDA text repeats countries ("KR, KR"); dedupe, keep first-seen order
    result["ous_countries"] = list(dict.fromkeys(result["ous_countries"]))
    return result


def distribution_pairs(text):
    """(predicate, object) pairs for a source converter to append to its recall
    block, so the structured geography lands on the same node in the same file.
    Does NOT re-emit ex:distributionPattern (the converter already writes it)."""
    p = parse_distribution(text)
    pairs = [("ex:distributedToState", f'"{s}"') for s in p["us_states"]]
    # common.lit escapes newlines too -- the hand-rolled escaping used here before
    # let a "\n" inside a country name through and broke Turtle (found on .83,
    # 2026-10-09: "Broken token (newline): CHILE" in recall_db.ttl)
    import common
    pairs += [("ex:distributedToCountry", common.lit(c)) for c in p["ous_countries"]]
    pairs = [(k, v) for k, v in pairs if v is not None]
    if p["us_nationwide"]:
        pairs.append(("ex:distributedNationwide", '"true"^^xsd:boolean'))
    if p["us_none"]:
        pairs.append(("ex:distributedInUS", '"false"^^xsd:boolean'))
    return pairs


# --------------------------------------------------------------------------
# RDF emission -- what the structured triples look like in the graph.
# The recall subject is minted by iri.recall(); this file mints no IRIs itself.
# --------------------------------------------------------------------------
def to_rdf(recall_number, parsed):
    """Emit the structured distribution as Turtle. Each US state becomes an
    explicit ex:distributedToState edge -- a join key an external graph can use.
    The subject is iri.recall(recall_number), i.e. exactly the node the recalls
    converter minted for this recall."""
    subj = f"<{iri.recall(recall_number)}>"
    triples = []
    for st in parsed["us_states"]:
        triples.append(f'ex:distributedToState "{st}"')
    for c in parsed["ous_countries"]:
        triples.append(f'ex:distributedToCountry "{c}"')
    if parsed["us_nationwide"]:
        triples.append('ex:distributedNationwide "true"^^xsd:boolean')
    if parsed["us_none"]:
        triples.append('ex:distributedInUS "false"^^xsd:boolean')
    # keep the original text as provenance -- we STRUCTURE, we never DELETE
    esc = parsed["raw"].replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    triples.append(f'ex:distributionPattern "{esc}"')
    body = " ;\n    ".join(triples)
    return f"{subj}\n    {body} .\n"


# ==========================================================================
#  SELF-TEST -- the real formats seen in the live MiniMed recalls.
# ==========================================================================
def _selftest():
    a = parse_distribution(
        "US: CT, MI, PA, WA, IA, NY, ND, AZ, TX, OH, NC, AL, MN, IN, NJ, KY, "
        "UT, CA, FL, VA, MS, NM, NV, TN, GA, MA, NH, OK, VT, IL, ME, SC, LA, "
        "WY, RI, SD, KS, WI, MD, CO, DE, AR, AK, ID, MO, NE, WV, MT, OR, DC, "
        "HI, VI, PR.  OUS: Worldwide")
    assert len(a["us_states"]) == 53, len(a["us_states"])   # 50 states + DC + VI + PR
    assert "CA" in a["us_states"] and "PR" in a["us_states"]
    assert a["worldwide"] is True and a["ous_countries"] == ["Worldwide"]

    b = parse_distribution("US: No distribution  OUS: Austria, Belgium, Italy, Spain")
    assert b["us_none"] is True and b["us_states"] == []
    assert b["ous_countries"] == ["Austria", "Belgium", "Italy", "Spain"]

    c = parse_distribution("Domestic distribution to NJ and WI.")
    assert c["us_states"] == ["NJ", "WI"], c["us_states"]   # narrow, joinable, specific

    d = parse_distribution("US Nationwide distribution.")
    assert d["us_nationwide"] is True and d["us_states"] == []

    e = parse_distribution("")
    assert e["us_states"] == [] and e["ous_countries"] == []

    # RDF emission produces a state edge per state
    r = to_rdf("Z-0001-2025", c)
    assert 'ex:distributedToState "NJ"' in r and 'ex:distributedToState "WI"' in r
    assert "distributionPattern" in r   # provenance preserved
    # subject is the iri.py recall node -- same slug the recalls converter mints,
    # NOT the old hyphen-preserving "z-0001-2025"
    assert r.startswith(f"<{iri.recall('Z-0001-2025')}>"), r.splitlines()[0]
    assert "recall/z_0001_2025" in r and "z-0001-2025" not in r

    # full state names (device/recall database style)
    f = parse_distribution("Vermont")
    assert f["us_states"] == ["VT"], f["us_states"]
    g = parse_distribution("Distributed in New York, West Virginia and Virginia.")
    assert g["us_states"] == ["NY", "VA", "WV"], g["us_states"]   # no stray NE/WV mix-ups
    # ambiguous two-letter words in prose are NOT states
    h = parse_distribution("Product shipped to hospitals in TX or OK.")
    assert "TX" in h["us_states"] and "IN" not in h["us_states"] and "OR" not in h["us_states"], h
    # ... but they ARE states inside an explicit code list
    i = parse_distribution("US: IN, OR, ME")
    assert i["us_states"] == ["IN", "ME", "OR"], i["us_states"]

    # unlabeled phrasings from the live sample
    j = parse_distribution("International distribution to the countries of Austria, Germany, Luxembourg.")
    assert j["ous_countries"] == ["Austria", "Germany", "Luxembourg"] and j["us_states"] == [], j
    k = parse_distribution("International distribution to the country of Japan.")
    assert k["ous_countries"] == ["Japan"], k
    assert parse_distribution("United States.")["us_nationwide"] is True
    assert parse_distribution("US and US Territories.")["us_nationwide"] is True
    w = parse_distribution("Worldwide distribution.")
    assert w["worldwide"] is True and w["ous_countries"] == ["Worldwide"], w

    assert parse_distribution("Distribution in the US state of MA")["us_states"] == ["MA"]
    row = parse_distribution("US and ROW.")
    assert row["us_nationwide"] is True and row["worldwide"] is True, row
    mix = parse_distribution("Worldwide - US Nationwide distribution in the states of FL, GA, IL, "
                             "and the countries of Guatemala and Panama.")
    assert mix["us_states"] == ["FL", "GA", "IL"], mix["us_states"]
    assert mix["worldwide"] and mix["ous_countries"] == ["Worldwide", "Guatemala", "Panama"], mix
    eu = parse_distribution("International distribution in the states of Austria, Spain.")
    assert eu["ous_countries"] == ["Austria", "Spain"] and eu["us_states"] == [], eu

    # converter helper
    pr = distribution_pairs("US Nationwide distribution.  OUS: Canada")
    assert ("ex:distributedNationwide", '"true"^^xsd:boolean') in pr
    assert ("ex:distributedToCountry", '"Canada"') in pr
    nl = distribution_pairs("OUS: Brazil, \nCHILE")
    assert all("\n" not in v for _, v in nl), nl            # regression: newline in a country

    print("distribution_parser.py self-test PASSED.")


if __name__ == "__main__":
    _selftest()
