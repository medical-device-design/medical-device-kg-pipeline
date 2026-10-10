#!/usr/bin/env python3
"""
standards.py -- FDA Recognized Consensus Standards -> RDF (closes defect D10).

The live graph holds 49 RecognizedConsensusStandard nodes. FDA recognizes ~1,700.
Two FDA sources, used together:

  1. BULK  https://www.accessdata.fda.gov/premarket/ftparea/standards.zip
     -> standards.txt, pipe-delimited, NO header, multi-line text fields.
     1,738 records on 2026-10-09. Field positions below were verified against
     the FDA detail page for record 36768 (ISO 81060-1, Rec# 3-96).
  2. DETAIL PAGE per standard (cfStandards/detail.cfm?standard__identification_no=)
     -> the regulation numbers, device class and PRODUCT CODES FDA lists for the
     standard, the SDO, and the Specialty Task Group. The flat file does not
     carry these, and the product-code link is the whole point (it is the only
     public bridge from a regulatory record to an engineering requirement).
     Fetched politely (1 request/second), cached on disk, so a monthly re-run
     only fetches standards it has not seen.

Not ingested, on purpose: the standards' own text (SDO copyright) and FDA staff
contact names/e-mails (not needed; personal data).
FDA says the listed product codes "are provided as examples and others may be
applicable" -- so ex:listsProductCode means "FDA lists", not "the only codes".
"""
import os
import re
import sys
import json
import time
import zipfile
import urllib.request
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402

NAME = "standards"
DATASET = "cdrh/standards"
BULK_URL = "https://www.accessdata.fda.gov/premarket/ftparea/standards.zip"
DETAIL_URL = ("https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfStandards/"
              "detail.cfm?standard__identification_no={}")
NFIELDS = 32
TAIL = 23              # fields 9..31 are stable; extra '|' only ever lands in field 8
# Verified field positions (standards.txt, 2026-10-09)
F_ID, F_REC, F_LIST, F_TITLE, F_DESIG = 0, 1, 2, 3, 4
F_GUIDANCE = 8
F_ENTRY_DATE = 13          # "March 16, 2012" = FR "Date of Entry"
F_TRANSITION_END = 16      # "2027/12/19 00:00:00.000" = end of transition period
F_PARTIAL_TEXT = 17        # present exactly when extent = P (149 of 149)
F_RATIONALE = 18
F_EXTENT = 19              # C = complete standard, P = partial
F_SCOPE = 20
F_TRANSITION_NOTE = 23
F_ADOPT_DESIG, F_ADOPT_TITLE, F_ADOPT_SDO = 26, 27, 30
_REC_SPLIT = re.compile(r"\r?\n(?=\d{3,7}\|)")
_TAGS = re.compile(r"<[^>]+>")
_MONTH_DATE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})$")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


# ---------------------------------------------------------------------------
def parse_flatfile(text):
    """Yield one list of exactly NFIELDS strings per record. Records whose free
    text contains '|' are repaired by anchoring the stable tail; anything that
    still cannot be aligned is yielded as None so the caller counts it skipped."""
    for chunk in _REC_SPLIT.split(text.strip()):
        f = chunk.split("|")
        if len(f) == NFIELDS:
            yield f
        elif len(f) > NFIELDS:
            yield f[:F_GUIDANCE] + ["|".join(f[F_GUIDANCE:len(f) - TAIL])] + f[len(f) - TAIL:]
        else:
            yield None


def clean(text):
    """Strip HTML and collapse whitespace (FDA stores <br>, <i> in text fields)."""
    if not text:
        return ""
    t = _TAGS.sub(" ", text.replace("<br>", "\n"))
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n", t)).strip()


def month_date(s):
    """'March 16, 2012' -> '2012-03-16' (or None)."""
    m = _MONTH_DATE.match((s or "").strip())
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"


_ROW = re.compile(r"<tr>(.*?)</tr>", re.S | re.I)
_REG = re.compile(r"section-([0-9]+\.[0-9]+)")
_PC = re.compile(r"classification\.cfm\?ID=([A-Z]{3})")
_CLASS = re.compile(r"Class\s+([123U]|Unclassified)", re.I)
_SDO = re.compile(r"Standards Development Organization</span>.*?<span[^>]*>\s*([^<]+?)\s*</span>"
                  r"\s*</td>\s*<td[^>]*>\s*<span[^>]*>\s*([^<]+?)\s*</span>", re.S)
_STG = re.compile(r"Specialty Task Group \(STG\)</span>.*?<span[^>]*>\s*([^<]+?)\s*</span>", re.S)


def parse_detail(html):
    """-> {'links': [(regulation, device_name, device_class, product_code)], 'sdo': (abbr, name), 'stg': str}"""
    out = {"links": [], "sdo": None, "stg": None}
    sec = html.split("Procode(s)", 1)
    if len(sec) == 2:
        body = sec[1].split("</table>", 1)[0]
        for row in _ROW.findall(body):
            pc = _PC.search(row)
            if not pc:
                continue
            reg = _REG.search(row)
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            name = clean(cells[1]) if len(cells) > 1 else ""
            cls = _CLASS.search(_TAGS.sub(" ", row))
            out["links"].append((reg.group(1) if reg else None, name,
                                 cls.group(1) if cls else None, pc.group(1)))
    m = _SDO.search(html)
    if m:
        out["sdo"] = (m.group(1).strip(), m.group(2).strip())
    m = _STG.search(html)
    if m:
        out["stg"] = m.group(1).strip()
    return out


# ---------------------------------------------------------------------------
def fetch(dest, with_details=True, delay=1.0, limit=None):
    """Bulk file always; detail pages for product codes unless with_details=False.
    Detail pages are cached in dest/detail/<id>.html and never re-fetched."""
    dest = Path(dest)
    f = base.bulk_download(BULK_URL, dest)
    files = [f]
    if with_details:
        text = zipfile.ZipFile(dest / f.path).read("standards.txt").decode("latin-1")
        ids = [r[F_ID].strip() for r in parse_flatfile(text) if r and r[F_ID].strip()]
        ddir = dest / "detail"
        ddir.mkdir(parents=True, exist_ok=True)
        fetched = 0
        for sid in ids[:limit] if limit else ids:
            p = ddir / f"{sid}.html"
            if p.exists() and p.stat().st_size > 1000:
                continue
            req = urllib.request.Request(DETAIL_URL.format(sid),
                                         headers={"User-Agent": "medical-device-kg/rebuild (Hofstra University)"})
            try:
                p.write_bytes(urllib.request.urlopen(req, timeout=60).read())
                fetched += 1
            except Exception as e:                        # one bad page must not stop the run
                (ddir / f"{sid}.error").write_text(str(e), encoding="utf-8")
            time.sleep(delay)
        (dest / "detail_fetch.json").write_text(json.dumps({"fetched_now": fetched, "ids": len(ids)}),
                                                encoding="utf-8")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)


def transform(src, out, retrieved_at=None):
    src = Path(src)
    man = src / "manifest.json"
    if retrieved_at is None and man.exists():
        try:
            retrieved_at = base.Manifest.read(man).retrieved_at
        except Exception:
            retrieved_at = None
    zp = next(iter(sorted(src.glob("*.zip"))), None)
    text = (zipfile.ZipFile(zp).read("standards.txt").decode("latin-1") if zp
            else (src / "standards.txt").read_text(encoding="latin-1"))
    st = base.Stats(source=NAME)
    regs, pcs = {}, set()
    with base.TurtleWriter(out) as w:
        rows = [r for r in parse_flatfile(text)]
        st.skipped += sum(1 for r in rows if r is None)
        rows = sorted((r for r in rows if r and r[F_ID].strip()), key=lambda r: int(r[F_ID]))
        for r in rows:
            sid, rec = r[F_ID].strip(), r[F_REC].strip()
            # FR list "NR" / extent "N" = a standard FDA reviewed and did NOT recognize
            # (8 records on 2026-10-09). Typing them RecognizedConsensusStandard would
            # be false, so they are counted as skipped.
            if r[F_LIST].strip().upper() == "NR" or r[F_EXTENT].strip() not in ("C", "P"):
                st.skipped += 1
                continue
            key = rec or f"id-{sid}"           # a few records carry no recognition number
            extent = {"C": "Complete standard", "P": "Partial"}.get(r[F_EXTENT].strip())
            trans = r[F_TRANSITION_END].strip()[:10].replace("/", "-") or None
            entry = month_date(r[F_ENTRY_DATE])
            pairs = [
                ("a", "ex:RecognizedConsensusStandard"),
                ("rdfs:label", common.lit(clean(r[F_TITLE]))),
                ("ex:recognitionNumber", common.lit(rec)),
                ("ex:frRecognitionList", common.lit(r[F_LIST].strip())),
                ("ex:standardDesignation", common.lit(clean(r[F_DESIG]))),
                ("ex:recognitionDate", common.date_lit(entry) if entry else common.lit(r[F_ENTRY_DATE])),
                ("ex:extentOfRecognition", common.lit(extent)),
                ("ex:partialRecognitionText", common.lit(clean(r[F_PARTIAL_TEXT]))),
                ("ex:scope", common.lit(clean(r[F_SCOPE]))),
                ("ex:recognitionRationale", common.lit(clean(r[F_RATIONALE]))),
                ("ex:relevantGuidance", common.lit(clean(r[F_GUIDANCE]))),
                ("ex:transitionPeriodEnd", common.date_lit(trans)),
                ("ex:transitionNote", common.lit(clean(r[F_TRANSITION_NOTE]))),
                ("ex:identicalAdoption", common.lit(" ".join(
                    x for x in (r[F_ADOPT_SDO].strip(), r[F_ADOPT_DESIG].strip()) if x))),
                ("ex:fdaStandardId", common.lit(sid)),
            ]
            if rec and "-" in rec:
                pairs.append(("ex:recognitionArea", common.lit(rec.split("-")[0])))
            dp = src / "detail" / f"{sid}.html"
            if dp.exists():
                d = parse_detail(dp.read_text(encoding="utf-8", errors="replace"))
                if d["sdo"]:
                    pairs.append(("ex:standardsDevelopmentOrganization", common.lit(d["sdo"][0])))
                pairs.append(("ex:specialtyTaskGroup", common.lit(d["stg"])))
                for reg, dname, dcls, pc in d["links"]:
                    pairs.append(("ex:listsProductCode", f"<{iri.product_code(pc)}>"))
                    pcs.add(pc)
                    if reg:
                        pairs.append(("ex:citesRegulation", f"<{iri.regulation(reg)}>"))
                        regs.setdefault(reg, dname)
            pairs += common.provenance(DATASET, sid, source_url=DETAIL_URL.format(sid),
                                       retrieved_at=retrieved_at)
            # FDA lists one product code under several regulations; keep each triple once
            w.block(iri.consensus_standard(key), list(dict.fromkeys(pairs)))
            st.records += 1
        # minimal typed hub nodes so the links never point at untyped IRIs
        for pc in sorted(pcs):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
        for reg in sorted(regs):
            w.block(iri.regulation(reg), [("a", "ex:RegulationNumber"),
                                          ("ex:regulationNumber", common.lit(reg))])
    st.triples = w.triples
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    # record 36768 verbatim layout (32 fields), plus a record with an embedded '|'
    r1 = ["36768", "3-96", "028", "Non-invasive sphygmomanometers - Part 1",
          "81060-1 First edition 2007-12-01", "", "", "",
          "NIBP Monitor Guidance, Issued March 1997. \r\n\r\n<i>Appropriate Use</i>", "A",
          "06/08/2018", "03/07/2018", "08/08/2024", "March 16, 2012", "", "", "", "", "",
          "C", "ISO 81060-1:2007 specifies requirements", "", "29530.0", "", "N", "",
          "81060-1:2007/(R)2013", "Non-invasive sphygmomanometers", "N", "", "ANSI AAMI ISO", "0"]
    r2 = ["47274", "9-133", "066", "Fluids for Haemodialysis", "23500-1 First edition 2019-02",
          "", "", "", "Guidance A | with a pipe | inside", "A", "05/25/2026", "05/13/2021",
          "06/01/2026", "June 7, 2021", "", "", "2028/07/02 00:00:00.000", "Section 7.6 only",
          "Relevant to devices", "P", "Base standard", "", "42491.0", "Superseded note", "T", "",
          "23500-1:2019", "Fluids", "N", "", "ANSI AAMI ISO", "0"]
    text = "|".join(r1) + "\r\n" + "|".join(r2)
    rows = list(parse_flatfile(text))
    assert len(rows) == 2 and all(len(r) == NFIELDS for r in rows), [len(r) for r in rows]
    assert rows[1][F_GUIDANCE] == "Guidance A | with a pipe | inside" and rows[1][F_EXTENT] == "P"
    assert month_date("March 16, 2012") == "2012-03-16" and month_date("Spring 2012") is None

    html = Path(__file__).with_name("_fixture_standard_36768.html")
    detail = (html.read_text(encoding="utf-8") if html.exists() else
              '<span>Public Law, CFR Citation(s) and Procode(s)* </span><table><tr><td>Regulation</td></tr>'
              '<tr><td><a href="https://www.ecfr.gov/current/title-21/section-870.1120">&#167;870.1120</a></td></td>'
              '<td>\nBlood Pressure Cuff\n</td><td>Class 2</td><td><a href="/scripts/cdrh/cfdocs/cfpcd/'
              'classification.cfm?ID=DXQ">DXQ</a></td></tr></table>'
              'Standards Development Organization</span><table><tr><td nowrap><span style="x">ISO</span></td>'
              '<td nowrap><span style="x">International Organization for Standardization</span></td></tr>'
              'FDA Specialty Task Group (STG)</span><table><tr><td><span style="x">Cardiovascular </span>')
    d = parse_detail(detail)
    assert d["links"] == [("870.1120", "Blood Pressure Cuff", "2", "DXQ")], d["links"]
    assert d["sdo"][0] == "ISO" and d["stg"] == "Cardiovascular", d

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "standards.txt").write_text(text, encoding="latin-1")
        (tmp / "detail").mkdir()
        (tmp / "detail" / "36768.html").write_text(detail, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-09", files=[]).write(tmp / "manifest.json")
        st = transform(tmp, tmp / "s.ttl")
        ttl = (tmp / "s.ttl").read_text(encoding="utf-8")
    assert st.records == 2 and st.skipped == 0, st
    assert f"<{iri.consensus_standard('3-96')}>" in ttl
    assert f"ex:listsProductCode <{iri.product_code('DXQ')}>" in ttl
    assert f"ex:citesRegulation <{iri.regulation('870.1120')}>" in ttl
    assert '"2012-03-16"^^xsd:date' in ttl and '"2028-07-02"^^xsd:date' in ttl
    assert 'ex:extentOfRecognition "Partial"' in ttl and "<i>" not in ttl
    print(f"standards.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
