#!/usr/bin/env bash
# Run the Python unit tests (aider_local and the refactoring benchmarks).
# Extra args go to unittest, e.g. -v. Opt-in live checks:
#   AIDER_TEST_LIVE=1          real Podman image and Firecracker assets
#   AIDER_TEST_PROCESS_LIVE=1  real process sandbox and its isolation checks
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/tests/unit"
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m unittest discover -s . "$@"
