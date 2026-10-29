#!/usr/bin/env python3
"""
patch_event_ttl.py -- apply the 2026-10-11 event.py fixes to an ALREADY-BUILT event.ttl,
without re-transforming 26 million MAUDE reports (which needs ~75 GB of scratch space).

The changes are exactly what the updated event.py now writes:
  1. every ex:MAUDEReport gets rdfs:label "MAUDE <report number> (<event type>)"
  2. one-letter patient-outcome codes become FDA's words (common.outcome_label)
  3. product-code text that is not three letters is kept as ex:reportedProductCodeText;
     its ex:hasProductCode link, its ex:productCode value and its hub block are dropped
  4. hub blocks carry the device name as skos:altLabel instead of rdfs:label
  5. three-letter product codes typed in lower case ("psv") are upper-cased ("PSV") on the
     report and on hub blocks, and the report's ex:hasProductCode uses iri.product_code (v0.0.7)

The output is written as numbered plain-Turtle parts of about --part-bytes each (every part
has the prefix header), so the patched file never needs a second full copy on disk. After
each part closes, --ship (a shell command with {path} and {n}) is run; the part is deleted
only when the command succeeds.

    python3 scripts/patch_event_ttl.py event.ttl OUTDIR [--part-bytes 4e9] [--ship "cmd {path} {n}"]
    python3 scripts/patch_event_ttl.py --selftest
"""
import argparse
import os
import re
import shlex
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "pipeline"))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "pipeline", "sources"))
import common          # noqa: E402
import iri             # noqa: E402

SKOS = "@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n"
_PC_LINK = re.compile(r'^ex:hasProductCode <[^>]*/product-code/([^>]*)>$')
_PC_VAL = re.compile(r'^ex:productCode "(.*)"$')
_OUT = re.compile(r'^ex:patientOutcome "(.)"$')
_LIT = re.compile(r'^"(.*)"$')


def _pairs(lines):
    """Block body lines -> ['pred obj', ...] (terminators stripped)."""
    out = []
    for ln in lines:
        s = ln.strip()
        if s.endswith(" ;") or s.endswith(" ."):
            s = s[:-2]
        out.append(s)
    return out


def _render(subject, pairs):
    if not pairs:
        return ""
    body = "".join(f"    {p}{' ;' if i < len(pairs) - 1 else ' .'}\n" for i, p in enumerate(pairs))
    return f"{subject}\n{body}\n"


def _value(pairs, pred):
    for p in pairs:
        if p.startswith(pred + " "):
            m = _LIT.match(p[len(pred) + 1:])
            if m:
                return m.group(1)
    return None


def patch_block(block, stats):
    """One Turtle subject block (text, no trailing blank line) -> patched text ('' to drop)."""
    lines = block.split("\n")
    subject, pairs = lines[0], _pairs(lines[1:])
    if "a ex:ProductCode" in pairs:                                   # hub block
        code = _value(pairs, "ex:productCode")
        if code is None or not common.product_code(code):
            stats["hub_dropped"] += 1
            return ""
        pairs = [("skos:altLabel " + p[len("rdfs:label "):]) if p.startswith("rdfs:label ") else p for p in pairs]
        if code != common.product_code(code):                         # "psv" -> "PSV"
            pairs = [f'ex:productCode "{common.product_code(code)}"' if _PC_VAL.match(p) else p for p in pairs]
            stats["hub_code_upper"] += 1
        return _render(subject, pairs)
    if "a ex:MAUDEReport" not in pairs:
        return _render(subject, pairs)
    new, seen = [], set()
    for p in pairs:
        m = _PC_LINK.match(p)
        if m and not common.product_code(m.group(1)):
            stats["link_dropped"] += 1
            continue
        if m and m.group(1) != iri.slug(common.product_code(m.group(1))):
            p = f"ex:hasProductCode <{iri.product_code(common.product_code(m.group(1)))}>"
            stats["link_canonical"] += 1                             # e.g. ".../product-code/PSV"
        m = _PC_VAL.match(p)
        if m and not common.product_code(m.group(1)):
            p = f'ex:reportedProductCodeText "{m.group(1)}"'
            stats["code_text"] += 1
        elif m and m.group(1) != common.product_code(m.group(1)):
            code = common.product_code(m.group(1))                   # "psv" -> "PSV" (three letters, lower case)
            p = f'ex:productCode "{code}"'
            stats["code_upper"] += 1
            link = f"ex:hasProductCode <{iri.product_code(code)}>"
            if link not in seen and not any(_PC_LINK.match(q) and _PC_LINK.match(q).group(1) == iri.slug(code)
                                            for q in pairs):
                new.append(link)                                     # the link always uses the hub IRI
                seen.add(link)
        m = _OUT.match(p)
        if m:
            p = f"ex:patientOutcome {common.lit(common.outcome_label(m.group(1)))}"
            stats["outcome_mapped"] += 1
        if p not in seen:                                            # mapping can create duplicates
            seen.add(p)
            new.append(p)
    if not any(p.startswith("rdfs:label ") for p in new):
        rn = _value(new, "ex:reportNumber") or ""
        et = _value(new, "ex:eventType")
        lab = "MAUDE " + rn + (f" ({et})" if et else "")
        new.insert(1, f"rdfs:label {common.lit(lab)}")
        stats["labelled"] += 1
    return _render(subject, new)


def run(src, outdir, part_bytes=4_000_000_000, ship=None):
    os.makedirs(outdir, exist_ok=True)
    stats = {k: 0 for k in ("blocks", "labelled", "outcome_mapped", "code_text", "code_upper", "link_canonical",
                            "link_dropped", "hub_dropped", "hub_code_upper")}
    header, part, size, fo, path = [], 0, 0, None, None

    def close():
        nonlocal fo
        fo.close()
        fo = None
        if ship:
            cmd = ship.format(path=shlex.quote(path), n=f"{part:02d}")
            if subprocess.run(cmd, shell=True).returncode != 0:
                sys.exit(f"ship failed for part {part}: {cmd}")
            os.remove(path)
        print(f"  part {part:02d} done ({size:,} bytes)", flush=True)

    buf = []
    with open(src, encoding="utf-8") as fi:
        for ln in fi:
            if ln.startswith("@prefix"):
                if ln not in header:
                    header.append(ln)
                continue
            if ln.strip():
                buf.append(ln.rstrip("\n"))
                continue
            if not buf:
                continue
            text = patch_block("\n".join(buf), stats)
            buf = []
            stats["blocks"] += 1
            if not text:
                continue
            if fo is None:
                part += 1
                path = os.path.join(outdir, f"event_{part:02d}.ttl")
                fo = open(path, "w", encoding="utf-8")
                hdr = header + ([SKOS] if SKOS not in header else [])
                fo.writelines(hdr + ["\n"])
                size = 0
            fo.write(text)
            size += len(text.encode("utf-8"))
            if size >= part_bytes:
                close()
        if buf:
            text = patch_block("\n".join(buf), stats)
            stats["blocks"] += 1
            if text:
                if fo is None:
                    part += 1
                    path = os.path.join(outdir, f"event_{part:02d}.ttl")
                    fo = open(path, "w", encoding="utf-8")
                    fo.writelines(header + ([SKOS] if SKOS not in header else []) + ["\n"])
                    size = 0
                fo.write(text)
    if fo is not None:
        close()
    print("patch_event_ttl:", stats, f"parts={part}", flush=True)
    return stats, part


def _selftest():
    import tempfile
    src_text = (
        "@prefix ex: <https://bmedesign.org/medical-device-kg/ns/> .\n\n"
        "<https://x/id/report/1-2000-1>\n    a ex:MAUDEReport ;\n    ex:reportNumber \"1-2000-1\" ;\n"
        "    ex:eventType \"Injury\" ;\n    ex:patientOutcome \"R\" ;\n    ex:patientOutcome \"Required Intervention\" ;\n"
        "    ex:hasProductCode <https://x/id/product-code/269> ;\n    ex:productCode \"269\" ;\n"
        "    ex:hasProductCode <https://x/id/product-code/frn> ;\n    ex:productCode \"FRN\" .\n\n"
        "<https://x/id/product-code/269>\n    a ex:ProductCode ;\n    rdfs:label \"INFUSION PUMP\" ;\n    ex:productCode \"269\" .\n\n"
        "<https://x/id/product-code/frn>\n    a ex:ProductCode ;\n    rdfs:label \"Pump, Infusion\" ;\n    ex:productCode \"FRN\" .\n\n"
        # lower-case three-letter code (v0.0.7 full test: 26 reports, e.g. "psv")
        "<https://x/id/report/2-2001-1>\n    a ex:MAUDEReport ;\n    ex:reportNumber \"2-2001-1\" ;\n"
        "    ex:hasProductCode <https://x/id/product-code/PSV> ;\n    ex:productCode \"psv\" .\n\n"
        "<https://x/id/report/3-2001-1>\n    a ex:MAUDEReport ;\n    ex:reportNumber \"3-2001-1\" ;\n"
        "    ex:productCode \"dqy\" .\n\n"
        "<https://x/id/product-code/psv>\n    a ex:ProductCode ;\n    rdfs:label \"Thing\" ;\n    ex:productCode \"psv\" .\n"
    )
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "event.ttl")
        open(src, "w", encoding="utf-8").write(src_text)
        stats, parts = run(src, os.path.join(d, "out"), part_bytes=10**9)
        out = open(os.path.join(d, "out", "event_01.ttl"), encoding="utf-8").read()
    assert parts == 1 and stats["labelled"] == 3 and stats["hub_dropped"] == 1, stats
    assert stats["code_upper"] == 2 and stats["hub_code_upper"] == 1 and stats["link_canonical"] == 1, stats
    r2 = out.split("<https://x/id/report/2-2001-1>", 1)[1].split("\n\n", 1)[0]
    assert 'ex:productCode "PSV"' in r2 and '"psv"' not in r2, r2
    assert r2.count("ex:hasProductCode") == 1 and f"<{iri.product_code('PSV')}>" in r2, r2
    r3 = out.split("<https://x/id/report/3-2001-1>", 1)[1].split("\n\n", 1)[0]
    assert 'ex:productCode "DQY"' in r3 and f"ex:hasProductCode <{iri.product_code('DQY')}>" in r3, r3
    assert '"psv"' not in out and 'ex:productCode "PSV" .' in out                    # hub literal too
    assert 'rdfs:label "MAUDE 1-2000-1 (Injury)"' in out
    assert out.count('ex:patientOutcome "Required Intervention"') == 1 and 'ex:patientOutcome "R"' not in out
    assert "product-code/269>" not in out and 'ex:reportedProductCodeText "269"' in out
    assert 'skos:altLabel "Pump, Infusion"' in out and 'rdfs:label "Pump, Infusion"' not in out
    assert out.startswith("@prefix ex:") and "@prefix skos:" in out
    print("patch_event_ttl.py self-test PASSED.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("outdir")
    ap.add_argument("--part-bytes", type=float, default=4e9)
    ap.add_argument("--ship")
    a = ap.parse_args()
    run(a.src, a.outdir, int(a.part_bytes), a.ship)
