#!/usr/bin/env python3
"""
asr.py -- MAUDE Alternative Summary Reports, 1999-2019 (CDRH MDR data files) -> RDF.

FDA ran the ASR program from 1997 to June 2019. Reports received under it are NOT
in MAUDE or openFDA; FDA publishes them only as yearly CSV zips on the MDR data
files page (checked October 12, 2026):
    https://www.accessdata.fda.gov/MAUDE/ftparea/ASR_<1999..2019>.zip   (6,066,980 rows)
    https://www.accessdata.fda.gov/MAUDE/ftparea/ASR_PPC.zip            (patient problem codes)
    https://www.accessdata.fda.gov/MAUDE/ftparea/ASR_DPC_terms.csv      (device problem code -> term)
    https://www.accessdata.fda.gov/MAUDE/ftparea/asr_ppc_terms.csv      (patient problem code -> term)
Header (verified October 12, 2026):
    exemptn_no,mfr_no,mfr_name,report_id,date_of_event,mfr_aware_date,event_type,
    dev_prob_cd,report_year,report_qtr,initial_report_flag,dev_id,product_code,
    brand_name,model_no,catalog_no,impl_avail_for_eval,impl_ret_to_mfr

Modeling
  * One ex:AlternativeSummaryReport per CSV row, keyed iri.asr_report(file year, line):
    rows carry no unique key (373 rows repeat exactly) and the files are frozen.
  * ASR rows are summary rows, NOT individual events. Supplements (flag S) repeat an
    initial report; FDA warns that adding ASR and companion MAUDE reports double-counts.
    Both facts are stated on every row (ex:initialOrSupplement) and in the ontology.
  * Patient problem codes come from ASR_PPC, joined on FDA's stated 5-column key
    (exemption, report id, product code, year, quarter); duplicate keys take the union.
  * Codes are kept (ex:deviceProblemCode / ex:patientProblemCode) next to FDA's terms
    (ex:deviceProblem / ex:patientProblem), the same predicates MAUDE uses.
  * To keep the 6 M-row file within the VM's disk, per-row provenance is the dataset
    and record id only; download URL and date sit once on the dataset node
    (ex:AlternativeSummaryReportDataset) that every row links to via ex:inDataset.
No patient identifiers exist in these files; nothing is removed.
"""
import os
import sys
import csv
import io
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
from identity import normalize_org   # noqa: E402

NAME = "asr"
DATASET = "cdrh/mdr-asr"
FTP = "https://www.accessdata.fda.gov/MAUDE/ftparea/"
YEARS = list(range(1999, 2020))
SOURCE_URL = "https://www.fda.gov/medical-devices/medical-device-reporting-mdr-how-report-medical-device-problems/mdr-data-files"
EXPECTED = ["exemptn_no", "mfr_no", "mfr_name", "report_id", "event_type", "dev_prob_cd",
            "report_year", "report_qtr", "initial_report_flag", "product_code"]
LONG_HEADERS = {  # ASR_2019.csv header (verified October 13, 2026) -> the short names of the other years
    "exemption number": "exemptn_no", "manufacturer registration number": "mfr_no",
    "manufacturer name": "mfr_name", "report id": "report_id", "date of event": "date_of_event",
    "manufacturer aware date": "mfr_aware_date", "event type": "event_type",
    "device problem codes": "dev_prob_cd", "report year": "report_year", "report quarter": "report_qtr",
    "initial report flag": "initial_report_flag", "device id": "dev_id", "product code": "product_code",
    "brand name": "brand_name", "model number": "model_no", "catalog number": "catalog_no",
    "implant available for evaluation": "impl_avail_for_eval",
    "implant returned to manufacturer": "impl_ret_to_mfr",
}
EVENT_TYPES = {"M": "Malfunction", "M-D": "Malfunction (patient death reported)",
               "IN": "Serious Injury", "IN-D": "Serious Injury (patient death reported)",
               "D": "Death", "O": "Other", "*": "No Information"}
FLAGS = {"I": "Initial", "S": "Supplement"}
YESNO = {"1": "Yes", "2": "No", "7": "No Information"}
DATASET_NODE = iri.ID + "dataset/cdrh-mdr-asr"


def fetch(dest):
    files = [base.bulk_download(f"{FTP}ASR_{y}.zip", dest) for y in YEARS]
    for n in ("ASR_PPC.zip", "ASR_DPC_terms.csv", "asr_ppc_terms.csv"):
        files.append(base.bulk_download(FTP + n, dest))
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def _terms(path):
    """code -> term from FDA's terms CSV (CODE,CODE_VALUE; a BOM may lead the file)."""
    out = {}
    p = Path(path)
    if not p.exists():
        return out
    with open(p, encoding="utf-8-sig", errors="replace", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[0].strip() and row[0].strip().upper() != "CODE":
                out[row[0].strip()] = row[1].strip()
    return out


def _codes(value):
    return [c.strip() for c in str(value or "").replace(",", ";").split(";") if c.strip()]


def _ppc_by_year(src):
    """{report_year: {5-key: [codes]}} from ASR_PPC.zip, built one year at a time on
    demand to keep memory to one year's keys (~0.5 M)."""
    z = Path(src) / "ASR_PPC.zip"
    if not z.exists():
        return lambda year: {}

    def load(year):
        got = {}
        with zipfile.ZipFile(z) as f:
            for name in f.namelist():
                with f.open(name) as fh:
                    r = csv.reader(io.TextIOWrapper(fh, encoding="latin-1", newline=""))
                    next(r, None)
                    for row in r:
                        if len(row) < 6 or row[3].strip() != str(year):
                            continue
                        k = tuple(c.strip() for c in row[:5])
                        got.setdefault(k, [])
                        for c in _codes(row[5]):
                            if c not in got[k]:
                                got[k].append(c)
        return got
    return load


def _rows(zpath):
    with zipfile.ZipFile(zpath) as f:
        for name in sorted(f.namelist()):
            if not name.lower().endswith(".csv"):
                continue
            with f.open(name) as fh:
                rdr = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1", newline=""))
                # ASR_2019 spells the header out ("Exemption Number", ...); map it to the short names
                hdr = [LONG_HEADERS.get(h.strip().lower(), h.strip().lower()) for h in (rdr.fieldnames or [])]
                missing = [c for c in EXPECTED if c not in hdr]
                if missing:
                    raise ValueError(f"{zpath.name}: ASR header changed; missing {missing}")
                rdr.fieldnames = hdr
                for line, r in enumerate(rdr, 1):
                    yield line, {k: (v or "").strip() for k, v in r.items() if k}


def record_pairs(r, file_year, line, dpc, ppc_terms, ppc_codes):
    pc = common.product_code(r.get("product_code"))
    et = r.get("event_type", "")
    nf = normalize_org(r.get("mfr_name"))
    dev_codes = _codes(r.get("dev_prob_cd"))
    key5 = (r.get("exemptn_no", ""), r.get("report_id", ""), r.get("product_code", ""),
            r.get("report_year", ""), r.get("report_qtr", ""))
    pat_codes = ppc_codes.get(key5, [])
    label = common.label(f"ASR {r.get('report_id')}" if r.get("report_id") else f"ASR row {file_year}-{line}",
                         EVENT_TYPES.get(et, et) or None, r.get("brand_name") or None, sep=" | ")
    pairs = [
        ("a", "ex:AlternativeSummaryReport"),
        ("rdfs:label", label),
        ("ex:inDataset", f"<{DATASET_NODE}>"),
        ("ex:exemptionNumber", common.lit(r.get("exemptn_no"))),
        ("ex:manufacturerRegistrationNumber", common.lit(r.get("mfr_no"))),
        ("ex:manufacturerName", common.lit(common.norm_text(r.get("mfr_name")))),
        ("ex:reportId", common.lit(r.get("report_id"))),
        ("ex:dateOfEvent", common.date_lit(r.get("date_of_event"))),
        ("ex:dateManufacturerReceived", common.date_lit(r.get("mfr_aware_date"))),
        ("ex:eventType", common.lit(EVENT_TYPES.get(et, et))),
        ("ex:reportYear", common.gyear_lit(r.get("report_year"))),
        ("ex:reportQuarter", common.int_lit(r.get("report_qtr"))),
        ("ex:initialOrSupplement", common.lit(FLAGS.get(r.get("initial_report_flag", ""), r.get("initial_report_flag")))),
        ("ex:deviceId", common.lit(r.get("dev_id"))),
        ("ex:productCode", common.lit(pc)),
        ("ex:reportedProductCodeText", common.lit(r.get("product_code")) if r.get("product_code") and not pc else None),
        ("ex:brandName", common.lit(common.norm_text(r.get("brand_name")))),
        ("ex:modelNumber", common.lit(r.get("model_no"))),
        ("ex:catalogNumber", common.lit(r.get("catalog_no"))),
        ("ex:implantAvailableForEvaluation", common.lit(YESNO.get(r.get("impl_avail_for_eval", ""), r.get("impl_avail_for_eval")))),
        ("ex:implantReturnedToManufacturer", common.lit(YESNO.get(r.get("impl_ret_to_mfr", ""), r.get("impl_ret_to_mfr")))),
    ]
    if r.get("exemptn_no"):
        pairs.append(("ex:hasExemption", f"<{iri.asr_exemption(r['exemptn_no'])}>"))
    if pc:
        pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
    if nf:
        pairs.append(("ex:hasManufacturerFirm", f"<{iri.firm_name(nf)}>"))
    for c in dev_codes:
        pairs.append(("ex:deviceProblemCode", common.lit(c)))
        if c in dpc:
            pairs.append(("ex:deviceProblem", common.lit(dpc[c])))
    for c in pat_codes:
        pairs.append(("ex:patientProblemCode", common.lit(c)))
        if c in ppc_terms:
            pairs.append(("ex:patientProblem", common.lit(ppc_terms[c])))
    pairs += common.provenance(DATASET, f"ASR_{file_year}.csv line {line}")
    seen, uniq = set(), []
    for p in pairs:
        if p[1] is not None and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq, pc, nf, r.get("mfr_name"), r.get("exemptn_no")


def transform(src, out, retrieved_at=None):
    src = Path(src)
    man = src / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    dpc = _terms(src / "ASR_DPC_terms.csv")
    ppc_terms = _terms(src / "asr_ppc_terms.csv")
    ppc_for = _ppc_by_year(src)
    st = base.Stats(source=NAME)
    hub, firms, exemptions = set(), {}, set()
    with base.TurtleWriter(out) as w:
        w.block(DATASET_NODE, [
            ("a", "ex:SourceDataset"),
            ("rdfs:label", common.lit("FDA MDR Alternative Summary Reports, 1999-2019")),
            ("ex:sourceUrl", common.lit(SOURCE_URL)),
            ("ex:retrievedAt", common.date_lit(retrieved_at)),
            ("ex:usageNote", common.lit(
                "Summary reporting rows, not individual events. Supplements repeat initial reports, "
                "and FDA notes that adding ASR rows to the companion MAUDE reports double-counts events. "
                "Not usable as event rates.")),
        ])
        for y in YEARS:
            z = src / f"ASR_{y}.zip"
            if not z.exists():
                continue
            ppc_codes = ppc_for(y)
            n0 = st.records
            for line, r in _rows(z):
                if not any(r.values()):
                    st.skipped += 1
                    continue
                pairs, pc, nf, raw, ex_no = record_pairs(r, y, line, dpc, ppc_terms, ppc_codes)
                w.block(iri.asr_report(y, line), pairs)
                st.records += 1
                if pc:
                    hub.add(pc)
                if nf:
                    firms.setdefault(nf, raw)
                if ex_no:
                    exemptions.add(ex_no)
            print(f"  ASR {y}: {st.records - n0:,} rows")
        for e in sorted(exemptions):
            w.block(iri.asr_exemption(e), [("a", "ex:ASRExemption"),
                                          ("rdfs:label", common.lit(f"ASR exemption {e}")),
                                          ("ex:exemptionNumber", common.lit(e))])
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hub):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    hdr = ("exemptn_no,mfr_no,mfr_name,report_id,date_of_event,mfr_aware_date,event_type,dev_prob_cd,"
           "report_year,report_qtr,initial_report_flag,dev_id,product_code,brand_name,model_no,catalog_no,"
           "impl_avail_for_eval,impl_ret_to_mfr")
    rows = [  # first two data lines verbatim from ASR_2010.zip, October 12, 2026
        "1999011,2183157,MALLINCKRODT INC. TYCO HEALTHCARE,218315710100,,02/08/2010,M,1142,2010,1,I,LP10,CBK,VOLUME VENTILATOR,,,,",
        '2001013,2242630,"IMPACT INSTRUMENTATION, INC.",224263010001,,02/04/2010,M,2379,2010,1,I,754,CBK,IMPACT,754,,,',
        "1997033,1022353,X,R9,,,IN,1142;2379,2010,4,S,CD1,L W,,,,1,2",
    ]
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        with zipfile.ZipFile(d / "ASR_2010.zip", "w") as z:
            z.writestr("ASR_2010.csv", "\n".join([hdr] + rows) + "\n")
        with zipfile.ZipFile(d / "ASR_PPC.zip", "w") as z:
            z.writestr("ASR_PPC.csv", '"EXEMPTN_NO","REPORT_ID","PRODUCT_CODE","REPORT_YEAR","REPORT_QTR","PATIENT_PROB_CD"\n'
                                      '"1999011","218315710100","CBK","2010","1","1930;2013"\n')
        (d / "ASR_DPC_terms.csv").write_text("﻿CODE,CODE_VALUE\n1142,Device Inoperable\n", encoding="utf-8")
        (d / "asr_ppc_terms.csv").write_text("CODE,CODE_VALUE\n1930,No Known Impact Or Consequence To Patient\n", encoding="utf-8")
        out = d / "asr.ttl"
        st = transform(d, out, retrieved_at="2026-10-12")
        ttl = out.read_text(encoding="utf-8")
        assert st.records == 3, st
        assert "<https://bmedesign.org/medical-device-kg/id/asr-report/2010/1>" in ttl
        assert 'ex:deviceProblem "Device Inoperable"' in ttl
        assert 'ex:patientProblemCode "2013"' in ttl and 'ex:patientProblem "No Known Impact Or Consequence To Patient"' in ttl
        assert 'ex:eventType "Serious Injury"' in ttl and 'ex:initialOrSupplement "Supplement"' in ttl
        assert 'ex:reportedProductCodeText "L W"' in ttl            # junk product code kept as text, no hub
        assert "product-code/lw" not in ttl
        assert 'ex:dateManufacturerReceived "2010-02-08"^^xsd:date' in ttl
        assert ttl.count("a ex:ProductCode") == 1
        assert 'ex:implantAvailableForEvaluation "Yes"' in ttl
        # ASR_2019 long header (first data line verbatim)
        long_hdr = ("Exemption Number,Manufacturer Registration Number,Manufacturer Name,Report ID,Date of Event,"
                    "Manufacturer Aware Date,Event Type,Device Problem Codes,Report Year,Report Quarter,Initial Report Flag,"
                    "Device ID,Product Code,Brand Name,Model Number,Catalog Number,Implant Available for Evaluation,"
                    "Implant Returned to Manufacturer")
        with zipfile.ZipFile(d / "ASR_2019.zip", "w") as z:
            z.writestr("ASR_2019.csv", long_hdr + "\r\n2007014,9617229,ALLERGAN,200517.S2,2/16/2007,2/21/2007,IN,"
                       "2616;1546,2019,1,S,45,FTR,Natrelle Silicone-Filled Breast Implants,45,45-550,2,2\r\n")
        st = transform(d, out, retrieved_at="2026-10-12")
        ttl = out.read_text(encoding="utf-8")
        assert st.records == 4 and "/id/asr-report/2019/1>" in ttl and 'ex:dateOfEvent "2007-02-16"^^xsd:date' in ttl
    print("asr.py self-test PASSED")


if __name__ == "__main__":
    _selftest()
