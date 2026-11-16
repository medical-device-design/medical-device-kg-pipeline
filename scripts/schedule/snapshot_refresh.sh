#!/bin/bash
# snapshot_refresh.sh -- refresh every release source to ONE snapshot date (monthly job; template of the
# v0.0.7 refresh of 2026-11-04, work/out-2026-11-04/refresh.sh, with the date as a parameter).
#
#   SNAP_DATE=YYYY-MM-DD bash scripts/schedule/snapshot_refresh.sh       (run by monthly_refresh.sh)
#
#   outputs   work/out-$SNAP_DATE/        downloads   work/downloads-$SNAP_DATE/
#   main log  logs/refresh-$SNAP_DATE.log      lane logs   logs/refresh-$SNAP_DATE-<lane>.log
# Each module's own fetch() + transform() (bulk downloads; never the capped openFDA API). Lanes run in
# parallel, one lane per host, sources inside a lane one after another, so no host sees more than one
# request per second. Seeded caches (real copies; the old caches are never written): archive parts
# (warning_letters, safety_comms, shortages, device_eua letters) and pages a module treats as final.
# Not refreshed here: devices_fda (its HEAD-check crawl is separate; monthly_refresh.sh links the newest
# devices_fda_with_pma.ttl), asr / legacy_mdr / xray_assembler (frozen), rh_actions (stub; FDA search down).
# MAUDE: event bulk -> event.ttl + event_text.ttl -> scrub (v2 rules, 4 workers) -> leak check (accepted only
# if non-benign matches are 0 or all prefix+dictionary words) -> patched parts. The unscrubbed files are
# removed once the scrubbed file is accepted / the parts are written; nothing is uploaded.
set -u
M=/srv/medical-device-kg; W=$M/work; WT=${MDKG_WT:-$W/v007}; D=${SNAP_DATE:?set SNAP_DATE=YYYY-MM-DD}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$W/out-$D; DL=$W/downloads-$D; ST=$OUT/.status; LD=$M/logs; LOG=$LD/refresh-$D.log
V=$M/downloads/v007; J=$M/tools/apache-jena-5.2.0/bin
export V7C_OUT=$OUT V7C_DL=$DL TMPDIR=$W/tmp-refresh-$D MDKG_WT=$WT PYTHONDONTWRITEBYTECODE=1
mkdir -p $OUT $DL $ST $TMPDIR
exec >> $LOG 2>&1 < /dev/null
log() { echo "[$(date -Is)] $*"; }
free_gb() { df --output=avail -BG $W | tail -1 | tr -dc 0-9; }
need() {   # need <GB> <what>: wait until <GB> plus the 100 GB reserve are free
  while [ "$(free_gb)" -lt $(( $1 + 100 )) ]; do
    log "$2: needs $1 GB plus the 100 GB reserve; $(free_gb) GB free; waiting 10 min"; sleep 600; done; }
waitfor() { for n in "$@"; do
  while [ ! -e $ST/$n.done ] && [ ! -e $ST/$n.failed ]; do sleep 60; done
  [ -e $ST/$n.failed ] && log "note: dependency $n failed"; done; return 0; }
src() {    # src <lane> <source> [stage]
  local lane=$1 n=$2 stg=${3:-all} t0=$(date +%s) rc
  rm -f $ST/$n.failed
  log "[$lane] $n $stg start"
  ( cd $WT && nice -n 10 ionice -c2 -n7 python3 -I $HERE/src_run.py $n $stg ) >> $LD/refresh-$D-$lane.log 2>&1
  rc=$?
  if [ $rc = 0 ]; then
    [ "$stg" = fetch ] || touch $ST/$n.done
    log "[$lane] $n $stg OK ($(( $(date +%s) - t0 )) s) $(grep "^\[$n\] transform:" $LD/refresh-$D-$lane.log | tail -1 | cut -c1-170)"
  else
    touch $ST/$n.failed
    log "[$lane] $n $stg FAILED rc=$rc ($(( $(date +%s) - t0 )) s): $(grep -v '^\s*$' $LD/refresh-$D-$lane.log | tail -1 | cut -c1-200)"
  fi
  return $rc
}
rv_sample() {   # syntax check of the first 200 MB of whole blocks (JVM 2 GB)
  head -c 200000000 "$1" | awk 'BEGIN{RS="";ORS="\n\n"} {b[NR]=$0} END{for(i=1;i<NR;i++) print b[i]}' > $TMPDIR/sample.ttl
  JVM_ARGS=-Xmx2G $J/riot --validate --syntax=turtle $TMPDIR/sample.ttl > $TMPDIR/sample.err 2>&1 && echo OK || echo "FAIL: $(head -2 $TMPDIR/sample.err | tr '\n' ' ')"
  rm -f $TMPDIR/sample.ttl $TMPDIR/sample.err; }
seed() {   # seed <old cache dir/file> <new path>: real copy, only if the new path does not exist yet
  [ -e "$2" ] && return 0; [ -e "$1" ] || { log "seed: $1 missing"; return 0; }
  mkdir -p "$(dirname "$2")"; cp -a "$1" "$2" && log "seeded $2 from $1 ($(find "$2" -type f | wc -l) files)"; }

log "== snapshot refresh start (pid $$); snapshot $D; $(free_gb) GB free"
cd $WT || exit 1
git log --oneline -1; md5sum pipeline/scrub.py pipeline/scrub_words.txt scripts/scrub_maude_text.py scripts/patch_event_ttl.py | sed 's/^/  /'
python3 -I pipeline/run.py --selftest | tail -1
python3 -I pipeline/scrub.py > $TMPDIR/scrub_selftest.log 2>&1 || { log "scrub.py self-test FAILED; stop"; exit 1; }
tail -1 $TMPDIR/scrub_selftest.log

# ---------- seeded caches
seed $W/downloads/recall_class/pages $DL/recall_class/pages
seed $V/cecv/pages $DL/cecv/pages
seed $V/medsun/search $DL/medsun/search; seed $V/medsun/pages $DL/medsun/pages
seed $V/guidance/pages $DL/guidance/pages
for s in warning_letters safety_comms shortages; do seed $V/$s/archive $DL/$s/archive; seed $V/$s/archive.json $DL/$s/archive.json; done
seed $V/device_eua/letters $DL/device_eua/letters; seed $V/device_eua/eua_letters.tsv $DL/device_eua/eua_letters.tsv

# ---------- lane openfda: small bulk files first (other lanes wait on them), then UDI, MAUDE, TPLC
lane_openfda() {
  local L=openfda
  for s in classification recalls recall_db registrationlisting k510 pma; do src $L $s; done
  need 25 "udi + event downloads"
  src $L udi fetch; src $L event fetch; src $L tplc fetch
  need 20 "udi transform"
  ( export V7C_RIOT=0; src $L udi transform ) && log "[$L] udi.ttl $(stat -c %s $OUT/udi.ttl) bytes; sample syntax check: $(rv_sample $OUT/udi.ttl)"
  need 85 "event transform (event.ttl + event_text.ttl)"
  if ( export V7C_RIOT=0; src $L event transform ); then
    log "[$L] event.ttl $(stat -c %s $OUT/event.ttl) bytes, event_text.ttl $(stat -c %s $OUT/event_text.ttl) bytes; event.ttl sample: $(rv_sample $OUT/event.ttl)"
    src $L tplc transform
    maude_text; maude_patch
  else
    src $L tplc transform
    touch $ST/event_text_scrubbed.failed $ST/event_parts.failed
  fi
}
maude_text() {   # scrub -> leak check -> (triage) -> riot -> accept
  local L=openfda S=$OUT/scrub; mkdir -p $S
  need 35 "MAUDE narrative scrub"
  log "[$L] MAUDE scrub v2 start: $OUT/event_text.ttl -> $OUT/event_text_scrubbed.ttl (4 workers)"
  python3 -I scripts/scrub_maude_text.py scrub $OUT/event_text.ttl $OUT/event_text_scrubbed.ttl --workers 4 --every-gb 1 \
    > $S/scrub.log 2>&1 || { log "[$L] scrub FAILED: $(tail -2 $S/scrub.log | tr '\n' ' ')"; touch $ST/event_text_scrubbed.failed; return 1; }
  log "[$L] scrub done: $(tail -1 $S/scrub.log | cut -c1-200)"
  python3 -I scripts/scrub_maude_text.py leakcheck $OUT/event_text_scrubbed.ttl --examples 10 --workers 4 > $S/leak.log 2>&1
  local nb; nb=$(grep -o "leak check event_text_scrubbed.ttl: [0-9,]* matches in categories not judged benign" $S/leak.log | tail -1 | awk '{print $4}' | tr -d ,)
  log "[$L] leak check: $(grep 'leak check event_text_scrubbed.ttl:' $S/leak.log | head -1 | cut -c1-400); non-benign: ${nb:-?}"
  local ok=no why=""
  if [ "$nb" = "0" ]; then ok=yes; why="0 non-benign matches"
  elif [ -n "$nb" ]; then
    python3 -I scripts/scrub_maude_text.py triage $OUT/event_text_scrubbed.ttl --workers 4 > $S/triage.log 2>&1
    grep -v "^ROW" $S/triage.log | tail -25
    local dw; dw=$(grep -o "feature [a-z_]*:[a-z_]* prefix+dictionary_word: [0-9]*" $S/triage.log | awk '{s+=$NF} END {print s+0}')
    if [ "$dw" = "$nb" ]; then ok=yes; why="all $nb non-benign matches are prefix + dictionary words: $(grep -o 'English words, not names): .*' $S/triage.log | tail -1 | cut -c1-200)"
    else why="$nb non-benign matches, only ${dw:-0} judged prefix + dictionary words"; fi
  fi
  JVM_ARGS=-Xmx3G nice -n 10 $J/riot --validate $OUT/event_text_scrubbed.ttl > $S/riot.log 2>&1; local rr=$?
  log "[$L] event_text_scrubbed.ttl riot rc=$rr ($(wc -l < $S/riot.log) message lines)"
  [ $rr = 0 ] || { ok=no; why="$why; riot rc=$rr"; }
  if [ $ok = yes ]; then
    echo "$(date -Is) accepted: $why; $(stat -c %s $OUT/event_text_scrubbed.ttl) bytes; riot OK" > $OUT/event_text_scrubbed.accepted
    log "[$L] scrubbed narratives ACCEPTED ($why); removing the unscrubbed $OUT/event_text.ttl (own scratch, never staged)"
    rm -f $OUT/event_text.ttl; touch $ST/event_text_scrubbed.done
  else
    echo "$(date -Is) NOT accepted: $why. Review $S/leak.log and $S/triage.log; create event_text_scrubbed.accepted only after review." > $OUT/event_text_scrubbed.REVIEW
    log "[$L] scrubbed narratives NOT accepted ($why); unscrubbed file kept for re-scrub; orchestrator will wait"
    touch $ST/event_text_scrubbed.review
  fi
}
maude_patch() {
  local L=openfda
  need 50 "MAUDE patch into parts"
  log "[$L] patch_event_ttl.py start"
  mkdir -p $OUT/event_parts
  python3 scripts/patch_event_ttl.py $OUT/event.ttl $OUT/event_parts --part-bytes 4e9 > $OUT/scrub/patch.log 2>&1; local rc=$?
  log "[$L] patch rc=$rc: $(tail -1 $OUT/scrub/patch.log | cut -c1-400)"
  if [ $rc = 0 ] && ls $OUT/event_parts/event_*.ttl >/dev/null 2>&1; then
    local chk; chk=$(rv_sample $OUT/event_parts/event_01.ttl)
    log "[$L] parts: $(ls $OUT/event_parts/event_*.ttl | wc -l) files, $(du -s --block-size=1G $OUT/event_parts | cut -f1) GB; event_01 sample: $chk"
    if [ "$chk" = OK ]; then rm -f $OUT/event.ttl; log "[$L] removed the unpatched $OUT/event.ttl (own scratch; the parts replace it)"; touch $ST/event_parts.done
    else touch $ST/event_parts.failed; fi
  else touch $ST/event_parts.failed; fi
}

# ---------- lane accessdata (www.accessdata.fda.gov)
lane_accessdata() {
  local L=accessdata
  for s in hde pma_memo denovo pas rh_codes advisory ivd_home_use postmarket_522 clia standards asca third_party_review; do src $L $s; done
  waitfor recalls recall_db; src $L recall_class
  src $L cecv
  src $L medsun && { python3 -I scripts/scrub_maude_text.py leakcheck $OUT/medsun_text.ttl --examples 5 > $OUT/.status/leak_medsun_text.log 2>&1
                     log "[$L] medsun_text leak check: $(grep 'matches in categories not judged benign' $OUT/.status/leak_medsun_text.log | tail -1)"; }
  waitfor classification; src $L classification_detail; src $L fda_tplc
}
# ---------- lane fdagov (www.fda.gov)
lane_fdagov() {
  local L=fdagov
  for s in mammography ai_devices mddt guidance shortages safety_comms device_eua warning_letters; do src $L $s; done
}
# ---------- lane dashboard (FDA Data Dashboard API; credentials only in this lane's environment)
lane_dashboard() {
  local L=dashboard
  set -a; . $M/.fda_dashboard.env; set +a
  src $L dd_inspections; src $L dd_citations
  waitfor classification; src $L dd_import_refusals
  waitfor warning_letters; src $L dd_compliance_actions
  unset FDA_DD_USER FDA_DD_KEY
}
# ---------- lane other (eCFR, Census)
lane_other() {
  local L=other
  src $L cfr
  waitfor classification registrationlisting; src $L naics
  waitfor mammography; src $L geospatial
}

mkdir -p $OUT/derived
lane_openfda & P1=$!; lane_accessdata & P2=$!; lane_fdagov & P3=$!; lane_dashboard & P4=$!; lane_other & P5=$!
log "lanes started: openfda $P1, accessdata $P2, fdagov $P3, dashboard $P4, other $P5"
wait $P2 $P3 $P4 $P5
log "lanes accessdata, fdagov, dashboard, other finished"

# ---------- derived views (rh_actions_derived, tplc_premarket) once their inputs are refreshed
waitfor tplc k510 pma hde denovo rh_codes recall_db recalls recall_class
log "derived_views start"
python3 -I pipeline/derived_views.py $OUT/derived $OUT/tplc.ttl $OUT/k510.ttl $OUT/pma.ttl $OUT/hde.ttl \
  $OUT/denovo.ttl $OUT/rh_codes.ttl $OUT/recall_db.ttl $OUT/recalls.ttl $OUT/recall_class.ttl > $OUT/derived.log 2>&1 \
  && { touch $ST/derived_views.done; log "derived_views OK: $(tail -3 $OUT/derived.log | tr '\n' ' ' | cut -c1-300)"; } \
  || { touch $ST/derived_views.failed; log "derived_views FAILED: $(tail -2 $OUT/derived.log | tr '\n' ' ')"; }
for f in $OUT/derived/*.ttl; do log "riot $(basename $f): $(JVM_ARGS=-Xmx2G $J/riot --validate $f > $TMPDIR/d.err 2>&1 && echo OK || head -1 $TMPDIR/d.err)"; done
python3 -I scripts/scrub_maude_text.py leakcheck $OUT/advisory_text.ttl --examples 5 > $ST/leak_advisory_text.log 2>&1
log "advisory_text leak check: $(grep 'matches in categories not judged benign' $ST/leak_advisory_text.log | tail -1)"

wait $P1
log "lane openfda finished"
{ printf "source\tstatus\trecords\ttriples\tbytes\n"
  for j in $ST/*.json; do n=$(basename $j .json); st=done; [ -e $ST/$n.failed ] && st=FAILED
    python3 -I -c "import json,sys,os; d=json.load(open('$j')); o=d.get('out',''); print('\t'.join(['$n','$st',str(d.get('records','')),str(d.get('triples','')),str(os.path.getsize(o)) if o and os.path.exists(o) else '']))"; done
  for x in event_text_scrubbed event_parts derived_views; do st=missing; [ -e $ST/$x.done ] && st=done; [ -e $ST/$x.failed ] && st=FAILED; [ -e $ST/$x.review ] && st=REVIEW; printf "%s\t%s\n" $x $st; done
} > $OUT/SNAPSHOT_STATUS.tsv
F=$(ls $ST/*.failed 2>/dev/null | xargs -r -n1 basename | sed 's/\.failed$//' | tr '\n' ' ')
log "refresh summary: $(grep -c $'\tdone' $OUT/SNAPSHOT_STATUS.tsv) done; failed: ${F:-none}; $(free_gb) GB free"
cat $OUT/SNAPSHOT_STATUS.tsv
echo "$(date -Is) failed: ${F:-none}" > $OUT/REFRESH_DONE
log "== snapshot refresh end"
