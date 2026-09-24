#!/usr/bin/env bash
# Backend 2: rootless Podman (or any OCI runtime exposing the same flags).
# Shares the host kernel but adds user/mount/pid/net namespaces and a read-only
# root filesystem. The launcher REFUSES known-unsafe configurations before it
# starts anything, and records the effective config it actually used.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/../common/lib.sh"

RUN_DIR="$1"
source "$RUN_DIR/expected.env"
source "$RUN_DIR/hostside/ports.env"

require command -v podman >/dev/null

# --- fail-closed: must be genuinely rootless -------------------------------
ROOTLESS="$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || echo false)"
[ "$ROOTLESS" = "true" ] || die "podman is not rootless; refuse to run"

# Rootless podman on cgroup v1 drops --memory/--pids-limit/--cpus with only a
# warning, so the resource budget would silently not apply.
CGROUPS="$(podman info --format '{{.Host.CgroupsVersion}}' 2>/dev/null || echo unknown)"
[ "$CGROUPS" = "v2" ] || die "rootless podman needs cgroup v2 to enforce limits (host has $CGROUPS); refuse to run"

IMAGE="${CONTAINER_IMAGE}"
podman image exists "$IMAGE" || die "image $IMAGE not built; run: podman build -t ${IMAGE#localhost/} -f $HERE/Containerfile $REPO_ROOT"
IMAGE_DIGEST="$(podman image inspect "$IMAGE" --format '{{.Digest}}' 2>/dev/null || echo unknown)"
IMAGE_SIZE="$(podman image inspect "$IMAGE" --format '{{.Size}}' 2>/dev/null || echo 0)"

# --- network policy ---------------------------------------------------------
NET=(--network none)
GATEWAY_MOUNT=()
if [ "${RUN_MODE:-emulate}" = "agent" ]; then
  # Give ONLY the gateway socket, via a bind mount; still --network none.
  GATEWAY_MOUNT=(--mount "type=bind,src=$RUN_DIR/hostside/gateway.sock,dst=/run/model.sock,ro")
fi

# --- assemble the argv, then verify it before running -----------------------
ARGS=(
  run --rm
  # Map the invoking host user to uid 1000 inside so /work (owned by that
  # user) is writable; plain --user 1000 lands on an unrelated subuid.
  --userns=keep-id:uid=1000,gid=1000
  --user 1000:1000
  "${NET[@]}"
  --read-only
  --cap-drop=all
  --security-opt=no-new-privileges
  --security-opt label=disable
  --pids-limit "${LIMIT_PIDS}"
  --memory "${LIMIT_MEM}"
  --cpus "${LIMIT_CPUS}"
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m
  --tmpfs /tmp/home:rw,nosuid,nodev,size=64m
  --mount "type=bind,src=$RUN_DIR/work,dst=/work,rw"
  --mount "type=bind,src=$RUN_DIR/input,dst=/input,ro"
  --mount "type=bind,src=$REPO_ROOT/lab,dst=/lab,ro"
  # NOTE: hostside (canary/creds) is intentionally NOT mounted.
  --env DEMO_BACKEND=rootless-container
  --env DEMO_WORK=/work
  --env DEMO_RUN_ENV=/input/run.env
  --env "RUN_MODE=${RUN_MODE:-emulate}"
  --env "DEMO_MODEL=${DEMO_MODEL:-openai/gpt-4o-mini}"
  --env "GATEWAY_PORT=${GATEWAY_PORT:-8080}"
  "${GATEWAY_MOUNT[@]}"
  "$IMAGE"
)

# Reject dangerous flags if a well-meaning edit ever introduces them.
verify_no() {
  local bad="$1"
  for a in "${ARGS[@]}"; do
    if [[ "$a" == *"$bad"* ]]; then die "refusing: found forbidden option '$bad'"; fi
  done
  return 0
}
verify_no "--privileged"
verify_no "--network=host"; verify_no "--net=host"
verify_no "--pid=host";     verify_no "--ipc=host"
verify_no "/var/run/docker.sock"; verify_no "/run/podman/podman.sock"
verify_no "-v /:"; verify_no "src=/,"
ok "container config verified (rootless, no-net, ro-root, caps dropped)"

POLICY_DIGEST="sha256:$(printf '%s\n' "${ARGS[@]}" | sha256sum | cut -d' ' -f1)"

set +e
podman "${ARGS[@]}" \
  > "$RUN_DIR/collected/stdout.log" 2> "$RUN_DIR/collected/stderr.log"
RC=$?
set -e

[ -f "$RUN_DIR/work/probes.json" ] && cp "$RUN_DIR/work/probes.json" "$RUN_DIR/collected/probes.json"

write_metadata "$RUN_DIR" "rootless-container" "$POLICY_DIGEST" \
  "image=$IMAGE" "image_digest=$IMAGE_DIGEST" "image_size_bytes=$IMAGE_SIZE" \
  "network_mode=$([ "${RUN_MODE:-emulate}" = agent ] && echo gateway-socket || echo none)" \
  "read_only_root=true" "caps=drop-all" "run_mode=${RUN_MODE:-emulate}" "inside_rc=$RC"

echo "$RC" > "$RUN_DIR/collected/.inside_rc"
ok "podman run finished (inside rc=$RC)"
