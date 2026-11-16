#!/usr/bin/env python3
"""
hud_designations.py -- Humanitarian Use Device (HUD) designations read from HDE summaries -> RDF.

FDA publishes no list of HUD designations (re-checked November 11, 2026). Each HDE's
Summary of Safety and Probable Benefit (SSPB) states, in its first section, the HUD
designation number and the date of designation, e.g.

    Humanitarian Use Device (HUD) Designation Number: DEV-2015-0345
    Date of HUD Designation: December 21, 2015                       (H170001)
    Humanitarian Use Device (HUD) Designation Number: #09-0222       (H130005)

fetch(dest, hde_ttl=...)
    For every original HDE in hde.ttl (an iri.hde(number) node, no supplement): one request
    for its HDE database page (cfHDE/hde.cfm?id=<H number>), which links the documents as
    cdrh_docs/<folder>/<H number>A.pdf (Approval Order) and ...B.pdf (Summary of Safety
    And Probable Benefit) -- the links are read from the page, never guessed (6 pages
    checked November 15, 2026: H960001 links only an approval order). Then the B and the
    A PDF where linked. Same polite rules as k510_predicates.fetch: at most 1 request per
    second, the same User-Agent, files present with size > 0 are not asked again, refusals
    retried after 30 s, 2 and 10 min, one log line per request (fetch_log.tsv).

transform(src, out, hde_ttl=None)
    pdftotext on each PDF (image-only PDFs, fewer than 100 characters of text, are listed in
    <out stem>.ocr_backlog.tsv). From the SSPB, else the approval order:
      number   a designation number (#dd-dddd, dd-dddd, dddd-dddd or DEV-yyyy-nnnn) after
               "HUD Designation Number", "HUD #", "Humanitarian Use Device (HUD)
               Designation" or "HUD designation" (within 120 characters) -> high; such a
               number within 400 characters of "HUD" / "Humanitarian Use" -> medium;
      date     "Date of HUD Designation" / "Date of Humanitarian Use Device Designation" /
               "designated ... as a HUD on" followed by a date (within 120 characters);
      indication
               the text after an "Indications for Use" heading up to the next heading, kept
               only when it is at most 800 characters.
    Confidence is "high" for a value read from the SSPB under its label, "medium" for a
    value read from the approval order or found by proximity.

Model: <HDE> ex:hasHUDDesignation <iri.hud_designation(number)>; the ex:HUDDesignation node
has one rdfs:label, ex:hudDesignationNumber, ex:designationDate, ex:designatedIndication
(short only), ex:extractionMethod, ex:confidence and provenance (ex:sourceUrl = the PDF
read, ex:retrievedAt = its download date). An HDE whose summary gives a designation date
but no number gets a node keyed on the HDE number (iri.hud_designation("hde-<number>")),
labelled as such. Two HDEs under one designation share the node.
"""
import json
import os
import re
import sys
import time
import urllib.error
from collections import Counter
from pathlib import Path

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_PIPELINE = os.path.dirname(_SRC_DIR)
for _p in (_SRC_DIR, _PIPELINE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import iri        # noqa: E402
import common     # noqa: E402
import base       # noqa: E402
import k510_predicates as kp   # noqa: E402  (polite getter, pdftotext wrapper)

NAME = "hud_designations"
DATASET = "cdrh/hde-summary-pdf"
METHOD = "pdftotext+regex v1"
PAGE_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfHDE/hde.cfm?id={}"
MAX_INDICATION = 800

_SUBJ = re.compile(r"^<([^>]+)>")


def hde_originals(hde_ttl):
    """Sorted H numbers of every original HDE node (no supplement) in hde.ttl."""
    pref = iri.hde("H000000").rsplit("/", 1)[0] + "/"
    out = set()
    with open(hde_ttl, encoding="utf-8") as fh:
        for line in fh:
            m = _SUBJ.match(line)
            if m and m.group(1).startswith(pref) and "/supplement/" not in m.group(1):
                out.add(m.group(1)[len(pref):].upper())
    return sorted(out)


def _read_log(dest):
    rows = {}
    p = Path(dest) / "fetch_log.tsv"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            f = line.split("\t")
            if len(f) >= 6:
                rows[(f[0], f[1])] = {"url": f[2], "status": f[3], "bytes": f[4], "date": f[5]}
    return rows


def doc_links(html, number):
    """{letter: url} for the <number>A.pdf / B.pdf links on an HDE page."""
    out = {}
    for m in re.finditer(r'https?://www\.accessdata\.fda\.gov/cdrh_docs/[A-Za-z0-9_/]+?/' + re.escape(number)
                         + r'([A-Da-d])\.pdf', html or "", re.I):
        out.setdefault(m.group(1).upper(), m.group(0))
    return out


def fetch(dest, hde_ttl=None, numbers=None, sleep=time.sleep, getter=kp._get):
    dest = Path(dest)
    (dest / "pages").mkdir(parents=True, exist_ok=True)
    (dest / "pdf").mkdir(parents=True, exist_ok=True)
    if hde_ttl is None:
        hde_ttl = Path(os.environ.get("V7C_OUT", ".")) / "hde.ttl"
    nums = hde_originals(hde_ttl)
    if numbers:
        nums = [n for n in nums if n in {x.upper() for x in numbers}]
    log = _read_log(dest)
    new = not (dest / "fetch_log.tsv").exists()
    logf = open(dest / "fetch_log.tsv", "a", encoding="utf-8")
    if new:
        logf.write("number\tdocument\turl\tstatus\tbytes\tdate\n")
    last = [0.0]
    refused_row = 0

    def get(url):
        status, data = None, None
        for wait in (0,) + kp.RETRY_WAITS:
            if wait:
                sleep(wait)
            gap = time.time() - last[0]
            if gap < 1.0:
                sleep(1.0 - gap)
            last[0] = time.time()
            try:
                code, data = getter(url)
                return str(code), data
            except urllib.error.HTTPError as e:
                status = str(e.code)
                if e.code == 404:
                    return status, None
            except Exception as e:
                status = "error:" + type(e).__name__
        return "refused:" + str(status), None

    try:
        for n in nums:
            page = dest / "pages" / f"{n}.html"
            if not (page.exists() and page.stat().st_size > 0):
                url = PAGE_URL.format(n)
                status, data = get(url)
                if status == "200" and data:
                    page.write_bytes(data)
                logf.write(f"{n}\tpage\t{url}\t{status}\t{len(data or b'')}\t{base.today()}\n")
                logf.flush()
                refused_row = refused_row + 1 if status.startswith("refused") else 0
                if refused_row >= kp.MAX_REFUSED_IN_A_ROW:
                    print(f"[{NAME}] FDA refused {refused_row} requests in a row; stopped", flush=True)
                    break
            if not page.exists():
                continue
            links = doc_links(page.read_text(encoding="utf-8", errors="replace"), n)
            for letter in ("B", "A"):
                if letter not in links:
                    continue
                pdf = dest / "pdf" / f"{n}{letter}.pdf"
                if (pdf.exists() and pdf.stat().st_size > 0) or log.get((n, letter), {}).get("status") in ("404", "notpdf"):
                    continue
                status, data = get(links[letter])
                if status == "200" and data is not None:
                    if data[:1024].lstrip().startswith(b"%PDF"):
                        pdf.write_bytes(data)
                    else:
                        status = "notpdf"
                logf.write(f"{n}\t{letter}\t{links[letter]}\t{status}\t{len(data or b'')}\t{base.today()}\n")
                logf.flush()
    finally:
        logf.close()
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[])
    man.write(dest / "manifest.json")
    return man


# ===========================================================================
_NUM = r"(DEV-\d{4}-\d{3,5}|#?\s?\d{2,4}\s?-\s?\d{3,4})"
_NUM_L = r"(DEV[-\s]?\d{4}-\d{3,5}|#?\s?\d{2,4}\s?-\s?\d{3,4}|\d{2}\s0\d{3})"   # labelled: also "98 0028"
_LABEL_NUM = re.compile(r"(?:HUD\s+Designation\s+(?:Number|No\.?|#)|HUD\s*(?:#|No\.?|Number)|"
                        r"Humanitarian\s+Use\s+Devices?\s*\(HUD\)\s*Designation(?:\s+(?:Number|No\.?|#))?|"
                        r"Humanitarian\s+Use\s+Designation\s*\(HUD\)|HUD\s+designation|"
                        r"Humanitarian\s+Use\s+Device\s+Designation\s+(?:Number|No\.?|#)|"
                        r"Humanitarian\s+Use\s+Device\s+Designation\s*:?\s*(?=HUD))"
                        r"[^0-9#]{0,120}?" + _NUM_L, re.I)
_NEAR_NUM = re.compile(r"(?:HUD|Humanitarian\s+Use)[\s\S]{0,400}?" + _NUM, re.I)
_MONTHS = ("january february march april may june july august september october november december").split()
_DATE_TXT = r"((?:%s)\s*\.?\s*\d{1,2}\s*,?\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})" % "|".join(
    m.capitalize() + "|" + m.upper() + "|" + m for m in _MONTHS)
_LABEL_DATE = re.compile(r"(?:Date\s*of\s*(?:the\s+)?[^:.]{0,45}?Designation\s*(?:\([^)]{0,30}\))?"
                         r"|designated\s+(?:as\s+)?(?:a\s+)?(?:HUD|Humanitarian\s+Use\s+Device)[^.]{0,80}?\bon)"
                         r"\s*:?[^A-Za-z0-9]{0,40}" + _DATE_TXT, re.I)
_IND = re.compile(r"(?im)^[ \t]*(?:[IVX]{1,4}\.|\d{1,2}\.)?[ \t]*INDICATIONS?\s+FOR\s+USE[ \t]*(?::[ \t]*|$)\n?")
_NEXT_HDR = re.compile(r"\n[ \t]*(?:[IVX]{1,4}\.|\d{1,2}\.)[ \t]*[A-Z][A-Za-z ,/&()-]{3,80}\n|\n[ \t]*[A-Z][A-Z ,/&()-]{6,80}\n")


def parse_date(s):
    s = re.sub(r"\s+", " ", s or "").strip().rstrip(".")
    m = re.match(r"(?i)([a-z]+)\s*\.?\s*(\d{1,2})\s*,?\s*(\d{4})$", s)
    if m and m.group(1).lower() in _MONTHS:
        mo = _MONTHS.index(m.group(1).lower()) + 1
        return f"{m.group(3)}-{mo:02d}-{int(m.group(2)):02d}" if kp_valid(m.group(3), mo, m.group(2)) else None
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        y = m.group(3)
        y = ("19" + y if int(y) > 50 else "20" + y) if len(y) == 2 else y
        return f"{y}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if kp_valid(y, m.group(1), m.group(2)) else None
    return None


def kp_valid(y, m, d):
    import datetime as dt
    try:
        dt.date(int(y), int(m), int(d))
        return True
    except ValueError:
        return False


def clean_number(raw):
    raw = re.sub(r"^(\d{2})\s(0\d{3})$", r"\1-\2", (raw or "").strip())      # "98 0028" -> "98-0028"
    raw = re.sub(r"(?i)^DEV\s", "DEV-", raw)                                   # "DEV 2017-0381"
    s = re.sub(r"\s+", "", raw).lstrip("#")
    return s.upper() if s.upper().startswith("DEV") else s


def extract(text):
    """{number, number_conf, date, indication} read from one summary or order text."""
    text = (text or "").replace("\f", "\n")
    flat = re.sub(r"\s+", " ", text)
    out = {"number": None, "number_conf": None, "date": None, "indication": None}
    m = _LABEL_NUM.search(flat)
    if m:
        out["number"], out["number_conf"] = clean_number(m.group(1)), "high"
    else:
        for m in _NEAR_NUM.finditer(flat):
            n = clean_number(m.group(1))
            # a near number must look like a designation, not a date, docket or page range
            if re.match(r"^(DEV-\d{4}-\d{3,5}|\d{2}-\d{4})$", n) and not re.search(
                    r"(?i)docket|\bpages?\b", flat[max(0, m.start(1) - 40):m.start(1)]):
                out["number"], out["number_conf"] = n, "medium"
                break
    m = _LABEL_DATE.search(flat)
    if m:
        out["date"] = parse_date(m.group(1))
    for m in _IND.finditer(text):
        rest = text[m.end():m.end() + 6000]
        nx = _NEXT_HDR.search("\n" + rest)
        ind = re.sub(r"\s+", " ", rest[:max(0, nx.start() - 1)] if nx else rest[:1500]).strip()
        # the statement only: not FDA's note on how it differs from the designation, no trailing section number
        ind = re.split(r"\s(?=The indications? for use statement)", ind)[0]
        ind = re.sub(r"\s+(?:[IVX1l]{1,4}|\d{1,2})\.?\s*$", "", ind).strip()
        if len(ind) >= 20:
            out["indication"] = ind if len(ind) <= MAX_INDICATION else None
            break
    return out


def transform(src, out, hde_ttl=None):
    src, out = Path(src), Path(out)
    if hde_ttl is None:
        hde_ttl = out.parent / "hde.ttl"
    nums = hde_originals(hde_ttl)
    log = _read_log(src)
    st = base.Stats(source=NAME)
    counts = Counter()
    rows, ocr = [], []
    nodes = {}          # designation IRI -> (pairs, [hde numbers])
    for n in nums:
        counts["hde_originals"] += 1
        page = src / "pages" / f"{n}.html"
        if page.exists():
            links = doc_links(page.read_text(encoding="utf-8", errors="replace"), n)
            counts["pages"] += 1
            counts["pages_linking_summary_B"] += "B" in links
            counts["pages_linking_order_A"] += "A" in links
        found, used = None, None
        texts = {}
        for letter in ("B", "A"):
            pdf = src / "pdf" / f"{n}{letter}.pdf"
            if not pdf.exists():
                continue
            text, err = kp.text_of(pdf, src / "text")
            chars = len(re.sub(r"\s+", "", text))
            if err or chars < kp.MIN_TEXT_CHARS:
                ocr.append((n, letter, log.get((n, letter), {}).get("url", ""), chars, err or "image-only"))
                counts[f"ocr_backlog_{letter}"] += 1
                continue
            counts[f"text_{letter}"] += 1
            texts[letter] = extract(text)
        b, a = texts.get("B"), texts.get("A")
        number, conf, letter = None, None, None
        if b and b["number"]:
            number, conf, letter = b["number"], b["number_conf"], "B"
        elif a and a["number"]:
            number, conf, letter = a["number"], "medium", "A"
        date = (b or {}).get("date") or (a or {}).get("date")
        date_from = "B" if (b or {}).get("date") else ("A" if (a or {}).get("date") else None)
        ind = (b or {}).get("indication")
        if not number and not date:
            rows.append((n, "", "", "", "", "none found" if texts else "no text"))
            continue
        letter = letter or date_from
        url = log.get((n, letter), {}).get("url")
        retrieved = log.get((n, letter), {}).get("date")
        if number:
            key, lab = number, f"HUD designation {number}"
            counts["with_number"] += 1
        else:
            key, lab = f"hde-{n}", f"HUD designation of HDE {n} (number not stated in the summary)"
            conf = "medium"
            counts["date_only"] += 1
        if date:
            counts["with_date"] += 1
        if ind:
            counts["with_indication"] += 1
        d_iri = iri.hud_designation(key)
        if d_iri not in nodes:
            pairs = [("a", "ex:HUDDesignation"), ("rdfs:label", common.lit(lab)),
                     ("ex:hudDesignationNumber", common.lit(number)),
                     ("ex:designationDate", common.date_lit(date) if date else None),
                     ("ex:designatedIndication", common.lit(ind) if ind else None),
                     ("ex:extractionSection", common.lit("Summary of Safety and Probable Benefit, general information"
                                                         if letter == "B" else "Approval order")),
                     ("ex:extractionMethod", common.lit(METHOD)),
                     ("ex:confidence", common.lit(conf))]
            pairs += common.provenance(DATASET, n, source_url=url, retrieved_at=retrieved)
            nodes[d_iri] = (pairs, [n])
        else:
            nodes[d_iri][1].append(n)
            counts["shared_designation"] += 1
        rows.append((n, number or "", date or "", conf, letter, (ind or "")[:120]))
    with base.TurtleWriter(out) as w:
        for d_iri in sorted(nodes):
            pairs, hdes = nodes[d_iri]
            for n in hdes:
                w.block(iri.hde(n), [("ex:hasHUDDesignation", f"<{d_iri}>")])
            w.block(d_iri, pairs)
            st.records += 1
    st.triples = w.triples
    stem = out.with_suffix("")
    with open(f"{stem}.rows.tsv", "w", encoding="utf-8") as f:
        f.write("hde\tdesignation_number\tdesignation_date\tconfidence\tread_from\tindication_start\n")
        for r in rows:
            f.write("\t".join(str(x).replace("\t", " ") for x in r) + "\n")
    with open(f"{stem}.ocr_backlog.tsv", "w", encoding="utf-8") as f:
        f.write("hde\tdocument\turl\ttext_chars\treason\n")
        for r in ocr:
            f.write("\t".join(map(str, r)) + "\n")
    summary = {"method": METHOD, **dict(counts), "designation_nodes": st.records, "triples": st.triples}
    Path(f"{stem}.counts.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"[{NAME}] transform: {json.dumps(summary)}", flush=True)
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
def _selftest():
    import tempfile
    # layouts seen in real SSPBs (H170001, H050003, H130005, H990014), November 15, 2026
    e = extract("I. GENERAL INFORMATION\nHumanitarian Use Device (HUD) Designation Number: DEV-2015-0345\n"
                "Date of HUD Designation: December 21, 2015\nDate of Notice of Approval to Applicant: August 23, 2019\n"
                "II. INDICATIONS FOR USE\nThe MID-C System is indicated for use in patients with adolescent "
                "idiopathic scoliosis. The indication for use statement has been modified from that granted.\n111.\n"
                "III. CONTRAINDICATIONS\nNone.")
    assert e == {"number": "DEV-2015-0345", "number_conf": "high", "date": "2015-12-21",
                 "indication": "The MID-C System is indicated for use in patients with adolescent idiopathic scoliosis."}, e
    e = extract("Humanitarian Use Device (HUD)\nDesignation Number:\n\n#09-0222\n\nDate of HUD\nDesignation:\n\nFebruary 19, 2010\n")
    assert e["number"] == "09-0222" and e["number_conf"] == "high" and e["date"] == "2010-02-19", e
    e = extract("Humanitarian Use Designation (HUD)\n\n05-0149\n\nDate of HUD\nDesignation:\n\nMarch 21, 2005")
    assert e["number"] == "05-0149" and e["date"] == "2005-03-21", e
    e = extract("Date of Humanitarian UseDeviceDesignation:\n\nSeptember23, 1999\nDocket 99M-1234")
    assert e["number"] is None and e["date"] == "1999-09-23", e
    e = extract("Humanitarian Device Exemption Number H990003 Date of Humanitarian Use Device Designation HUD 98 0028 "
                "December 22 1998 Date of Panel Recommendation")
    assert e["number"] == "98-0028" and e["number_conf"] == "high", e
    e = extract("Dateof HumanitarianUseDeviceDesignation: April 24, 1998 Dateof PanelRecommendation:")
    assert e["date"] == "1998-04-24", e
    e = extract("Date of HDE: December 8, 1998 Date of Humanitarian Use Device Designation (secondary indication): October 6, 1998")
    assert e["date"] == "1998-10-06", e
    e = extract("Date of Humanitarian Use Device Designation: Filed: March 30,2004 Date(s) of Panel Recommendation: None")
    assert e["date"] is None, e
    assert parse_date("13/45/2001") is None and parse_date("2/3/04") == "2004-02-03"
    assert doc_links('<a href="https://www.accessdata.fda.gov/cdrh_docs/pdf17/H170001B.pdf">x</a>'
                     '<a href="https://www.accessdata.fda.gov/cdrh_docs/pdf17/H170001A.pdf">', "H170001") == {
        "B": "https://www.accessdata.fda.gov/cdrh_docs/pdf17/H170001B.pdf",
        "A": "https://www.accessdata.fda.gov/cdrh_docs/pdf17/H170001A.pdf"}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "hde.ttl").write_text(f"<{iri.hde('H170001')}>\n    a ex:HumanitarianDeviceExemption .\n\n"
                                   f"<{iri.hde('H170001', 'S001')}>\n    a ex:HumanitarianDeviceExemption .\n\n"
                                   f"<{iri.hde('H990014')}>\n    a ex:HumanitarianDeviceExemption .\n", encoding="utf-8")
        assert hde_originals(d / "hde.ttl") == ["H170001", "H990014"]
        pages = {"H170001": b'<a href="https://www.accessdata.fda.gov/cdrh_docs/pdf17/H170001B.pdf">',
                 "H990014": b'<a href="https://www.accessdata.fda.gov/cdrh_docs/pdf/H990014B.pdf">'}

        def getter(url):
            for n, html in pages.items():
                if url.endswith("id=" + n):
                    return 200, html
            return 200, b"%PDF-1.4 fake"
        src = d / "dl"
        fetch(src, hde_ttl=d / "hde.ttl", sleep=lambda s: None, getter=getter)
        assert (src / "pdf" / "H170001B.pdf").exists() and not (src / "pdf" / "H170001A.pdf").exists()
        for n, txt in (("H170001", "Humanitarian Use Device (HUD) Designation Number: DEV-2015-0345\n"
                                   "Date of HUD Designation: December 21, 2015\n" + "x" * 200),
                       ("H990014", "Date of Humanitarian UseDeviceDesignation:\n\nSeptember23, 1999\n" + "y" * 200)):
            t = src / "text" / "pdf" / f"{n}B.txt"
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text(txt, encoding="utf-8")
            os.utime(t, (time.time() + 5, time.time() + 5))
        out = d / "hud_designations.ttl"
        st = transform(src, out, hde_ttl=d / "hde.ttl")
        ttl = out.read_text(encoding="utf-8")
    assert st.records == 2, st
    assert f"<{iri.hde('H170001')}>\n    ex:hasHUDDesignation <{iri.hud_designation('DEV-2015-0345')}>" in ttl
    assert 'ex:designationDate "2015-12-21"^^xsd:date' in ttl and 'ex:confidence "high"' in ttl
    assert f"<{iri.hud_designation('hde-H990014')}>" in ttl and "number not stated" in ttl
    assert ttl.count("rdfs:label") == 2
    assert 'ex:sourceUrl "https://www.accessdata.fda.gov/cdrh_docs/pdf17/H170001B.pdf"' in ttl
    print(f"hud_designations.py self-test PASSED ({st.records} designations, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
