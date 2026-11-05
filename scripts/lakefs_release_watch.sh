#!/bin/bash
# lakefs_release_watch.sh -- READ-ONLY watch on the lakeFS repo for a release in progress.
#
# After a merge into lakeFS `main`, RENCI's converter builds HDT/QLever files and, on
# success, creates a branch `stable_vX_Y_Z`. Nobody is notified, so this script checks
# the branch list, tags and recent action runs, and writes a short status report:
#
#   $MDKG/monitor/lakefs/latest.md
#
# When the report changes (a new stable branch, tag or action run), it calls
# scripts/publish_vm_reports.sh so the report appears on the `vm-reports` branch as
# lakefs/LATEST.md, readable without the VPN.
#
#   bash scripts/lakefs_release_watch.sh [EXPECTED_BRANCH]     # default: next stable_v* after the newest
#
# Never creates tags, merges, uploads or deletes anything in lakeFS. Safe to run from cron.
set -u
MDKG=/srv/medical-device-kg
L="$MDKG/tools/lakectl -c $MDKG/.lakectl.yaml --no-color"
R=lakefs://medical-device-kg
OUTD=$MDKG/monitor/lakefs
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUTD"

branches=$($L branch list $R --amount 200 2>&1) || { echo "watch: lakectl failed"; exit 1; }
tags=$($L tag list $R --amount 200 2>&1 | awk 'NF>=2 {print $1, substr($2,1,12)}')
runs=$($L actions runs list $R --amount 5 2>&1 | awk -F'\t' 'NF>=7 {print "| " $2 " | " substr($3,1,19) " | " ($5==""?"(tag)":$5) " | " substr($6,1,12) " | " $NF " |"}')
main=$(echo "$branches" | awk '$1=="main" {print substr($2,1,12)}')
stables=$(echo "$branches" | awk '$1 ~ /^stable_v/ {print $1}' | sort -V)
newest=$(echo "$stables" | tail -1)
if [ -n "${1:-}" ]; then
  expect=$1
else  # next patch number after the newest stable branch
  expect=$(echo "$newest" | awk -F_ '{printf "%s_%s_%s_%d", $1, $2, $3, $4+1}')
fi
if echo "$stables" | grep -qx "$expect"; then
  status="READY: \`$expect\` exists. The data owner can now create the release tag on it (this script never tags)."
else
  status="WAITING: \`$expect\` does not exist yet. The newest stable branch is \`$newest\`."
fi

new=$(mktemp)
{
  echo "# lakeFS release watch: medical-device-kg"
  echo
  echo "**Status:** $status"
  echo
  echo "- \`main\` head: \`$main\`"
  echo "- Stable branches: $(echo $stables | sed 's/ /, /g')"
  echo "- Tags: $(echo "$tags" | awk '{print $1}' | sort -V | tr '\n' ' ' | sed 's/ $//; s/ /, /g')"
  echo
  echo "## Last 5 lakeFS action runs"
  echo
  echo "| Hook | Started (UTC) | Branch | Commit | Result |"
  echo "|---|---|---|---|---|"
  echo "$runs"
  echo
  echo "A failed post-merge run, or no stable branch long after a merge, means the conversion"
  echo "needs attention from RENCI."
} > "$new"

cur=$OUTD/latest.md
if [ -f "$cur" ] && cmp -s <(head -n -2 "$cur") "$new"; then   # last 2 lines: blank + Changed:
  echo "watch: no change ($status)"; rm -f "$new"; exit 0
fi
{ cat "$new"; echo; echo "Changed: $(date -u +%Y-%m-%dT%H:%MZ)"; } > "$cur"; rm -f "$new"
echo "watch: report changed -> $status"
if [ -f "$HERE/publish_vm_reports.sh" ]; then bash "$HERE/publish_vm_reports.sh"; fi
