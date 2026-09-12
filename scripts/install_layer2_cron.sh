#!/usr/bin/env bash
#
# install_layer2_cron.sh -- install or refresh the weekly Layer 2 health check in
# the current user's crontab on the VM (.83). Idempotent: re-running replaces the
# existing entry rather than adding a duplicate.
#
#   bash scripts/install_layer2_cron.sh
#
# Schedule: Mondays 06:30 UTC (30 min after Layer 1's weekly smoke). Output is
# appended to $MDKG/health/cron.log; each run also writes a timestamped report.
#
set -euo pipefail

MDKG="${MDKG:-/srv/medical-device-kg}"
REPO="$MDKG/repo"
LOG="$MDKG/health/cron.log"
MARK="medical-device-kg Layer 2 health check"
LINE="30 6 * * 1 cd $REPO && /usr/bin/env bash scripts/vm_health_check.sh >> $LOG 2>&1 # $MARK"

mkdir -p "$MDKG/health"

tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v "$MARK" > "$tmp" || true
echo "$LINE" >> "$tmp"
crontab "$tmp"
rm -f "$tmp"

echo "Installed/refreshed weekly Layer 2 cron:"
crontab -l | grep "$MARK"
