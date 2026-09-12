#!/usr/bin/env bash
#
# migrate_namespace.sh — rewrite the retired third-party namespace to the
# Hofstra-owned one, in place, with verification.
#
#   ./scripts/migrate_namespace.sh <directory-of-ttl-files>
#
#   http://medicaldevice.com/ontology/resource/  ->  https://bmedesign.org/medical-device-kg/id/
#   http://medicaldevice.com/ontology/           ->  https://bmedesign.org/medical-device-kg/ns/
#
# WHY: medicaldevice.com is owned by a third party (a medical device consultant
# who is actively advertising the domain for sale). The Proto-OKN graph
# construction guidelines require a namespace you own.
#
# RUN THIS ONLY AFTER THE SEPTEMBER 25 SHOWCASE.

set -euo pipefail

# IRI LAYOUT — verified against the live endpoint 2026-09-07.
#
# The graph is NOT cleanly split into vocabulary and instance namespaces. Most
# instances live under /ontology/ alongside the class and property terms:
#
#   http://medicaldevice.com/ontology/MedicalDevice              <- vocabulary
#   http://medicaldevice.com/ontology/hasManufacturer            <- vocabulary
#   http://medicaldevice.com/ontology/product-code/brt           <- INSTANCE
#   http://medicaldevice.com/ontology/manufacturer/0625_llc      <- INSTANCE
#   http://medicaldevice.com/ontology/k510/den000001             <- INSTANCE
#   http://medicaldevice.com/ontology/pma/n10389/supplement/s001 <- INSTANCE
#   http://medicaldevice.com/ontology/recall/94520               <- INSTANCE
#   http://medicaldevice.com/ontology/tplc/brt                   <- INSTANCE
#   http://medicaldevice.com/ontology/regulation/610_40          <- INSTANCE
#   http://medicaldevice.com/ontology/cfr_section/1000_1         <- INSTANCE
#   http://medicaldevice.com/resource/device/plate__fixation     <- INSTANCE
#   http://medicaldevice.com/resource/report/MW5096527           <- INSTANCE
#
# The reliable discriminator: a VOCABULARY term has no further slash after
# /ontology/ and begins with a capital (classes) or a lowercase letter with no
# path segment (properties). An INSTANCE always has a lowercase path segment
# followed by a slash.
#
# Hence the rules below, in this exact order:
#   1. /resource/<anything>            -> /id/<anything>
#   2. /ontology/<lowercase-seg>/...   -> /id/<lowercase-seg>/...   (instances)
#   3. /ontology/<remaining>           -> /ns/<remaining>           (vocabulary)
#
# ORDER IS LOAD-BEARING. Rule 3 must run last or it will swallow every instance.

OLD_BASE="http://medicaldevice\.com"
NEW_NS="https://bmedesign.org/medical-device-kg/ns/"
NEW_ID="https://bmedesign.org/medical-device-kg/id/"

SRC="${1:-}"
if [[ -z "$SRC" || ! -d "$SRC" ]]; then
  echo "usage: $0 <directory-of-ttl-files>" >&2
  exit 64
fi

shopt -s nullglob
files=("$SRC"/*.ttl)
shopt -u nullglob
(( ${#files[@]} )) || { echo "error: no .ttl files in '$SRC'" >&2; exit 66; }

have_riot=0
command -v riot >/dev/null 2>&1 && have_riot=1
if (( ! have_riot )); then
  echo "warning: 'riot' (Apache Jena) not found." >&2
  echo "         The triple-count check below is the main safety net for this" >&2
  echo "         migration. Strongly consider installing Jena before proceeding." >&2
  read -r -p "Continue without validation? [y/N] " ans
  [[ "$ans" == [yY] ]] || exit 1
fi

# ---- Baseline -------------------------------------------------------------
before_count=""
if (( have_riot )); then
  echo "==> Baseline triple count"
  before_count="$(riot --count "${files[@]}" | awk '{print $1}' | paste -sd+ | bc)"
  echo "    $before_count triples"
fi

before_hits="$(grep -o "medicaldevice\.com" "${files[@]}" | wc -l | tr -d ' ')"
echo "==> Occurrences of the old namespace before: $before_hits"
(( before_hits )) || { echo "Nothing to do — already migrated."; exit 0; }

# ---- Rewrite --------------------------------------------------------------
echo "==> Rewriting (backups written alongside as *.ttl.bak)"
sed -E -i.bak \
  -e "s|${OLD_BASE}/resource/|${NEW_ID}|g" \
  -e "s|${OLD_BASE}/ontology/([a-z][a-z0-9_-]*)/|${NEW_ID}\1/|g" \
  -e "s|${OLD_BASE}/ontology/|${NEW_NS}|g" \
  "${files[@]}"

# ---- Verify: three checks, all must pass ----------------------------------
fail=0

echo "==> Check 1/3: no remaining references to the old namespace"
if grep -l "medicaldevice\.com" "${files[@]}" 2>/dev/null; then
  echo "    FAIL — files above still contain the old namespace" >&2
  fail=1
else
  echo "    PASS"
fi

if (( have_riot )); then
  echo "==> Check 2/3: Turtle still parses"
  if riot --validate "${files[@]}"; then
    echo "    PASS"
  else
    echo "    FAIL — syntax errors introduced" >&2
    fail=1
  fi

  echo "==> Check 3/3: triple count unchanged"
  after_count="$(riot --count "${files[@]}" | awk '{print $1}' | paste -sd+ | bc)"
  if [[ "$after_count" == "$before_count" ]]; then
    echo "    PASS — $after_count triples"
  else
    echo "    FAIL — was $before_count, now $after_count" >&2
    echo "    The rewrite changed the data, not just the names." >&2
    fail=1
  fi
else
  echo "==> Checks 2 and 3 skipped (no riot)"
fi

if (( fail )); then
  cat <<'EOF' >&2

MIGRATION FAILED. Restore the originals before doing anything else:

    for f in *.ttl.bak; do mv "$f" "${f%.bak}"; done

Do not upload these files.
EOF
  exit 70
fi

cat <<EOF

==> Migration complete and verified.

Sanity-check one instance IRI by eye, then:

  ./scripts/publish_to_lakefs.sh "$SRC"

Remember the downstream files that also carry the old prefix and must change in
the same commit:
  - ontology/ontology.ttl
  - queries/*.rq
  - README.md
  - the DeviceDesignKG_SKILL.md skills file (frontmatter + every PREFIX ex: line)
  - the registry entry and its example queries

Remove the .bak files once the graph is live and verified.
EOF
