#!/bin/bash
# weekly_fetch.sh -- weekly fetch of the fast-changing sources into a dated folder, with change
# detection against the previous week (cron, Saturdays 01:10 local).
#
#   bash scripts/schedule/weekly_fetch.sh [--dry-run]
#
# Folder: work/weekly-YYYY-MM-DD/{downloads,out,logs} and REPORT.md (also monitor/weekly/YYYY-MM-DD.md).
# Sources, one lane per host so no host sees more than one request per second (the modules pace
# themselves); inside a lane one source after another; each = the module's own fetch() + transform()
# (no riot, so no JVM):
#   openfda     recalls recall_db k510 pma registrationlisting; event (MAUDE bulk, ~19 GB) only when
#               api.fda.gov/download.json shows a new export date for device/event and the disk gate
#               allows it (download only; the transform runs in the monthly refresh); udi: export date and
#               record count from download.json only (openFDA publishes no UDI delta files; the full UDI
#               download runs monthly)
#   accessdata  recall_class denovo
# The whole run is deferred while the 510(k) summary PDF crawl runs (k510_predicates fetch; same FDA host)
# and skipped while k510_pred_finalize.sh or the monthly refresh holds the heavy lock.
#   fdagov      warning_letters safety_comms shortages
#   dashboard   dd_inspections dd_citations dd_import_refusals dd_compliance_actions (the Data Dashboard
#               user and key are loaded into this lane's environment from the VM's env file, which this
#               script never prints; _ddapi.py puts them only in request headers)
# Page caches (recall_class, denovo, warning_letters, safety_comms, shortages) are copied from the previous
# weekly folder so only new pages are requested; archive parts are copied from downloads/v007 as in the
# 2026-11-04 refresh. Change detection: compare_snapshots.py (records, triples, file SHA-256, openFDA
# export dates) against the previous weekly folder, or the release snapshot work/out-2026-11-04 the first time.
# Gates: vm-status first; skip when another project's JVM over 4 GB runs or free disk < need + 100 GB; flock
# (weekly) and the heavy lock shared with the monthly refresh. Nothing is uploaded, merged or deleted;
# older weekly folders are listed in monitor/retention_candidates.md for PI-approved deletion.
# --dry-run: gates, plan, openFDA index and change detection only (no source downloads).
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh" "$@"
lock weekly_fetch
D=$(today)
WK=$W/weekly-$D; [ $DRY = 1 ] && WK=$W/tmp-schedule/weekly-dryrun-$D
REP=$MON/weekly/$D$([ $DRY = 1 ] && echo -dryrun).md
BASELINE=$W/out-2026-11-04
PREV=$(ls -d $W/weekly-20[0-9][0-9]-[0-9][0-9]-[0-9][0-9] 2>/dev/null | grep -v "$WK" | sort | tail -1)
[ -z "$PREV" ] && PREV=$BASELINE
SMALL_GB=6; EVENT_GB=25
mkdir -p $WK/downloads $WK/out/.status $WK/logs
LOG=$WK/logs/weekly.log
exec > >(tee -a $LOG) 2>&1
log "== weekly fetch $D$([ $DRY = 1 ] && echo ' (dry run)'); folder $WK; previous $PREV"
STATUS="complete"
{
  echo "## vm-status (read-only)"; echo '```'; vm_status; echo '```'
} > $WK/vmstatus.md
if ! heavy_lock; then STATUS="skipped: the monthly refresh or the 510(k) predicate finalize holds the heavy lock"; log "$STATUS"; fi
if [ "$STATUS" = complete ] && pdf_crawl_running; then
  STATUS="deferred: the 510(k) summary PDF crawl is running (same FDA host); next run next week"; log "$STATUS"
  [ $DRY = 0 ] && alert weekly_fetch "$STATUS"; fi
if [ "$STATUS" = complete ] && ! gate weekly $SMALL_GB; then STATUS="deferred: $GATE_MSG"; log "$STATUS"; alert weekly_fetch "$STATUS"; fi

export V7C_DL=$WK/downloads V7C_OUT=$WK/out V7C_RIOT=0 MDKG_WT=$WT TMPDIR=$WK/tmp
mkdir -p $TMPDIR
# the transforms of dd_import_refusals and fda-linked sources read classification.ttl from the release snapshot
ln -sfn $BASELINE/classification.ttl $WK/out/classification.ttl

# openFDA index: export dates and record counts (tiny; also in a dry run)
curl -s --max-time 120 -A "medical-device-kg/rebuild (Hofstra University)" -o $WK/openfda_index.json https://api.fda.gov/download.json \
  && log "openFDA index saved" || log "openFDA index not reachable"
ev_new=$(python3 -I -c "import json;print(json.load(open('$WK/openfda_index.json'))['results']['device']['event']['export_date'])" 2>/dev/null)
ev_old=$(python3 -I -c "import json;print(json.load(open('$PREV/openfda_index.json'))['results']['device']['event']['export_date'])" 2>/dev/null)
log "openFDA device/event export date: ${ev_old:-unknown} (previous) -> ${ev_new:-unknown} (now)"

seed() { [ -e "$2" ] && return 0; [ -e "$1" ] || return 0; mkdir -p "$(dirname "$2")"; cp -a "$1" "$2" && log "seeded $2"; }
src() {   # src <lane> <source> [stage]
  local lane=$1 n=$2 stg=${3:-all} t0=$(date +%s) rc
  if [ $DRY = 1 ]; then echo "would run: src_run.py $n $stg  (lane $lane)" >> $WK/plan.txt; return 0; fi
  ( cd $WT && nicely python3 -I $HERE/src_run.py $n $stg ) >> $WK/logs/$lane.log 2>&1; rc=$?
  [ $rc = 0 ] && log "[$lane] $n $stg OK ($(( $(date +%s) - t0 )) s)" || { log "[$lane] $n $stg FAILED rc=$rc"; touch $WK/out/.status/$n.failed; }
  return $rc; }
waitfor() { local n; for n in "$@"; do [ $DRY = 1 ] && return 0
  while [ ! -e $WK/out/.status/$n.json ] && [ ! -e $WK/out/.status/$n.failed ] && [ -e $WK/out/.status/lane_fdagov.running ]; do sleep 60; done; done; }

lane_openfda() { local s
  for s in recalls recall_db k510 pma registrationlisting; do src openfda $s; done
  if [ -n "$ev_new" ] && [ "$ev_new" != "$ev_old" ]; then
    if gate event $EVENT_GB; then src openfda event fetch; else log "[openfda] event fetch deferred: $GATE_MSG"; echo "event: deferred ($GATE_MSG)" >> $WK/deferred.txt; fi
  else log "[openfda] event: export date unchanged (${ev_new:-unknown}); not downloaded"; echo "event: export date unchanged" >> $WK/deferred.txt; fi
  echo "udi: change from openFDA index only (no delta files)" >> $WK/deferred.txt; }
lane_accessdata() { local s
  if pdf_crawl_running; then log "[accessdata] recall_class, denovo deferred: the 510(k) summary PDF crawl uses the same host"
    echo "recall_class, denovo: deferred (PDF crawl running on accessdata.fda.gov)" >> $WK/deferred.txt; return 0; fi
  for s in recall_class denovo; do seed $PREV/downloads/$s $WK/downloads/$s; src accessdata $s; done; }
lane_fdagov() { local s; touch $WK/out/.status/lane_fdagov.running
  for s in warning_letters safety_comms shortages; do
    seed $PREV/downloads/$s $WK/downloads/$s; seed $M/downloads/v007/$s/archive $WK/downloads/$s/archive
    seed $M/downloads/v007/$s/archive.json $WK/downloads/$s/archive.json; src fdagov $s; done
  rm -f $WK/out/.status/lane_fdagov.running; }
lane_dashboard() {
  [ $DRY = 1 ] || { set -a; . $M/.fda_dashboard.env; set +a; }
  src dashboard dd_inspections; src dashboard dd_citations; src dashboard dd_import_refusals
  waitfor warning_letters; src dashboard dd_compliance_actions
  unset FDA_DD_USER FDA_DD_KEY; }

if [ "$STATUS" = complete ]; then
  lane_openfda & P1=$!; lane_accessdata & P2=$!; lane_fdagov & P3=$!; ( lane_dashboard ) & P4=$!
  wait $P1 $P2 $P3 $P4
  F=$(ls $WK/out/.status/*.failed 2>/dev/null | xargs -r -n1 basename | sed 's/\.failed$//' | tr '\n' ' ')
  [ -n "$F" ] && { STATUS="complete with failures: $F"; alert weekly_fetch "failed sources: $F"; }
fi
SRCS=recalls,recall_db,k510,pma,registrationlisting,event,udi,recall_class,denovo,warning_letters,safety_comms,shortages,dd_inspections,dd_citations,dd_import_refusals,dd_compliance_actions
python3 -I $HERE/compare_snapshots.py $WK $PREV $WK/CHANGES.md --sources $SRCS
{
  echo "# Weekly fetch $D$([ $DRY = 1 ] && echo ' (DRY RUN)')"
  echo
  echo "Status: $STATUS"
  echo "Folder: $WK; compared with: $PREV; free disk now: $(free_gb) GB"
  echo
  cat $WK/vmstatus.md; echo
  [ -e $WK/plan.txt ] && { echo "## Plan (dry run)"; sed 's/^/- /' $WK/plan.txt; echo; }
  [ -e $WK/deferred.txt ] && { echo "## Not fetched this week"; sed 's/^/- /' $WK/deferred.txt; echo; }
  cat $WK/CHANGES.md
  echo
  echo "Nothing was uploaded, merged or deleted. Older weekly folders: monitor/retention_candidates.md."
} > $WK/REPORT.md
cp $WK/REPORT.md $REP
retention_report
log "== weekly fetch end: $STATUS; report $REP"
