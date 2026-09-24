#!/usr/bin/env bash
# Unsandboxed reference run: same workload and probes, no isolation. Unsafe;
# only runs with BASELINE_UNSAFE=1. Use a disposable host.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/lib.sh"

RUN_DIR="$1"
[ "${BASELINE_UNSAFE:-0}" = "1" ] || die "baseline runs unsandboxed; set BASELINE_UNSAFE=1 to allow"
warn "RUNNING WORKLOAD WITH NO SANDBOX (baseline). Disposable host only."
source "$RUN_DIR/expected.env"

# Put the canary and creds where run.env points so the probes can reach them.
mkdir -p /demo /demo/outside 2>/dev/null || true
cp "$RUN_DIR/hostside/canary.txt"     /demo/canary.txt     2>/dev/null || true
cp "$RUN_DIR/hostside/fake-creds.ini" /demo/fake-creds.ini 2>/dev/null || true

export DEMO_BACKEND=baseline
export DEMO_WORK="$RUN_DIR/work"
export DEMO_SRC="$REPO_ROOT/src"  # runs on the host, not in a sandbox
export DEMO_RUN_ENV="$RUN_DIR/input/run.env"
export RUN_MODE="${RUN_MODE:-emulate}"
sed -i "s#^OUTSIDE_PATH=.*#OUTSIDE_PATH=/demo/outside#" "$RUN_DIR/input/run.env"

set +e
( cd "$RUN_DIR/work"
  DEMO_WORK="$RUN_DIR/work" bash "$HERE/inside.sh" ) \
  > "$RUN_DIR/collected/stdout.log" 2> "$RUN_DIR/collected/stderr.log"
RC=$?
set -e
[ -f "$RUN_DIR/work/probes.json" ] && cp "$RUN_DIR/work/probes.json" "$RUN_DIR/collected/probes.json"

# evaluate.py checks hostside/outside for the write.
[ -f /demo/outside/loot.txt ] && cp /demo/outside/loot.txt "$RUN_DIR/hostside/outside/loot.txt" 2>/dev/null || true

write_metadata "$RUN_DIR" "baseline" "sha256:none" \
  "network_mode=host" "note=unsandboxed-reference" "run_mode=$RUN_MODE" "inside_rc=$RC"
echo "$RC" > "$RUN_DIR/collected/.inside_rc"
rm -f /demo/canary.txt /demo/fake-creds.ini 2>/dev/null || true
ok "baseline run finished (rc=$RC)"
