#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
backend="${1:-}"

export REPO="${REPO:-$ROOT/workload}"

case "$backend" in
  process|bubblewrap|landlock)
    exec "$ROOT/process-sandbox/run.sh"
    ;;
  podman|container|rootless)
    exec "$ROOT/rootless-container/run.sh"
    ;;
  firecracker|microvm)
    exec "$ROOT/firecracker/run.sh"
    ;;
  *)
    cat >&2 <<'EOF'
Usage: ./run-demo.sh {process|podman|firecracker}

The dispatcher has no host-execution fallback. Each backend launcher
must pass its own preflight checks before the workload starts.
EOF
    exit 64
    ;;
esac
