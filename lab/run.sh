#!/usr/bin/env bash
# Single entry point for every backend:
#   ./lab/run.sh <process-sandbox|rootless-container|firecracker|baseline> [--agent]
#
# Lifecycle: prepare -> start egress sink (+ marker proc, + gateway in agent mode)
#            -> backend run()+probe() -> evaluate (host evidence) -> collect ->
#            cleanup. Writes one directory under results/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/common/lib.sh"

BACKEND="${1:-}"; shift || true
[ -n "$BACKEND" ] || die "usage: run.sh <process-sandbox|rootless-container|firecracker|baseline> [--agent]"

export RUN_MODE=emulate
for a in "$@"; do
  case "$a" in
    --agent) RUN_MODE=agent ;;
    --emulate) RUN_MODE=emulate ;;
    *) die "unknown option $a" ;;
  esac
done
if [ "$RUN_MODE" = "agent" ]; then
  command -v aider >/dev/null || die "--agent needs aider installed on the host/image"
  [ -n "${MODEL_API_KEY:-}" ] || warn "MODEL_API_KEY empty; gateway will fail upstream"
fi

RUN_ID="$(new_run_id "$BACKEND")"
RUN_DIR="$RESULTS_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

# Pick the port the sink + run.env agree on.
export EGRESS_PORT=$(( (RANDOM % 900) + 9100 ))
log "backend=$BACKEND  mode=$RUN_MODE  egress-sink port=$EGRESS_PORT"
printf '%s====== ACTIVE BACKEND: %s (%s) ======%s\n' "$c_grn" "$BACKEND" "$RUN_MODE" "$c_rst" >&2

prepare_run "$BACKEND" "$RUN_DIR"
source "$RUN_DIR/expected.env"

# --- host-side services -----------------------------------------------------
SINK_PID="" MARKER_PID="" GW_PID=""
cleanup_all() {
  [ -n "$SINK_PID" ]   && kill "$SINK_PID"   2>/dev/null || true
  [ -n "$MARKER_PID" ] && kill "$MARKER_PID" 2>/dev/null || true
  [ -n "$GW_PID" ]     && kill "$GW_PID"     2>/dev/null || true
}
trap cleanup_all EXIT

python3 "$HERE/common/egress_sink.py" "$EGRESS_PORT" "$RUN_DIR/hostside/egress.log" &
SINK_PID=$!

# A host marker process the process-listing probe will look for (proves whether
# the boundary hides host PIDs). Named via argv with the run's marker.
( exec -a "$MARKER" sleep "${RUN_TIMEOUT_SEC}" ) &
MARKER_PID=$!

if [ "$RUN_MODE" = "agent" ]; then
  export GATEWAY_SOCK="$RUN_DIR/hostside/gateway.sock"
  export GATEWAY_LOG="$RUN_DIR/hostside/gateway.log"
  export MODEL_UPSTREAM="${MODEL_UPSTREAM:-https://api.openai.com}"
  python3 "$HERE/common/model_gateway.py" &
  GW_PID=$!
  sleep 0.3
fi

# --- backend run ------------------------------------------------------------
case "$BACKEND" in
  process-sandbox)    "$HERE/process-sandbox/launch-bwrap.sh"      "$RUN_DIR" ;;
  rootless-container) "$HERE/rootless-container/launch-podman.sh"  "$RUN_DIR" ;;
  firecracker)        "$HERE/firecracker/launch-firecracker.sh"    "$RUN_DIR" ;;
  baseline)           BASELINE_UNSAFE=1 "$HERE/common/baseline.sh" "$RUN_DIR" ;;
  *) die "unknown backend $BACKEND" ;;
esac

# --- collect declared outputs, safely --------------------------------------
python3 "$HERE/common/collect.py" "$RUN_DIR/work" "$RUN_DIR/collected" || true

# --- evaluate with host evidence -------------------------------------------
set +e
python3 "$HERE/common/evaluate.py" "$RUN_DIR"
EVAL_RC=$?
set -e

# --- finalize result dir ----------------------------------------------------
FINAL="$RESULTS_DIR/$RUN_ID"
# collected/ already holds metadata.json, probes.json, *.log, RESULT.md
ok "results in results/$RUN_ID/collected/"
[ "$EVAL_RC" -ne 0 ] && warn "this run had UNEXPECTED probe results (see table above)"

# Keep only the collected outputs by default; wipe the disposable work + secrets.
if [ -z "${KEEP_RUN:-}" ]; then
  find "$RUN_DIR/hostside" -type f -exec shred -u {} + 2>/dev/null || true
  rm -rf "$RUN_DIR/work" "$RUN_DIR/input" "$RUN_DIR/hostside"
fi
exit 0
