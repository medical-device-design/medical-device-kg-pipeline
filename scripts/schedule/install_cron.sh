#!/bin/bash
# install_cron.sh -- add (or replace) the four scheduled medical-device-kg jobs in the user's crontab.
#
#   bash scripts/schedule/install_cron.sh [--print]      (--print: show the result, change nothing)
#
# User crontab only (no root). Existing lines are kept; only lines tagged "# mdkg-schedule:" are replaced.
# The jobs run from the worktree given by MDKG_WT (default /srv/medical-device-kg/work/v007).
# Times are local (America/New_York on the VM) and avoid 09:00-12:00 and the existing Monday jobs
# (05:30 recall weekly, 06:30 health check, 07:15 report publish, hourly :17 lakeFS watcher).
set -u
WT=${MDKG_WT:-/srv/medical-device-kg/work/v007}
S=$WT/scripts/schedule
L=/srv/medical-device-kg/logs
NEW="40 6 * * * nice -n 10 bash $S/daily_health.sh >> $L/schedule-daily.log 2>&1 # mdkg-schedule: daily health monitor
10 1 * * 6 nice -n 10 bash $S/weekly_fetch.sh >> $L/schedule-weekly.log 2>&1 # mdkg-schedule: weekly fetch + change detection
20 2 3 * * nice -n 10 bash $S/monthly_refresh.sh >> $L/schedule-monthly.log 2>&1 # mdkg-schedule: monthly refresh + release candidate (no upload)
*/30 0-8,12-23 * * * nice -n 10 bash $S/k510_crawl_watch.sh >> $L/schedule-k510-watch.log 2>&1 # mdkg-schedule: 510(k) PDF crawl watcher, starts k510_pred_finalize.sh when the crawl completes (no upload)"
CUR=$(crontab -l 2>/dev/null | grep -v '# mdkg-schedule:')
RES=$(printf "%s\n%s\n" "$CUR" "$NEW" | sed '/^$/d')
if [ "${1:-}" = "--print" ]; then echo "$RES"; exit 0; fi
B=/srv/medical-device-kg/monitor/crontab.bak-$(date +%Y%m%d%H%M%S)
crontab -l > "$B" 2>/dev/null || true
echo "$RES" | crontab -
echo "crontab installed (previous saved to $B):"
crontab -l
