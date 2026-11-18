#!/bin/bash
# monthly_refresh.sh -- monthly full refresh and release-candidate test (cron, day 3 of the month 02:20 local).
#
#   bash scripts/schedule/monthly_refresh.sh [--dry-run]
#
# 1. vm-status (read-only); flock (monthly) + the heavy lock shared with the weekly fetch.
# 2. Disk projection: a full snapshot needs about the size of the current release set (RELEASE_INPUTS.txt)
#    + 25 GB of downloads + 85 GB of MAUDE intermediates (event.ttl, event_text.ttl before the scrub) at the
#    peak, and the full-size load another ~175 GB (store + xloader temp). The run starts only when free disk
#    >= that need + the 100 GB reserve and no other project's JVM over 4 GB runs; otherwise it writes a
#    "deferred" report with the shortfall and the retention candidates (older snapshots / stores the PI may
#    approve deleting) and exits. Nothing is deleted automatically.
# 3. snapshot_refresh.sh -> work/out-YYYY-MM-DD (every source's own fetch + transform, MAUDE scrub, derived
#    views), then the 510(k) predicate and HUD designation transforms on the cached PDFs (new PDFs are
#    fetched first, politely, for newly listed summaries), then fulltest.sh -> work/fulltest-YYYY-MM-DD +
#    work/tdb-YYYY-MM-DD and RC_REPORT.md.
# 4. Report: monitor/monthly/YYYY-MM-DD.md. It is a release CANDIDATE: no upload, merge or tag happens
#    without the PI's approval.
# --dry-run: steps 1-2 and the plan only.
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh" "$@"
lock monthly_refresh
D=$(today)
REP=$MON/monthly/$D$([ $DRY = 1 ] && echo -dryrun).md
REL=$(ls -t $W/out-20*/RELEASE_INPUTS.txt 2>/dev/null | head -1)
REL_GB=$(grep -v '^#' "$REL" 2>/dev/null | awk -F'\t' '{s+=$1} END {printf "%.0f", s/1e9}')
SNAP_NEED=$(( ${REL_GB:-110} + 25 + 85 )); LOAD_NEED=175; NEED=$(( SNAP_NEED + LOAD_NEED ))
STATUS=""
VMS=$(vm_status)
if ! heavy_lock; then STATUS="deferred: the weekly fetch or the 510(k) predicate finalize holds the heavy lock (rerun after it ends)"
elif [ -e $W/out-$D ] && [ $DRY = 0 ]; then STATUS="deferred: work/out-$D already exists"
elif ! gate monthly $NEED; then STATUS="deferred: $GATE_MSG"
elif pdf_crawl_running; then STATUS="deferred: the 510(k) summary PDF crawl is still running (same FDA host as the accessdata lane)"
else STATUS="ready"; fi
retention_report
write_report() {
  {
    echo "# Monthly refresh $D$([ $DRY = 1 ] && echo ' (DRY RUN)')"
    echo
    echo "Status: $1"
    echo
    echo "Disk projection: release set ${REL_GB:-?} GB ($REL) + downloads 25 GB + MAUDE intermediates 85 GB"
    echo "= ${SNAP_NEED} GB for the snapshot, + ${LOAD_NEED} GB for the full-size load = ${NEED} GB, plus the ${RESERVE_GB} GB reserve."
    echo "Free now: $(free_gb) GB."
    echo
    echo "## vm-status (read-only)"; echo '```'; echo "$VMS"; echo '```'
    echo
    echo "## Plan"
    echo "1. SNAP_DATE=$D scripts/schedule/snapshot_refresh.sh -> work/out-$D, work/downloads-$D"
    echo "2. k510_predicates fetch (new summaries only) + transform; hud_designations fetch (new HDEs) + transform"
    echo "3. SNAP_DATE=$D scripts/schedule/fulltest.sh -> work/fulltest-$D, work/tdb-$D, RC_REPORT.md"
    echo "4. Release candidate report for the PI. No upload, merge or tag."
    echo
    echo "## Retention candidates (PI approval needed; nothing deleted)"
    grep '^| ' $MON/retention_candidates.md
    [ -n "${2:-}" ] && { echo; echo "## Release candidate"; cat "$2"; }
  } > $REP
}
if [ "$STATUS" != ready ] || [ $DRY = 1 ]; then
  [ $DRY = 1 ] && [ "$STATUS" = ready ] && STATUS="dry run: gates pass; would start the refresh"
  write_report "$STATUS"
  [ $DRY = 0 ] && alert monthly_refresh "$STATUS"
  log "monthly refresh: $STATUS; report $REP"; exit 0
fi

write_report "running since $(now)"
export SNAP_DATE=$D MDKG_WT=$WT
nicely bash $HERE/snapshot_refresh.sh
OUT=$W/out-$D
if grep -q "failed: none" $OUT/REFRESH_DONE 2>/dev/null; then
  cp -p $(ls -t $W/out-20*/devices_fda_with_pma.ttl | grep -v "out-$D/" | head -1) $OUT/devices_fda_with_pma.ttl
  for s in k510_predicates hud_designations; do
    ( cd $WT && V7C_OUT=$OUT nicely python3 -I $HERE/src_run_pdf.py $s all $OUT ) >> $LD/refresh-$D-pdf.log 2>&1 \
      || alert monthly_refresh "$s refresh failed (see logs/refresh-$D-pdf.log)"
  done
  nicely bash $HERE/fulltest.sh
  write_report "release candidate ready for PI review (not uploaded)" $W/fulltest-$D/RC_REPORT.md
else
  write_report "refresh finished with failures: $(cat $OUT/REFRESH_DONE 2>/dev/null); full test not started"
  alert monthly_refresh "refresh $D finished with failures"
fi
log "monthly refresh end; report $REP"
