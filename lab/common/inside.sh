#!/usr/bin/env bash
# In-boundary entrypoint. Runs identically in bwrap, the container, and the guest.
# It expects, inside the boundary:
#   /work    read-write workspace (the copied repo)
#   /input   read-only: task.txt, run.env
#   /lab     read-only: this repo's lab/ (for common/*.py)
# It runs the agent (real or emulated), then the probes. It must not need network
# except, in agent mode, the model gateway path the backend wired up.
set -uo pipefail

WORK="${DEMO_WORK:-/work}"
export DEMO_WORK="$WORK"
export DEMO_RUN_ENV="${DEMO_RUN_ENV:-/input/run.env}"
RUN_MODE="${RUN_MODE:-emulate}"
cd "$WORK"

echo "[inside] backend=${DEMO_BACKEND:-?} mode=$RUN_MODE uid=$(id -u) work=$WORK"

# ---- agent phase -----------------------------------------------------------
if [ "$RUN_MODE" = "agent" ]; then
  echo "[inside] running real aider"
  # The backend must export OPENAI_API_BASE (or equivalent) pointing at the
  # gateway socket/vsock shim, plus a dummy key; the real key stays on the host.
  aider --yes --no-git --message-file /input/task.txt \
        --model "${DEMO_MODEL:-openai/gpt-4o-mini}" \
        src/app.py tests/test_app.py > "$WORK/agent.log" 2>&1
  rc=$?
  echo "[inside] aider rc=$rc"
else
  python3 /lab/common/agent_emulator.py
fi

# ---- probe phase -----------------------------------------------------------
echo "[inside] running probes"
python3 /lab/common/probe_runner.py "$WORK/probes.json"

echo "[inside] done"
