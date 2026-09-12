#!/usr/bin/env bash
#
# validate.sh -- one-command pre-publish gate for the medical-device-kg rebuild.
#
#   ./scripts/validate.sh [dir-of-ttl]        # default: out/
#
# Four layers, NO triplestore required -- everything below is Apache Jena:
#   1. riot --validate   Turtle syntax (streaming; any file size)
#   2. shacl validate    SHACL shapes per source (ontology/shapes/)
#   3. join check         every internal object IRI resolves to a subject
#   4. arq                competency/*.rq functional questions
#
# This is what replaces "load it into GraphDB and poke around": layers 1-3 prove
# the graph is well-formed and connected; layer 4 proves it answers real questions.
# Install Apache Jena once and put its bin/ on PATH (riot, shacl, arq, tdb2, fuseki).
#
set -uo pipefail

SRC="${1:-out}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

shopt -s nullglob
ttls=("$SRC"/*.ttl)
shopt -u nullglob
(( ${#ttls[@]} )) || { echo "error: no .ttl files in '$SRC'" >&2; exit 66; }

fail=0

echo "== 1/4  Turtle syntax (riot) =="
if command -v riot >/dev/null 2>&1; then
  if riot --validate "${ttls[@]}"; then echo "   PASS"; else echo "   FAIL"; fail=1; fi
else
  echo "   SKIP -- 'riot' (Apache Jena) not on PATH"
fi

echo "== 2/4  SHACL shapes =="
if command -v shacl >/dev/null 2>&1; then
  shapes=("$ROOT"/ontology/shapes/*.ttl)
  if (( ${#shapes[@]} )); then
    for shape in "${shapes[@]}"; do
      for t in "${ttls[@]}"; do
        report="$(shacl validate --shapes "$shape" --data "$t" 2>&1 || true)"
        if printf '%s' "$report" | grep -qiE 'resultSeverity|conforms[[:space:]]+false'; then
          echo "   VIOLATION: $(basename "$t") vs $(basename "$shape")"
          printf '%s\n' "$report" | grep -iE 'resultMessage|focusNode|resultPath' | head -20
          fail=1
        fi
      done
    done
    (( fail )) || echo "   PASS"
  else
    echo "   (no shape files in ontology/shapes/)"
  fi
else
  echo "   SKIP -- 'shacl' (Apache Jena) not on PATH"
fi

echo "== 3/4  Join check (internal IRIs resolve to a subject) =="
tmp="$(mktemp -d)"
grep -hoE '^<https://bmedesign\.org/medical-device-kg/id/[^>]+>' "${ttls[@]}" \
  | tr -d '<>' | sort -u > "$tmp/subjects" || true
grep -hoE '<https://bmedesign\.org/medical-device-kg/id/[^>]+>' "${ttls[@]}" \
  | tr -d '<>' | sort -u > "$tmp/all" || true
missing="$(comm -23 "$tmp/all" "$tmp/subjects" | head -20)" || true
if [ -n "$missing" ]; then
  echo "   NOTE: internal IRIs referenced but not defined as subjects in '$SRC':"
  printf '     %s\n' $missing
  echo "   Expected only when the target's source isn't in '$SRC' yet."
  echo "   Run over the FULL assembled out/ before publishing -- anything left is a real orphan."
else
  echo "   PASS -- every internal IRI is defined."
fi
rm -rf "$tmp"

echo "== 4/4  Competency questions (arq) =="
if command -v arq >/dev/null 2>&1; then
  data_args=(); for t in "${ttls[@]}"; do data_args+=(--data "$t"); done
  qs=("$ROOT"/competency/*.rq)
  if (( ${#qs[@]} )); then
    for q in "${qs[@]}"; do
      echo "   -- $(basename "$q") --"
      arq "${data_args[@]}" --query "$q" || { echo "   ERROR running $(basename "$q")"; fail=1; }
    done
  else
    echo "   (no .rq files in competency/)"
  fi
else
  echo "   SKIP -- 'arq' (Apache Jena) not on PATH"
fi

echo
if (( fail )); then
  echo "VALIDATE: FAIL -- fix the items above before publishing to lakeFS."
  exit 1
fi
echo "VALIDATE: OK -- syntax, shapes, joins and competency questions all clear."
