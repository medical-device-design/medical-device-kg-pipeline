#!/usr/bin/env python3
"""
common.py -- shared Turtle literal, provenance, and serialization helpers.

The division of labour in the pipeline:
    iri.py     mints every IRI (the subjects and object references)
    common.py  formats every LITERAL, the provenance block, and writes blocks
    sources/*  decide WHICH triples a record has; they own no formatting

Every source converter imports from here, so a fix to date typing or literal
escaping happens once and applies everywhere. That is the whole point.

THE DATE FIX (defect D4)
------------------------
The legacy graph stored dates as untyped "YYYYMMDD" strings, so no range query
worked. openFDA delivers dates two ways -- UDI as ISO "2016-09-12", most other
device endpoints as 8-digit "20160912". date_lit() accepts both, VALIDATES that
the value is a real calendar date, and emits xsd:date. A value that is not a real
date (FDA ships "00000000", "20169999", partial dates) is kept as an untyped
string rather than emitted as an INVALID xsd:date -- because an invalid typed
literal makes SPARQL FILTER(?d > "..."^^xsd:date) throw, which is worse than an
untyped string. Never silently downgrade a good date; never fabricate a bad one.
"""

import re
from datetime import datetime

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YYYYMMDD = re.compile(r"^\d{8}$")


# ---------------------------------------------------------------------------
# String literals
# ---------------------------------------------------------------------------
def esc(value):
    """Escape a Python string for a Turtle double-quoted literal."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def lit(value):
    """Plain string literal, or None if blank (None objects are skipped later)."""
    if value is None:
        return None
    s = str(value).strip()
    return f'"{esc(s)}"' if s else None


def lang_lit(value, lang="en"):
    """Language-tagged string literal (for human-readable text where it helps)."""
    if value is None:
        return None
    s = str(value).strip()
    return f'"{esc(s)}"@{lang}' if s else None


# ---------------------------------------------------------------------------
# Typed literals
# ---------------------------------------------------------------------------
def bool_lit(value):
    """openFDA serialises booleans as the STRINGS "true"/"false"; emit a real
    xsd:boolean, or None when absent/unparseable."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("true", "yes", "y", "1"):
        return '"true"^^xsd:boolean'
    if s in ("false", "no", "n", "0"):
        return '"false"^^xsd:boolean'
    return None


def int_lit(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return f'"{int(str(value).strip())}"^^xsd:integer'
    except (ValueError, TypeError):
        return None


def float_lit(value):
    """Decimal measurement (storage temperature, quantities). xsd:decimal."""
    if value is None or str(value).strip() == "":
        return None
    try:
        f = float(str(value).strip())
    except (ValueError, TypeError):
        return None
    # canonical, no exponent, no trailing noise
    return f'"{f:g}"^^xsd:decimal'


def _valid_date(y, m, d):
    try:
        datetime(int(y), int(m), int(d))
        return True
    except ValueError:
        return False


def date_lit(value):
    """
    Emit xsd:date for a real calendar date, whether it arrives ISO (2016-09-12)
    or as 8 digits (20160912). Anything that is not a real date is preserved as
    an untyped string (see the module docstring on why). Returns None if blank.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    if ISO_DATE.match(s):
        y, m, d = s[0:4], s[5:7], s[8:10]
        return f'"{s}"^^xsd:date' if _valid_date(y, m, d) else f'"{esc(s)}"'

    if YYYYMMDD.match(s):
        y, m, d = s[0:4], s[4:6], s[6:8]
        if _valid_date(y, m, d):
            return f'"{y}-{m}-{d}"^^xsd:date'
        return f'"{esc(s)}"'          # e.g. "20169999" -- keep, don't fake a date

    # some other shape -- keep verbatim as a string, never guess
    return f'"{esc(s)}"'


# ---------------------------------------------------------------------------
# Multi-valued fields
# ---------------------------------------------------------------------------
def as_list(value):
    """Normalise a repeatable field to a list of non-empty strings. openFDA is
    inconsistent: some multi-valued fields arrive as JSON lists, others as
    semicolon-delimited strings. Handle both rather than assuming."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).split(";") if p.strip()]


# ---------------------------------------------------------------------------
# Provenance -- every record must trace to its FDA source
# ---------------------------------------------------------------------------
def provenance(dataset, record_id, source_url=None, retrieved_at=None):
    """
    Return the standard provenance (predicate, object) pairs, in a FIXED order,
    skipping any that are absent. Emits only what is supplied, so a converter can
    adopt this without changing its output until it also passes retrieved_at.

    retrieved_at MUST come from the fetch manifest (the download date), NEVER
    datetime.now() -- otherwise the output stops being reproducible.
    """
    pairs = [
        ("ex:sourceDataset", lit(dataset)),
        ("ex:sourceRecordId", lit(record_id)),
    ]
    if source_url is not None:
        pairs.append(("ex:sourceUrl", lit(source_url)))
    if retrieved_at is not None:
        pairs.append(("ex:retrievedAt", date_lit(retrieved_at)))
    return pairs


# ---------------------------------------------------------------------------
# Turtle serialization
# ---------------------------------------------------------------------------
def write_block(out, subject, pairs):
    """Write one Turtle subject block, skipping pairs whose object is None.
    Returns the number of triples written (0 if the block was empty)."""
    valid = [(p, o) for p, o in pairs if o is not None]
    if not valid:
        return 0
    out.write(f"{subject}\n")
    for i, (pred, obj) in enumerate(valid):
        sep = " ;" if i < len(valid) - 1 else " ."
        out.write(f"    {pred} {obj}{sep}\n")
    out.write("\n")
    return len(valid)


def prefixes(extra=None):
    """The standard prefix header. `extra` is an optional dict of prefix->IRI.
    ex: is bound to iri.NS by the caller (kept out of here to avoid a circular
    import; the source converter passes it in or builds the header itself)."""
    base = {
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
    }
    if extra:
        base.update(extra)
    return "".join(f"@prefix {p}: <{u}> .\n" for p, u in base.items())


# ===========================================================================
#  SELF-TEST -- run `python3 common.py`. Focuses on the date fix (D4), which is
#  the subtle one, plus the typed-literal contracts.
# ===========================================================================
def _selftest():
    # --- dates: both input shapes -> xsd:date -----------------------------
    assert date_lit("2016-09-12") == '"2016-09-12"^^xsd:date'
    assert date_lit("20160912") == '"2016-09-12"^^xsd:date'   # D4: 8-digit typed
    assert date_lit("20040630") == '"2004-06-30"^^xsd:date'
    # --- invalid dates: kept as string, NOT emitted as invalid xsd:date ---
    assert date_lit("00000000") == '"00000000"'
    assert date_lit("20169999") == '"20169999"'               # bad month/day
    assert date_lit("2016-13-45") == '"2016-13-45"'           # ISO-shaped but unreal
    assert date_lit("") is None
    assert date_lit(None) is None
    # --- booleans: strings -> typed --------------------------------------
    assert bool_lit("true") == '"true"^^xsd:boolean'
    assert bool_lit("false") == '"false"^^xsd:boolean'
    assert bool_lit("Yes") == '"true"^^xsd:boolean'
    assert bool_lit("maybe") is None
    assert bool_lit(None) is None
    # --- ints / floats ----------------------------------------------------
    assert int_lit("42") == '"42"^^xsd:integer'
    assert int_lit(" 7 ") == '"7"^^xsd:integer'
    assert int_lit("x") is None
    assert float_lit("2.0") == '"2"^^xsd:decimal'
    assert float_lit("-40") == '"-40"^^xsd:decimal'
    assert float_lit("") is None
    # --- strings / escaping ----------------------------------------------
    assert lit("  hi  ") == '"hi"'
    assert lit("") is None
    assert lit('a"b') == '"a\\"b"'
    assert lit("a\nb") == '"a\\nb"'
    # --- lists ------------------------------------------------------------
    assert as_list(["a", " b ", ""]) == ["a", "b"]
    assert as_list("a; b ;;c") == ["a", "b", "c"]
    assert as_list(None) == []
    # --- provenance: only what's supplied, fixed order --------------------
    p = provenance("device/udi", "REC1", source_url="http://x/REC1")
    assert p == [
        ("ex:sourceDataset", '"device/udi"'),
        ("ex:sourceRecordId", '"REC1"'),
        ("ex:sourceUrl", '"http://x/REC1"'),
    ], p
    p2 = provenance("device/recall", "94520", retrieved_at="20261002")
    assert ("ex:retrievedAt", '"2026-10-02"^^xsd:date') in p2

    # --- write_block: skips None, closes with a period --------------------
    import io
    buf = io.StringIO()
    k = write_block(buf, "<s>", [("ex:a", '"1"'), ("ex:b", None), ("ex:c", '"3"')])
    assert k == 2
    assert buf.getvalue() == '<s>\n    ex:a "1" ;\n    ex:c "3" .\n\n', repr(buf.getvalue())

    print("common.py self-test PASSED.")


if __name__ == "__main__":
    _selftest()
