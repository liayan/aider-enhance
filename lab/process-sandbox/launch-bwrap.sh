#!/usr/bin/env bash
# Backend 1: Bubblewrap process sandbox, with Landlock filesystem rules where
# the kernel supports them. Lightest boundary; shares the host kernel.
#
# Contract (see lib.sh): prepare() already built $RUN_DIR. This script implements
# run()+probe() by launching inside.sh under bwrap, and leaves collection to run.sh.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/../common/lib.sh"

RUN_DIR="$1"
source "$RUN_DIR/expected.env"
source "$RUN_DIR/hostside/ports.env"

# --- fail-closed preconditions ---------------------------------------------
require command -v bwrap >/dev/null
# user namespaces must actually work (not just exist). Bind a real binary so the
# check tests namespaces, not PATH.
bwrap --unshare-user --unshare-pid --ro-bind /usr /usr \
      $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) \
      --ro-bind /lib /lib --ro-bind /bin /bin -- /bin/true 2>/dev/null || \
  die "user namespaces unavailable; refuse to run unsandboxed"

# Landlock detection (informational; policy is enforced by landlock_guard.py)
LL_ABI="$(python3 "$HERE/landlock_guard.py" --abi 2>/dev/null || echo 0)"
if [ "$LL_ABI" -ge 1 ]; then
  ok "Landlock ABI $LL_ABI present; filesystem rules will be enforced"
  LL_ACTIVE=true
else
  warn "Landlock unavailable; relying on bind-mount visibility only"
  LL_ACTIVE=false
fi

POLICY_DIGEST="sha256:$(sha256sum "$HERE/launch-bwrap.sh" "$HERE/landlock_guard.py" \
  "$HERE/../common/inside.sh" | sha256sum | cut -d' ' -f1)"

# --- network policy ---------------------------------------------------------
# Default: no network namespace sharing => isolated loopback only, so the sink at
# 127.0.0.1 is NOT the host's sink (egress is effectively blocked). This is what
# we want for the security comparison. In agent mode we instead keep the gateway
# socket reachable via a bind mount, never raw host networking.
NET_ARGS=(--unshare-net)
GATEWAY_ARGS=()
if [ "${RUN_MODE:-emulate}" = "agent" ]; then
  GATEWAY_SOCK="$RUN_DIR/hostside/gateway.sock"
  GATEWAY_ARGS=(--ro-bind "$GATEWAY_SOCK" /run/model.sock)
  # aider talks to a localhost shim that proxies the unix socket; kept offline otherwise.
fi

# --- launch -----------------------------------------------------------------
# Mounts model:
#   /work   rw  disposable workspace           (declared, writable)
#   /input  ro  task + run.env
#   /lab    ro  probe/emulator code
#   /demo   -- deliberately NOT bound: canary/creds live only on the host. A probe
#              hitting /demo/* therefore tests whether the boundary invents access.
#   /tmp    tmpfs
# The canary path in run.env is /demo/canary.txt; because /demo is unbound, a
# correct sandbox yields ENOENT/EACCES. To demo a *leak*, add --ro-bind of
# hostside to /demo and watch the verdict flip.
export DEMO_BACKEND=process-sandbox
export DEMO_WORK=/work
export DEMO_RUN_ENV=/input/run.env
export RUN_MODE="${RUN_MODE:-emulate}"

set +e
bwrap \
  --die-with-parent \
  --new-session \
  --unshare-user --unshare-pid --unshare-uts --unshare-ipc --unshare-cgroup \
  "${NET_ARGS[@]}" \
  --uid 1000 --gid 1000 \
  --clearenv \
  --setenv HOME /tmp/home \
  --setenv PATH /usr/local/bin:/usr/bin:/bin \
  --setenv DEMO_BACKEND process-sandbox \
  --setenv DEMO_WORK /work \
  --setenv DEMO_RUN_ENV /input/run.env \
  --setenv RUN_MODE "$RUN_MODE" \
  --setenv DEMO_MODEL "${DEMO_MODEL:-openai/gpt-4o-mini}" \
  --setenv LIMIT_MEM "${LIMIT_MEM}" \
  --setenv LIMIT_PIDS "${LIMIT_PIDS}" \
  --setenv LIMIT_CPU_SEC "${RUN_TIMEOUT_SEC}" \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --dir /tmp/home \
  --ro-bind /usr /usr \
  --ro-bind /bin /bin \
  --ro-bind /lib /lib \
  $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) \
  --ro-bind /etc /etc \
  --ro-bind "$REPO_ROOT/lab" /lab \
  --ro-bind "$RUN_DIR/input" /input \
  --bind "$RUN_DIR/work" /work \
  "${GATEWAY_ARGS[@]}" \
  --chdir /work \
  -- python3 /lab/process-sandbox/landlock_guard.py --enforce -- \
       /bin/bash /lab/common/inside.sh \
  > "$RUN_DIR/collected/stdout.log" 2> "$RUN_DIR/collected/stderr.log"
RC=$?
set -e

# Copy the agent-produced probes.json into collected/ (raw; evaluator finalizes it).
[ -f "$RUN_DIR/work/probes.json" ] && cp "$RUN_DIR/work/probes.json" "$RUN_DIR/collected/probes.json"

write_metadata "$RUN_DIR" "process-sandbox" "$POLICY_DIGEST" \
  "landlock_active=$LL_ACTIVE" "landlock_abi=$LL_ABI" \
  "network_mode=$([ "${RUN_MODE:-emulate}" = agent ] && echo gateway-socket || echo none)" \
  "run_mode=${RUN_MODE:-emulate}" "inside_rc=$RC"

echo "$RC" > "$RUN_DIR/collected/.inside_rc"
ok "bwrap run finished (inside rc=$RC)"
