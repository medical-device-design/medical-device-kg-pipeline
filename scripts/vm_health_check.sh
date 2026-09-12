#!/usr/bin/env bash
#
# vm_health_check.sh -- Layer 2 of the maintenance bot: a read-only health report
# for the VM (.83), the shared tree, the graph data on disk, and lakeFS
# reachability. Cloud CI (Layer 1) cannot see the VPN-locked VM or lakeFS; this
# runs on .83, which can. See docs/ci-and-monitoring.md.
#
# Usage:
#   bash scripts/vm_health_check.sh [--full]
#     --full   also run verify_current_graph.sh on $MDKG/nstest (heavier arq pass)
#
# Writes a timestamped report to $MDKG/health/ and refreshes health/latest.txt.
# Strictly read-only: never writes outside health/, never touches lakeFS
# main/tags, never starts or stops a service. Safe to run from cron.
#
set -uo pipefail

MDKG="${MDKG:-/srv/medical-device-kg}"
REPO="$MDKG/repo"
HEALTH="$MDKG/health"
JENA_BIN="$MDKG/tools/apache-jena-5.2.0/bin"
LAKEFS_ENDPOINT="${LAKEFS_ENDPOINT:-https://repository.okn.us/}"
FULL=0; [ "${1:-}" = "--full" ] && FULL=1

[ -d "$JENA_BIN" ] && PATH="$JENA_BIN:$PATH"
mkdir -p "$HEALTH"
ts="$(date -u +%Y%m%d-%H%M%S)"
REPORT="$HEALTH/health-$ts.txt"

pass=0; warn=0; fail=0
ok(){ echo "   PASS $*"; pass=$((pass+1)); }
wn(){ echo "   WARN $*"; warn=$((warn+1)); }
er(){ echo "   FAIL $*"; fail=$((fail+1)); }

# A brace group with a file redirect runs in the CURRENT shell (no subshell),
# so pass/warn/fail survive for the exit code below. Do not switch this to a pipe.
{
echo "############################################################"
echo "# medical-device-kg VM health  --  Layer 2"
echo "# host: $(hostname)   user: $(whoami)   date_utc: $(date -u +%FT%TZ)"
echo "# MDKG: $MDKG"
echo "############################################################"

echo; echo "== 1. tooling =="
command -v java    >/dev/null 2>&1 && ok "java   $(java -version 2>&1 | head -1)"   || er "java not found"
command -v python3 >/dev/null 2>&1 && ok "python $(python3 -V 2>&1)"                || er "python3 not found"
command -v riot    >/dev/null 2>&1 && ok "riot   $(riot --version 2>&1 | head -1)"  || wn "riot not on PATH (expected $JENA_BIN)"
command -v git     >/dev/null 2>&1 && ok "git    $(git --version 2>&1)"             || er "git not found"
command -v s5cmd   >/dev/null 2>&1 && ok "s5cmd  present (lakeFS upload tool)"       || wn "s5cmd not found (needed to publish to lakeFS)"
if command -v docker >/dev/null 2>&1; then
  if docker ps >/dev/null 2>&1; then ok "docker reachable"
  else wn "docker present but not usable by $(whoami) (expected: not in docker group)"; fi
else wn "docker CLI not found"; fi

echo; echo "== 2. disk =="
df -h "$MDKG" 2>/dev/null
used_pct="$(df -P "$MDKG" 2>/dev/null | awk 'NR==2{gsub("%","",$5); print $5}')"
if [ -n "${used_pct:-}" ]; then
  if   [ "$used_pct" -ge 90 ]; then er "disk ${used_pct}% used on $MDKG"
  elif [ "$used_pct" -ge 80 ]; then wn "disk ${used_pct}% used on $MDKG"
  else ok "disk ${used_pct}% used on $MDKG"; fi
fi
echo "   tree sizes:"; du -sh "$MDKG"/repo "$MDKG"/downloads "$MDKG"/out "$MDKG"/nstest 2>/dev/null | sed 's/^/     /'

echo; echo "== 3. repo (git) =="
if [ -d "$REPO/.git" ]; then
  br="$(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  sha="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)"
  ok "repo at $REPO  branch=$br  head=$sha"
  if git -C "$REPO" fetch --quiet origin 2>/dev/null; then
    behind="$(git -C "$REPO" rev-list --count HEAD..@{u} 2>/dev/null || echo '?')"
    ahead="$(git -C "$REPO" rev-list --count @{u}..HEAD 2>/dev/null || echo '?')"
    [ "$behind" = "0" ] && ok "up to date with origin/$br" || wn "behind origin by $behind commit(s) -- run: git -C $REPO pull"
    [ "$ahead" != "0" ] && [ "$ahead" != "?" ] && wn "ahead of origin by $ahead local commit(s)"
  else wn "could not fetch origin (network / auth)"; fi
  dirty="$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  [ "$dirty" = "0" ] && ok "working tree clean" || wn "$dirty uncommitted change(s) in working tree"
else er "no git repo at $REPO"; fi

echo; echo "== 4. structure lint =="
if [ -f "$REPO/scripts/structure_lint.sh" ]; then
  if ( cd "$REPO" && bash scripts/structure_lint.sh ) >/tmp/mdkg_lint.$$ 2>&1; then ok "structure lint OK"; else er "structure lint FAILED"; fi
  sed 's/^/     /' /tmp/mdkg_lint.$$; rm -f /tmp/mdkg_lint.$$
else wn "scripts/structure_lint.sh not present in repo"; fi

echo; echo "== 5. graph data on disk (riot) =="
if command -v riot >/dev/null 2>&1; then
  for d in out nstest; do
    shopt -s nullglob; ttls=("$MDKG/$d"/*.ttl); shopt -u nullglob
    if [ "${#ttls[@]}" -gt 0 ]; then
      if riot --validate "${ttls[@]}" >/tmp/mdkg_riot.$$ 2>&1; then
        cnt="$(riot --count "${ttls[@]}" 2>&1 | awk -F'Triples = ' '/Triples = /{gsub(/[^0-9]/,"",$2); s+=$2} END{print s+0}')"
        ok "$d/: ${#ttls[@]} file(s) valid, $cnt triple(s)"
      else er "$d/: riot --validate failed"; sed 's/^/     /' /tmp/mdkg_riot.$$; fi
      rm -f /tmp/mdkg_riot.$$
    else echo "     ($d/ has no .ttl -- skipped)"; fi
  done
else wn "riot unavailable -- skipping graph validation"; fi
if [ "$FULL" = "1" ] && [ -d "$MDKG/nstest" ] && [ -f "$REPO/scripts/verify_current_graph.sh" ]; then
  echo "   --full: verify_current_graph.sh on nstest/"
  ( cd "$REPO" && bash scripts/verify_current_graph.sh "$MDKG/nstest" ) 2>&1 | sed 's/^/     /'
fi

echo; echo "== 6. lakeFS reachability (read-only) =="
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$LAKEFS_ENDPOINT" 2>/dev/null || echo 000)"
[ "$code" != "000" ] && ok "reachable: $LAKEFS_ENDPOINT (HTTP $code)" || er "cannot reach $LAKEFS_ENDPOINT (VPN down?)"
credf="$HOME/.config/medical-device-kg/lakefs.env"
[ -f "$credf" ] && ok "lakeFS creds present ($credf)" || wn "lakeFS creds not on this host ($credf) -- publishing not set up here"

echo; echo "== 7. mdkg-check (outstanding items) =="
if command -v mdkg-check >/dev/null 2>&1; then mdkg-check 2>&1 | sed 's/^/     /'
elif [ -x "$REPO/scripts/mdkg-check" ]; then "$REPO/scripts/mdkg-check" 2>&1 | sed 's/^/     /'
else echo "     (mdkg-check not installed -- skipped)"; fi

echo
echo "############################################################"
echo "# SUMMARY: PASS=$pass  WARN=$warn  FAIL=$fail"
if [ "$fail" -gt 0 ]; then echo "# STATUS: FAIL -- see items above"
elif [ "$warn" -gt 0 ]; then echo "# STATUS: WARN"
else echo "# STATUS: OK"; fi
echo "############################################################"
} > "$REPORT" 2>&1

cat "$REPORT"
cp -f "$REPORT" "$HEALTH/latest.txt" 2>/dev/null || true
# retain only the 20 most recent reports
ls -1t "$HEALTH"/health-*.txt 2>/dev/null | tail -n +21 | xargs -r rm -f
echo
echo "report written: $REPORT"
[ "$fail" -gt 0 ] && exit 1 || exit 0
