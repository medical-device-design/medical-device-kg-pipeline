#!/bin/bash
# common.sh -- shared helpers for the scheduled jobs in scripts/schedule/ (sourced, not run).
#
# Every job: runs vm-status first (read-only), takes a flock so a job never overlaps itself
# (and heavy jobs never overlap each other), runs under nice/ionice, keeps every JVM at or
# below 8 GB, defers when another project's JVM over 4 GB runs or when free disk would fall
# below what it needs plus the 100 GB reserve, writes its report under $MON and appends
# alerts to $MON/alerts.log. Nothing here uploads, merges, tags or deletes data.
set -u
M=/srv/medical-device-kg
W=$M/work
WT=${MDKG_WT:-$W/v007}                 # pipeline worktree the jobs run
MON=$M/monitor
LD=$M/logs
J=$M/tools/apache-jena-5.2.0/bin
VMSTATUS=/srv/fda-products-kg/tools/bin/vm-status
RESERVE_GB=100
LOCKDIR=$M/monitor/locks
mkdir -p "$MON" "$LD" "$LOCKDIR" "$MON/daily" "$MON/weekly" "$MON/monthly"
export PYTHONDONTWRITEBYTECODE=1
# OCR text of image-only 510(k)/HDE PDFs (Star HPC batches, scripts\star\ocr_batch.sh on the PI's PC):
# the PDF transforms read it from these folders (":"-separated, wildcards expanded by k510_predicates.py)
export MDKG_OCR_DIR=${MDKG_OCR_DIR:-$W/ocr-*}

DRY=0
for a in "$@"; do [ "$a" = "--dry-run" ] && DRY=1; done

now() { date -Is; }
today() { date +%F; }
free_gb() { df --output=avail -BG "$W" | tail -1 | tr -dc 0-9; }
log() { echo "[$(now)] $*"; }
alert() {   # alert <job> <message>: one line in alerts.log (the daily report shows the newest)
  echo "$(now)	$1	$2" >> "$MON/alerts.log"; log "ALERT: $2"; }

# lock <name>: take $LOCKDIR/<name>.lock or exit 0 with a note (another run holds it)
lock() {
  exec 9> "$LOCKDIR/$1.lock"
  if ! flock -n 9; then log "another $1 run holds $LOCKDIR/$1.lock; this run exits"; exit 0; fi
}
# heavy_lock: weekly fetch and monthly refresh never run at the same time
heavy_lock() {
  exec 8> "$LOCKDIR/heavy.lock"
  if ! flock -n 8; then return 1; fi
  return 0
}

vm_status() {   # read-only; output kept for the report
  if [ -x "$VMSTATUS" ]; then timeout 120 "$VMSTATUS" 2>&1 | head -40; else echo "vm-status not found at $VMSTATUS"; fi
}

big_other_jvms() {   # java processes of other projects with -Xmx over 4 GB (or none given), or RSS over 4 GB
  ps -eo pid=,user=,rss=,args= | awk '$4 ~ /(^|\/)java$/' | grep -v "/srv/medical-device-kg/" | while read -r pid user rss args; do
    x=$(echo "$args" | grep -o -- '-Xmx[0-9]*[gGmM]' | tail -1); g=5
    case "$x" in *[gG]) g=$(echo "$x" | tr -dc 0-9);; *[mM]) g=$(( $(echo "$x" | tr -dc 0-9) / 1024 ));; esac
    r=$(( rss / 1048576 ))
    if [ "$g" -gt 4 ] || [ "$r" -gt 4 ]; then echo "pid $pid $user ${x:-no -Xmx} rss ${r}G: $(echo "$args" | cut -c1-120)"; fi
  done; }

# gate <job> <needed GB>: 0 when no other project's JVM over 4 GB runs and free >= needed + reserve
GATE_MSG=""
gate() {
  local job=$1 need=$2 f o
  f=$(free_gb); o=$(big_other_jvms)
  if [ -n "$o" ]; then GATE_MSG="another project's JVM over 4 GB is running: $(echo "$o" | tr '\n' ' ')"; return 1; fi
  if [ "$f" -lt $(( need + RESERVE_GB )) ]; then
    GATE_MSG="needs ${need} GB plus the ${RESERVE_GB} GB reserve; ${f} GB free (short by $(( need + RESERVE_GB - f )) GB)"; return 1; fi
  GATE_MSG="ok: ${f} GB free, needs ${need} GB + ${RESERVE_GB} GB reserve; no other JVM over 4 GB"
  return 0
}

pdf_crawl_running() { pgrep -f "v007e_run.py k510_predicates fetch|k510_predicates.*fetch" >/dev/null 2>&1; }

# run a command niced (CPU 10, idle-ish IO); JVMs started inside get JVM_ARGS from the caller (<= 8 GB)
nicely() { nice -n 10 ionice -c2 -n7 "$@"; }

# retention: list (never delete) dated directories beyond the newest two of each kind
retention_report() {
  local out=$MON/retention_candidates.md kind d keep
  {
    echo "# Retention candidates ($(now))"
    echo
    echo "Policy: keep the newest 2 dated directories of each kind. Older ones are listed here for"
    echo "deletion with the PI's approval; no job deletes them. Directories named in a release list"
    echo "(out-*/RELEASE_INPUTS.txt) or in a lakeFS upload plan are marked and must be kept until that"
    echo "release is uploaded and verified."
    echo
    echo "| kind | directory | size | note |"
    echo "|---|---|---|---|"
    for kind in out downloads weekly fulltest; do
      ls -d $W/$kind-20[0-9][0-9]-[0-9][0-9]-[0-9][0-9] 2>/dev/null | sort -r | tail -n +3 | while read -r d; do
        note=""
        grep -lq "$d/" $W/out-*/RELEASE_INPUTS.txt 2>/dev/null && note="in a release list: keep until uploaded"
        echo "| $kind | ${d#$W/} | $(du -sh "$d" 2>/dev/null | cut -f1) | ${note:-older than the newest 2} |"
      done
    done
    ls -d $W/tdb-* 2>/dev/null | sort -r | tail -n +3 | while read -r d; do
      echo "| tdb store | ${d#$W/} | $(du -sh "$d" 2>/dev/null | cut -f1) | older than the newest 2 stores |"; done
    echo
    echo "Free on /srv: $(free_gb) GB (reserve ${RESERVE_GB} GB)."
  } > "$out.part" && mv "$out.part" "$out"
}
