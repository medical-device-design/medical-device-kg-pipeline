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
US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")      # CDRH flat files: 02/17/2017


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


def capped_pairs(pred, value, cap):
    """For very long free text (recall code_info lists thousands of serial numbers):
    emit the first `cap` characters, plus <pred>Truncated true and <pred>Length,
    so nothing is silently lost -- the full text stays at the record's sourceUrl.
    Measured 2026-10-09: code_info was 411 MB of a 612 MB recall_db.ttl."""
    if value is None:
        return []
    s = str(value).strip()
    if not s:
        return []
    if cap is None or len(s) <= cap:        # cap=None -> keep everything (project default)
        return [(pred, lit(s))]
    return [(pred, lit(s[:cap] + " …")),
            (pred + "Truncated", '"true"^^xsd:boolean'),
            (pred + "Length", f'"{len(s)}"^^xsd:integer')]


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


def gyear_lit(value):
    """A calendar year as xsd:gYear ("2024"^^xsd:gYear), or None if not 4 digits."""
    if value is None:
        return None
    s = str(value).strip()[:4]
    return f'"{s}"^^xsd:gYear' if len(s) == 4 and s.isdigit() else None


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

    m = US_DATE.match(s)              # MM/DD/YYYY, used by the CDRH ftparea files
    if m:
        mo, d, y = m.groups()
        if _valid_date(y, mo, d):
            return f'"{y}-{int(mo):02d}-{int(d):02d}"^^xsd:date'
        return f'"{esc(s)}"'

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
# Value hygiene shared by several sources (query-test findings, 2026-10-11)
# ---------------------------------------------------------------------------
_PRODUCT_CODE = re.compile(r"^[A-Za-z]{3}$")


def product_code(value):
    """An FDA product code is exactly three letters. Returns it upper-cased, or None
    for reporter-typed noise ("269", "N/A", "F T") so no junk hub node is minted.
    Callers keep the raw text in a literal when they need it."""
    s = str(value or "").strip()
    return s.upper() if _PRODUCT_CODE.match(s) else None


_WS = re.compile(r"\s+")


def norm_text(value):
    """Collapse runs of whitespace (including line breaks) to one space. Two FDA feeds
    publish the same recall text with different line breaks; normalizing makes the
    literals identical, so the record carries one value instead of two."""
    if value is None:
        return None
    s = _WS.sub(" ", str(value)).strip()
    return s or None


# A placeholder is a value made only of "nothing here" tokens: N/A, NA, N.A., NONE, NULL,
# UNKNOWN, NO PHONE, "NOT APPLICABLE" (also as FDA's fixed-width cut "NOT AP"), runs of
# zeros ("0000", "00000") or dashes, joined by spaces or = + / - (e.g. "N/A=NOT APPLICABLE",
# "0000 N/A"). A value with any other word in it ("N/A MOBILE", "YAN/AN XI ROAD") is real
# text and is kept whole: placeholders are dropped, never cut out of real text.
_NA_WORDS = {"NA", "NONE", "NULL", "UNKNOWN", "UNK"}
_NA_FORMS = [
    (re.compile(r"\bNOT\s+(A[A-Z]*)"), lambda m: " NA " if "APPLICABLE".startswith(m.group(1)) else m.group(0)),
    (re.compile(r"\bN\s*/\s*A\b"), lambda m: " NA "),          # N/A (not inside YAN/AN)
    (re.compile(r"\bN\.A\b\.?"), lambda m: " NA "),            # N.A / N.A.
    (re.compile(r"\bNO[\s-]?PHONE\b"), lambda m: " NA "),
]
_NA_SEP = re.compile(r"[\s=+/:;,.#-]+")


def is_placeholder(value):
    """True when the value only says that there is no value: every word in it is a
    placeholder token (N/A, NA, NONE, NULL, UNKNOWN, NOT APPLICABLE or a fixed-width cut of
    it, NO PHONE, a run of zeros) and nothing else; dashes or punctuation alone count too."""
    s = norm_text(value)
    if not s:
        return True
    s = s.upper()
    for rx, rep in _NA_FORMS:
        s = rx.sub(rep, s)
    toks = [t for t in _NA_SEP.split(s) if t]
    return all(t in _NA_WORDS or set(t) == {"0"} for t in toks)


_PHONE_EXT_NA = re.compile(r"\s*-?\s*X\s*(N\s*/?\s*A|NONE|0+)\s*$", re.I)


def clean_value(value):
    """The value as given (stripped), or None when it is a placeholder (no triple is written)."""
    if value is None or is_placeholder(value):
        return None
    return str(value).strip()


def clean_phone(value):
    """A phone number without a placeholder extension ("3086353033-XN/A" -> "3086353033");
    None when the number itself is a placeholder ("N/A", "0000000000", "NO-PHONE-XN/A")."""
    if value is None:
        return None
    s = str(value).strip()
    m = _PHONE_EXT_NA.search(s)
    if m and m.start() > 0:
        s = s[:m.start()].strip() or None
    return clean_value(s)


def norm_address(*parts):
    """Join address lines, dropping placeholder parts such as "N/A", "0000 N/A" or
    "N/A=NOT APPLICABLE" (see is_placeholder)."""
    keep = []
    for p in parts:
        s = norm_text(p)
        if s and not is_placeholder(s):
            keep.append(s)
    return ", ".join(keep) or None


def label(*parts, sep=": "):
    """A human-readable rdfs:label from the non-empty parts, e.g. "K230972: Ingenia"."""
    vals = [norm_text(p) for p in parts]
    return lit(sep.join(v for v in vals if v)) if any(vals) else None


def firm_name_pairs(normalized, raw=None):
    """The FirmName block. Several sources write the same node, so its one rdfs:label is
    the normalized name (identical everywhere); each source's spelling is an altLabel."""
    raw_s = norm_text(raw)
    return [
        ("a", "ex:FirmName"),
        ("rdfs:label", lit(normalized)),
        ("skos:altLabel", lit(raw_s) if raw_s and raw_s != normalized else None),
        ("ex:normalizedName", lit(normalized)),
    ]


# MAUDE patient outcome: older reports carry FDA's one-letter codes, newer ones the words.
OUTCOME_CODES = {
    "D": "Death", "L": "Life Threatening", "H": "Hospitalization", "S": "Disability",
    "C": "Congenital Anomaly", "R": "Required Intervention", "O": "Other",
    "U": "Unknown", "I": "Invalid Data", "N": "Not Applicable", "*": "No Information",
}


def outcome_label(value):
    """Map a one-letter MAUDE outcome code to FDA's wording; pass words through."""
    s = str(value or "").strip()
    if not s:
        return None
    return OUTCOME_CODES.get(s.upper(), s) if len(s) == 1 else s


def implausible_year_pairs(date_value, what, lo=1976, hi=None):
    """[(ex:dataQualityNote, "...")] when a date's year is outside [lo, hi] -- FDA typos
    such as year 0012 for 2012. The date itself is kept as published."""
    s = str(date_value or "").strip()
    m = re.match(r"^(\d{4})", s.replace("-", "")) if s else None
    if not m:
        return []
    y = int(m.group(1))
    hi = hi or datetime.utcnow().year + 1
    if lo <= y <= hi:
        return []
    return [("ex:dataQualityNote", lit(f"{what}: year {m.group(1)} is implausible as published by FDA"))]


_GTIN14 = re.compile(r"(?<!\d)(\d{14})(?!\d)")


def gs1_dis(text):
    """GS1 device identifiers (14-digit GTINs with a valid check digit) quoted in free
    text such as a recall's code_info. Order kept, duplicates dropped. The check digit
    rejects most lot or serial numbers that happen to be 14 digits long."""
    out = []
    for g in _GTIN14.findall(str(text or "")):
        body, check = g[:13], int(g[13])
        total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(body))
        if (10 - total % 10) % 10 == check and g not in out:
            out.append(g)
    return out


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
    assert date_lit("02/17/2017") == '"2017-02-17"^^xsd:date'   # CDRH flat-file shape
    assert date_lit("2/3/2017") == '"2017-02-03"^^xsd:date'
    assert date_lit("13/45/2017") == '"13/45/2017"'                # unreal -> kept as string
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
    # --- capped long text --------------------------------------------------
    assert capped_pairs("ex:codeInfo", "abc", 10) == [("ex:codeInfo", '"abc"')]
    cp = capped_pairs("ex:codeInfo", "x" * 25, 10)
    assert cp[0][1].startswith('"xxxxxxxxxx') and ("ex:codeInfoTruncated", '"true"^^xsd:boolean') in cp
    assert ("ex:codeInfoLength", '"25"^^xsd:integer') in cp
    assert capped_pairs("ex:codeInfo", "  ", 10) == []
    assert capped_pairs("ex:codeInfo", "x" * 25, None) == [("ex:codeInfo", '"' + "x" * 25 + '"')]  # no loss
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

    # --- gs1_dis: GTIN-14 check digit, order kept, duplicates dropped -----
    s = "UDI-DI 10197344133253 Lot EP250618; (case) 20888277407517 serial 12345678901234 10197344133253"
    assert gs1_dis(s) == ["10197344133253", "20888277407517"], gs1_dis(s)
    assert gs1_dis(None) == [] and gs1_dis("lot 123456789012345") == []

    # --- value hygiene ------------------------------------------------------
    assert product_code("frn") == "FRN" and product_code(" DSP ") == "DSP"
    assert product_code("269") is None and product_code("N/A") is None and product_code("F T") is None
    assert norm_text("a\n\n b  c ") == "a b c" and norm_text("  ") is None
    assert norm_address("1300 Macarthur Blvd", "N/A") == "1300 Macarthur Blvd"
    assert norm_address("3 Lakes Dr", None) == norm_address("3 Lakes Dr", "n/a")
    # placeholders found in the X-ray assembler file (v0.0.7 full test N10): dropped whole
    for v in ("N/A", "N/A=NOT APPLICABLE", "0000 N/A", "NA", "na", "N.A.", "N.A", "NONE", "UNKNOWN",
              "N/A=NOT AP", "N/A=NOT  A", "N/A+NOT AP", "N/A NOT AP", "NOT APPLICABLE:", "NA/", "N/A/",
              "00000", "0000000000", "#0", "-", "NO-PHONE"):
        assert is_placeholder(v) and clean_value(v) is None and norm_address(v) is None, v
    # real text that merely contains N/A-like letters is kept whole, never cut
    for v in ("YAN/AN XI ROAD", "N/A MOBILE", "MOBILE N/A", "N/A (OR-2)", "N/A896", "N/AQ", "OB/GYN",
              "SOLOMON/ANTHONY", "GERONTOLGY RESEASRCH CENTER N/A", "NOT AVAILABLE", "NONE ST", "NA PALI",
              "100 N A ST", "0 MAIN ST", "NASHVILLE", "N"):
        assert not is_placeholder(v) and clean_value(v) == v, v
    assert norm_address("YAN/AN XI ROAD", "0000 N/A") == "YAN/AN XI ROAD"
    assert clean_phone("3086353033-XN/A") == "3086353033" and clean_phone("(334)953-0393") == "(334)953-0393"
    assert clean_phone("NO-PHONE-XN/A") is None and clean_phone("N/A") is None and clean_phone("0000000000") is None
    assert clean_phone("5551234567-X123") == "5551234567-X123" and clean_phone("FAX") == "FAX"
    assert label("K230972", "Ingenia\nElition") == '"K230972: Ingenia Elition"'
    assert label(None, "") is None
    assert outcome_label("R") == "Required Intervention" and outcome_label("Death") == "Death"
    assert outcome_label("h") == "Hospitalization" and outcome_label("") is None
    assert implausible_year_pairs("0012-05-01", "recall initiation date")
    assert implausible_year_pairs("1930-01-01", "x") and not implausible_year_pairs("2012-05-01", "x")
    assert not implausible_year_pairs(None, "x")

    print("common.py self-test PASSED.")


if __name__ == "__main__":
    _selftest()
