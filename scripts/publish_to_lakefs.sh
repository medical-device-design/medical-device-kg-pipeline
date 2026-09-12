#!/usr/bin/env bash
#
# publish_to_lakefs.sh — upload Turtle files to the FRINK lakeFS landing zone.
#
#   ./scripts/publish_to_lakefs.sh <directory-of-ttl-files>
#
# Uploads to the `develop` branch. Does NOT commit, merge, or tag — those are
# deliberate manual steps in the lakeFS UI. See PUBLISHING.md.
#
# Requires: s5cmd (https://github.com/peak/s5cmd)
# Credentials: source your env file first, e.g.
#   set -a; . ~/.config/medical-device-kg/lakefs.env; set +a

set -euo pipefail

LAKEFS_ENDPOINT="${LAKEFS_ENDPOINT:-https://repository.okn.us/}"
LAKEFS_REPO="${LAKEFS_REPO:-medical-device-kg}"
BRANCH="${LAKEFS_BRANCH:-develop}"

SRC="${1:-}"
if [[ -z "$SRC" ]]; then
  echo "usage: $0 <directory-of-ttl-files>" >&2
  exit 64
fi
if [[ ! -d "$SRC" ]]; then
  echo "error: '$SRC' is not a directory" >&2
  exit 66
fi

for var in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  if [[ -z "${!var:-}" ]]; then
    echo "error: $var is not set. Source your lakeFS credentials first." >&2
    echo "       Request credentials from the FRINK data team <the OKN Slack (FRINK team)>." >&2
    exit 78
  fi
done

command -v s5cmd >/dev/null 2>&1 || {
  echo "error: s5cmd not found. See https://github.com/peak/s5cmd" >&2
  exit 127
}

shopt -s nullglob
files=("$SRC"/*.ttl)
shopt -u nullglob

if (( ${#files[@]} == 0 )); then
  echo "error: no .ttl files found in '$SRC'" >&2
  exit 66
fi

# Refuse to publish anything still carrying the retired namespace.
if grep -l "medicaldevice\.com" "${files[@]}" >/dev/null 2>&1; then
  echo "error: these files still contain the retired medicaldevice.com namespace:" >&2
  grep -l "medicaldevice\.com" "${files[@]}" >&2
  echo "       Run scripts/migrate_namespace.sh first." >&2
  exit 65
fi

# Validate before uploading, if Jena is available. Cheaper to fail here than
# to fail the HDT conversion after a merge.
if command -v riot >/dev/null 2>&1; then
  echo "==> Validating Turtle syntax with Apache Jena riot"
  riot --validate "${files[@]}"
  echo "==> Triple count:"
  riot --count "${files[@]}"
else
  echo "warning: 'riot' not found — skipping syntax validation." >&2
  echo "         Install Apache Jena to catch errors before upload." >&2
fi

COMMIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"

echo
echo "==> Uploading ${#files[@]} file(s)"
echo "    endpoint : $LAKEFS_ENDPOINT"
echo "    repo     : $LAKEFS_REPO"
echo "    branch   : $BRANCH"
echo "    code rev : $COMMIT_SHA"
echo

# Sequential by design. FRINK asks for no parallelization so their server is
# not overwhelmed; s5cmd parallelizes aggressively by default.
for f in "${files[@]}"; do
  name="$(basename "$f")"
  echo "    -> $name"
  s5cmd --endpoint-url "$LAKEFS_ENDPOINT" --numworkers 1 \
        cp "$f" "s3://${LAKEFS_REPO}/${BRANCH}/${name}"
done

cat <<EOF

==> Upload complete. THE UPLOAD IS NOT PUBLISHED YET.

Finish in the lakeFS UI at ${LAKEFS_ENDPOINT}

  1. Uncommitted Changes tab -> Commit Changes
     Suggested message:
       "$(whoami): upload from code rev ${COMMIT_SHA}"

  2. Merge  develop -> main     (triggers HDT conversion)
  3. Wait for the stable_vX_X_X branch to be created
  4. Confirm the tag is created  (the TAG is what deploys)

If the live endpoint does not change, check for the tag before assuming failure.
See PUBLISHING.md.
EOF
