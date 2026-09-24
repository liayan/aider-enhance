#!/usr/bin/env bash
# Process sandbox: bubblewrap, plus Landlock filesystem rules if the kernel
# supports them, in a cgroup for memory, pids and CPU. Shares the host kernel.
#
# Called by run.sh after prepare_run. Runs inside.sh under bwrap; run.sh does
# collection.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/../common/lib.sh"

RUN_DIR="$1"
source "$RUN_DIR/expected.env"
source "$RUN_DIR/hostside/ports.env"

require command -v bwrap >/dev/null
# Check that user namespaces actually work, not just that bwrap exists.
bwrap --unshare-user --unshare-pid --ro-bind /usr /usr \
      $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) \
      --ro-bind /lib /lib --ro-bind /bin /bin -- /bin/true 2>/dev/null || \
  die "user namespaces unavailable; refuse to run unsandboxed"

# Memory, pids and CPU are limited by a cgroup (a systemd user scope), as for
# the container. RLIMIT_AS isn't used for memory: it counts reserved address
# space, and aider reserves more than 1G while using much less. Check that
# the scope really gets the limits before trusting it.
require command -v systemd-run >/dev/null
MEM_BYTES="$(numfmt --from=iec "$LIMIT_MEM")"
SCOPE=(systemd-run --user --scope --quiet
       -p "MemoryMax=$LIMIT_MEM" -p MemorySwapMax=0
       -p "TasksMax=$LIMIT_PIDS" -p "CPUQuota=$(( LIMIT_CPUS * 100 ))%"
       # Kill only the process that hit the limit, as podman does; the
       # default stops the whole scope.
       -p OOMPolicy=continue)
GOT="$("${SCOPE[@]}" -- sh -c 'd=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
  echo "$(cat $d/memory.max) $(cat $d/memory.swap.max) $(cat $d/pids.max)"' 2>/dev/null || true)"
[ "$GOT" = "$MEM_BYTES 0 $LIMIT_PIDS" ] || \
  die "systemd user scope doesn't enforce memory/pids limits (got '${GOT:-nothing}'); refuse to run"

# Landlock is applied by landlock_guard.py; this is only for reporting.
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

# Own network namespace: 127.0.0.1 inside is not the host's loopback, so the
# sink is unreachable. Agent mode adds only the gateway socket as a bind mount.
NET_ARGS=(--unshare-net)
GATEWAY_ARGS=()
if [ "${RUN_MODE:-emulate}" = "agent" ]; then
  GATEWAY_SOCK="$RUN_DIR/hostside/gateway.sock"
  GATEWAY_ARGS=(--ro-bind "$GATEWAY_SOCK" /run/model.sock)
fi

# Mounts:
#   /work   rw     workspace copy
#   /input  ro     task, run.env
#   /src    ro     probe and emulator code
#   /tmp    tmpfs
# /demo (canary, creds) is not mounted, so probes for it should get ENOENT.
# To show a leak, add --ro-bind "$RUN_DIR/hostside" /demo.
export DEMO_BACKEND=process-sandbox
export DEMO_WORK=/work
export DEMO_RUN_ENV=/input/run.env
export RUN_MODE="${RUN_MODE:-emulate}"

set +e
"${SCOPE[@]}" -- bwrap \
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
  --setenv LIMIT_PIDS "${LIMIT_PIDS}" \
  --setenv LIMIT_CPU_SEC "${RUN_TIMEOUT_SEC}" \
  --setenv GATEWAY_PORT "${GATEWAY_PORT:-8080}" \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --dir /tmp/home \
  --ro-bind /usr /usr \
  --ro-bind /bin /bin \
  --ro-bind /lib /lib \
  $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) \
  --ro-bind /etc /etc \
  --ro-bind "$REPO_ROOT/src" /src \
  --ro-bind "$RUN_DIR/input" /input \
  --bind "$RUN_DIR/work" /work \
  "${GATEWAY_ARGS[@]}" \
  --chdir /work \
  -- python3 /src/process-sandbox/landlock_guard.py --enforce -- \
       /bin/bash /src/common/inside.sh \
  > "$RUN_DIR/collected/stdout.log" 2> "$RUN_DIR/collected/stderr.log"
RC=$?
set -e

# Raw probe output; evaluate.py produces the final verdicts.
[ -f "$RUN_DIR/work/probes.json" ] && cp "$RUN_DIR/work/probes.json" "$RUN_DIR/collected/probes.json"

write_metadata "$RUN_DIR" "process-sandbox" "$POLICY_DIGEST" \
  "landlock_active=$LL_ACTIVE" "landlock_abi=$LL_ABI" \
  "cgroup=systemd-user-scope" "memory_max=$LIMIT_MEM" "pids_max=$LIMIT_PIDS" \
  "network_mode=$([ "${RUN_MODE:-emulate}" = agent ] && echo gateway-socket || echo none)" \
  "run_mode=${RUN_MODE:-emulate}" "inside_rc=$RC"

echo "$RC" > "$RUN_DIR/collected/.inside_rc"
ok "bwrap run finished (inside rc=$RC)"
