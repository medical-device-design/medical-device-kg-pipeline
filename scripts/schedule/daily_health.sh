#!/bin/bash
# daily_health.sh -- lightweight daily monitor (cron, every day 06:40 local).
#
#   bash scripts/schedule/daily_health.sh [--dry-run]
#
# Checks, all read-only: vm-status; free disk on /srv (alert below 130 GB, i.e. 30 GB above
# the 100 GB reserve); JVMs (ours <= 8 GB, other project's over 4 GB noted); the lakeFS
# release watcher (its crontab line and the age of logs/lakefs-watch.log); the long PDF crawl
# (progress.json); the last weekly and monthly runs; reachability of every source endpoint
# (one HEAD/GET per host family, status code and time only, no credentials sent) including the
# radiation corrective-action search (cfPCD_RH/rh_res.cfm, HTTP 503 since October 2026),
# whose status is appended daily to monitor/radiation_corrective_actions.tsv; retention
# candidates (listed, never deleted).
# Output: monitor/latest.md (overwritten), monitor/daily/YYYY-MM-DD.md, monitor/alerts.log.
# --dry-run: everything except the network checks, which are listed but not requested.
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/common.sh" "$@"
lock daily_health
UA="medical-device-kg/monitor (Hofstra University; daily reachability check)"
D=$(today)
REP=$MON/daily/$D.md
NEW_ALERTS=()
a() { NEW_ALERTS+=("$1"); alert daily_health "$1"; }

ENDPOINTS="openfda_index|https://api.fda.gov/download.json
openfda_bulk|https://download.open.fda.gov/device/enforcement/device-enforcement-0001-of-0001.json.zip
accessdata_510k|https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/pmn.cfm
accessdata_pdf|https://www.accessdata.fda.gov/cdrh_docs/pdf8/K080949.pdf
accessdata_hde|https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfHDE/hde.cfm
accessdata_denovo|https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/denovo.cfm
accessdata_recall|https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRES/res.cfm
accessdata_hde_zip|https://www.accessdata.fda.gov/premarket/ftparea/hde.zip
radiation_corrective_actions|https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD_RH/rh_res.cfm
fdagov_warning_letters|https://www.fda.gov/inspections-compliance-enforcement-and-criminal-investigations/compliance-actions-and-activities/warning-letters
fdagov_safety_comms|https://www.fda.gov/medical-devices/medical-device-safety/safety-communications
fdagov_shortages|https://www.fda.gov/medical-devices/medical-device-supply-chain-and-shortages/medical-device-shortages-list
data_dashboard_api|https://api-datadashboard.fda.gov/v1/
ecfr|https://www.ecfr.gov/api/versioner/v1/titles.json
census_geocoder|https://geocoding.geo.census.gov/geocoder/benchmarks"

{
echo "# medical-device-kg daily health $(now)$([ $DRY = 1 ] && echo '  (DRY RUN)')"
echo
echo "## vm-status (read-only)"
echo '```'; vm_status; echo '```'
echo
echo "## Disk"
F=$(free_gb)
echo "- /srv free: ${F} GB (reserve ${RESERVE_GB} GB; alert below 130 GB)"
[ "$F" -lt 130 ] && a "free disk ${F} GB is below 130 GB (reserve 100 GB)"
echo "- largest work directories: $(du -s --block-size=1G $W/* 2>/dev/null | sort -rn | head -5 | awk '{printf "%s %s GB; ", $2, $1}' | sed "s#$W/##g")"
echo
echo "## JVMs"
ps -eo pid=,etime=,rss=,args= | awk '$4 ~ /(^|\/)java$/ {printf "- pid %s up %s rss %.1f GB: %s\n", $1, $2, $3/1048576, substr($0, index($0,$4), 110)}'
O=$(big_other_jvms); [ -n "$O" ] && echo "- other project's JVM over 4 GB: $O"
for p in 161134 345937; do [ -d /proc/$p ] && echo "- Fuseki pid $p running (not touched)" || echo "- Fuseki pid $p not running"; done
echo
echo "## lakeFS release watcher"
if crontab -l 2>/dev/null | grep -q lakefs_release_watch.sh; then echo "- crontab entry present: $(crontab -l | grep lakefs_release_watch.sh | cut -c1-140)"
else echo "- crontab entry MISSING"; a "lakeFS watcher crontab entry missing"; fi
WL=$LD/lakefs-watch.log
if [ -e $WL ]; then
  age=$(( ($(date +%s) - $(stat -c %Y $WL)) / 60 ))
  echo "- logs/lakefs-watch.log last written ${age} min ago; last line: $(tail -1 $WL | cut -c1-160)"
  [ $age -gt 150 ] && a "lakeFS watcher log not written for ${age} min"
  [ -e $MON/lakefs/latest.md ] && echo "- monitor/lakefs/latest.md: $(grep -m1 -i 'status\|stable' $MON/lakefs/latest.md | cut -c1-160)"
else a "lakeFS watcher log missing"; fi
echo
echo "## Long jobs"
P=$W/downloads-2026-11-04/k510_summaries/progress.json
if [ -e $P ]; then
  echo "- 510(k) summary PDF crawl: $(python3 -c "import json;d=json.load(open('$P'));print(f\"{d['files_present']}/{d['total']} PDFs present, {d['rate_per_s']}/s, ETA {d['eta_hours']} h, stop: {d['stop_reason']}, updated {d['updated']}\")")"
  pdf_crawl_running && echo "  (crawl process running)" || echo "  (no crawl process running)"
  [ -e $MON/k510_crawl_status.md ] && grep "projected free" $MON/k510_crawl_status.md | sed "s/^/  /"
  [ -e $MON/k510_finalize.done ] && echo "- k510_predicates finalize: done ($(cat $MON/k510_finalize.done))"
  [ -e $MON/k510_finalize.failed ] && echo "- k510_predicates finalize: FAILED ($(cat $MON/k510_finalize.failed))"
fi
for j in weekly monthly; do
  L=$(ls -t $MON/$j/*.md 2>/dev/null | head -1)
  echo "- last $j report: ${L:-none}${L:+ ($(grep -m1 -i '^status' $L | cut -c1-120))}"
done
echo
echo "## Source endpoints"
echo "| endpoint | HTTP | seconds | url |"
echo "|---|---|---|---|"
echo "$ENDPOINTS" | while IFS='|' read -r name url; do
  if [ $DRY = 1 ]; then echo "| $name | (dry run) | | $url |"; continue; fi
  r=$(curl -s -o /dev/null -A "$UA" --max-time 40 -r 0-1023 -w "%{http_code} %{time_total}" "$url" 2>/dev/null); code=${r%% *}; t=${r##* }
  echo "| $name | ${code:-000} | ${t:-} | $url |"
  case "$name" in
    radiation_corrective_actions) printf "%s\t%s\t%s\n" "$(now)" "${code:-000}" "$t" >> $MON/radiation_corrective_actions.tsv ;;
    data_dashboard_api) case "$code" in 2*|3*|4*) ;; *) a "endpoint $name answered ${code:-no response}";; esac ;;
    *) case "$code" in 2*|3*) ;; *) a "endpoint $name answered ${code:-no response}";; esac ;;
  esac
  sleep 1
done
echo
echo "Radiation corrective-action search, last 7 checks:"
tail -7 $MON/radiation_corrective_actions.tsv 2>/dev/null | sed 's/^/    /'
echo
echo "## Retention"
retention_report
echo "- candidates for PI-approved deletion: $(grep -c '^| \(out\|downloads\|weekly\|fulltest\|tdb\)' $MON/retention_candidates.md) (monitor/retention_candidates.md)"
echo
echo "## Alerts in the last 24 h"
awk -F'\t' -v since="$(date -Is -d '24 hours ago')" '$1 >= since {print "- " $1 " " $2 ": " $3}' $MON/alerts.log 2>/dev/null | tail -20
} > "$REP.part" 2>&1
mv "$REP.part" "$REP"
cp "$REP" "$MON/latest.md"
log "daily health report: $REP"
