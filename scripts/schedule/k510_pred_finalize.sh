#!/bin/bash
# k510_pred_finalize.sh -- build and check k510_predicates.ttl from the 510(k) summary PDFs (no upload).
#
#   bash scripts/schedule/k510_pred_finalize.sh [first|final|test|auto]
#
#   first  the crawl has finished (started by k510_crawl_watch.sh): the steps of final, written to
#          work/out-2026-11-04/, but the release list and the upload plan are not touched. Its purpose is the
#          OCR backlog (k510_predicates.ocr_backlog.tsv: image-only PDFs still without OCR text); writes
#          monitor/k510_finalize_first.done. The watcher then writes monitor/k510_ocr_needed.md with the
#          command for the PI's PC (scripts\star\post_crawl_ocr.ps1: bulk OCR on Star HPC, then final).
#   final  transform every cached PDF into work/out-2026-11-04/k510_predicates.ttl, check it, and (only when
#          every check passes) add it to RELEASE_INPUTS.txt and to the ADD table of
#          work/fulltest-v007d/UPLOAD_PLAN.md (both backed up first by work/v007e/release_add.py).
#   test   the crawl is still running: the same steps on the PDFs downloaded so far, written to
#          work/tmp-v007e/pred-final-test/; the release list and the upload plan are not touched.
#   auto   (default) test while the crawl runs, final once it has stopped.
# OCR text: image-only PDFs are read from the OCR batch folders in $MDKG_OCR_DIR (":"-separated, wildcards
# allowed; common.sh sets work/ocr-* when it is not set), see k510_predicates.transform. Links read from OCR
# text carry ex:extractionMethod "ocrmypdf/tesseract+regex v1" and one confidence level less.
# Steps: vm-status; gates (no other project's JVM over 4 GB, free disk >= 10 GB + the 100 GB reserve);
# transform (pdftotext + extraction, 4 worker processes, nice/ionice) -> riot --validate --syntax=turtle and
# riot --count (JVM 2 GB) -> module self-tests -> scripts/structure_lint.sh -> scripts/query_contract.py ->
# one label per subject, no e-mail addresses -> scratch TDB2 store under work/tmp-v007e with k510.ttl,
# pma.ttl, denovo.ttl and the reference_fallback files (links resolve, no untyped target, no node with two
# labels, provenance on every link node, every annotation asserted) -> summary.
# Summary: monitor/k510_predicates_finalize.md (final), -first.md (first) or -test.md (test); final writes
# monitor/k510_finalize.done (and removes monitor/k510_ocr_needed.md) or monitor/k510_finalize.failed.
# Never uploads, merges, tags or deletes PDFs; the scratch store is removed after the queries.
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"
MODE=${1:-auto}
lock k510_pred_finalize
exec 8> "$LOCKDIR/heavy.lock"; flock 8          # the weekly fetch and the monthly refresh wait for this run
OUT=${FINAL_OUT:-$W/out-2026-11-04}
K=${PDF_CACHE_ROOT:-$W/downloads-2026-11-04}/k510_summaries
E=$W/v007e
case "$MODE" in first|final|test|auto) ;; *) log "unknown mode $MODE (first|final|test|auto)"; exit 2;; esac
if [ "$MODE" = auto ]; then if pdf_crawl_running; then MODE=test; else MODE=final; fi; fi
if [ "$MODE" != test ] && pdf_crawl_running; then log "the crawl is still running; $MODE mode refused"; exit 1; fi
case "$MODE" in
  final) T=$OUT; REP=$MON/k510_predicates_finalize.md;;
  first) T=$OUT; REP=$MON/k510_predicates_finalize-first.md;;
  *)     T=$W/tmp-v007e/pred-final-test; REP=$MON/k510_predicates_finalize-test.md;;
esac
S=$W/tmp-v007e/finalize-$MODE; DB=$S/tdb; Q=$S/q
rm -rf "$DB"; mkdir -p "$T" "$S" "$Q"
F=$T/k510_predicates.ttl
exec > "$S/finalize.log" 2>&1 < /dev/null
export JVM_ARGS=-Xmx2G TMPDIR=$S
FAIL=()
log "== k510_predicates finalize ($MODE) start; output $F; MDKG_OCR_DIR=${MDKG_OCR_DIR:-}"
vm_status > "$S/vmstatus.txt"
while ! gate k510_finalize 10; do log "gate: $GATE_MSG; waiting 10 min"; sleep 600; done
log "gate: $GATE_MSG"

# ---------- 1. transform
t0=$(date +%s)
if [ "$MODE" = final ] || [ "$MODE" = first ]; then
  ( cd "$WT" && nicely python3 -I "$HERE/src_run_pdf.py" k510_predicates transform "$OUT" --workers 4 ) > "$S/transform.log" 2>&1 || FAIL+=("transform")
else
  ( cd "$WT" && nicely python3 -I - "$K" "$F" "$OUT" ) > "$S/transform.log" 2>&1 <<'PY' || FAIL+=("transform")
import os, sys
wt = os.environ.get("MDKG_WT", "/srv/medical-device-kg/work/v007")
sys.path[:0] = [wt + "/pipeline", wt + "/pipeline/sources"]
import k510_predicates as kp
kp.transform(sys.argv[1], sys.argv[2], known_dir=sys.argv[3], workers=4)
PY
fi
log "transform ($(( $(date +%s) - t0 )) s): $(grep -o '"pdfs_read": [0-9]*' $T/k510_predicates.counts.json 2>/dev/null) $(tail -1 $S/transform.log | cut -c1-300)"
[ -s "$F" ] || { FAIL+=("no output"); }

# ---------- 2. riot
RIOT="FAIL"
if $J/riot --validate --syntax=turtle "$F" > "$S/riot.err" 2>&1; then RIOT="OK ($(wc -l < $S/riot.err) messages)"; else FAIL+=("riot"); RIOT="FAIL: $(head -3 $S/riot.err | tr '\n' ' ')"; fi
COUNT=$($J/riot --count --syntax=turtle "$F" 2>&1 | tail -1 | grep -o 'Triples = [0-9,]*')

# ---------- 3. self-tests, lint, contract
cd "$WT"
ST=""
for t in pipeline/iri.py pipeline/common.py pipeline/sources/base.py pipeline/sources/k510_predicates.py pipeline/sources/hud_designations.py pipeline/licence.py; do
  r=$(python3 $t 2>&1 | grep -E 'PASSED|Error|assert' | head -1); ST+="  - $t: $r"$'\n'
  echo "$r" | grep -q PASSED || FAIL+=("self-test $t")
done
LINT=$(bash scripts/structure_lint.sh 2>&1 | tail -1); echo "$LINT" | grep -q "OK" || FAIL+=("structure_lint")
CONTRACT=$(python3 scripts/query_contract.py 2>&1 | tail -1); echo "$CONTRACT" | grep -q "OK" || FAIL+=("query_contract")
MAXLAB=$(awk '/^</{s=$1} /rdfs:label/{c[s]++} END{m=0; for(k in c) if(c[k]>m) m=c[k]; print m}' "$F")
[ "${MAXLAB:-9}" -le 1 ] || FAIL+=("a subject with $MAXLAB labels")
EMAILS=$(grep -c -E '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' "$F")
[ "$EMAILS" = 0 ] || FAIL+=("$EMAILS e-mail addresses")

# ---------- 4. scratch TDB2 link-resolution test
cat > $Q/1_links.rq <<'RQ'
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?what (COUNT(*) AS ?n) WHERE {
  { ?s ex:predicateDevice ?o . BIND("predicateDevice statements" AS ?what) }
  UNION { ?s ex:referenceDevice ?o . BIND("referenceDevice statements" AS ?what) }
  UNION { ?l a ex:PredicateDeviceLink . BIND("PredicateDeviceLink nodes" AS ?what) }
  UNION { ?l a ex:ReferenceDeviceLink . BIND("ReferenceDeviceLink nodes" AS ?what) }
} GROUP BY ?what ORDER BY ?what
RQ
cat > $Q/2_targets.rq <<'RQ'
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?class (COUNT(DISTINCT ?o) AS ?targets) (SUM(IF(EXISTS { ?o ex:dataQualityNote ?n }, 1, 0)) AS ?with_note) WHERE {
  ?s ex:predicateDevice|ex:referenceDevice ?o . ?o a ?class
} GROUP BY ?class ORDER BY ?class
RQ
cat > $Q/3_problems.rq <<'RQ'
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?problem (COUNT(DISTINCT ?x) AS ?n) WHERE {
  { ?s ex:predicateDevice|ex:referenceDevice ?x . FILTER NOT EXISTS { ?x a ?t } BIND("untyped (dangling) target" AS ?problem) }
  UNION { ?x ex:predicateDevice|ex:referenceDevice ?o . FILTER NOT EXISTS { ?x a ?t } BIND("untyped citing 510(k)" AS ?problem) }
  UNION { ?s ex:predicateDevice|ex:referenceDevice ?x . FILTER NOT EXISTS { ?x rdfs:label ?lab } BIND("target without label" AS ?problem) }
  UNION { ?x ex:predicateDevice|ex:referenceDevice ?x . BIND("self link" AS ?problem) }
  UNION { VALUES ?c { ex:PredicateDeviceLink ex:ReferenceDeviceLink } ?x a ?c .
          FILTER NOT EXISTS { ?x rdfs:label ?a ; rdf:subject ?s ; rdf:predicate ?p ; rdf:object ?o ; ex:citedNumber ?cn ;
                              ex:extractionMethod ?m ; ex:confidence ?cf ; ex:sourceDataset ?d ; ex:sourceUrl ?u ; ex:retrievedAt ?r }
          BIND("link node without full provenance" AS ?problem) }
  UNION { VALUES ?c { ex:PredicateDeviceLink ex:ReferenceDeviceLink } ?x a ?c ; rdf:subject ?s ; rdf:predicate ?p ; rdf:object ?o .
          FILTER NOT EXISTS { ?s ?p ?o } BIND("annotation without the asserted link" AS ?problem) }
  UNION { ?s ex:predicateDevice ?o . FILTER NOT EXISTS { ?x a ex:PredicateDeviceLink ; rdf:subject ?s ; rdf:object ?o } BIND("predicate link without annotation" AS ?problem) }
  UNION { ?s ex:referenceDevice ?o . FILTER NOT EXISTS { ?x a ex:ReferenceDeviceLink ; rdf:subject ?s ; rdf:object ?o } BIND("reference link without annotation" AS ?problem) }
} GROUP BY ?problem ORDER BY ?problem
RQ
cat > $Q/4_labels.rq <<'RQ'
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT (COUNT(*) AS ?nodes_with_two_or_more_labels) WHERE { { SELECT ?s WHERE { ?s rdfs:label ?l } GROUP BY ?s HAVING (COUNT(?l) > 1) } }
RQ
cat > $Q/5_confidence.rq <<'RQ'
PREFIX ex: <https://bmedesign.org/medical-device-kg/ns/>
SELECT ?class ?confidence (COUNT(*) AS ?n) WHERE {
  VALUES ?class { ex:PredicateDeviceLink ex:ReferenceDeviceLink } ?x a ?class ; ex:confidence ?confidence
} GROUP BY ?class ?confidence ORDER BY ?class ?confidence
RQ
LOADSET="$F $OUT/k510.ttl $OUT/pma.ttl $OUT/denovo.ttl $(ls $OUT/reference_fallback*.ttl 2>/dev/null | tr '\n' ' ')"
TDB="not run"
if [ -s "$F" ] && [[ "$RIOT" == OK* ]]; then
  while o=$(big_other_jvms); [ -n "$o" ]; do log "scratch load: another project's JVM over 4 GB is running; waiting 10 min"; sleep 600; done
  if nicely $J/tdb2.tdbloader --loc "$DB" $LOADSET > "$S/load.log" 2>&1; then
    TDB="load OK ($(grep -i 'triples' $S/load.log | tail -1 | sed 's/.*:: *//'))"
    for q in $Q/*.rq; do
      echo "### $(basename $q .rq)"; echo '```'
      nicely $J/tdb2.tdbquery --loc "$DB" --results=TSV --file "$q" 2>&1
      echo '```'
    done > "$S/queries.md"
    PROB=$(nicely $J/tdb2.tdbquery --loc "$DB" --results=TSV --file $Q/3_problems.rq 2>/dev/null | tail -n +2 | grep -c .)
    LAB2=$(nicely $J/tdb2.tdbquery --loc "$DB" --results=TSV --file $Q/4_labels.rq 2>/dev/null | tail -1 | grep -o '^[0-9]*')
    [ "$PROB" = 0 ] || FAIL+=("scratch store: $PROB problem kinds (see queries)")
    [ "${LAB2:-1}" = 0 ] || FAIL+=("scratch store: ${LAB2:-?} nodes with two or more labels")
  else
    TDB="load FAILED: $(tail -2 $S/load.log | tr '\n' ' ')"; FAIL+=("scratch load")
  fi
  rm -rf "$DB"
fi

# ---------- 5. release list + upload plan (final only, all checks passed)
C=$T/k510_predicates.counts.json
jv() { python3 -I -c "import json,sys; d=json.load(open('$C')); print($1)" 2>/dev/null; }
LINKS=$(jv 'd["links"]'); PDFS=$(jv 'd["pdfs_read"]'); OCR=$(jv 'd.get("image_only_ocr_backlog",0)')
SUMS=$(jv 'd.get("summaries_with_links",0)'); HM=$(jv 'd.get("summaries_with_high_or_medium_predicate",0)')
BYC=$(jv '", ".join(f"{k} {v:,}" for k,v in d["links_by_kind_confidence"].items())')
OCRT=$(jv 'd.get("text_pdfs_from_ocr",0)'); OCRL=$(jv 'd.get("links_by_method",{}).get(d.get("ocr_method",""),0)')
OCRD=$(jv '" ".join(d.get("ocr_dirs",[])) or "none"')
REL="not changed ($MODE mode)"
if [ "$MODE" = final ] && [ ${#FAIL[@]} = 0 ]; then
  WHAT="predicate and reference devices read from ${PDFS} 510(k) summary PDFs (${LINKS} links from ${SUMS} summaries; ${HM} with a high- or medium-confidence predicate; ${OCRT:-0} PDFs read from OCR text, ${OCRL:-0} links from them, one confidence level lower; ${OCR} image-only PDFs still without text)"
  NOTE="riot OK, ${COUNT#Triples = } triples; self-tests, structure_lint, query_contract OK; scratch TDB2 with k510/pma/denovo/reference_fallback: 0 dangling or untyped targets, 0 nodes with 2 labels, provenance complete"
  REL=$(python3 -I $E/release_add.py "$F" "$WHAT" "$NOTE" 2>&1 | tail -1)
  echo "$REL" | grep -q "RELEASE_INPUTS.txt:" || FAIL+=("release_add.py")
fi

# ---------- 6. summary
MISSING=$(python3 -I - "$K" <<'PY'
import sys
from pathlib import Path
K = Path(sys.argv[1]); last = {}
for l in (K / "fetch_log.tsv").read_text(encoding="utf-8").splitlines()[1:]:
    f = l.split("\t")
    if len(f) >= 3: last[f[0]] = f[2]
urls = [l.split("\t")[0] for l in (K / "urls.tsv").read_text(encoding="utf-8").splitlines()[1:] if "\t" in l]
have = sum(1 for n in urls if (K / "pdf" / n[:3] / f"{n}.pdf").exists())
gone = sum(1 for n in urls if last.get(n) in ("404", "notpdf"))
ref = sum(1 for n in urls if str(last.get(n, "")).startswith("refused"))
print(f"{len(urls):,} summaries listed; {have:,} PDFs cached; {gone:,} answered 404 or not a PDF; {ref:,} refused (a rerun of the fetch asks for these again); {len(urls) - have - gone - ref:,} not yet requested")
PY
)
STATUS="PASSED"; [ ${#FAIL[@]} = 0 ] || STATUS="FAILED: ${FAIL[*]}"
{
  echo "# k510_predicates finalize ($MODE) $(now)"
  echo
  echo "Status: $STATUS"
  echo
  echo "Nothing uploaded, merged or tagged. Output: $F"
  echo
  echo "- PDFs: $MISSING"
  echo "- read: ${PDFS:-?} PDFs; ${SUMS:-?} summaries with links; ${HM:-?} with a high- or medium-confidence predicate; ${OCR:-?} image-only and still without OCR text (OCR backlog: ${F%.ttl}.ocr_backlog.tsv)"
  echo "- OCR text (ocrmypdf/tesseract on Star HPC): ${OCRT:-0} PDFs read from it, ${OCRL:-0} links (confidence one level lower); folders: ${OCRD:-none}"
  echo "- links: ${LINKS:-?} ($BYC)"
  echo "- riot --validate --syntax=turtle: $RIOT; $COUNT"
  echo "- self-tests:"; printf "%s" "$ST"
  echo "- structure_lint.sh: $LINT"
  echo "- query_contract.py: $CONTRACT"
  echo "- most labels on one subject: $MAXLAB; e-mail addresses: $EMAILS"
  echo "- scratch TDB2 ($LOADSET): $TDB"
  echo "- RELEASE_INPUTS.txt / UPLOAD_PLAN.md: $REL"
  echo
  echo "## Scratch-store queries"; cat "$S/queries.md" 2>/dev/null
  echo
  echo "## vm-status at start"; echo '```'; cat "$S/vmstatus.txt"; echo '```'
  echo
  echo "Log: $S/finalize.log; transform log: $S/transform.log"
} > "$REP.part" && mv "$REP.part" "$REP"
if [ "$MODE" = final ]; then
  if [ ${#FAIL[@]} = 0 ]; then echo "$(now) $REP" > $MON/k510_finalize.done; rm -f $MON/k510_finalize.failed $MON/k510_ocr_needed.md
  else echo "$(now) $STATUS" > $MON/k510_finalize.failed; alert k510_finalize "$STATUS"; fi
elif [ "$MODE" = first ]; then
  # the OCR backlog is what this pass is for: with a backlog file the OCR can go ahead even if a check failed
  # (the final run repeats every check); without one the watcher stops (k510_finalize.failed)
  if [ -f "${F%.ttl}.ocr_backlog.tsv" ] && [ -s "$F" ]; then echo "$(now) $STATUS $REP" > $MON/k510_finalize_first.done
  else echo "$(now) first pass: $STATUS" > $MON/k510_finalize.failed; fi
  [ ${#FAIL[@]} = 0 ] || alert k510_finalize "first pass $STATUS"
fi
log "== k510_predicates finalize ($MODE) end: $STATUS; summary $REP"
