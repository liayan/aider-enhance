#!/usr/bin/env bash
# In-boundary entrypoint. Runs identically in bwrap, the container, and the guest.
# It expects, inside the boundary:
#   /work    read-write workspace (the copied repo)
#   /input   read-only: task.txt (+ task-agent.txt in agent mode), run.env
#   /lab     read-only: this repo's lab/ (for common/*.py)
#
# Two task tiers:
#   emulate : the deterministic basic unit-test task (agent_emulator.py). Unchanged.
#   agent   : the REAL coding task, performed by aider talking to the model through
#             the single audited network path (portfwd -> mounted gateway socket).
set -uo pipefail

WORK="${DEMO_WORK:-/work}"
LAB="${DEMO_LAB:-/lab}"          # /lab inside a sandbox; repo lab/ on the host baseline
export DEMO_WORK="$WORK"
export DEMO_RUN_ENV="${DEMO_RUN_ENV:-/input/run.env}"
RUN_MODE="${RUN_MODE:-emulate}"
cd "$WORK"

echo "[inside] backend=${DEMO_BACKEND:-?} mode=$RUN_MODE uid=$(id -u) work=$WORK"

# ---- agent phase -----------------------------------------------------------
if [ "$RUN_MODE" = "agent" ]; then
  # Bring up the single egress path: 127.0.0.1:$GATEWAY_PORT -> /run/model.sock
  GATEWAY_PORT="${GATEWAY_PORT:-8080}"
  if [ -S /run/model.sock ]; then
    python3 "$LAB/common/portfwd.py" "$GATEWAY_PORT" /run/model.sock &
    FWD_PID=$!
    sleep 0.3
  else
    echo "[inside] WARNING: /run/model.sock not present; agent has no model path"
  fi

  export OPENAI_API_BASE="http://127.0.0.1:${GATEWAY_PORT}/v1"
  export OPENAI_API_KEY="sandbox-dummy"   # real key lives on the host gateway only
  export AIDER_LLM_HISTORY_FILE="$WORK/.aider.llm.history"
  export AIDER_CHAT_HISTORY_FILE="$WORK/.aider.chat.history.md"

  echo "[inside] running real aider on the coding task"
  # INSTRUCTIONS.md and docs/ (the untrusted injection fixture) are added as
  # read-only context so prompt-injection is genuinely exercised against a model.
  # --no-stream: one non-streamed response per turn -> clean per-turn trace.
  # --no-check-update / --no-show-release-notes: no incidental network at startup.
  aider --yes --no-git --no-stream --no-check-update --no-show-release-notes \
        --model "${DEMO_MODEL:-openai/gpt-4o-mini}" \
        --message-file /input/task-agent.txt \
        --llm-history-file "$AIDER_LLM_HISTORY_FILE" \
        --chat-history-file "$AIDER_CHAT_HISTORY_FILE" \
        --read INSTRUCTIONS.md --read docs/THIRD_PARTY_NOTES.md \
        src/app.py tests/test_app.py > "$WORK/agent.log" 2>&1
  echo "[inside] aider rc=$? (see agent.log)" | tee -a "$WORK/agent.log"
  [ -n "${FWD_PID:-}" ] && kill "$FWD_PID" 2>/dev/null || true
else
  python3 "$LAB/common/agent_emulator.py"
fi

# ---- acceptance grading (real task, agent mode only) -----------------------
# The acceptance tests grade the REAL coding task. They must not run against the
# emulate tier (basic unit-test task), which never implements summarize().
if [ "$RUN_MODE" = "agent" ] && [ -d /input/acceptance ]; then
  cp -r /input/acceptance "$WORK/acceptance"
  echo "[inside] running acceptance tests"
  python3 -m unittest discover -s "$WORK/acceptance" -v \
      > "$WORK/acceptance.log" 2>&1
  echo "$?" > "$WORK/.acceptance_rc"
fi

# ---- probe phase -----------------------------------------------------------
echo "[inside] running probes"
python3 "$LAB/common/probe_runner.py" "$WORK/probes.json"

echo "[inside] done"
