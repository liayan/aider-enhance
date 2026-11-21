#!/usr/bin/env bash
# Build the rootless-container image (agent-sandbox-demo:1).
#   setup/build-container-image.sh              emulate mode only
#   WITH_AIDER=1 setup/build-container-image.sh  adds aider (~620 MB) for --agent
# Extra args go to podman build, e.g. --network none.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
args=()
[ "${WITH_AIDER:-0}" = 1 ] && args+=(--build-arg WITH_AIDER=1)
exec podman build "${args[@]}" "$@" -t agent-sandbox-demo:1 \
  -f "$ROOT/src/rootless-container/Containerfile" "$ROOT"
