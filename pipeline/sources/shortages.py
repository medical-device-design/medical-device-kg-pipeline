#!/usr/bin/env python3
"""
shortages.py -- CDRH Medical Device Shortages List and Discontinuance List -> RDF (v0.0.7).

SOURCE (one page, two HTML tables; verified 2026-10-28 from the build VM)
    https://www.fda.gov/medical-devices/medical-device-supply-chain-and-shortages/medical-device-shortages-list
The older address .../medical-device-safety/medical-device-shortages-list now answers
404 (the empty body seen from another network). The page carries:
  * the Device Shortage List (section 506J): Category | Product Code (Description) |
    Availability and Estimated Shortage Duration | Additional Information |
    Reason for Interruption (per 506J) | Date (YYYY/MM/DD)  -- 6 rows on 2026-10-28;
    the Date cell lists "YYYY/MM/DD Initial" and later "YYYY/MM/DD Reverified" entries;
  * the Discontinuance List (section 506J(g)): Date Posted | Category | Product Code |
    Manufacturer Name | Device Trade Name | Reason for Discontinuance;
  * an "Update: <date>" callout naming product codes FDA removed from the shortage list.
The page's "Excel" button is DataTables' client-side export of these same HTML tables,
so the HTML is the complete published data. FDA links the 2020-2024 discontinuances
only to an archived copy on web.archive.org; that archive is NOT fetched (project rule).

STATUS ACROSS RUNS
Each run caches the page as pages/medical-device-shortages-list_<YYYY-MM-DD>.html and
transform() reads every cached snapshot, so a shortage that drops off the list keeps
its node with ex:shortageListStatus "removed from shortage list" and ex:lastSeenOnList.
Codes the latest callout says were removed also get that status with
ex:removedFromListDate (the callout date).

OUTPUT
    <device-shortage/<product code(s)>> a ex:DeviceShortage
        rdfs:label, ex:deviceCategory, ex:productCode, ex:hasProductCode, ex:deviceName,
        ex:shortageDuration, ex:shortageInformation, ex:relatedPageUrl*, ex:shortageReason*,
        ex:shortageInitialDate, ex:shortageReverifiedDate*, ex:shortageDateText,
        ex:shortageListStatus, ex:lastSeenOnList, ex:removedFromListDate
    <device-discontinuance/<16 hex>> a ex:DeviceDiscontinuance
        rdfs:label, ex:datePosted, ex:deviceCategory, ex:productCode, ex:hasProductCode,
        ex:deviceName, ex:manufacturerName, ex:hasManufacturerFirm, ex:tradeName,
        ex:discontinuanceReason, ex:shortageListStatus "permanent discontinuance",
        ex:lastSeenOnList
    plus FirmName nodes and typed ProductCode hub stubs.
Free text (additional information, discontinuance reason) passes through
scrub.scrub_text; manufacturer names are firms (kept), no persons are listed.

MINTERS (pipeline/iri.py):
    iri.device_shortage(product_codes): a Medical Device Shortages List entry, keyed on its
        product code(s) joined with "_".   iri.device_shortage(['HBA']) -> .../id/device-shortage/hba
    iri.device_discontinuance(key): a Discontinuance List row; FDA gives no row id, so the key is
        the first 16 hex of sha1("date posted|product codes|manufacturer|trade name").
        -> .../id/device-discontinuance/0123456789abcdef
"""
import os
import re
import sys
import json
import hashlib
import html as htmllib
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
import _cfweb     # noqa: E402
from identity import normalize_org   # noqa: E402

NAME = "shortages"
DATASET = "cdrh/device-shortages"
SITE = "https://www.fda.gov"
PAGE_URL = SITE + "/medical-devices/medical-device-supply-chain-and-shortages/medical-device-shortages-list"
SNAP = "medical-device-shortages-list_{}.html"
SHORTAGE_HDR = ["Category", "Product Code (Description)", "Availability and Estimated Shortage Duration",
                "Additional Information", "Reason for Interruption (per 506J)", "Date (YYYY/MM/DD)"]
DISC_HDR = ["Date Posted", "Category", "Product Code", "Manufacturer Name", "Device Trade Name",
            "Reason for Discontinuance"]
ST_CURRENT = "on shortage list"
ST_REMOVED = "removed from shortage list"
ST_DISC = "permanent discontinuance"

_TABLE = re.compile(r"(?is)<table\b.*?</table>")
_TR = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr>")
_CELL = re.compile(r"(?is)<t([hd])\b[^>]*>(.*?)</t\1>")
_HREF = re.compile(r'(?i)href="([^"#][^"]*)"')
_DATE_ENTRY = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*([A-Za-z]+)?")
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]


# ===========================================================================
#  Fetch
# ===========================================================================
def fetch(dest, delay=1.0):
    dest = Path(dest)
    f = _cfweb.Fetcher(dest, delay=delay)
    day = base.today()
    name = SNAP.format(day)[:-5]                 # Fetcher adds ".html"
    body = f.get(PAGE_URL, name)                 # one snapshot per day, reused within the day
    if "Device Shortage List" not in body or "<table" not in body:
        raise _cfweb.Unavailable(f"{PAGE_URL}: page has no shortage table (empty or changed body)")
    (dest / "fetch_log.json").write_text(json.dumps(
        {"date": day, "fetched_now": f.fetched, "cached": f.cached, "bytes": len(body)}), encoding="utf-8")
    print(f"  shortages: snapshot {day} ({len(body):,} bytes; fetched {f.fetched}, cached {f.cached})")
    return base.Manifest(source=NAME, dataset=DATASET, retrieved_at=day, files=[])


# ===========================================================================
#  Parse
# ===========================================================================
def _txt(fragment):
    t = _cfweb.one_line(fragment)
    return t.strip() if t else None


def _items(fragment):
    lis = re.findall(r"(?is)<li\b[^>]*>(.*?)</li>", fragment or "")
    vals = [_txt(x) for x in lis] if lis else [_txt(fragment)]
    return [v for v in vals if v]


def _abs(u):
    u = htmllib.unescape(u).strip()
    return SITE + u if u.startswith("/") else u


def _tables(html):
    """-> (header list, [row cells html]) for every table on the page."""
    out = []
    for t in _TABLE.findall(html):
        rows = _TR.findall(t)
        if not rows:
            continue
        hdr = [_txt(c[1]) or "" for c in _CELL.findall(rows[0])]
        body = [[c[1] for c in _CELL.findall(r)] for r in rows[1:]]
        out.append((hdr, [b for b in body if b]))
    return out


def split_code_cell(text):
    """'HBA (Neurosurgical pattie)' -> (['HBA'], 'Neurosurgical pattie');
    'CAF (NEBULIZER (DIRECT PATIENT INTERFACE)' -> (['CAF'], 'NEBULIZER (DIRECT PATIENT INTERFACE)')."""
    t = (text or "").strip()
    i = t.find("(")
    head, desc = (t[:i], t[i + 1:]) if i >= 0 else (t, "")
    codes = []
    for c in re.findall(r"\b[A-Z]{3}\b", head):
        if c not in codes:
            codes.append(c)
    desc = desc.strip()
    if desc.endswith(")") and desc.count("(") < desc.count(")"):
        desc = desc[:-1]
    return codes, desc.strip() or None


def _clean(text):
    t, _ = scrub.scrub_text(text)
    return t


def parse_page(html):
    """-> dict(shortages=[...], discontinuances=[...], update_date, removed=[(codes, desc)])."""
    res = {"shortages": [], "discontinuances": [], "update_date": None, "removed": []}
    found = set()
    for hdr, rows in _tables(html):
        if hdr == SHORTAGE_HDR:
            found.add("s")
            for c in rows:
                if len(c) != 6:
                    continue
                codes, desc = split_code_cell(_txt(c[1]))
                dates = [(f"{y}-{int(m):02d}-{int(d):02d}", (k or "").lower())
                         for y, m, d, k in _DATE_ENTRY.findall(_txt(c[5]) or "")]
                res["shortages"].append({
                    "category": _txt(c[0]), "codes": codes, "desc": desc, "code_text": _txt(c[1]),
                    "duration": _txt(c[2]), "info": _clean(_txt(c[3])),
                    "links": [_abs(u) for u in _HREF.findall(c[3])],
                    "reasons": _items(c[4]), "date_text": _txt(c[5]), "dates": dates})
        elif hdr == DISC_HDR:
            found.add("d")
            for c in rows:
                if len(c) != 6:
                    continue
                codes, desc = split_code_cell(_txt(c[2]))
                res["discontinuances"].append({
                    "posted": _txt(c[0]), "category": _txt(c[1]), "codes": codes, "desc": desc,
                    "code_text": _txt(c[2]), "manufacturer": _txt(c[3]), "trade": _txt(c[4]),
                    "reason": _clean(_txt(c[5]))})
    if found != {"s", "d"}:          # header drift must stop the build, not produce empty output
        raise ValueError(f"shortage page tables changed; found {sorted(found)} of ['d', 's']")
    m = re.search(r"(?is)<div class=\"callout\">(.*?)</div>", html)
    if m:
        call = _txt(m.group(1)) or ""
        u = re.search(r"Update:\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", call)
        if u and u.group(1).lower() in _MONTHS:
            res["update_date"] = f"{u.group(3)}-{_MONTHS.index(u.group(1).lower()) + 1:02d}-{int(u.group(2)):02d}"
        for desc, codes in re.findall(r"removing\s+([^()]*?)\s*\(product codes?\s+([A-Z]{3}(?:(?:\s*,\s*|\s+and\s+|"
                                      r"\s*,\s*and\s+)[A-Z]{3})*)\)", call):
            res["removed"].append((re.findall(r"[A-Z]{3}", codes), desc.strip() or None))
    return res


def disc_key(d):
    raw = "|".join([d["posted"] or "", ",".join(d["codes"]) or (d["code_text"] or ""),
                    d["manufacturer"] or "", d["trade"] or ""])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def snapshots(src):
    pages = Path(src) / "pages"
    out = []
    for p in sorted(pages.glob("medical-device-shortages-list_*.html")):
        m = re.search(r"_(\d{4}-\d{2}-\d{2})\.html$", p.name)
        if m:
            out.append((m.group(1), p))
    return out


# ===========================================================================
#  Transform
# ===========================================================================
def transform(src, out, retrieved_at=None):
    snaps = snapshots(src)
    if not snaps:
        raise FileNotFoundError(f"no shortage-list snapshot under {Path(src) / 'pages'}")
    latest_day = snaps[-1][0]
    st = base.Stats(source=NAME)
    short, short_seen, disc, disc_seen = {}, {}, {}, {}
    removed, update_date = {}, None
    for day, p in snaps:                                  # oldest first; later snapshots win
        d = parse_page(p.read_text(encoding="utf-8", errors="replace"))
        for s in d["shortages"]:
            if not s["codes"]:
                st.skipped += 1
                continue
            k = "_".join(s["codes"])
            short[k], short_seen[k] = s, day
        # FDA repeats some rows: exactly (13 of 236 on 2026-10-28) or with a second category
        # or reason. Rows sharing date, codes, manufacturer and trade name are one
        # discontinuance; their differing categories and reasons are all kept.
        merged = {}
        for x in d["discontinuances"]:
            k = disc_key(x)
            m = merged.setdefault(k, dict(x, category=[], reason=[]))
            for f in ("category", "reason"):
                if x[f] and x[f] not in m[f]:
                    m[f].append(x[f])
        for k, m in merged.items():
            disc[k], disc_seen[k] = m, day
        if day == latest_day:
            update_date = d["update_date"]
            for codes, desc in d["removed"]:
                removed["_".join(codes)] = (codes, desc)
    hubs, firms, counts = set(), {}, {ST_CURRENT: 0, ST_REMOVED: 0, ST_DISC: 0}
    with base.TurtleWriter(out) as w:
        for k in sorted(set(short) | set(removed)):
            s = short.get(k)
            if s is None:                                 # named only in the removal callout
                codes, desc = removed[k]
                s = {"codes": codes, "desc": desc}
            current = k in short and short_seen[k] == latest_day and k not in removed
            status = ST_CURRENT if current else ST_REMOVED
            counts[status] += 1
            seen = short_seen.get(k)
            label = f"Device shortage: {', '.join(s['codes'])}" + (f" ({s['desc']})" if s.get("desc") else "")
            pairs = [("a", "ex:DeviceShortage"), ("rdfs:label", common.lit(label)),
                     ("ex:deviceCategory", common.lit(s.get("category")))]
            for c in s["codes"]:
                pc = common.product_code(c)
                if pc:
                    hubs.add(pc)
                    pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
            pairs += [("ex:deviceName", common.lit(s.get("desc"))),
                      ("ex:shortageDuration", common.lit(s.get("duration"))),
                      ("ex:shortageInformation", common.lit(s.get("info")))]
            pairs += [("ex:relatedPageUrl", common.lit(u)) for u in s.get("links", [])]
            pairs += [("ex:shortageReason", common.lit(r)) for r in s.get("reasons", [])]
            for day, kind in s.get("dates", []):
                if kind == "initial":
                    pairs.append(("ex:shortageInitialDate", common.date_lit(day)))
                elif kind.startswith("reverif"):
                    pairs.append(("ex:shortageReverifiedDate", common.date_lit(day)))
            pairs += [("ex:shortageDateText", common.lit(s.get("date_text"))),
                      ("ex:shortageListStatus", common.lit(status)),
                      ("ex:lastSeenOnList", common.date_lit(seen)),
                      ("ex:removedFromListDate", common.date_lit(update_date) if k in removed else None)]
            pairs += common.provenance(DATASET, f"shortage/{k}", source_url=PAGE_URL + "#shortage",
                                       retrieved_at=seen or retrieved_at or latest_day)
            w.block(iri.device_shortage(s["codes"]), list(dict.fromkeys(pairs)))
            st.records += 1
        for k in sorted(disc):
            x = disc[k]
            counts[ST_DISC] += 1
            pairs = [("a", "ex:DeviceDiscontinuance"),
                     ("rdfs:label", common.lit(f"Device discontinuance: {x['trade'] or x['code_text']}"
                                               + (f" ({x['manufacturer']})" if x["manufacturer"] else ""))),
                     ("ex:datePosted", common.date_lit(x["posted"]))]
            pairs += [("ex:deviceCategory", common.lit(c)) for c in x["category"]]
            for c in x["codes"]:
                pc = common.product_code(c)
                if pc:
                    hubs.add(pc)
                    pairs += [("ex:productCode", common.lit(pc)), ("ex:hasProductCode", f"<{iri.product_code(pc)}>")]
            if not x["codes"]:
                pairs.append(("ex:productCode", common.lit(x["code_text"])))   # kept as published
            nf = normalize_org(x["manufacturer"])
            pairs += [("ex:deviceName", common.lit(x["desc"])),
                      ("ex:manufacturerName", common.lit(x["manufacturer"])),
                      ("ex:hasManufacturerFirm", f"<{iri.firm_name(nf)}>" if nf else None),
                      ("ex:tradeName", common.lit(x["trade"]))]
            pairs += [("ex:discontinuanceReason", common.lit(r)) for r in x["reason"]]
            pairs += [("ex:shortageListStatus", common.lit(ST_DISC)),
                      ("ex:lastSeenOnList", common.date_lit(disc_seen[k]))]
            if nf:
                firms.setdefault(nf, x["manufacturer"])
            pairs += common.provenance(DATASET, f"discontinuance/{k}", source_url=PAGE_URL + "#discontinuance",
                                       retrieved_at=disc_seen[k])
            w.block(iri.device_discontinuance(k), list(dict.fromkeys(pairs)))
            st.records += 1
        for nf in sorted(firms):
            w.block(iri.firm_name(nf), common.firm_name_pairs(nf, firms[nf]))
        for pc in sorted(hubs):
            w.block(iri.product_code(pc), [("a", "ex:ProductCode"), ("ex:productCode", common.lit(pc))])
    st.triples = w.triples
    Path(out).with_suffix(".status.json").write_text(json.dumps(counts, sort_keys=True), encoding="utf-8")
    print(f"  shortages: {st.records} records {counts}; {len(hubs)} product codes; {len(firms)} firms")
    return st


def validate(ttl):
    return base.default_validate(ttl, shapes=os.path.join(os.path.dirname(_PIPELINE), "ontology", "shapes"))


# ===========================================================================
#  Self-test: verbatim fragments of the page fetched 2026-10-28 (rows trimmed)
# ===========================================================================
_PAGE = """<p></p><div class="callout">
	<p><strong>Update: October 26, 2026</strong></p><p>The FDA is updating the medical device shortage list (MDSL) by removing angiographic control syringes with rotating adapters (product code DXT), and adding implantable gastric electrical stimulation systems (product code LNQ) and discontinuances reported since June 2026.</p><p>
</p></div>
<hr><h2><a class="ck-anchor" id="shortage"></a>Device Shortage List</h2><p>Categories of devices that are currently on the MDSL:</p><div class="lcds-datatable lcds-datatable--ckeditor table-responsive"><table class="table" aria-readonly="true" data-excel-button="true" data-order="[ 0, &quot;desc&quot; ]" data-paging="true" data-searching="true" role="grid"><thead><tr role="row"><th scope="col">Category</th><th scope="col">Product Code (Description)</th><th scope="col">Availability and Estimated Shortage Duration</th><th scope="col">Additional Information</th><th scope="col">Reason for Interruption (per 506J)</th><th scope="col">Date (YYYY/MM/DD)</th></tr></thead><tbody><tr><td>Neurology – Neurosurgical, Neurointerventional and Neurodiagnostic Devices</td><td><strong>HBA</strong> (Neurosurgical pattie)</td><td>Estimated through Q4 2026</td><td>Neurosurgical patties, sponges &amp; strip products are in shortage. The FDA has issued a <a href="/medical-devices/letters-health-care-providers/disruptions-availability-neurosurgical-patties-sponges-and-strips-letter-health-care-providers" data-entity-type="node" data-entity-uuid="b6b33db0-c557-4e7e-b544-4b0888fe7b74" data-entity-substitution="canonical" title="Disruptions in Availability of Neurosurgical Patties, Sponges, and Strips - Letter to Health Care Providers">Letter to Health Care Providers</a> to provide recommendations to health care providers and facilities that use neurosurgical patties.</td><td><ul><li>Shortage or discontinuance of a component, part or accessory of the device.</li></ul></td><td>2026/05/06 initial</td></tr><tr><td>Cardiovascular - Circulatory Support, Structural and Vascular Devices</td><td><strong>BYS </strong>(Oxygenator, Long Term Support Greater Than 6 Hours)</td><td>Estimated through Q1 2027</td><td>Oxygenator devices intended for extracorporeal circulation are in shortage.</td><td><ul><li>Shortage or discontinuance of a component, part or accessory of the device.&nbsp;</li></ul></td><td><p>2023/09/11 Initial</p><p>2025/07/16 Reverified</p><p>2026/03/13<br>Reverified</p><p>2026/06/16<br>Reverified</p></td></tr></tbody></table></div><h2><a class="ck-anchor" id="discontinuance"></a>Discontinuance List</h2><p><a href="https://web.archive.org/web/20251224005704/https:/www.fda.gov/medical-devices/medical-device-supply-chain-and-shortages/medical-device-shortages-list">Discontinuances reported from 2020-2024 (archived)</a></p><div class="lcds-datatable lcds-datatable--ckeditor table-responsive"><table class="table" aria-readonly="true" data-excel-button="true" data-order="[ 0, &quot;desc&quot; ]" data-paging="true" data-searching="true" role="grid"><thead><tr role="row"><th scope="col">Date Posted</th><th>Category</th><th scope="col">Product Code</th><th scope="col">Manufacturer Name</th><th scope="col">Device Trade Name</th><th scope="col">Reason for Discontinuance</th></tr></thead><tbody><tr><td>10/26/2026</td><td>HEMATOLOGY - Immunology and Hemotology Devices</td><td>GKZ&nbsp; (COUNTER, DIFFERENTIAL CELL)</td><td>Abbott Laboratories</td><td>CELL-DYN Sapphire Systems (Model/Catalog # 08H00-01; 08H00-03)</td><td>Discontinuance of the manufacture of the device</td></tr><tr><td>10/26/2026</td><td>ANESTHESIOLOGY - Anesthesia, Respiratory, &amp; Sleep Devices</td><td>CAF (NEBULIZER (DIRECT PATIENT INTERFACE)</td><td>SunMed Group Holdings, LLC dba AirLife</td><td>Customed Nebulizer Kit (SKU # 1004391)</td><td>Discontinuance of the manufacture of the device; The device has not been commercially distributed in the US for an extended period.</td></tr><tr><td>10/26/2026</td><td>ANESTHESIOLOGY - Anesthesia, Respiratory, &amp; Sleep Devices</td><td>CAF (NEBULIZER (DIRECT PATIENT INTERFACE)</td><td>SunMed Group Holdings, LLC dba AirLife</td><td>Customed Nebulizer Kit (SKU # 1004391)</td><td>Discontinuance of the manufacture of the device</td></tr></tbody></table></div>"""


def _selftest():
    import tempfile
    assert split_code_cell("CAF (NEBULIZER (DIRECT PATIENT INTERFACE)") == (["CAF"], "NEBULIZER (DIRECT PATIENT INTERFACE)")
    assert split_code_cell("BYS (Oxygenator, Long Term Support Greater Than 6 Hours)")[0] == ["BYS"]
    d = parse_page(_PAGE)
    assert len(d["shortages"]) == 2 and len(d["discontinuances"]) == 3, d
    assert d["update_date"] == "2026-10-26" and d["removed"] == [(["DXT"], "angiographic control syringes with rotating adapters")], d["removed"]
    bys = d["shortages"][1]
    assert bys["dates"][0] == ("2023-09-11", "initial") and len(bys["dates"]) == 4, bys["dates"]
    assert d["shortages"][0]["links"][0].startswith("https://www.fda.gov/medical-devices/letters-health-care-providers/")
    try:
        parse_page(_PAGE.replace("Reason for Discontinuance", "Why"))
    except ValueError:
        pass
    else:
        raise AssertionError("header drift not detected")
    old = _PAGE.replace("BYS", "XYZ")             # an older snapshot listing a code that later dropped off
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "pages").mkdir()
        (t / "pages" / SNAP.format("2026-09-01")).write_text(old, encoding="utf-8")       # had code XYZ
        (t / "pages" / SNAP.format("2026-10-28")).write_text(_PAGE, encoding="utf-8")
        base.Manifest(source=NAME, dataset=DATASET, retrieved_at="2026-10-28", files=[]).write(t / "manifest.json")
        st = transform(t, t / "shortages.ttl")
        ttl = (t / "shortages.ttl").read_text(encoding="utf-8")
    # HBA, BYS current; XYZ dropped off (older snapshot); DXT removed per callout; 2 discontinuances
    assert st.records == 6, st
    hba = ttl.split(f"<{iri.device_shortage(['HBA'])}>")[1].split("\n\n")[0]
    assert 'ex:shortageListStatus "on shortage list"' in hba and 'ex:shortageInitialDate "2026-05-06"^^xsd:date' in hba
    xyz = ttl.split(f"<{iri.device_shortage(['XYZ'])}>")[1].split("\n\n")[0]
    assert 'ex:shortageListStatus "removed from shortage list"' in xyz and '"2026-09-01"^^xsd:date' in xyz
    dxt = ttl.split(f"<{iri.device_shortage(['DXT'])}>")[1].split("\n\n")[0]
    assert 'ex:removedFromListDate "2026-10-26"^^xsd:date' in dxt
    assert ttl.count("ex:shortageReverifiedDate") == 6              # BYS and XYZ, three each
    assert 'ex:datePosted "2026-10-26"^^xsd:date' in ttl and 'ex:shortageListStatus "permanent discontinuance"' in ttl
    assert f"ex:hasManufacturerFirm <{iri.firm_name(normalize_org('Abbott Laboratories'))}>" in ttl
    assert f"<{iri.product_code('GKZ')}>\n    a ex:ProductCode" in ttl
    assert ttl.count("ex:discontinuanceReason") == 3                # repeated CAF row: both reasons kept
    assert "web.archive.org" not in ttl
    print(f"shortages.py self-test PASSED ({st.records} records, {st.triples} triples).")


if __name__ == "__main__":
    _selftest()
