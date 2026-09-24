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

now_ms() { echo $(( $(date +%s%N) / 1000000 )); }
T_PREP0=$(now_ms)
prepare_run "$BACKEND" "$RUN_DIR"
PREP_MS=$(( $(now_ms) - T_PREP0 ))
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

# --- backend run (timed + footprint-sampled) --------------------------------
backend_cmd() {
  case "$BACKEND" in
    process-sandbox)    "$HERE/process-sandbox/launch-bwrap.sh"      "$RUN_DIR" ;;
    rootless-container) "$HERE/rootless-container/launch-podman.sh"  "$RUN_DIR" ;;
    firecracker)        "$HERE/firecracker/launch-firecracker.sh"    "$RUN_DIR" ;;
    baseline)           BASELINE_UNSAFE=1 "$HERE/common/baseline.sh" "$RUN_DIR" ;;
    *) die "unknown backend $BACKEND" ;;
  esac
}
T_RUN0=$(now_ms)
backend_cmd &
BE_PID=$!
python3 "$HERE/common/sampler.py" 0.2 "$RUN_DIR/hostside/footprint-sample.json" "$BE_PID" &
SAMPLER_PID=$!
set +e; wait "$BE_PID"; BE_RC=$?; set -e
kill -TERM "$SAMPLER_PID" 2>/dev/null || true
wait "$SAMPLER_PID" 2>/dev/null || true
RUN_MS=$(( $(now_ms) - T_RUN0 ))
# Preserve the footprint sample before hostside/ is wiped at teardown.
[ -f "$RUN_DIR/hostside/footprint-sample.json" ] && \
  cp "$RUN_DIR/hostside/footprint-sample.json" "$RUN_DIR/collected/footprint-sample.json"

# --- collect declared outputs, safely --------------------------------------
python3 "$HERE/common/collect.py" "$RUN_DIR/work" "$RUN_DIR/collected" || true

# --- evaluate with host evidence -------------------------------------------
set +e
python3 "$HERE/common/evaluate.py" "$RUN_DIR"
EVAL_RC=$?
set -e

# --- operational footprint / cost ------------------------------------------
T_TD0=$(now_ms)
# teardown timing is measured around the disposable-state wipe below; write a
# provisional timings file now so footprint has prep+run, then refine.
cat > "$RUN_DIR/timings.env" <<EOF
PREP_MS=$PREP_MS
RUN_MS=$RUN_MS
TEARDOWN_MS=0
EOF
python3 "$HERE/common/footprint.py" "$RUN_DIR" || true

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
TEARDOWN_MS=$(( $(now_ms) - T_TD0 ))
# Finalize timings + footprint now that teardown is measured.
cat > "$RUN_DIR/timings.env" <<EOF
PREP_MS=$PREP_MS
RUN_MS=$RUN_MS
TEARDOWN_MS=$TEARDOWN_MS
EOF
# footprint sample lived under hostside/ (now wiped); it was already read into
# footprint.json on the first pass, so re-run only refreshes timing fields.
python3 "$HERE/common/footprint.py" "$RUN_DIR" 2>/dev/null || true
exit 0
