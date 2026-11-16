#!/bin/bash
# fulltest.sh -- full-size load and query batteries for one snapshot (monthly job; template of the v0.0.7
# release-candidate test work/fulltest-v007d/run.sh, with the snapshot date as a parameter).
#
#   SNAP_DATE=YYYY-MM-DD bash scripts/schedule/fulltest.sh            (run by monthly_refresh.sh)
#
# Steps: (1) stage the input set of work/out-$SNAP_DATE as symlinks in work/fulltest-$SNAP_DATE/inputs
# (files the refresh does not produce -- devices_fda_with_pma, foia_reading_room, safety_comms_incl_pre2011,
# warning_letters_archive, the frozen asr / legacy_mdr / xray_assembler -- are carried over from the newest
# release snapshot and listed as such); (2) reference_fallback.py + product_code_fallback.py; (3) gate:
# vm-status, no other project's JVM over 4 GB, free disk after store + xloader temp >= 100 GB; (4)
# tdb2.xloader (JVM_ARGS=-Xmx8G, 4 threads) into work/tdb-$SNAP_DATE with a disk watchdog, tdb2.tdbstats;
# (5) batteries 1-7 (copied from work/fulltest-v007d; per-query timeout, JVM 6 GB); (6) RC_REPORT.md.
# Never deletes an older store (monitor/retention_candidates.md lists it), never uploads, merges or tags.
set -u
M=/srv/medical-device-kg; W=$M/work; D=${SNAP_DATE:?set SNAP_DATE=YYYY-MM-DD}
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh"
OUT=$W/out-$D; ST=$W/fulltest-$D; DB=$W/tdb-$D; LOG=$LD/fulltest-$D.log
BASE=${BASELINE_OUT:-$W/out-2026-11-04}; O7=$W/out-v007; ONT=$WT/ontology; BT=$W/fulltest-v007d
STORE_GB=${STORE_GB:-145}; XTMP_GB=${XTMP_GB:-30}
export TMPDIR=$ST/tmp
mkdir -p $TMPDIR $ST/inputs
exec >> $LOG 2>&1 < /dev/null
die() { log "ABORT: $*"; echo "ABORTED: $*" > $ST/FULLTEST_DONE; exit 1; }
rv() { JVM_ARGS=-Xmx2G $J/riot --validate --syntax=turtle "$1" >/dev/null 2>$TMPDIR/riot.err && echo OK || echo "FAIL: $(head -2 $TMPDIR/riot.err | tr '\n' ' ')"; }
wait_jvms() { local o; while o=$(big_other_jvms); [ -n "$o" ]; do log "$1: another project's JVM over 4 GB is running; waiting 10 min"; sleep 600; done; }

log "== fulltest $D start; snapshot $OUT; store $DB"
[ -e $DB ] && die "$DB already exists"
[ -e $OUT/REFRESH_DONE ] || die "the refresh of $D has not finished ($OUT/REFRESH_DONE missing)"

# ---------- (1) stage
SNAP="classification clia hde naics registrationlisting tplc cfr k510 pma recall_class recall_db recalls udi geospatial
 ivd_home_use mammography postmarket_522 standards advisory advisory_text ai_devices cecv classification_detail denovo
 device_eua fda_tplc guidance medsun medsun_text pas pma_memo rh_codes shortages warning_letters mddt asca
 third_party_review dd_inspections dd_citations dd_compliance_actions dd_import_refusals event_text_scrubbed"
CARRY="devices_fda_with_pma foia_reading_room safety_comms_incl_pre2011 warning_letters_archive reference_fallback_wl_archive"
OPT="k510_predicates hud_designations"
: > $ST/carried_over.txt
for s in $SNAP; do [ -s $OUT/$s.ttl ] || die "snapshot file missing: $OUT/$s.ttl"; ln -sfn $OUT/$s.ttl $ST/inputs/$s.ttl; done
for s in $CARRY; do
  if [ -s $OUT/$s.ttl ]; then ln -sfn $OUT/$s.ttl $ST/inputs/$s.ttl
  elif [ -s $BASE/$s.ttl ]; then ln -sfn $BASE/$s.ttl $ST/inputs/$s.ttl; echo "$s.ttl from $BASE" >> $ST/carried_over.txt
  else log "note: $s.ttl in neither $OUT nor $BASE"; fi; done
for s in $OPT; do [ -s $OUT/$s.ttl ] && ln -sfn $OUT/$s.ttl $ST/inputs/$s.ttl; done
mv -f $ST/inputs/devices_fda_with_pma.ttl $ST/inputs/devices_fda.ttl 2>/dev/null
for s in rh_actions_derived tplc_premarket; do [ -s $OUT/derived/$s.ttl ] || die "missing $OUT/derived/$s.ttl"; ln -sfn $OUT/derived/$s.ttl $ST/inputs/$s.ttl; done
for s in asr legacy_mdr legacy_mdr_text xray_assembler; do ln -sfn $O7/$s.ttl $ST/inputs/$s.ttl; echo "$s.ttl frozen (out-v007)" >> $ST/carried_over.txt; done
for s in ontology udi_extension void; do ln -sfn $ONT/$s.ttl $ST/inputs/$s.ttl; done
ls $ST/inputs | grep -E '^event\.ttl$|^event_text\.ttl$|^safety_comms\.ttl$' && die "forbidden file staged"
ls $OUT/event_parts/event_*.ttl >/dev/null 2>&1 || die "no MAUDE parts in $OUT/event_parts"

# ---------- (2) fallbacks
( cd $WT && nicely python3 scripts/reference_fallback.py --out $OUT --date $D --workers 4 $ST/inputs $OUT/event_parts ) > $ST/reference_fallback.log 2>&1 || die "reference_fallback failed"
( cd $WT && nicely python3 scripts/product_code_fallback.py $OUT/cfr.ttl $ST/inputs $OUT/event_parts ) > $ST/product_code_fallback.log 2>&1 || die "product_code_fallback failed"
ln -sfn $OUT/reference_fallback.ttl $ST/inputs/reference_fallback.ttl
ln -sfn $OUT/product_code_fallback.ttl $ST/inputs/product_code_fallback.ttl
ls $ST/inputs/*.ttl $OUT/event_parts/event_*.ttl > $ST/load_list.txt
for f in $(cat $ST/load_list.txt); do printf "%s\t%s\n" "$(stat -L -c %s $f)" "$(readlink -f $f)"; done > $ST/staged_files.tsv
log "staged $(grep -c . $ST/load_list.txt) files, $(awk -F'\t' '{s+=$1} END {printf "%.0f", s/1e9}' $ST/staged_files.tsv) GB"

# ---------- (3) gate
{ echo "== vm-status $(now)"; vm_status; } > $ST/preflight.txt
while ! gate fulltest $(( STORE_GB + XTMP_GB )); do log "gate: $GATE_MSG; waiting 30 min"; sleep 1800; done
log "gate: $GATE_MSG"

# ---------- (4) load
mkdir -p $ST/xtmp
JVM_ARGS="-Xmx8G" setsid nice -n 10 $J/tdb2.xloader --loc $DB --tmpdir $ST/xtmp --threads 4 $(cat $ST/load_list.txt) > $ST/load.log 2>&1 &
LP=$!
while kill -0 $LP 2>/dev/null; do
  sleep 60
  F=$(free_gb)
  if [ $F -lt $((RESERVE_GB + 5)) ]; then kill -TERM -- -$LP; sleep 60; kill -KILL -- -$LP 2>/dev/null
    die "load stopped by the disk watchdog (${F} GB free); $DB and $ST/xtmp left for inspection"; fi
done
wait $LP; RC=$?; log "xloader rc=$RC"; [ $RC = 0 ] || die "load failed (see $ST/load.log)"
rm -rf $ST/xtmp
wait_jvms tdbstats
JVM_ARGS=-Xmx6G $J/tdb2.tdbstats --loc $DB > $ST/stats.txt 2> $ST/stats.err
CNT=$(grep -o -E '\(count [0-9]+\)' $ST/stats.txt | head -1 | tr -dc 0-9)
printf "%'d distinct triples (tdb2.tdbstats, %s)\n" "${CNT:-0}" "$(now)" > $ST/triple_count.txt

# ---------- (5) batteries
cp -p $BT/bt_run.py $BT/battery*.py $ST/
cd $ST
for i in 1 2 3 4 5 6 7; do
  b=battery$i.py; [ $i = 1 ] && b=battery.py
  wait_jvms battery$i
  extra=""; [ $i -ge 6 ] && extra="$ST/inputs $WT/docs/queries"
  BT_TIMEOUT=1800 BT_JVM=-Xmx6G nicely python3 bt_run.py $b $DB $ST/battery${i}_report.md $extra > $ST/battery$i.log 2>&1
  log "battery$i rc=$?: $(tail -1 $ST/battery$i.log)"
done

# ---------- (6) release-candidate report
{
  echo "# Release candidate $D (scheduled monthly refresh)"
  echo
  echo "Not uploaded, merged or tagged: the PI decides whether this becomes a release."
  echo
  echo "- snapshot: work/out-$D ($(grep -c . $OUT/SNAPSHOT_STATUS.tsv 2>/dev/null) status rows; $(cat $OUT/REFRESH_DONE))"
  echo "- store: work/tdb-$D, $(cat $ST/triple_count.txt)"
  echo "- staged files: $(grep -c . $ST/load_list.txt)"
  echo "- carried over from earlier snapshots:"; sed 's/^/  - /' $ST/carried_over.txt
  echo
  echo "| battery | PASS | CHECK | FAIL |"; echo "|---|---|---|---|"
  for r in $ST/battery*_report.md; do
    echo "| $(basename $r .md) | $(grep -c 'PASS' $r) | $(grep -c 'CHECK' $r) | $(grep -c 'FAIL' $r) |"; done
  echo
  echo "Per-source status (work/out-$D/SNAPSHOT_STATUS.tsv):"; echo '```'; cat $OUT/SNAPSHOT_STATUS.tsv 2>/dev/null; echo '```'
} > $ST/RC_REPORT.md
echo "$(now) done" > $ST/FULLTEST_DONE
log "== fulltest $D end: $ST/RC_REPORT.md"
