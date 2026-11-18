#!/usr/bin/env python3
"""
legacy_mdr.py -- the MDR database, 1984-1997 (CDRH Device Experience Network) -> RDF.

FDA's "MDR (Medical Device Reporting)" database (search.CFM, no longer updated) holds
the reports filed before MAUDE. FDA publishes them as pipe-delimited yearly files
(checked October 12, 2026; 620,179 rows):
    https://www.accessdata.fda.gov/MAUDE/ftparea/mdr84.zip ... mdr97.zip
    https://www.accessdata.fda.gov/MAUDE/ftparea/disclaim.zip   (manufacturer disclaimers)
Fields (FDA's order, no header row):
    Access Type and Number|Date Received|Product Description|Manufacturer name code|
    Manufacturer Name|Street address|City|State|Zipcode|Report type|Model number|
    Catalog number|FDA panel|FDA Product code|Event description type|Event description|Closeout text

Modeling
  * One ex:LegacyMDRReport per row. 29,070 access numbers occur more than once (most
    are a PRELIMINARY and a FINAL report); later rows take an ordinal suffix in file
    order (iri.legacy_mdr). Files are frozen, so the order is stable.
  * 19 lines hold two records run together (33 fields); they are split back into two.
  * Two-digit years (11/15/90) are all 1984-1997 and are read as 19yy.
  * The event description and closeout text are narratives. Like MAUDE, they go to a
    separate file (legacy_mdr_text.ttl) after scrub.scrub_text, and are not counted in
    the structured total. Street address is the manufacturer's, not a patient's.
"""
import os
import re
import sys
import zipfile
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
import scrub      # noqa: E402
from identity import normalize_org   # noqa: E402

NAME = "legacy_mdr"
DATASET = "cdrh/mdr-den"
FTP = "https://www.accessdata.fda.gov/MAUDE/ftparea/"
YEARS = ["84", "85", "86", "87", "88", "89", "90", "91", "92", "93", "94", "95", "96", "97"]
SOURCE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfmdr/search.CFM"
FIELDS = ["access", "date_received", "product_description", "mfr_code", "mfr_name", "street",
          "city", "state", "zip", "report_type", "model", "catalog", "panel", "product_code",
          "description_type", "event_description", "closeout_text"]
_DATE2 = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2})$")
_ACCESS_TAIL = re.compile(r"([MVR]\d{5,})$")


def fetch(dest):
    files = [base.bulk_download(f"{FTP}mdr{y}.zip", dest) for y in YEARS]
    files.append(base.bulk_download(FTP + "disclaim.zip", dest))
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def date2(value):
    """MM/DD/YY (all 1984-1997) -> xsd:date; anything else via common.date_lit."""
    s = str(value or "").strip()
    m = _DATE2.match(s)
    if m:
        mo, d, yy = m.groups()
        return common.date_lit(f"{int(mo):02d}/{int(d):02d}/19{yy}")
    return common.date_lit(s)


def split_line(line):
    """One file line -> one or two 17-field records (19 lines carry two records)."""
    p = line.rstrip("\r\n").split("|")
    if len(p) == 17:
        return [p]
    if len(p) == 33:
        m = _ACCESS_TAIL.search(p[16])
        if m:
            first = p[:16] + [p[16][:m.start()]]
            second = [m.group(1)] + p[17:33]
            return [first, second]
    if len(p) > 17:                      # keep the record, fold any extra fields into the closeout text
        return [p[:16] + ["|".join(p[16:])]]
    return []


def _rows(src):
    for y in YEARS:
        z = Path(src) / f"mdr{y}.zip"
        if not z.exists():
            continue
        with zipfile.ZipFile(z) as f:
            for name in sorted(f.namelist()):
                with f.open(name) as fh:
                    for raw in fh:
                        for rec in split_line(raw.decode("latin-1")):
                            yield y, dict(zip(FIELDS, (v.strip() for v in rec)))


def _disclaimers(src):
    """mfr name code -> (name, disclaimer text) from disclaim.zip (fixed-width: code 16, name 50, text)."""
    out = {}
    z = Path(src) / "disclaim.zip"
    if not z.exists():
        return out
    with zipfile.ZipFile(z) as f:
        for name in f.namelist():
            for line in f.read(name).decode("latin-1").splitlines():
                code, nm, text = line[:16].strip(), line[16:66].strip(), line[66:].strip()
                if code:
                    out[code] = (nm, text)
    return out


def transform(src, out, retrieved_at=None):
    src, out = Path(src), Path(out)
    man = src / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    st = base.Stats(source=NAME)
    seen, hub, firms, codes = {}, set(), {}, {}
    disc = _disclaimers(src)
    text_out = out.with_name(f"{out.stem}_text.ttl")
    texts = 0
    with base.TurtleWriter(out) as w, base.TurtleWriter(text_out) as wt:
        for y, r in _rows(src):
            acc = r.get("access")
            if not acc or not any(ch.isalnum() for ch in acc):
                st.skipped += 1
                continue
            seen[acc] = seen.get(acc, 0) + 1
            subj = iri.legacy_mdr(acc, seen[acc])
            pc = common.product_code(r.get("product_code"))
            nf = normalize_org(r.get("mfr_name"))
            pairs = [
                ("a", "ex:LegacyMDRReport"),
                ("rdfs:label", common.label(f"MDR {acc}", r.get("report_type"), r.get("product_description"), sep=" | ")),
                ("ex:accessNumber", common.lit(acc)),
                ("ex:reportSourceCode", common.lit({"M": "Mandatory", "V": "Voluntary", "R": "R"}.get(acc[:1].upper()))),
                ("ex:dateReceived", date2(r.get("date_received"))),
                ("ex:deviceName", common.lit(common.norm_text(r.get("product_description")))),
                ("ex:manufacturerNameCode", common.lit(r.get("mfr_code"))),
                ("ex:manufacturerName", common.lit(common.norm_text(r.get("mfr_name")))),
                ("ex:address", common.lit(common.norm_address(r.get("street")))),
                ("ex:city", common.lit(r.get("city"))),
                ("ex:state", common.lit(r.get("state"))),
                ("ex:postalCode", common.lit(r.get("zip"))),
                ("ex:eventType", common.lit((r.get("report_type") or "").title() or None)),
                ("ex:modelNumber", common.lit(r.get("model"))),
                ("ex:catalogNumber", common.lit(r.get("catalog"))),
                ("ex:advisoryCommittee", common.lit((r.get("panel") or "").title() or None)),
                ("ex:productCode", common.lit(pc)),
                ("ex:reportedProductCodeText", common.lit(r.get("product_code")) if r.get("product_code") and not pc else None),
                ("ex:reportStage", common.lit((r.get("description_type") or "").title() or None)),
                ("ex:fileYear", common.gyear_lit(f"19{y}")),
            ]
            if seen[acc] > 1:
                pairs.append(("ex:sameAccessNumberAs", f"<{iri.legacy_mdr(acc, 1)}>"))
            if pc:
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                hub.add(pc)
            if nf:
                pairs.append(("ex:hasManufacturerFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, r.get("mfr_name"))
            if r.get("mfr_code"):
                pairs.append(("ex:hasLegacyManufacturer", f"<{iri.legacy_mdr_manufacturer(r['mfr_code'])}>"))
                codes.setdefault(r["mfr_code"], r.get("mfr_name"))
            pairs += common.provenance(DATASET, f"mdr{y}.txt {acc}" + (f" #{seen[acc]}" if seen[acc] > 1 else ""),
                                       source_url=SOURCE_URL, retrieved_at=retrieved_at)
            w.block(subj, pairs)
            st.records += 1
            tp = []
            for pred, key in (("ex:eventDescription", "event_description"), ("ex:closeoutText", "closeout_text")):
                body = str(r.get(key) or "").strip()
                if body:
                    body, _ = scrub.scrub_text(body)
                    tp.append((pred, common.lit(body)))
            if tp:
                wt.block(subj, tp)
        for code in sorted(codes):
            nm, text = disc.get(code, (None, None))
            w.block(iri.legacy_mdr_manufacturer(code), [
                ("a", "ex:LegacyMDRManufacturer"),
                ("rdfs:label", common.lit(common.norm_text(codes[code]) or nm or code)),
                ("ex:manufacturerNameCode", common.lit(code)),
                ("ex:manufacturerDisclaimer", common.lit(common.norm_text(text))),
            ])
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hub):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    texts = wt.triples
    print(f"  narratives: {texts:,} triples -> {text_out.name} (not counted in the structured total)")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    rows = [  # verbatim from mdr90.zip, October 12, 2026 (closeout shortened)
        "M177985|11/15/90|SWAN GANZ THERMODILUTION CATHETER|EDWACVS|EDWARDS CRITICAL CARE DIV. BAXTER HEALTHCARE CORP."
        "|17221 RED HILL AVE.|IRVINE|CA|92714|SERIOUS INJURY|93A-131-7F|93A-131-7F|CARDIOVASCULAR|DYG|FINAL|"
        "CO HAS RECEIVED REPORT THAT A CATHETER SEPARATED DURING REMOVAL. CALL 555-123-4567.|CAUSE UNK.",
        "M177985|11/16/90|SWAN GANZ THERMODILUTION CATHETER|EDWACVS|EDWARDS CRITICAL CARE|||||SERIOUS INJURY|||"
        "CARDIOVASCULAR|DYG|PRELIMINARY|SECOND ROW.|",
        # two records run together on one line (33 fields)
        "V100001|01/02/90|PUMP|ABCDEFGH|ACME|||||MALFUNCTION|||GENERAL HOSPITAL|FRN|FINAL|TEXT ONE|CLOSE ONEV100002"
        "|01/03/90|PUMP|ABCDEFGH|ACME|||||DEATH|||GENERAL HOSPITAL|F R|FINAL|TEXT TWO|CLOSE TWO",
    ]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        with zipfile.ZipFile(d / "mdr90.zip", "w") as z:
            z.writestr("mdr90.txt", "\n".join(rows) + "\n")
        with zipfile.ZipFile(d / "disclaim.zip", "w") as z:
            z.writestr("disclaim.txt", "EDWACVS".ljust(16) + "EDWARDS".ljust(50) + "THE FILING OF THIS REPORT IS NOT AN ADMISSION.\n")
        out = d / "legacy_mdr.ttl"
        st = transform(d, out, retrieved_at="2026-10-12")
        ttl = out.read_text(encoding="utf-8")
        txt = (d / "legacy_mdr_text.ttl").read_text(encoding="utf-8")
        assert st.records == 4, st
        assert "/id/legacy-mdr/m177985>" in ttl and "/id/legacy-mdr/m177985_2>" in ttl
        assert 'ex:dateReceived "1990-11-15"^^xsd:date' in ttl
        assert "/id/legacy-mdr/v100002>" in ttl and 'ex:eventType "Death"' in ttl
        assert 'ex:reportedProductCodeText "F R"' in ttl
        assert "THE FILING OF THIS REPORT IS NOT AN ADMISSION." in ttl
        assert "CATHETER SEPARATED" not in ttl and "CATHETER SEPARATED" in txt     # narratives kept apart
        assert "555-123-4567" not in txt                                           # scrubbed
        assert 'ex:reportStage "Preliminary"' in ttl
    print("legacy_mdr.py self-test PASSED")


if __name__ == "__main__":
    _selftest()
