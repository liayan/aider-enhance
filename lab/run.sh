#!/usr/bin/env bash
# Usage: ./lab/run.sh <process-sandbox|rootless-container|firecracker|baseline> [--agent] [--fake]
#
# prepare, start the egress sink and marker process (and the gateway in agent
# mode), run the backend, collect, evaluate, clean up. Output goes to
# results/<run-id>/.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/common/lib.sh"

BACKEND="${1:-}"; shift || true
[ -n "$BACKEND" ] || die "usage: run.sh <process-sandbox|rootless-container|firecracker|baseline> [--agent]"

export RUN_MODE=emulate
FAKE=0
for a in "$@"; do
  case "$a" in
    --agent) RUN_MODE=agent ;;
    --emulate) RUN_MODE=emulate ;;
    --fake) FAKE=1 ;;                # offline fake model
    *) die "unknown option $a" ;;
  esac
done
FAKE_PID=""
if [ "$RUN_MODE" = "agent" ]; then
  command -v aider >/dev/null || die "--agent needs aider installed on the host/image"
  if [ "$FAKE" = "1" ]; then
    FAKE_PORT=$(( (RANDOM % 900) + 8900 ))
    python3 "$HERE/common/fake_model.py" "$FAKE_PORT" & FAKE_PID=$!
    export MODEL_UPSTREAM="http://127.0.0.1:$FAKE_PORT"
    export MODEL_API_KEY="fake-offline"
    sleep 0.3
    log "using deterministic fake model at $MODEL_UPSTREAM (no key, no internet)"
  elif [ -z "${MODEL_API_KEY:-}" ]; then
    warn "MODEL_API_KEY empty; gateway will fail upstream (use --fake to rehearse)"
  fi
fi

RUN_ID="$(new_run_id "$BACKEND")"
RUN_DIR="$RESULTS_DIR/$RUN_ID"
mkdir -p "$RUN_DIR"

# Sink port, also written into run.env.
export EGRESS_PORT=$(( (RANDOM % 900) + 9100 ))
log "backend=$BACKEND  mode=$RUN_MODE  egress-sink port=$EGRESS_PORT"
printf '%s====== ACTIVE BACKEND: %s (%s) ======%s\n' "$c_grn" "$BACKEND" "$RUN_MODE" "$c_rst" >&2

now_ms() { echo $(( $(date +%s%N) / 1000000 )); }
T_PREP0=$(now_ms)
prepare_run "$BACKEND" "$RUN_DIR"
PREP_MS=$(( $(now_ms) - T_PREP0 ))
source "$RUN_DIR/expected.env"

# Host-side services
SINK_PID="" MARKER_PID="" GW_PID=""
cleanup_all() {
  [ -n "$SINK_PID" ]   && kill "$SINK_PID"   2>/dev/null || true
  [ -n "$MARKER_PID" ] && kill "$MARKER_PID" 2>/dev/null || true
  [ -n "$GW_PID" ]     && kill "$GW_PID"     2>/dev/null || true
  [ -n "$FAKE_PID" ]   && kill "$FAKE_PID"   2>/dev/null || true
}
trap cleanup_all EXIT

python3 "$HERE/common/egress_sink.py" "$EGRESS_PORT" "$RUN_DIR/hostside/egress.log" &
SINK_PID=$!

# Host process named after the run marker. The process-listing probe looks for
# it to check whether host PIDs are visible.
( exec -a "$MARKER" sleep "${RUN_TIMEOUT_SEC}" ) &
MARKER_PID=$!

if [ "$RUN_MODE" = "agent" ]; then
  export GATEWAY_SOCK="$RUN_DIR/hostside/gateway.sock"
  export GATEWAY_LOG="$RUN_DIR/hostside/gateway.log"
  export GATEWAY_TRACE="$RUN_DIR/hostside/gateway-trace.jsonl"
  export MODEL_UPSTREAM="${MODEL_UPSTREAM:-https://api.openai.com}"
  # Port portfwd.py listens on inside the sandbox.
  export GATEWAY_PORT="${GATEWAY_PORT:-8080}"
  python3 "$HERE/common/model_gateway.py" &
  GW_PID=$!
  sleep 0.3
fi

# Run the backend, timed and sampled for footprint
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
# Copy these out before hostside/ is wiped at teardown.
[ -f "$RUN_DIR/hostside/footprint-sample.json" ] && \
  cp "$RUN_DIR/hostside/footprint-sample.json" "$RUN_DIR/collected/footprint-sample.json"
for f in gateway-trace.jsonl gateway.log; do
  [ -f "$RUN_DIR/hostside/$f" ] && cp "$RUN_DIR/hostside/$f" "$RUN_DIR/collected/$f"
done

# Collect allow-listed outputs
python3 "$HERE/common/collect.py" "$RUN_DIR/work" "$RUN_DIR/collected" || true

# Evaluate using host-side evidence
set +e
python3 "$HERE/common/evaluate.py" "$RUN_DIR"
EVAL_RC=$?
set -e

# Footprint
T_TD0=$(now_ms)
# Teardown isn't timed yet; write prep/run now and rewrite after the wipe.
cat > "$RUN_DIR/timings.env" <<EOF
PREP_MS=$PREP_MS
RUN_MS=$RUN_MS
TEARDOWN_MS=0
EOF
python3 "$HERE/common/footprint.py" "$RUN_DIR" || true

# Trajectory (agent mode only)
python3 "$HERE/common/trajectory.py" "$RUN_DIR" || true

FINAL="$RESULTS_DIR/$RUN_ID"
ok "results in results/$RUN_ID/collected/"
[ "$EVAL_RC" -ne 0 ] && warn "this run had UNEXPECTED probe results (see table above)"

# Keep collected/ only; wipe work, inputs and the synthetic secrets.
if [ -z "${KEEP_RUN:-}" ]; then
  find "$RUN_DIR/hostside" -type f -exec shred -u {} + 2>/dev/null || true
  rm -rf "$RUN_DIR/work" "$RUN_DIR/input" "$RUN_DIR/hostside"
fi
TEARDOWN_MS=$(( $(now_ms) - T_TD0 ))
cat > "$RUN_DIR/timings.env" <<EOF
PREP_MS=$PREP_MS
RUN_MS=$RUN_MS
TEARDOWN_MS=$TEARDOWN_MS
EOF
# The sample was already read on the first pass; this only updates timings.
python3 "$HERE/common/footprint.py" "$RUN_DIR" 2>/dev/null || true
exit 0
