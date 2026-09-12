#!/usr/bin/env python3
"""
iri.py -- the single source of every IRI in the medical-device knowledge graph.

WHY THIS FILE EXISTS
--------------------
Every identifier in the graph -- for a device, a product code, a 510(k) record,
a recall -- is minted HERE and nowhere else. Two consequences follow, and they
are the entire reason the module exists:

  1. A namespace migration is a TWO-LINE edit (NS and ID below) plus one
     regeneration. It is never a 1.3 GB text substitution across TTL files.

  2. Joins cannot silently miss. When the UDI converter links a device to its
     clearance record, it calls the SAME k510() function the k510 converter used
     to mint that record. Same input, same IRI, guaranteed -- because there is
     only one function.

If you are tempted to build an IRI by string concatenation anywhere else in the
pipeline: don't. Add a function here instead.

THE T-BOX / A-BOX SPLIT
-----------------------
  NS  vocabulary  (T-Box): classes and properties        -> term()
  ID  instances   (A-Box): the actual devices, records    -> the mint_* functions

A deliberate cleanup from the old graph: the legacy layout scattered instances
across BOTH /ontology/<seg>/ AND /resource/ (device and report lived under
/resource/, everything else under /ontology/). That inconsistency is gone here --
EVERY instance is under ID. Every vocabulary term is under NS. No exceptions.

DETERMINISM
-----------
Same input always yields the same IRI. No timestamps, no counters, no randomness.
A rerun that produces a different IRI is a bug in this file.
"""

import re


# ===========================================================================
#  THE TWO LINES THAT DEFINE THE NAMESPACE.
#  To migrate namespaces: change these, regenerate, retag. Nothing else moves.
#
#  Legacy (do NOT use for new builds): http://medicaldevice.com/ontology/
#  and .../resource/ -- a third-party domain we do not own.
# ===========================================================================
NS = "https://bmedesign.org/medical-device-kg/ns/"   # vocabulary  (classes, properties)
ID = "https://bmedesign.org/medical-device-kg/id/"   # instances   (the things)


# ---------------------------------------------------------------------------
#  Slug convention. Must match the convention already in the graph, verified
#  against the live endpoint 2026-09-07:
#     "BRT"        -> "brt"            (product code)
#     "610.40"     -> "610_40"         (regulation number: dot -> underscore)
#     "1000.1"     -> "1000_1"         (CFR section)
#     "DEN000001"  -> "den000001"      (De Novo number)
#  Rule: lowercase, then every run of non-[a-z0-9] becomes a single underscore,
#  then strip leading/trailing underscores.
# ---------------------------------------------------------------------------
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# a token that is already safe to use verbatim in a path segment (e.g. a UUID)
_SAFE_VERBATIM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$")


def slug(value):
    """Lowercase URI-safe slug. Raises on a value that slugs to nothing, because
    an instance with no usable key cannot be given a stable IRI -- surfacing that
    is the point (it prevents silent 'unknown' orphan nodes)."""
    if value is None:
        raise ValueError("slug() received None")
    s = _NON_ALNUM.sub("_", str(value).strip().lower()).strip("_")
    if not s:
        raise ValueError(f"value {value!r} slugs to empty; cannot mint an IRI")
    return s


def _verbatim(value):
    """Use an already-safe opaque key (like a UUID) unchanged, so it round-trips
    exactly. Hyphens are preserved -- a UUID must NOT be slugged."""
    s = str(value).strip()
    if not _SAFE_VERBATIM.match(s):
        raise ValueError(f"key {value!r} is not URI-safe for verbatim use")
    return s


# ---------------------------------------------------------------------------
#  Core builders. Everything below is a thin, named wrapper over these two.
# ---------------------------------------------------------------------------
def term(name):
    """A vocabulary term (class or property) in the NS namespace.
        term('UDIDeviceRecord') -> <NS>UDIDeviceRecord
        term('hasProductCode')  -> <NS>hasProductCode
    Names are used as-is (they are our own controlled vocabulary), not slugged."""
    if not name or not _SAFE_VERBATIM.match(str(name)):
        raise ValueError(f"invalid vocabulary term name: {name!r}")
    return f"{NS}{name}"


def _instance(*segments):
    """Build an instance IRI under ID from already-prepared path segments."""
    return ID + "/".join(segments)


# ===========================================================================
#  INSTANCE MINTERS -- one per entity type. Each documents the join it anchors.
#  Grouped by FDA domain. The docstring example is the PATH the live graph uses;
#  the self-test at the bottom pins every one of these.
# ===========================================================================

# ---- Premarket review records --------------------------------------------
def product_code(code):
    """FDA product code -- THE hub. Nearly every query routes through it.
        product_code('BRT') -> .../id/product-code/brt"""
    return _instance("product-code", slug(code))


def k510(number):
    """510(k) or De Novo clearance record. UDI links to this via relatedK510Record.
        k510('K033394')   -> .../id/k510/k033394
        k510('DEN000001') -> .../id/k510/den000001"""
    return _instance("k510", slug(number))


def pma(number, supplement=None):
    """PMA approval, optionally a specific supplement (nested, matching the graph).
        pma('N10389')              -> .../id/pma/n10389
        pma('N10389', 'S001')      -> .../id/pma/n10389/supplement/s001"""
    if supplement:
        return _instance("pma", slug(number), "supplement", slug(supplement))
    return _instance("pma", slug(number))


def de_novo(number):
    """De Novo request record.  de_novo('DEN160001') -> .../id/de-novo/den160001"""
    return _instance("de-novo", slug(number))


def hde(number):
    """Humanitarian Device Exemption.  hde('H100001') -> .../id/hde/h100001"""
    return _instance("hde", slug(number))


# ---- Classification, regulation, standards -------------------------------
def classification(product_code_value):
    """Product classification record, keyed on its product code.
        classification('FRN') -> .../id/classification/frn"""
    return _instance("classification", slug(product_code_value))


def regulation(number):
    """21 CFR regulation number (dot -> underscore).
        regulation('880.5725') -> .../id/regulation/880_5725"""
    return _instance("regulation", slug(number))


def cfr_section(section):
    """A 21 CFR section.  cfr_section('1000.1') -> .../id/cfr-section/1000_1"""
    return _instance("cfr-section", slug(section))


def cfr_part(part):
    """A 21 CFR part.  cfr_part('880') -> .../id/cfr-part/880"""
    return _instance("cfr-part", slug(part))


def consensus_standard(number):
    """FDA-recognized consensus standard.
        consensus_standard('5-91') -> .../id/consensus-standard/5_91"""
    return _instance("consensus-standard", slug(number))


# ---- Postmarket safety ----------------------------------------------------
def tplc(product_code_value):
    """Total Product Life Cycle aggregate, keyed on product code.
        tplc('BRT') -> .../id/tplc/brt"""
    return _instance("tplc", slug(product_code_value))


def recall(number):
    """Recall record.  recall('94520') -> .../id/recall/94520"""
    return _instance("recall", slug(number))


def report(key):
    """A MAUDE adverse-event report (opaque report id, kept verbatim).
        report('MW5012345') -> .../id/report/MW5012345"""
    return _instance("report", _verbatim(key))


def postmarket_522(order_number):
    """Section 522 postmarket surveillance study.
        postmarket_522('PS120001') -> .../id/postmarket-522/ps120001"""
    return _instance("postmarket-522", slug(order_number))


# ---- Establishments & devices --------------------------------------------
def manufacturer(fei=None, name=None):
    """A manufacturer/establishment. Prefer the FEI number (stable); fall back to
    a name slug only when no FEI exists. NB: this key space is FEI/name-based and
    is DELIBERATELY separate from UDI's DUNS-based labeler() -- FDA publishes no
    FEI<->DUNS crosswalk, so the two must not be conflated.
        manufacturer(fei='3001234567') -> .../id/manufacturer/3001234567
        manufacturer(name='0625 LLC')  -> .../id/manufacturer/0625_llc"""
    if fei:
        return _instance("manufacturer", slug(fei))
    if name:
        return _instance("manufacturer", slug(name))
    raise ValueError("manufacturer() needs an fei or a name")


def establishment(registration_number):
    """A registered establishment/listing record.
        establishment('3001234567') -> .../id/establishment/3001234567"""
    return _instance("establishment", slug(registration_number))


def device(key):
    """A device record (opaque key kept verbatim).
        device('abc-123') -> .../id/device/abc-123"""
    return _instance("device", _verbatim(key))


def mammography_facility(fac_id):
    """A certified mammography facility.
        mammography_facility('FAC0001') -> .../id/mammography-facility/fac0001"""
    return _instance("mammography-facility", slug(fac_id))


def xray_assembler(assembler_id):
    """An X-ray assembler record.
        xray_assembler('XA0001') -> .../id/xray-assembler/xa0001"""
    return _instance("xray-assembler", slug(assembler_id))


def clia_analyte(analyte_id):
    """A CLIA-waived analyte record.
        clia_analyte('12345') -> .../id/clia-analyte/12345"""
    return _instance("clia-analyte", slug(analyte_id))


# ---- UDI / AccessGUDID (the physical-device layer) -----------------------
#  These MUST match udi_to_rdf.py exactly -- that converter should be refactored
#  to import these functions rather than mint its own strings.
def udi_device(record_key):
    """A UDI device record, keyed on public_device_record_key (a UUID -> verbatim).
        udi_device('248d4b67-35ce-438b-86f3-d399d065dcc9')
        -> .../id/udi/248d4b67-35ce-438b-86f3-d399d065dcc9"""
    return _instance("udi", _verbatim(record_key))


def udi_identifier(issuing_agency, identifier_id):
    """A UDI/DI barcode identifier under its issuing agency.
        udi_identifier('GS1', '00889842') -> .../id/udi-identifier/gs1/00889842"""
    return _instance("udi-identifier", slug(issuing_agency), slug(identifier_id))


def gmdn(code):
    """A GMDN term -- the ONE external controlled vocabulary in FDA device data,
    so its IRI must be stable to anchor future skos: links outward.
        gmdn('35304') -> .../id/gmdn/35304"""
    return _instance("gmdn", slug(code))


def labeler(duns):
    """A UDI labeler, keyed on DUNS. Separate from manufacturer() on purpose.
        labeler('123456789') -> .../id/labeler/123456789"""
    return _instance("labeler", slug(duns))


def storage_condition(device_record_key, index):
    """A storage-condition node hanging off a UDI device.
        storage_condition('abc-1', 0) -> .../id/udi/abc-1/storage/0"""
    return _instance("udi", _verbatim(device_record_key), "storage", str(int(index)))


# ===========================================================================
#  SELF-TEST -- pins every minter against the layout verified on the live
#  endpoint (2026-09-07). Run `python3 iri.py`; a failure means an IRI drifted
#  and joins would break. This is a regression guard, not decoration.
# ===========================================================================
def _selftest():
    def suffix(iri):
        # everything after the base, so the test is namespace-independent
        assert iri.startswith(ID), f"{iri} not under ID base"
        return iri[len(ID):]

    cases = {
        product_code("BRT"):                 "product-code/brt",
        k510("K033394"):                     "k510/k033394",
        k510("DEN000001"):                   "k510/den000001",
        pma("N10389"):                       "pma/n10389",
        pma("N10389", "S001"):               "pma/n10389/supplement/s001",
        de_novo("DEN160001"):                "de-novo/den160001",
        hde("H100001"):                      "hde/h100001",
        classification("FRN"):               "classification/frn",
        regulation("880.5725"):              "regulation/880_5725",
        cfr_section("1000.1"):               "cfr-section/1000_1",
        cfr_part("880"):                     "cfr-part/880",
        consensus_standard("5-91"):          "consensus-standard/5_91",
        tplc("BRT"):                         "tplc/brt",
        recall("94520"):                     "recall/94520",
        report("MW5012345"):                 "report/MW5012345",
        postmarket_522("PS120001"):          "postmarket-522/ps120001",
        manufacturer(fei="3001234567"):      "manufacturer/3001234567",
        manufacturer(name="0625 LLC"):       "manufacturer/0625_llc",
        establishment("3001234567"):         "establishment/3001234567",
        device("abc-123"):                   "device/abc-123",
        mammography_facility("FAC0001"):     "mammography-facility/fac0001",
        xray_assembler("XA0001"):            "xray-assembler/xa0001",
        clia_analyte("12345"):               "clia-analyte/12345",
        udi_device("248d4b67-35ce-438b-86f3-d399d065dcc9"):
                                             "udi/248d4b67-35ce-438b-86f3-d399d065dcc9",
        udi_identifier("GS1", "00889842"):   "udi-identifier/gs1/00889842",
        gmdn("35304"):                       "gmdn/35304",
        labeler("123456789"):                "labeler/123456789",
        storage_condition("abc-1", 0):       "udi/abc-1/storage/0",
    }
    for got, want in cases.items():
        assert suffix(got) == want, f"MINT DRIFT: got {suffix(got)!r}, want {want!r}"

    # vocabulary terms live under NS, not ID
    assert term("UDIDeviceRecord") == f"{NS}UDIDeviceRecord"
    assert term("hasProductCode") == f"{NS}hasProductCode"

    # determinism: same input -> same IRI, always
    assert product_code("BRT") == product_code("brt") == product_code(" BRT ")

    # empties are refused, not silently turned into orphan 'unknown' nodes
    for bad in ("", "   ", None, "!!!"):
        try:
            product_code(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"product_code({bad!r}) should have raised")

    print(f"iri.py self-test PASSED ({len(cases)} minters).")
    print(f"  NS = {NS}")
    print(f"  ID = {ID}")


if __name__ == "__main__":
    _selftest()
