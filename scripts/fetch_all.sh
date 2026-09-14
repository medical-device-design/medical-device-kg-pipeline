#!/usr/bin/env bash
#
# fetch_all.sh -- bulk-download every source's raw files via the orchestrator.
# Bulk downloads only (never the paging API) -- see docs/rebuild-strategy.md.
#
#   bash scripts/fetch_all.sh [downloads-dir]
#
set -euo pipefail
cd "$(dirname "$0")/.."
python3 pipeline/run.py --sources all --stage fetch --downloads "${1:-downloads}"
