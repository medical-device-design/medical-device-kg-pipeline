#!/usr/bin/env python3
"""
mddt.py -- CDRH Medical Device Development Tools (MDDT): the qualified tools -> RDF.

PAGE  https://www.fda.gov/medical-devices/medical-device-development-tools-mddt
      Section "Qualified Medical Device Development Tools (MDDTs)" holds one HTML table
      (DataTables, FDA offers no CSV/Excel file for it). Header (verified November 3, 2026):
          Tool (Link to SEBQ) | Product Area(s) | MDDT Category | Date Qualified
      21 rows on November 3, 2026. Each tool links to FDA's "Summary of Evidence and Basis of
      Qualification" (SEBQ) PDF, /media/<id>/download?attachment.
SEBQ  The PDF's BACKGROUND block gives the MDDT name, the MDDT submission number
      (U240030/S001, MDDT020, Q191161-S001 ...), the date of submission and a contact block;
      the "QUALIFIED CONTEXT OF USE" section gives the context of use. The text is extracted
      at fetch time with pdftotext (poppler) and cached next to the PDF.

PERSONAL DATA
  The SEBQ contact block names a person (with e-mail, sometimes phone). Only the
  ORGANIZATION lines of that block are kept (ex:submitterOrganization): the first line (the
  person) is always dropped, and a later line is kept only if it carries an organization word
  (University, Institute, Inc, Office, Administration ...) and no e-mail, phone, or degree
  token. Addresses, e-mails and phones are never written.

MODEL
  ex:MedicalDeviceDevelopmentTool at mddt(key), key = the MDDT submission number without its
  supplement suffix (U240030/S001 -> U240030; Q191161-S001 -> Q191161), or the tool name when
  the SEBQ is missing or carries no number.
      rdfs:label                 tool name as listed on the page
      ex:mddtCategory            FDA's category, spelling unified to one of
                                 "Clinical Outcome Assessment", "Biomarker Test",
                                 "Nonclinical Assessment Model", "Other"
      ex:productArea             one per comma-separated product area
      ex:qualificationDate       xsd:date (Date Qualified)
      ex:mddtSubmissionNumber    as printed in the SEBQ (U240030/S001)
      ex:dateReceived            SEBQ "Date of Submission" (xsd:date when parseable)
      ex:submitterOrganization   organization lines of the SEBQ contact block
      ex:qualifiedContextOfUse   SEBQ "QUALIFIED CONTEXT OF USE" text (FDA's text)
      ex:summaryUrl              SEBQ URL
      provenance                 sourceUrl = SEBQ URL (the page URL when there is none)

MINTER (iri.py)
    iri.mddt(key)   mddt('U240030') -> .../id/mddt/u240030
"""
import html as htmllib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
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
import _cfweb     # noqa: E402

NAME = "mddt"
DATASET = "cdrh/mddt"
SITE = "https://www.fda.gov"
PAGE_URL = SITE + "/medical-devices/medical-device-development-tools-mddt"
UA = _cfweb.UA
HEADER = ["Tool (Link to SEBQ)", "Product Area(s)", "MDDT Category", "Date Qualified"]
CATEGORIES = {"clinical outcome assessment": "Clinical Outcome Assessment",
              "biomarker test": "Biomarker Test",
              "nonclinical assessment model": "Nonclinical Assessment Model",
              "non-clinical assessment model": "Nonclinical Assessment Model",
              "other": "Other"}


mddt_iri = iri.mddt


# ===========================================================================
#  Parsing the page
# ===========================================================================
def _cells(row, tag="td"):
    return re.findall(rf"(?is)<{tag}\b[^>]*>(.*?)</{tag}>", row)


def parse_page(page):
    """-> list of dicts: name, href (absolute or None), areas, category_raw, date_raw."""
    i = page.find("Tool (Link to SEBQ)")
    if i < 0:
        raise ValueError("MDDT table not found (header 'Tool (Link to SEBQ)' missing)")
    t0 = page.rfind("<table", 0, i)
    t1 = page.find("</table>", i)
    table = page[t0:t1]
    head = [_cfweb.one_line(c) for c in _cells(table.split("</thead>", 1)[0], "th")]
    if head != HEADER:
        raise ValueError(f"MDDT table header changed: {head}")
    body = table.split("<tbody>", 1)[1] if "<tbody>" in table else table
    out = []
    for row in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", body):
        c = _cells(row)
        if len(c) != 4:
            continue
        h = re.search(r'href="([^"]+)"', c[0])
        href = htmllib.unescape(h.group(1)) if h else None
        if href and href.startswith("/"):
            href = SITE + href
        out.append({"name": _cfweb.one_line(c[0]), "href": href,
                    "areas": [a.strip() for a in (_cfweb.one_line(c[1]) or "").split(",") if a.strip()],
                    "category_raw": _cfweb.one_line(c[2]), "date_raw": _cfweb.one_line(c[3])})
    return out


def media_id(href):
    m = re.search(r"/media(?:-base)?/(\d+)", href or "")
    return m.group(1) if m else None


def canonical_sebq_url(href):
    """FDA links one tool as /media-base/<id>, which answers 'page not found'; the
    download form of the same media id is /media/<id>/download?attachment."""
    mid = media_id(href)
    if not mid:
        return href
    return f"{SITE}/media/{mid}/download?attachment"


def category(raw):
    return CATEGORIES.get(" ".join((raw or "").lower().split()), raw)


# ===========================================================================
#  Parsing the SEBQ text (pdftotext -layout output)
# ===========================================================================
_LABEL = re.compile(r"^\s*(MDDT NAME|SUBMISSION NUMBER|DATE OF SUBMISSION|CONTACT)\b\s*:?\s*(.*)$", re.I)
# SEBQ section headings (capitals in older summaries, title case in newer ones)
_HEADING = re.compile(r"^\s{0,8}(BACKGROUND|TOOL DESCRIPTION|(QUALIFIED\s+)?CONTEXT OF USE|SUMMARY OF EVIDENCE|"
                      r"DISCUSSION OF THE|ASSESSMENT OF|ADDITIONAL EVIDENCE|CONCLUSIONS?|REFERENCES?|"
                      r"CONTACT INFORMATION|CONTACT INFO FOR)\b", re.I)
_COU = re.compile(r"^\s{0,8}(QUALIFIED\s+)?CONTEXT OF USE\b\s*:?\s*(.*)$", re.I)
_FOOTER = re.compile(r"^\s*(MDDT\b.*?(\(\s*[A-Z]{1,4}\d[\w/-]*\s*\))?\s+\d+(\s+of\s+\d+)?|(page\s+)?\d+(\s+of\s+\d+)?)\s*$", re.I)
# contact-block segment filters (a segment is one comma-separated part of a line)
_ORG_WORDS = re.compile(
    r"(?i)\b(universit\w*|institut\w*|inc|llc|ltd|limited|gmbh|ag|bv|b\.v|corp\w*|company|laborator\w*|"
    r"cent(er|re)s?|office|administration|hospital|foundation|division|department|school|college|"
    r"clinic|society|association|academy|group|agency|ministry|council|consortium|initiative)\b")
_LEGAL_ONLY = re.compile(r"(?i)^(inc|llc|ltd|limited|gmbh|ag|bv|b\.v|corp|corporation|co)\.?$")
_TITLE = re.compile(r"(?i)\b(director|president|vice|fellow|manager|specialist|head|chief|professor|prof|"
                    r"co-?pi|pi|officer|scientist|engineer|lead|coordinator|chair\w*|dean|ms|mr|mrs|dr)\b")
_PERSONISH = re.compile(r"(?i)(@|e-?mail|phone|tel\b|fax|\bph\.?\s?d\b|\bm\.?d\b|\bmph\b|\bmsc\b|\brn\b|"
                        r"\bp\.?e\b\.?|\bpharmd\b|\bdvm\b)")
_ADDRESS = re.compile(r"(?i)(^\W*\d|\b(box|suite|room|floor|street|st|avenue|ave|road|rd|blvd|boulevard|"
                      r"drive|way|lane|park|parkway|pkwy)\b|\d{4,})")
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}


def parse_date(s):
    """'09/30/2025', 'JANUARY 19, 2016', 'October 3rd, 2023', '4 November 2024' -> 'YYYY-MM-DD'."""
    s = " ".join((s or "").split())
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$", s)
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.match(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})$", s)
    if m and m.group(2).lower() in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def organization_lines(contact_lines):
    """Organization part of an SEBQ contact block, which mixes a person, the person's title,
    the organization, an address, e-mail and phone in no fixed order. A comma-separated
    segment is kept only when it names an organization word (University, Institute, Inc,
    Office, Hospital ...) and carries no title, degree, e-mail, phone or address token; a
    bare legal form ("Inc.") is joined to the segment before it. Person names carry no
    organization word, so they are never kept."""
    out = []
    for ln in contact_lines:
        segs = [" ".join(s.split()).strip(" ;") for s in " ".join(ln.split()).split(",")]
        prev = None
        for s in segs:
            if not s:
                continue
            if _LEGAL_ONLY.match(s) and prev and not (_PERSONISH.search(prev) or _TITLE.search(prev)
                                                      or _ADDRESS.search(prev)):
                cand = f"{prev}, {s}"
                if cand not in out:
                    out.append(cand)
                prev = None
                continue
            prev = s
            if _PERSONISH.search(s) or _TITLE.search(s) or _ADDRESS.search(s) or not _ORG_WORDS.search(s):
                continue
            if s not in out:
                out.append(s)
    return ", ".join(out) or None


def _section_end(lines, start):
    return next((i for i in range(start, len(lines)) if _HEADING.match(lines[i]) and len(lines[i].strip()) < 90
                 and not lines[i].strip().endswith(".")), len(lines))


def parse_sebq(text):
    """-> dict: name, submission, date_raw, organization, context_of_use."""
    lines = [ln.rstrip() for ln in (text or "").replace("\f", "\n").split("\n")]
    lines = [ln if ln.strip() else "" for ln in lines]
    d = {"name": None, "submission": None, "date_raw": None, "organization": None, "context_of_use": None}
    # BACKGROUND block: labeled fields, then the contact block (labeled CONTACT, or unlabeled
    # after the date of submission)
    b0 = next((i for i, ln in enumerate(lines) if ln.strip().upper() == "BACKGROUND"), -1)
    if b0 >= 0:
        block = lines[b0 + 1:_section_end(lines, b0 + 1)]
        cur, contact = None, []
        for ln in block:
            m = _LABEL.match(ln)
            if m:
                cur = m.group(1).upper()
                val = m.group(2).strip()
                if cur == "MDDT NAME":
                    d["name"] = val
                elif cur == "SUBMISSION NUMBER":
                    d["submission"] = val or None
                elif cur == "DATE OF SUBMISSION":
                    d["date_raw"] = val or None
                    cur = "CONTACT"
                elif cur == "CONTACT":
                    contact.append(val)
                continue
            if cur == "MDDT NAME" and ln.strip():
                d["name"] = f"{d['name']} {ln.strip()}".strip()
            elif cur == "CONTACT":
                contact.append(ln)
        contact = [c for c in contact if c.strip()]
        d["organization"] = organization_lines(contact) if contact else None
    # (QUALIFIED) CONTEXT OF USE section, up to the next section heading
    c0 = next((i for i, ln in enumerate(lines) if _COU.match(ln) and len(ln.strip()) < 90), -1)
    if c0 >= 0:
        rest = _COU.match(lines[c0]).group(2).strip()
        body = [rest] if rest else []
        for ln in lines[c0 + 1:_section_end(lines, c0 + 1)]:
            if _FOOTER.match(ln):
                continue
            body.append(ln.strip())
        paras, cur = [], []
        for ln in body:
            if ln:
                cur.append(ln)
            elif cur:
                paras.append(" ".join(cur))
                cur = []
        if cur:
            paras.append(" ".join(cur))
        d["context_of_use"] = "\n".join(paras) or None
        # FDA's context-of-use text names no person; refuse it if an e-mail or phone slipped in
        if d["context_of_use"] and re.search(r"@|(?i:\bphone\b|\be-?mail\b)", d["context_of_use"]):
            d["context_of_use"], d["context_of_use_refused"] = None, True
    return d


def record_key(sebq, name):
    sub = (sebq or {}).get("submission")
    if sub:
        base_no = re.split(r"\s*[/-]\s*S\d+\b", sub.strip(), maxsplit=1)[0].strip()
        if base_no:
            return base_no.upper()
    return name


# ===========================================================================
#  Fetch
# ===========================================================================
def _download(fetcher, url, path, tries=4):
    """Binary download through the fetcher's 1 request/second throttle; returns bytes."""
    last = None
    for attempt in range(tries + 1):
        wait = fetcher._next - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        fetcher._next = time.monotonic() + fetcher.delay
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=fetcher.timeout) as r:
                raw = r.read()
            tmp = Path(str(path) + ".part")
            tmp.write_bytes(raw)
            os.replace(tmp, path)
            fetcher.fetched += 1
            return raw
        except urllib.error.HTTPError as e:
            last = e
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:
            last = e
        if attempt < tries:
            time.sleep(2 ** (attempt + 1))
    fetcher.errors += 1
    raise _cfweb.Unavailable(f"{url}: {last}")


def pdf_to_text(pdf, txt):
    exe = shutil.which("pdftotext")
    if not exe:
        return False
    subprocess.run([exe, "-layout", "-enc", "UTF-8", str(pdf), str(txt)], check=True,
                   capture_output=True, timeout=120)
    return True


def fetch(dest, delay=1.0):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    page = f.get(PAGE_URL, "mddt_page", max_age_days=1)
    rows = parse_page(page)                      # refuse a page whose table changed
    sebq_dir = dest / "sebq"
    sebq_dir.mkdir(parents=True, exist_ok=True)
    problems = []
    for r in rows:
        if not r["href"]:
            problems.append({"tool": r["name"], "problem": "no SEBQ link"})
            continue
        mid = media_id(r["href"])
        pdf = sebq_dir / f"{mid}.pdf"
        if not (pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-"):
            urls = [r["href"]] + ([canonical_sebq_url(r["href"])] if canonical_sebq_url(r["href"]) != r["href"] else [])
            for u in urls:
                try:
                    raw = _download(f, u, pdf)
                except _cfweb.Unavailable as e:
                    problems.append({"tool": r["name"], "url": u, "problem": str(e)})
                    continue
                if raw[:5] == b"%PDF-":
                    if u != r["href"]:
                        problems.append({"tool": r["name"], "url": r["href"],
                                         "problem": f"link answers 'page not found'; SEBQ taken from {u}"})
                    break
                problems.append({"tool": r["name"], "url": u, "problem": "not a PDF (FDA page-not-found)"})
                pdf.unlink()
        txt = pdf.with_suffix(".txt")
        if pdf.exists() and not txt.exists():
            pdf_to_text(pdf, txt)
    (dest / "fetch_problems.json").write_text(json.dumps(problems, indent=1), encoding="utf-8")
    files = [base.FileRef(url=PAGE_URL, path="pages/mddt_page.html",
                          sha256=base.sha256_file(f.path("mddt_page")), bytes=f.path("mddt_page").stat().st_size)]
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=files)
    man.write(dest / "manifest.json")
    print(f"  mddt: {len(rows)} tools; fetched {f.fetched}, cached {f.cached}, problems {len(problems)}")
    return man


# ===========================================================================
#  Transform
# ===========================================================================
def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    page = (src / "pages" / "mddt_page.html").read_text(encoding="utf-8", errors="replace")
    rows = parse_page(page)
    st = base.Stats(source=NAME)
    seen, report = set(), {"tools": 0, "with_sebq": 0, "with_submission_number": 0,
                           "with_organization": 0, "with_context_of_use": 0, "categories": {},
                           "missing_sebq": []}
    recs = []
    for r in rows:
        mid = media_id(r["href"])
        txtp = src / "sebq" / f"{mid}.txt" if mid else None
        pdfp = src / "sebq" / f"{mid}.pdf" if mid else None
        if txtp and not txtp.exists() and pdfp and pdfp.exists():
            pdf_to_text(pdfp, txtp)
        sebq = parse_sebq(txtp.read_text(encoding="utf-8", errors="replace")) if txtp and txtp.exists() else None
        url = (canonical_sebq_url(r["href"]) if r["href"] and "/media-base/" in r["href"] else r["href"])
        recs.append((record_key(sebq, r["name"]), r, sebq, url if sebq else None))
    recs.sort(key=lambda x: x[0].upper())
    with base.TurtleWriter(out) as w:
        for key, r, sebq, url in recs:
            if key.upper() in seen:
                st.skipped += 1
                continue
            seen.add(key.upper())
            cat = category(r["category_raw"])
            report["categories"][cat] = report["categories"].get(cat, 0) + 1
            qd = parse_date(r["date_raw"])
            pairs = [("a", "ex:MedicalDeviceDevelopmentTool"),
                     ("rdfs:label", common.lit(r["name"])),
                     ("ex:mddtCategory", common.lit(cat))]
            pairs += [("ex:productArea", common.lit(a)) for a in r["areas"]]
            pairs.append(("ex:qualificationDate", common.date_lit(qd) if qd else common.lit(r["date_raw"])))
            if sebq:
                report["with_sebq"] += 1
                sd = parse_date(sebq["date_raw"])
                pairs += [("ex:mddtSubmissionNumber", common.lit(sebq["submission"])),
                          ("ex:dateReceived", common.date_lit(sd) if sd else common.lit(sebq["date_raw"])),
                          ("ex:submitterOrganization", common.lit(sebq["organization"])),
                          ("ex:qualifiedContextOfUse", common.lit(sebq["context_of_use"])),
                          ("ex:summaryUrl", common.lit(url))]
                report["with_submission_number"] += bool(sebq["submission"])
                report["with_organization"] += bool(sebq["organization"])
                report["with_context_of_use"] += bool(sebq["context_of_use"])
                if sebq.get("context_of_use_refused"):
                    report.setdefault("context_of_use_refused", []).append(key)
            else:
                report["missing_sebq"].append({"tool": r["name"], "link": r["href"]})
            pairs += common.provenance(DATASET, key, source_url=url or PAGE_URL, retrieved_at=retrieved_at)
            w.block(mddt_iri(key), pairs)
            st.records += 1
    st.triples = w.triples
    report["tools"] = st.records
    Path(out).with_suffix(".report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"  mddt: {st.records} tools, {report['with_sebq']} with SEBQ text, "
          f"{report['with_organization']} with organization")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- table rows copied verbatim from the MDDT page and SEBQ text from
#  pdftotext -layout of /media/191032 and /media/108301 (November 3, 2026)
# ===========================================================================
_PAGE = """<h2>Qualified Medical Device Development Tools (MDDTs)</h2><div class="lcds-datatable lcds-datatable--ckeditor"><div class="table-responsive"><table class="table" aria-readonly="true" data-column-defs="[{ &quot;targets&quot;: 3, &quot;type&quot;: &quot;date&quot; }]" data-excel-button="true" data-order="[ 0, &quot;desc&quot; ]" data-searching="true" role="grid"><thead><tr><th scope="col">Tool (Link to SEBQ)</th><th scope="col">Product Area(s)</th><th scope="col">MDDT Category</th><th scope="col">Date Qualified</th></tr></thead><tbody><tr><td><a href="/media/191032/download?attachment" data-entity-type="media" data-entity-uuid="01c6127b-92f5-4e6e-b546-55ba8a368f98" data-entity-substitution="media_download" title="Image Viewer Integrity Evaluation System (IVIES)">Image Viewer Integrity Evaluation System (IVIES)</a></td><td>Digital Pathology</td><td>Non-clinical Assessment Model</td><td>02/03/2026</td></tr><tr><td><a href="/media-base/163472" data-entity-type="media" data-entity-uuid="9269f606-1133-4b20-a21f-8426f370a061" data-entity-substitution="media_download">CHemical RISk Calculator (CHRIS) - Color Additives</a></td><td>Toxicology, Biocompatibility</td><td>Non-clinical Assessment Model</td><td>11/28/2022</td></tr><tr><td><a href="/media/108301/download?attachment" data-entity-type="media" data-entity-uuid="x" data-entity-substitution="media_download" title="KCCQ">Kansas City Cardiomyopathy Questionnaire (KCCQ)</a></td><td>Cardiology</td><td>Clinical Outcome Assessment</td><td>10/19/2017</td></tr></tbody></table></div></div>"""

_SEBQ_IVIES = """MDDT SUMMARY OF EVIDENCE AND BASIS OF QUALIFICATION DECISION FOR
 IMAGE VIEWER INTEGRITY EVALUATION SYSTEM FOR WHOLE-SLIDE IMAGING DEVICES


BACKGROUND

      MDDT Name: Image Viewer Integrity Evaluation System (IVIES)
      Submission Number: U240030/S001
      Date of Submission: 09/30/2025
                  Wei-Chung Cheng, PhD
                  Office of Science and Engineering Laboratories
                  Center for Devices and Radiological Health
                  U.S. Food and Drug Administration
                  10903 New Hampshire Avenue
                  Silver Spring, MD 20993
                  Email: Wei-Chung.Cheng@fda.hhs.gov

TOOL DESCRIPTION AND PRINCIPLE OF OPERATION

   The Image Viewer Integrity Evaluation System (IVIES) for whole-slide imaging devices is a Non-clinical
   Assessment Model (NAM), software tool that measures the output differences between two pathology whole slide

QUALIFIED CONTEXT OF USE

   The Image Viewer Integrity Evaluation System (IVIES) is a software tool qualified to assist with assessing
   whether a pathology whole slide image viewing software device can decode the same input image file and display
   pixelwise identical images when compared to the predicate whole slide imaging device image review
   manipulation software.
   IVIES is not suitable for evaluation of third-party viewers that intentionally alter the image rendering or the
   downstream components such as the computer environment or the display system.




   MDDT SEBQ IVIES (U240030)                                                                1 of 6
\fSUMMARY OF EVIDENCE TO SUPPORT QUALIFICATION

   a. Reliable predictions of the device performance using ΔE00
"""

_SEBQ_KCCQ = """             MEDICAL DEVICE DEVELOPMENT TOOL (MDDT) QUALIFICATION
                             DECISION SUMMARY FOR
              KANSAS CITY CARDIOMYOPATHY QUESTIONNAIRE (KCCQ)

BACKGROUND

       MDDT NAME: KANSAS CITY CARDIOMYOPATHY QUESTIONNAIRE (KCCQ)

       SUBMISSION NUMBER: MDDT020

       DATE OF SUBMISSION: JANUARY 19, 2016

       CONTACT: JOHN SPERTUS, MD, MPH
                SAINT LUKE’S MID AMERICA HEART INSTITUTE
                4401 WORNALL ROAD
                KANSAS CITY, MO 64111
                EMAIL: SPERTUSJ@UMKC.EDU

TOOL DESCRIPTION AND PRINCIPLE OF OPERATION
The Kansas City Cardiomyopathy Questionnaire (KCCQ) is a 23-item self-administered

QUALIFIED CONTEXT OF USE
The paper self -administered version of the 23-item KCCQ questionnaire is used for quantifying
patients’ health status, including the symptoms (frequency and burden), physical and social
limitations, and quality of life impact due to the heart failure syndrome. The instrument can be
used in feasibility and pivotal studies of patients with symptomatic heart failure (e.g. AHA/ACC
Stages of Heart Failure C and D, or NYHA II-IV).

"""


def _selftest():
    import tempfile
    rows = parse_page(_PAGE)
    assert len(rows) == 3 and rows[0]["href"] == SITE + "/media/191032/download?attachment", rows
    assert rows[1]["areas"] == ["Toxicology", "Biocompatibility"]
    assert canonical_sebq_url(rows[1]["href"]) == SITE + "/media/163472/download?attachment"
    assert category("Non-clinical Assessment Model") == category("Nonclinical Assessment Model") \
        == "Nonclinical Assessment Model"
    assert parse_date("JANUARY 19, 2016") == "2016-01-19" and parse_date("October 3rd, 2023") == "2023-10-03"
    assert parse_date("09/30/2025") == "2025-09-30" and parse_date("soon") is None
    s = parse_sebq(_SEBQ_IVIES)
    assert s["submission"] == "U240030/S001" and s["date_raw"] == "09/30/2025", s
    assert s["organization"] == ("Office of Science and Engineering Laboratories, Center for Devices and "
                                 "Radiological Health, U.S. Food and Drug Administration"), s["organization"]
    assert s["context_of_use"].startswith("The Image Viewer Integrity Evaluation System (IVIES) is a software")
    assert "1 of 6" not in s["context_of_use"] and "display system." in s["context_of_use"]
    assert "Cheng" not in json.dumps(s["organization"])
    k = parse_sebq(_SEBQ_KCCQ)
    assert k["submission"] == "MDDT020" and k["organization"] == "SAINT LUKE’S MID AMERICA HEART INSTITUTE", k
    assert k["context_of_use"].endswith("NYHA II-IV).")
    # contact blocks of /media/177482, /media/178230 and /media/150166 (pdftotext lines)
    assert organization_lines(["Fabio Badilini, Director Center for Physiologic Research",
                               "Center for Physiologic Research,",
                               "Division of Cardiology, University of California San Francisco,",
                               "505 Parnassus Avenue, Room M1182A, Box 0124", "San Francisco, CA 94143-0124",
                               "Email: fabio.badilini@ucsf.edu"]) == \
        "Center for Physiologic Research, Division of Cardiology, University of California San Francisco"
    assert organization_lines(["Sam Surette", "Apple, Inc.", "One Apple Park Way", "Cupertino, CA 95014",
                               "Email: ssurette@apple.com"]) == "Apple, Inc."
    assert organization_lines(["AMERICAN ACADEMY OF OPHTHALMOLOGY, FLORA LUM, M.D., VICE",
                               "PRESIDENT, QUALITY AND DATA SCIENCE (FLUM@AAO.ORG; 415/516-8592)"]) == \
        "AMERICAN ACADEMY OF OPHTHALMOLOGY"
    assert parse_date("4 November 2024") == "2024-11-04" and parse_date("AUGUST 01 2018") == "2018-08-01"
    assert record_key(s, "x") == "U240030" and record_key({"submission": "Q191161-S001"}, "x") == "Q191161"
    assert record_key(None, "CHRIS") == "CHRIS"
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "sebq").mkdir()
        (t / "pages" / "mddt_page.html").write_text(_PAGE, encoding="utf-8")
        (t / "sebq" / "191032.txt").write_text(_SEBQ_IVIES, encoding="utf-8")
        (t / "sebq" / "108301.txt").write_text(_SEBQ_KCCQ, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-11-03", files=[]).write(t / "manifest.json")
        st = transform(t, t / "mddt.ttl")
        ttl = (t / "mddt.ttl").read_text(encoding="utf-8")
    assert st.records == 3 and st.skipped == 0, st
    assert f"<{mddt_iri('U240030')}>" in ttl and mddt_iri("U240030").endswith("/id/mddt/u240030")
    assert f"<{mddt_iri('MDDT020')}>" in ttl and f"<{mddt_iri('CHemical RISk Calculator (CHRIS) - Color Additives')}>" in ttl
    assert 'ex:mddtCategory "Nonclinical Assessment Model"' in ttl and 'ex:qualificationDate "2026-02-03"^^xsd:date' in ttl
    assert 'ex:dateReceived "2016-01-19"^^xsd:date' in ttl and 'ex:mddtSubmissionNumber "U240030/S001"' in ttl
    assert 'ex:productArea "Toxicology"' in ttl and 'ex:summaryUrl "https://www.fda.gov/media/191032/download?attachment"' in ttl
    assert "SPERTUS" not in ttl and "UMKC" not in ttl and "Wei-Chung" not in ttl and "WORNALL" not in ttl
    assert ttl.count("rdfs:label") == 3
    assert 'ex:sourceDataset "cdrh/mddt"' in ttl and 'ex:retrievedAt "2026-11-03"^^xsd:date' in ttl
    try:
        parse_page(_PAGE.replace("Date Qualified", "Qualified On"))
    except ValueError:
        pass
    else:
        raise AssertionError("schema drift not detected")
    print(f"mddt.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
