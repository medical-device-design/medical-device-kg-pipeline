#!/bin/bash
# publish_vm_reports.sh -- copy the VM's latest monitoring reports to the `vm-reports`
# branch on GitHub, so they can be read without the Hofstra VPN (e.g. by a cloud
# scheduled summary, the Layer 3 agent, or a person on a phone).
#
# Publishes ONLY small text reports, never data or credentials:
#   recalls/<date>/report.md, report.json, new_recalls.tsv, class_changes.tsv
#   recalls/history.csv, recalls/LATEST.md       (from scripts/recall_weekly.sh)
#   health/LATEST.txt                            (from scripts/vm_health_check.sh, Layer 2)
#
# Uses its own deploy key ($MDKG/.git-deploy-key-monitor, write access, created on the
# VM; the private key never leaves it). If the key is missing it does nothing.
# Called at the end of recall_weekly.sh and by cron after the Layer 2 health check.
set -u
MDKG=/srv/medical-device-kg
KEY=$MDKG/.git-deploy-key-monitor
MON=$MDKG/monitor/recalls
HEALTH=$MDKG/health/latest.txt
REPO=git@github.com:medical-device-design/medical-device-kg-pipeline.git
BRANCH=vm-reports
PUB=$MDKG/work/vm-reports
[ -f "$KEY" ] || { echo "publish: no $KEY; skipping"; exit 0; }
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new"

if [ ! -d "$PUB/.git" ]; then
  if git ls-remote --exit-code --heads "$REPO" "$BRANCH" >/dev/null 2>&1; then
    git clone -q --branch "$BRANCH" --single-branch "$REPO" "$PUB" || exit 1
  else
    mkdir -p "$PUB" && cd "$PUB" && git init -q && git checkout -q --orphan "$BRANCH" \
      && git remote add origin "$REPO" || exit 1
  fi
fi
cd "$PUB" || exit 1
git pull -q --rebase origin "$BRANCH" 2>/dev/null || true

LATEST=$(ls -1d $MON/20*/ 2>/dev/null | sort | tail -1)
if [ -n "$LATEST" ]; then
  D=$(basename "$LATEST")
  mkdir -p "recalls/$D"
  for f in report.md report.json new_recalls.tsv class_changes.tsv; do
    [ -f "$LATEST/$f" ] && cp "$LATEST/$f" "recalls/$D/"
  done
  cp "$LATEST/report.md" recalls/LATEST.md
  [ -f "$MON/history.csv" ] && cp "$MON/history.csv" recalls/history.csv
fi
if [ -f "$HEALTH" ]; then
  mkdir -p health && sed 's/\x1b\[[0-9;]*m//g' "$HEALTH" > health/LATEST.txt   # strip terminal colours
fi
cat > README.md <<'EOF'
# vm-reports

Weekly monitoring reports copied from VM `.83` by `scripts/publish_vm_reports.sh`
(branch `rebuild-stage3-sources` / `main`). Text reports only: no data, no credentials.

- `recalls/LATEST.md`: the latest recall monitor report (FDA totals vs graph, class coverage, new recalls, warnings, validation)
- `recalls/<date>/`: each week's report, `new_recalls.tsv` and `class_changes.tsv`
- `recalls/history.csv`: one line per weekly run
- `health/LATEST.txt`: the latest Layer 2 VM + lakeFS health report

Never merge this branch into `main`.
EOF

# guard: never publish anything that looks like a credential
if grep -rIlE "PRIVATE KEY|secret_access_key|AKIA[0-9A-Z]{16}|lakectl.*secret" --exclude-dir=.git . >/dev/null 2>&1; then
  echo "publish: credential-like text found; NOT publishing"; exit 1
fi
git add -A
if git -c user.name="MDKG VM .83 monitor" -c user.email="mdkg-monitor@users.noreply.github.com" \
     commit -q -m "VM reports $(date +%F)"; then
  git push -q origin "HEAD:$BRANCH" && echo "publish: pushed to $BRANCH" || { echo "publish: push FAILED"; exit 1; }
else
  echo "publish: nothing new"
fi
