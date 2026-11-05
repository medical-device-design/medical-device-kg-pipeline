#!/bin/bash
# publish_vm_reports.sh -- copy the build VM's latest monitoring reports to the
# `vm-reports` branch on GitHub, so they can be read without the VPN (e.g. by a cloud
# scheduled summary, the Layer 3 agent, or a person on a phone).
#
# The repository is PUBLIC, so every file is REDACTED before it is published, and a
# guard refuses to publish if anything identifying is left:
#   real hostname            -> build-vm
#   every local username     -> <user>
#   /home/<anything>         -> ~
#   credentials file paths   -> <credentials file>
# Names are detected at run time (hostname, the users in /home, whoami); none are
# written in this script.
#
# Publishes ONLY small text reports, never data or credentials:
#   recalls/<date>/report.md, report.json, new_recalls.tsv, class_changes.tsv
#   recalls/history.csv, recalls/LATEST.md       (from scripts/recall_weekly.sh)
#   health/LATEST.txt                            (from scripts/vm_health_check.sh, Layer 2)
#   lakefs/LATEST.md                             (from scripts/lakefs_release_watch.sh)
#
#   bash scripts/publish_vm_reports.sh                  # redact, guard, commit, push
#   bash scripts/publish_vm_reports.sh --dry-run DIR    # redact + guard into DIR; no git, no push
#
# Uses its own deploy key ($MDKG/.git-deploy-key-monitor, created on the VM; the private
# key never leaves it). Does nothing if the key is missing or $MDKG/.publish-paused exists.
set -u
MDKG=/srv/medical-device-kg
KEY=$MDKG/.git-deploy-key-monitor
MON=$MDKG/monitor/recalls
HEALTH=$MDKG/health/latest.txt
LAKEFS=$MDKG/monitor/lakefs/latest.md
REPO=git@github.com:medical-device-design/medical-device-kg-pipeline.git
BRANCH=vm-reports
PUB=$MDKG/work/vm-reports
DRY=""
if [ "${1:-}" = "--dry-run" ]; then DRY="${2:?--dry-run needs a directory}"; fi

if [ -z "$DRY" ]; then
  [ -f "$MDKG/.publish-paused" ] && { echo "publish: paused ($MDKG/.publish-paused exists)"; exit 0; }
  [ -f "$KEY" ] || { echo "publish: no deploy key; skipping"; exit 0; }
fi

# ---------------------------------------------------------------- identifiers, found at run time
# longest first, so a fully qualified name is replaced before its short form
HOSTS=$( { hostname; hostname -s; hostname -f; } 2>/dev/null | tr 'A-Z' 'a-z' | grep -v '^$' | sort -u \
         | awk '{ print length, $0 }' | sort -rn | cut -d' ' -f2- )
USERS=$( { ls -1 /home 2>/dev/null; whoami; echo "${USER:-}"; } | grep -v '^$' | sort -u )

redact() {   # redact FILE in place
  local f=$1 h u
  # credentials file paths first (they may also contain /home/<user>)
  sed -E -i 's#[^[:space:]()"'"'"']*(lakefs\.env|lakectl[^[:space:]()"'"'"']*\.ya?ml|\.git-deploy-key[^[:space:]()"'"'"']*|\.aws/credentials|\.netrc)#<credentials file>#g' "$f"
  sed -E -i 's#/home/[^[:space:]/()"'"'"']+#~#g' "$f"
  for h in $HOSTS; do sed -i "s/$h/build-vm/Ig" "$f"; done
  for u in $USERS; do sed -E -i "s/\\b$u\\b/<user>/g" "$f"; done
  sed -i 's/\x1b\[[0-9;]*m//g' "$f"                         # terminal colours
}

guard() {    # exit non-zero if DIR still holds anything identifying or credential-like
  local d=$1 bad=0 h u
  if grep -rIlE "PRIVATE KEY|secret_access_key|AKIA[0-9A-Z]{16}" --exclude-dir=.git "$d" >/dev/null 2>&1; then
    echo "guard: credential-like text"; bad=1; fi
  for h in $HOSTS; do
    grep -rIliF "$h" --exclude-dir=.git "$d" >/dev/null 2>&1 && { echo "guard: hostname found"; bad=1; }
  done
  for u in $USERS; do
    grep -rIlwF "$u" --exclude-dir=.git "$d" >/dev/null 2>&1 && { echo "guard: a local username found"; bad=1; }
  done
  grep -rIlF "/home/" --exclude-dir=.git "$d" >/dev/null 2>&1 && { echo "guard: /home/ path found"; bad=1; }
  grep -rIlE '(^|[^0-9.])(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3})($|[^0-9])' \
       --exclude-dir=.git "$d" >/dev/null 2>&1 && { echo "guard: private IP address found"; bad=1; }
  grep -rIlE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' --exclude-dir=.git "$d" >/dev/null 2>&1 \
       && { echo "guard: email address found"; bad=1; }
  return $bad
}

# ---------------------------------------------------------------- stage: copy, then redact
STAGE=$(mktemp -d)
LATEST=$(ls -1d $MON/20*/ 2>/dev/null | sort | tail -1)
if [ -n "$LATEST" ]; then
  D=$(basename "$LATEST")
  mkdir -p "$STAGE/recalls/$D"
  for f in report.md report.json new_recalls.tsv class_changes.tsv; do
    [ -f "$LATEST/$f" ] && cp "$LATEST/$f" "$STAGE/recalls/$D/"
  done
  cp "$LATEST/report.md" "$STAGE/recalls/LATEST.md"
  [ -f "$MON/history.csv" ] && cp "$MON/history.csv" "$STAGE/recalls/history.csv"
fi
[ -f "$HEALTH" ] && { mkdir -p "$STAGE/health"; cp "$HEALTH" "$STAGE/health/LATEST.txt"; }
[ -f "$LAKEFS" ] && { mkdir -p "$STAGE/lakefs"; cp "$LAKEFS" "$STAGE/lakefs/LATEST.md"; }
cat > "$STAGE/README.md" <<'EOF'
# vm-reports

Weekly monitoring reports copied from the build VM by `scripts/publish_vm_reports.sh`.
Text reports only: no data, no credentials. Every file is redacted before publishing
(hostname -> build-vm, usernames -> <user>, home paths -> ~, credentials file paths ->
<credentials file>), and publishing is refused if anything identifying remains.

- `recalls/LATEST.md`: the latest recall monitor report (FDA totals vs graph, class coverage, new recalls, warnings, validation)
- `recalls/<date>/`: each week's report, `new_recalls.tsv` and `class_changes.tsv`
- `recalls/history.csv`: one line per weekly run
- `health/LATEST.txt`: the latest Layer 2 VM + lakeFS health report
- `lakefs/LATEST.md`: release watch (stable branches, tags, last action runs), from `scripts/lakefs_release_watch.sh`

Never merge this branch into `main`.
EOF
find "$STAGE" -type f | while read -r f; do redact "$f"; done

if ! guard "$STAGE"; then
  echo "publish: GUARD FAILED -- nothing published"; [ -n "$DRY" ] && { mkdir -p "$DRY"; cp -r "$STAGE"/. "$DRY"/; }
  rm -rf "$STAGE"; exit 1
fi
echo "guard: PASS (no hostname, usernames, /home/ paths, private IPs, emails or credentials)"

if [ -n "$DRY" ]; then
  mkdir -p "$DRY" && cp -r "$STAGE"/. "$DRY"/ && rm -rf "$STAGE"
  echo "dry run: redacted files in $DRY (nothing pushed)"; exit 0
fi

# ---------------------------------------------------------------- publish
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
if [ ! -d "$PUB/.git" ]; then
  if git ls-remote --exit-code --heads "$REPO" "$BRANCH" >/dev/null 2>&1 < /dev/null; then
    git clone -q --branch "$BRANCH" --single-branch "$REPO" "$PUB" < /dev/null || exit 1
  else
    mkdir -p "$PUB" && cd "$PUB" && git init -q && git checkout -q --orphan "$BRANCH" \
      && git remote add origin "$REPO" || exit 1
  fi
fi
cd "$PUB" || exit 1
git pull -q --rebase origin "$BRANCH" < /dev/null 2>/dev/null || true
cp -r "$STAGE"/. "$PUB"/ && rm -rf "$STAGE"
guard "$PUB" || { echo "publish: GUARD FAILED on the branch copy -- nothing pushed"; exit 1; }
git add -A
if git -c user.name="MDKG build-vm monitor" -c user.email="mdkg-monitor@users.noreply.github.com" \
     commit -q -m "VM reports $(date +%F)"; then
  git push -q origin "HEAD:$BRANCH" < /dev/null && echo "publish: pushed to $BRANCH" || { echo "publish: push FAILED"; exit 1; }
else
  echo "publish: nothing new"
fi
