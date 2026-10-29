#!/usr/bin/env python3
"""
ai_devices.py -- FDA's "Artificial Intelligence-Enabled Medical Devices" list -> RDF.

PAGE  https://www.fda.gov/medical-devices/software-medical-device-samd/artificial-intelligence-enabled-medical-devices
      (redirects to .../artificial-intelligence-enabled-medical-devices/list-artificial-intelligence-enabled-medical-devices)
      The page offers the same table as CSV, Excel and XML downloads. This module reads the CSV:
          https://www.fda.gov/media/178541/download?attachment   (aiml-devices-csv.csv)
      Checked October 28, 2026: Last-Modified September 4, 2026; 1,614 rows (1,553 510(k),
      40 De Novo, 21 PMA or PMA supplement), no duplicate submission numbers.
      Header (verified): Date of Final Decision,Submission Number,Device,Company,Panel (Lead),
                         Primary Product Code

MODEL
  Each row is an ex:AIEnabledDeviceListing at iri.ai_enabled_device(submission number), with
      ex:premarketSubmissionNumber, ex:decisionDate, ex:deviceName, ex:companyName,
      ex:reviewPanel (FDA's "Panel (Lead)"), ex:productCode + ex:hasProductCode,
      ex:hasApplicantFirm <iri.firm_name(normalize_org(company))>,
      ex:listedSubmission -> the premarket record node, chosen by prefix:
          K...            iri.k510(number)
          DEN...          iri.de_novo(number)   (denovo.py's node)
          P...[/S...]     iri.pma(number[, supplement])
          H...            iri.hde(number)       (none in the list today)
  The submission number is the record key; FDA lists each submission once.

MINTER: iri.ai_enabled_device(submission_number)
    iri.ai_enabled_device('K253628')       -> .../id/ai-enabled-device/k253628
    iri.ai_enabled_device('P130020/S005')  -> .../id/ai-enabled-device/p130020_s005
"""
import os
import re
import sys
import csv
import io
import json
import time
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
from identity import normalize_org   # noqa: E402

NAME = "ai_devices"
DATASET = "cdrh/ai-enabled-devices"
PAGE_URL = ("https://www.fda.gov/medical-devices/software-medical-device-samd/"
            "artificial-intelligence-enabled-medical-devices")
CSV_URL = "https://www.fda.gov/media/178541/download?attachment"
CSV_NAME = "aiml-devices.csv"
UA = "medical-device-kg/rebuild (Hofstra University; 1 request per second)"
HEADER = ["Date of Final Decision", "Submission Number", "Device", "Company",
          "Panel (Lead)", "Primary Product Code"]
_SUB = re.compile(r"^(K\d{6}|DEN\d{6}|[PH]\d{6})(?:/(S\d{3,4}))?$")


def submission_node(number):
    """The existing premarket node for a submission number, or None if the shape is unknown."""
    m = _SUB.match(number or "")
    if not m:
        return None
    base_no, supp = m.group(1), m.group(2)
    if base_no.startswith("K"):
        return iri.k510(base_no) if not supp else None
    if base_no.startswith("DEN"):
        return iri.de_novo(base_no) if not supp else None
    if base_no.startswith("P"):
        return iri.pma(base_no, supp)
    return iri.hde(base_no, supp)


# ===========================================================================
#  Fetch: one file, polite (UA, retries with backoff)
# ===========================================================================
def _get(url, tries=5, delay=1.0):
    err = None
    for i in range(tries):
        time.sleep(delay if i == 0 else 2 ** i)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read(), r.headers.get("Last-Modified", "") or ""
        except Exception as e:                 # network error or 5xx: back off and retry
            err = e
            code = getattr(e, "code", None)
            if code is not None and code < 500 and code != 429:
                break
    raise RuntimeError(f"{url}: {err}")


def fetch(dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    body, last_mod = _get(CSV_URL)
    _parse(body.decode("utf-8-sig"))           # refuse to cache a file whose header changed
    p = dest / CSV_NAME
    tmp = p.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(p)
    f = base.FileRef(url=CSV_URL, path=CSV_NAME, sha256=base.sha256_file(p), bytes=p.stat().st_size,
                     etag=last_mod)
    man = base.Manifest(source=NAME, dataset=DATASET, retrieved_at=base.today(), files=[f])
    man.write(dest / "manifest.json")
    print(f"  ai_devices: {CSV_URL} -> {p} ({p.stat().st_size:,} bytes, Last-Modified {last_mod})")
    return man


# ===========================================================================
#  Parse + transform
# ===========================================================================
def _parse(text):
    rdr = csv.reader(io.StringIO(text))
    hdr = [h.strip() for h in next(rdr, [])]
    if hdr != HEADER:
        # schema drift must stop the build, not silently produce empty triples
        raise ValueError(f"AI-enabled device CSV header changed: {hdr}")
    out = []
    for r in rdr:
        if not any(c.strip() for c in r):
            continue
        r = (r + [""] * len(HEADER))[:len(HEADER)]
        out.append({h: common.norm_text(v) for h, v in zip(HEADER, r)})
    return out


def transform(src, out, retrieved_at=None):
    src = Path(src)
    if retrieved_at is None and (src / "manifest.json").exists():
        try:
            retrieved_at = base.Manifest.read(src / "manifest.json").retrieved_at
        except Exception:
            retrieved_at = None
    rows = _parse((src / CSV_NAME).read_text(encoding="utf-8-sig"))
    st = base.Stats(source=NAME)
    seen, firms, hub_codes, kinds, unlinked = set(), {}, set(), {}, []
    rows.sort(key=lambda r: (r["Submission Number"] or ""))
    with base.TurtleWriter(out) as w:
        for r in rows:
            num = (r["Submission Number"] or "").upper().replace(" ", "")
            if not num or num in seen:
                st.skipped += 1
                continue
            seen.add(num)
            pc = common.product_code(r["Primary Product Code"])
            company = r["Company"]
            nf = normalize_org(company)
            sub = submission_node(num)
            kind = re.match(r"[A-Z]+", num).group(0) if re.match(r"[A-Z]+", num) else "?"
            kinds[kind] = kinds.get(kind, 0) + 1
            pairs = [("a", "ex:AIEnabledDeviceListing"),
                     ("rdfs:label", common.label(num, r["Device"])),
                     ("ex:premarketSubmissionNumber", common.lit(num)),
                     ("ex:decisionDate", common.date_lit(r["Date of Final Decision"])),
                     ("ex:deviceName", common.lit(r["Device"])),
                     ("ex:companyName", common.lit(company)),
                     ("ex:reviewPanel", common.lit(r["Panel (Lead)"])),
                     ("ex:productCode", common.lit(pc)),
                     ("ex:reportedProductCodeText",
                      common.lit(r["Primary Product Code"]) if r["Primary Product Code"] and not pc else None)]
            if pc:
                pairs.append(("ex:hasProductCode", f"<{iri.product_code(pc)}>"))
                hub_codes.add(pc)
            if sub:
                pairs.append(("ex:listedSubmission", f"<{sub}>"))
            else:
                unlinked.append(num)
            if nf:
                pairs.append(("ex:hasApplicantFirm", f"<{iri.firm_name(nf)}>"))
                firms.setdefault(nf, company)
            pairs += common.provenance(DATASET, num, source_url=PAGE_URL, retrieved_at=retrieved_at)
            w.block(iri.ai_enabled_device(num), pairs)
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hub_codes):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    Path(out).with_suffix(".report.json").write_text(json.dumps(
        {"records": st.records, "by_prefix": kinds, "unlinked_submission_numbers": unlinked,
         "product_codes": len(hub_codes), "firms": len(firms)}, indent=1), encoding="utf-8")
    print(f"  ai_devices: {st.records} listings {kinds}; {len(unlinked)} without a submission link")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  SELF-TEST -- lines copied verbatim from aiml-devices-csv.csv (October 28, 2026)
# ===========================================================================
_CSV = ("﻿Date of Final Decision,Submission Number,Device,Company,Panel (Lead),Primary Product Code\r\n"
        "06/29/2026,K253628,\"Auto-Seg (SO-0012), Spine Auto-Seg (SO-0012)\",\"Agada Medical, Ltd.\",Radiology,QIH\r\n"
        "06/26/2026,K260714,MammoScreen® (5),Therapixel,Radiology,QDQ\r\n"
        "05/22/2026,DEN250057,PCWP Analysis Software,\"Cardiosense, Inc.\",Cardiovascular,SIF\r\n"
        "11/21/2025,P130020/S005,Senographe Pristina 3D,GE Healthcare,Radiology,OTE\r\n"
        "04/28/2023,P210011,xT CDx,\"Tempus Labs, Inc.\",Pathology,PQP\r\n")


def _selftest():
    import tempfile
    assert submission_node("K253628") == iri.k510("K253628")
    assert submission_node("DEN250057") == iri.de_novo("DEN250057")
    assert submission_node("P130020/S005") == iri.pma("P130020", "S005")
    assert submission_node("P210011") == iri.pma("P210011")
    assert submission_node("X1") is None
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / CSV_NAME).write_bytes(_CSV.encode("utf-8"))
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-28", files=[]).write(t / "manifest.json")
        st = transform(t, t / "ai_devices.ttl")
        ttl = (t / "ai_devices.ttl").read_text(encoding="utf-8")
    assert st.records == 5 and st.skipped == 0, st
    s = f"<{iri.ai_enabled_device('K253628')}>"
    assert s in ttl and s.endswith("/ai-enabled-device/k253628>")
    assert f"ex:listedSubmission <{iri.k510('K253628')}>" in ttl
    assert f"ex:listedSubmission <{iri.de_novo('DEN250057')}>" in ttl
    assert f"ex:listedSubmission <{iri.pma('P130020', 'S005')}>" in ttl
    assert f"<{iri.ai_enabled_device('P130020/S005')}>" in ttl
    assert 'ex:decisionDate "2026-06-29"^^xsd:date' in ttl
    assert 'ex:deviceName "Auto-Seg (SO-0012), Spine Auto-Seg (SO-0012)"' in ttl
    assert 'ex:deviceName "MammoScreen® (5)"' in ttl                     # UTF-8 kept
    assert 'ex:reviewPanel "Radiology"' in ttl and 'ex:companyName "Agada Medical, Ltd."' in ttl
    assert f"ex:hasProductCode <{iri.product_code('QIH')}>" in ttl
    assert f"<{iri.product_code('QIH')}>\n    a ex:ProductCode" in ttl
    assert f"ex:hasApplicantFirm <{iri.firm_name(normalize_org('Tempus Labs, Inc.'))}>" in ttl
    assert 'rdfs:label "K253628: Auto-Seg (SO-0012), Spine Auto-Seg (SO-0012)"' in ttl
    assert 'ex:sourceDataset "cdrh/ai-enabled-devices"' in ttl and 'ex:retrievedAt "2026-10-28"^^xsd:date' in ttl
    try:
        _parse("A,B\n1,2")
    except ValueError:
        pass
    else:
        raise AssertionError("schema drift not detected")
    print(f"ai_devices.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fetch":
        fetch(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "transform":
        s = transform(sys.argv[2], sys.argv[3])
        print(f"transform: {s.records} records, {s.triples} triples, {s.skipped} skipped")
    else:
        _selftest()
