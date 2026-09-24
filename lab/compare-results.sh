#!/usr/bin/env bash
# Build a cross-backend comparison from results/*/collected/probes.json.
# Prints a matrix to the terminal and writes results/comparison.md + .json.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/common/lib.sh"
RESULTS="${1:-$RESULTS_DIR}"
python3 "$HERE/common/compare.py" "$RESULTS"
