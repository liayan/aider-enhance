#!/usr/bin/env bash
# Compare results/*/collected across backends. Writes results/comparison.{md,json}.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common/lib.sh"
RESULTS="${1:-$RESULTS_DIR}"
python3 "$HERE/common/compare.py" "$RESULTS"
