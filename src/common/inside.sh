#!/usr/bin/env bash
# Entrypoint inside the boundary; the same script for every backend.
# Expects:
#   /work   rw workspace
#   /input  ro task.txt, task-agent.txt, run.env
#   /src    ro src/ from this repo
#
# emulate: agent_emulator.py does the basic task.
# agent:   aider does the real task, talking to the model through portfwd.py
#          and the mounted gateway socket.
set -uo pipefail

WORK="${DEMO_WORK:-/work}"
SRC="${DEMO_SRC:-/src}"  # baseline points this at the repo
export DEMO_WORK="$WORK"
export DEMO_RUN_ENV="${DEMO_RUN_ENV:-/input/run.env}"
RUN_MODE="${RUN_MODE:-emulate}"
cd "$WORK"

echo "[inside] backend=${DEMO_BACKEND:-?} mode=$RUN_MODE uid=$(id -u) work=$WORK"

if [ "$RUN_MODE" = "agent" ]; then
  # 127.0.0.1:$GATEWAY_PORT -> /run/model.sock, or the host vsock port in
  # the Firecracker guest (MODEL_VSOCK=<cid>:<port>).
  GATEWAY_PORT="${GATEWAY_PORT:-8080}"
  if [ -n "${MODEL_VSOCK:-}" ]; then
    python3 "$SRC/common/portfwd.py" "$GATEWAY_PORT" "vsock:$MODEL_VSOCK" &
    FWD_PID=$!
    sleep 0.3
  elif [ -S /run/model.sock ]; then
    python3 "$SRC/common/portfwd.py" "$GATEWAY_PORT" /run/model.sock &
    FWD_PID=$!
    sleep 0.3
  else
    echo "[inside] WARNING: /run/model.sock not present; agent has no model path"
  fi

  export OPENAI_API_BASE="http://127.0.0.1:${GATEWAY_PORT}/v1"
  export OPENAI_API_KEY="sandbox-dummy"   # gateway adds the real key
  export AIDER_LLM_HISTORY_FILE="$WORK/.aider.llm.history"
  export AIDER_CHAT_HISTORY_FILE="$WORK/.aider.chat.history.md"

  echo "[inside] running real aider on the coding task"
  # The injection fixture goes in as read-only context so the model sees it.
  # --no-stream gives one response per turn, which keeps the trace simple.
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
  python3 "$SRC/common/agent_emulator.py"
fi

# Acceptance tests for the real task. Emulate mode never adds summarize(), so
# skip them there.
if [ "$RUN_MODE" = "agent" ] && [ -d /input/acceptance ]; then
  cp -r /input/acceptance "$WORK/acceptance"
  echo "[inside] running acceptance tests"
  python3 -m unittest discover -s "$WORK/acceptance" -v \
      > "$WORK/acceptance.log" 2>&1
  echo "$?" > "$WORK/.acceptance_rc"
fi

echo "[inside] running probes"
python3 "$SRC/common/probe_runner.py" "$WORK/probes.json"

echo "[inside] done"
