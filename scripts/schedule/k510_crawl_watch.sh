#!/bin/bash
# k510_crawl_watch.sh -- watch the 510(k) summary PDF crawl; start k510_pred_finalize.sh when it completes.
# (cron, every 30 minutes outside 09:00-12:00 local)
#
#   bash scripts/schedule/k510_crawl_watch.sh [--dry-run]
#
# While the crawl runs: writes monitor/k510_crawl_status.md (PDFs present, rate, ETA, free disk, and the
# disk projection: free space at the end = free now - PDFs still to fetch x mean PDF size so far). When the
# projection falls below the 100 GB reserve it raises an alert (once a day); the crawl itself stops before
# free space falls below 105 GB (k510_predicates.fetch, min_free_gb) and can be resumed later -- files
# already present are never requested again. PDFs are never deleted.
# When the crawl process has ended: if its last "[k510_predicates] fetch:" line ends in "complete", start
#   k510_pred_finalize.sh final (nohup, nice; not between 09:00 and 12:00), once; the finalize writes
#   monitor/k510_predicates_finalize.md and monitor/k510_finalize.done (or .failed, which stops retries).
#   Any other ending (disk floor reached, FDA refusing requests, killed) -> alert + status, no finalize.
# --dry-run: status only, never starts the finalize.
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh" "$@"
lock k510_crawl_watch
K=$W/downloads-2026-11-04/k510_summaries
CL=$LD/v007e-k510-crawl.log
STAT=$MON/k510_crawl_status.md
H=$(date +%H)
proj() { python3 -I - "$K" "$(free_gb)" <<'PY'
import json, sys
from pathlib import Path
K, free = Path(sys.argv[1]), float(sys.argv[2])
p = json.loads((K / "progress.json").read_text())
done, size = 0, 0
for l in (K / "fetch_log.tsv").read_text(encoding="utf-8").splitlines()[1:]:
    f = l.split("\t")
    if len(f) >= 4 and f[2].startswith("200") and f[3].isdigit():
        done += 1; size += int(f[3])
mean = size / done if done else 1e6
left = max(0, p["total"] - p["files_present"])
end_free = free - left * mean / 1e9
print(f"{p['files_present']} {p['total']} {p['rate_per_s']} {p['eta_hours']} {mean/1e6:.2f} {left*mean/1e9:.0f} {end_free:.0f} {p['updated']} {p['stop_reason']}")
PY
}
read -r HAVE TOTAL RATE ETA MEANMB NEEDGB ENDFREE UPD STOP <<< "$(proj 2>/dev/null)"
{
  echo "# 510(k) summary PDF crawl $(now)"
  echo
  if pdf_crawl_running; then echo "State: running"; else echo "State: not running"; fi
  echo
  echo "- PDFs present: ${HAVE:-?} of ${TOTAL:-?}; rate ${RATE:-?} requests/s; ETA ${ETA:-?} h (progress.json updated ${UPD:-?})"
  echo "- mean PDF size so far: ${MEANMB:-?} MB; still to fetch about ${NEEDGB:-?} GB; free now $(free_gb) GB; projected free at the end ${ENDFREE:-?} GB (reserve ${RESERVE_GB} GB; the crawl stops itself below 105 GB)"
  echo "- last fetch line: $(grep '^\[k510_predicates\] fetch:' $CL 2>/dev/null | tail -1)"
  [ -e $MON/k510_finalize.done ] && echo "- finalize: done $(cat $MON/k510_finalize.done)"
  [ -e $MON/k510_finalize.failed ] && echo "- finalize: FAILED $(cat $MON/k510_finalize.failed) (fix, delete monitor/k510_finalize.failed to retry)"
  true
} > "$STAT.part" && mv "$STAT.part" "$STAT"
once() {   # once <key> <message>: one alert per key per day
  local m=$MON/locks/alert-$1-$(today); [ -e $m ] && return 0; touch $m; alert k510_crawl_watch "$2"; }

if pdf_crawl_running; then
  [ -n "${ENDFREE:-}" ] && [ "${ENDFREE%.*}" -lt "$RESERVE_GB" ] 2>/dev/null && \
    once projection "510(k) PDF crawl: projected free disk at the end ${ENDFREE} GB is below the ${RESERVE_GB} GB reserve (the crawl stops itself at 105 GB; move PDFs to the PI's archive or approve deletions of old snapshots)"
  exit 0
fi
[ -e $MON/k510_finalize.done ] && exit 0
[ -e $MON/k510_finalize.failed ] && exit 0
LAST=$(grep '^\[k510_predicates\] fetch:' $CL 2>/dev/null | tail -1)
case "$LAST" in
  *"; complete")
    if [ $DRY = 1 ]; then log "dry run: crawl complete; would start k510_pred_finalize.sh final"; exit 0; fi
    if [ "$H" -ge 9 ] && [ "$H" -lt 12 ]; then log "crawl complete; finalize waits until after 12:00"; exit 0; fi
    if pgrep -f "k510_pred_finalize.sh" > /dev/null; then log "finalize already running"; exit 0; fi
    log "crawl complete ($LAST); starting k510_pred_finalize.sh final"
    alert k510_crawl_watch "510(k) PDF crawl complete; k510_pred_finalize.sh final started (no upload)"
    MDKG_WT=$WT nohup setsid nice -n 10 bash $HERE/k510_pred_finalize.sh final > $LD/k510-finalize.log 2>&1 < /dev/null &
    ;;
  *)
    once stopped "510(k) PDF crawl is not running and did not complete: ${LAST:-no fetch summary line in $CL}. Resume with the command in docs/OPERATIONS.md (files present are not requested again)."
    ;;
esac
