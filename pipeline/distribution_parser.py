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

_US_SECTION = re.compile(r"\bUS\s*:\s*(.*?)(?:\bOUS\s*:|$)", re.I | re.S)
_OUS_SECTION = re.compile(r"\bOUS\s*:\s*(.*)$", re.I | re.S)
_TWO_LETTER = re.compile(r"\b([A-Z]{2})\b")
_NATIONWIDE = re.compile(r"nationwide", re.I)
_NO_DIST = re.compile(r"\bno\s+distribution\b", re.I)
_WORLDWIDE = re.compile(r"worldwide", re.I)


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
    us_text = m_us.group(1) if m_us else (s if not re.search(r"\bOUS\s*:", s, re.I) else "")
    m_ous = _OUS_SECTION.search(s)
    ous_text = m_ous.group(1) if m_ous else ""

    # --- US side ---
    if _NO_DIST.search(us_text):
        result["us_none"] = True
    else:
        codes = [c for c in _TWO_LETTER.findall(us_text.upper()) if c in US_STATES]
        # dedupe, keep sorted for determinism
        result["us_states"] = sorted(set(codes))
        if not result["us_states"] and _NATIONWIDE.search(us_text):
            result["us_nationwide"] = True

    # --- OUS side ---
    if _WORLDWIDE.search(ous_text):
        result["worldwide"] = True
        result["ous_countries"] = ["Worldwide"]
    elif ous_text.strip():
        # split on commas, drop the "only to the countries of:" boilerplate
        cleaned = re.sub(r"only\s+to\s+the\s+countries\s+of\s*:?", "", ous_text, flags=re.I)
        countries = [c.strip(" .").strip() for c in cleaned.split(",")]
        result["ous_countries"] = [c for c in countries if c and not c.isspace()]

    return result


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

    print("distribution_parser.py self-test PASSED.")


if __name__ == "__main__":
    _selftest()
