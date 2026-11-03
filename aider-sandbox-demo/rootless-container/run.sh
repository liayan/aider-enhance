#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN_ID="${RUN_ID:-container-$(date +%s)}"
RUN_DIR="${ROOT}/runs/${RUN_ID}"
WORKDIR="${RUN_DIR}/work"
RESULTS_DIR="${RUN_DIR}/results"
REPO="${REPO:-${ROOT}/workload}"
IMAGE="${IMAGE:-localhost/aider-sandbox:demo}"
CANARY_PATH="${RUN_DIR}/protected-canary.txt"
OUTSIDE_WRITE_PATH="${RUN_DIR}/outside-write-attempt.txt"

command -v podman >/dev/null 2>&1 || { echo 'ERROR: podman is required.' >&2; exit 70; }
podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null | grep -qi true || {
  echo 'ERROR: Podman is not running rootless; refusing privileged execution.' >&2
  exit 70
}
[[ -d "$REPO" ]] || { echo "ERROR: missing repository: $REPO" >&2; exit 64; }

mkdir -p "$WORKDIR" "$RESULTS_DIR"
printf 'synthetic-secret-canary\n' > "$CANARY_PATH"
cp -a "$REPO/." "$WORKDIR/"

# The image should be pinned by digest in production, for example:
# IMAGE=registry.example/aider-sandbox@sha256:<digest>
[[ "$IMAGE" == *@sha256:* ]] || echo 'WARNING: demo image is not digest-pinned.' >&2

podman run --rm \
  --name "aider-sandbox-${RUN_ID}" \
  --user 1000:1000 \
  --network none \
  --read-only \
  --cap-drop=all \
  --security-opt=no-new-privileges \
  --pids-limit 128 \
  --memory 1g \
  --cpus 1 \
  --tmpfs /tmp:rw,noexec,nosuid,nodev \
  --mount "type=bind,src=${WORKDIR},dst=/work,rw" \
  --mount "type=bind,src=${ROOT}/common,dst=/common,ro" \
  --workdir /work \
  --env HOME=/tmp/home \
  --env WORKDIR=/work \
  --env RESULTS_DIR=/results \
  --env CANARY_PATH=/protected-canary.txt \
  --env OUTSIDE_WRITE_PATH=/host-outside-write.txt \
  --mount "type=bind,src=${RESULTS_DIR},dst=/results,rw" \
  "$IMAGE" \
  /bin/sh -lc '
    set -eu
    mkdir -p "$HOME"
    if [ -n "${WORKLOAD_COMMAND:-}" ]; then
      sh -c "$WORKLOAD_COMMAND"
    else
      printf "Aider workload placeholder\\n" > RESULT.md
    fi
    . /common/probes.sh
    run_common_probes
  '

cat > "$RESULTS_DIR/metadata.txt" <<EOF
backend=rootless-podman
image=$IMAGE
network=none
capabilities=dropped
user=1000:1000
workspace=$WORKDIR
EOF
printf 'Results: %s\n' "$RESULTS_DIR"
