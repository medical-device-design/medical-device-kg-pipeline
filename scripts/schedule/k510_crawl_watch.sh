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
# When the crawl process has ended: if its last "[k510_predicates] fetch:" line ends in "complete":
#   1. start k510_pred_finalize.sh first (nohup, nice; not between 09:00 and 12:00), once: everything final
#      does except the release list, with the OCR text already collected ($MDKG_OCR_DIR, default work/ocr-*);
#      it writes k510_predicates.ocr_backlog.tsv (image-only PDFs still without text) and
#      monitor/k510_finalize_first.done;
#   2. then, until the final run is done: monitor/k510_ocr_needed.md with the backlog, an estimate of the Star
#      time and the command for the PI's PC (scripts\star\post_crawl_ocr.ps1: OCR batch on Star HPC through
#      the WSL relay, which cron on the VM cannot drive, then k510_pred_finalize.sh final with MDKG_OCR_DIR);
#      one alert. With an empty backlog the watcher starts k510_pred_finalize.sh final itself.
#   The final run writes monitor/k510_predicates_finalize.md and monitor/k510_finalize.done (or .failed,
#   which stops retries). Any other crawl ending (disk floor reached, FDA refusing requests, killed) ->
#   alert + status, no finalize.
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
  [ -e $MON/k510_finalize_first.done ] && echo "- first pass (OCR backlog): done $(cat $MON/k510_finalize_first.done)"
  [ -e $MON/k510_ocr_needed.md ] && echo "- OCR needed: see $MON/k510_ocr_needed.md"
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
BL=$W/out-2026-11-04/k510_predicates.ocr_backlog.tsv
ocr_needed() {   # monitor/k510_ocr_needed.md: backlog, Star estimate, the PC command; prints the backlog count
  python3 -I - "$BL" "$K" "$MON/k510_ocr_needed.md" "$(cat $MON/k510_finalize_first.done)" <<'PY'
import sys, time
from pathlib import Path
bl, K, out, first = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
rows = [l.split("\t") for l in bl.read_text(encoding="utf-8").splitlines()[1:] if l.strip()]
n = len(rows)
size = sum((K / "pdf" / r[0][:3] / f"{r[0]}.pdf").stat().st_size for r in rows
           if (K / "pdf" / r[0][:3] / f"{r[0]}.pdf").exists())
hours = n * 2.4 / 4 / 3600     # first Star batch: 2.4 s per 510(k) PDF (8 CPUs per task), 4 tasks at once
tasks = (n + 99) // 100
day = first.split("T")[0] or time.strftime("%Y-%m-%d")
b = f"{day}-k510"
txt = f"""# 510(k) summaries: OCR needed before the final k510_predicates.ttl ({time.strftime('%Y-%m-%dT%H:%M:%S%z')})

The 510(k) summary crawl is complete and the first pass ran ({first}).
Nothing has been uploaded, merged or tagged; RELEASE_INPUTS.txt and UPLOAD_PLAN.md are unchanged.

- image-only PDFs still without text: {n:,} ({size / 1e9:.1f} GB), listed in {bl}
- Star HPC estimate: {tasks} array tasks of 100 PDFs (8 CPUs, 16G each, 4 at once = the account cap of 32 CPUs / 64G),
  about {hours:.1f} h of run time at 2.4 s per PDF (first batch, November 16, 2026) plus queue waits; each task
  takes about 4 min, well inside the 1 h limit of QOS normal. No node pinned, no GPU requested.
- transfer through the PI's PC (WSL relay): about {size / 1e9:.1f} GB VM -> PC -> Star; OCR text back to the VM
  (a few hundred MB; the OCR'd PDFs only with -WithPdf).

Run from the PI's PC (PowerShell, outside 09:00-12:00; the script waits for the Star jobs and can be re-run
with the same -Batch to resume, each step skips work already done):

    & 'A:\\Big Data\\Knowledge Graphs\\medical-device-kg\\scripts\\star\\post_crawl_ocr.ps1' -Batch {b}

It runs ocr_batch.sh mklist -> stage -> submit -> wait (polls every 5 min) -> collect (OCR text to
work/ocr-{b}/out/txt on the VM) -> clean (PC relay copies) -> then on the VM
`MDKG_OCR_DIR='{K.parent.parent}/ocr-*' bash scripts/schedule/k510_pred_finalize.sh final`
(check, then RELEASE_INPUTS.txt / UPLOAD_PLAN.md; no upload). Single steps: -Step mklist|stage|submit|wait|collect|clean|finalize.

To finish without more OCR (the image-only PDFs then stay without links), on the VM:
`cd /srv/medical-device-kg/work/v007 && nohup setsid nice -n 10 bash scripts/schedule/k510_pred_finalize.sh final > /srv/medical-device-kg/logs/k510-finalize.log 2>&1 &`
"""
tmp = out.with_suffix(".part")
tmp.write_text(txt, encoding="utf-8")
tmp.replace(out)
print(n)
PY
}
case "$LAST" in
  *"; complete")
    if [ $DRY = 1 ]; then
      if [ -e $MON/k510_finalize_first.done ]; then log "dry run: crawl complete, first pass done; would write k510_ocr_needed.md"
      else log "dry run: crawl complete; would start k510_pred_finalize.sh first"; fi
      exit 0
    fi
    if pgrep -f "schedule/k510_pred_finalize[.]sh" > /dev/null; then log "finalize already running"; exit 0; fi
    if [ -e $MON/k510_finalize_first.done ]; then
      N=$(ocr_needed) || { log "could not write k510_ocr_needed.md"; exit 0; }
      if [ "$N" = 0 ]; then
        if [ "$H" -ge 9 ] && [ "$H" -lt 12 ]; then log "no OCR backlog; final waits until after 12:00"; exit 0; fi
        rm -f $MON/k510_ocr_needed.md
        log "no OCR backlog; starting k510_pred_finalize.sh final"
        alert k510_crawl_watch "510(k) first pass: no image-only PDFs left; k510_pred_finalize.sh final started (no upload)"
        MDKG_WT=$WT nohup setsid nice -n 10 bash $HERE/k510_pred_finalize.sh final > $LD/k510-finalize.log 2>&1 < /dev/null &
      else
        once ocr_needed "510(k) first pass done: $N image-only summary PDFs need OCR on Star before the final run; command in monitor/k510_ocr_needed.md (run from the PI's PC)"
      fi
      exit 0
    fi
    if [ "$H" -ge 9 ] && [ "$H" -lt 12 ]; then log "crawl complete; first pass waits until after 12:00"; exit 0; fi
    log "crawl complete ($LAST); starting k510_pred_finalize.sh first (OCR backlog; release list untouched)"
    alert k510_crawl_watch "510(k) PDF crawl complete; k510_pred_finalize.sh first started (OCR backlog; no upload, release list untouched)"
    MDKG_WT=$WT nohup setsid nice -n 10 bash $HERE/k510_pred_finalize.sh first > $LD/k510-finalize-first.log 2>&1 < /dev/null &
    ;;
  *)
    once stopped "510(k) PDF crawl is not running and did not complete: ${LAST:-no fetch summary line in $CL}. Resume with the command in docs/OPERATIONS.md (files present are not requested again)."
    ;;
esac
