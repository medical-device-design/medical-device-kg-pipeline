#!/usr/bin/env bash
#
# structure_lint.sh -- enforce the repo structure and converter standard that the
# maintenance bot (Layer 1) checks on every PR. Hard-fails on the things that
# must never happen; warns on drift worth a human's eye.
#
#   bash scripts/structure_lint.sh
#
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
fail=0

echo "== 1. no committed secrets =="
secrets="$(git ls-files | grep -EiI '(^|/)(\.env(\..*)?$|.*lakectl.*|.*deploy-key.*|credentials$|.*secret.*)' | grep -vE '\.env\.example$' || true)"
if [ -n "$secrets" ]; then
  echo "   FAIL: secret-looking files are tracked:"; printf '     %s\n' $secrets; fail=1
else
  echo "   PASS"
fi

echo "== 2. no committed data TTL (only ontology/*.ttl is allowed) =="
badttl="$(git ls-files '*.ttl' | grep -Ev '^ontology/' || true)"
if [ -n "$badttl" ]; then
  echo "   FAIL: generated/data TTL tracked outside ontology/ (belongs in lakeFS):"; printf '     %s\n' $badttl; fail=1
else
  echo "   PASS"
fi

echo "== 3. the lakeFS ontology snapshot must stay untracked =="
if git ls-files | grep -q 'ontology/ontology_CURRENT_from_lakefs.ttl'; then
  echo "   FAIL: ontology_CURRENT_from_lakefs.ttl is tracked; it is a snapshot, not source"; fail=1
else
  echo "   PASS"
fi

echo "== 4. IRIs minted only in pipeline/iri.py =="
hits="$(git ls-files 'pipeline/**/*.py' 'pipeline/*.py' | grep -v 'pipeline/iri.py' \
        | xargs grep -lE 'https?://[^\"'\'' ]*medical-device-kg/(ns|id)/' 2>/dev/null || true)"
if [ -n "$hits" ]; then
  echo "   FAIL: base IRI hardcoded outside iri.py (must route through iri.py):"; printf '     %s\n' $hits; fail=1
else
  echo "   PASS -- every module mints IRIs through iri.py"
fi

echo "== 5. required files present =="
[ -f ontology/ontology.ttl ] && echo "   PASS ontology/ontology.ttl" || { echo "   FAIL: ontology/ontology.ttl missing"; fail=1; }
[ -f pipeline/iri.py ] && echo "   PASS pipeline/iri.py" || { echo "   FAIL: pipeline/iri.py missing"; fail=1; }
[ -f pipeline/common.py ] && echo "   PASS pipeline/common.py" || { echo "   FAIL: pipeline/common.py missing"; fail=1; }
ls ontology/shapes/*.ttl >/dev/null 2>&1 && echo "   PASS SHACL shapes present" || echo "   WARN: no SHACL shapes in ontology/shapes/"

echo
if (( fail )); then
  echo "STRUCTURE LINT: FAIL"; exit 1
fi
echo "STRUCTURE LINT: OK"
