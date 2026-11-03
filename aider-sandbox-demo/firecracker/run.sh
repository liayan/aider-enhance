#!/usr/bin/env bash
set -Eeuo pipefail

# Firecracker requires a prepared guest kernel and root filesystem. This script
# intentionally stops if either is missing; it never substitutes host execution.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN_ID="${RUN_ID:-firecracker-$(date +%s)}"
RUN_DIR="${ROOT}/runs/${RUN_ID}"
RESULTS_DIR="${RUN_DIR}/results"
WORKDIR="${RUN_DIR}/work"
REPO="${REPO:-${ROOT}/workload}"
FIRECRACKER_BIN="${FIRECRACKER_BIN:-firecracker}"
KERNEL_IMAGE="${KERNEL_IMAGE:?Set KERNEL_IMAGE to a pinned guest kernel}"
ROOTFS_IMAGE="${ROOTFS_IMAGE:?Set ROOTFS_IMAGE to a pinned guest rootfs}"
WORKSPACE_IMAGE="${WORKSPACE_IMAGE:-${RUN_DIR}/workspace.ext4}"
API_SOCK="${RUN_DIR}/firecracker.sock"
VSOCK_CID="${VSOCK_CID:-42}"

command -v "$FIRECRACKER_BIN" >/dev/null 2>&1 || { echo 'ERROR: firecracker not found.' >&2; exit 70; }
[[ -r /dev/kvm ]] || { echo 'ERROR: /dev/kvm unavailable; microVM not executed.' >&2; exit 70; }
[[ -r "$KERNEL_IMAGE" ]] || { echo 'ERROR: kernel image unavailable.' >&2; exit 64; }
[[ -r "$ROOTFS_IMAGE" ]] || { echo 'ERROR: rootfs image unavailable.' >&2; exit 64; }
[[ -d "$REPO" ]] || { echo "ERROR: missing repository: $REPO" >&2; exit 64; }

mkdir -p "$RUN_DIR" "$RESULTS_DIR" "$WORKDIR"
cp -a "$REPO/." "$WORKDIR/"
printf 'synthetic-secret-canary\n' > "$RUN_DIR/protected-canary.txt"

# The workspace image must be created before this script and formatted with an
# ext4 filesystem. It should contain only the disposable workload and output.
[[ -f "$WORKSPACE_IMAGE" ]] || {
  echo 'ERROR: missing workspace image; create a disposable ext4 image first.' >&2
  exit 64
}

sha256sum "$KERNEL_IMAGE" "$ROOTFS_IMAGE" "$WORKSPACE_IMAGE" > "$RESULTS_DIR/image-sha256.txt"
rm -f "$API_SOCK"

"$FIRECRACKER_BIN" --api-sock "$API_SOCK" >/dev/null 2>"$RESULTS_DIR/firecracker.stderr" &
FC_PID=$!
cleanup() {
  kill "$FC_PID" 2>/dev/null || true
  wait "$FC_PID" 2>/dev/null || true
  rm -f "$API_SOCK"
}
trap cleanup EXIT

for _ in $(seq 1 100); do
  [[ -S "$API_SOCK" ]] && break
  sleep 0.05
done
[[ -S "$API_SOCK" ]] || { echo 'ERROR: Firecracker API socket did not appear.' >&2; exit 70; }

fc_api() {
  curl --silent --show-error --fail --unix-socket "$API_SOCK" \
    -H 'Content-Type: application/json' "$@"
}

fc_api -X PUT http://localhost/machine-config \
  -d '{"vcpu_count":1,"mem_size_mib":1024,"ht_enabled":false}'
fc_api -X PUT http://localhost/boot-source \
  -d "$(printf '{\"kernel_image_path\":\"%s\",\"boot_args\":\"console=ttyS0 reboot=k panic=1 pci=off\"}' "$KERNEL_IMAGE")"
fc_api -X PUT http://localhost/drives/rootfs \
  -d "$(printf '{\"drive_id\":\"rootfs\",\"path_on_host\":\"%s\",\"is_root_device\":true,\"is_read_only\":true}' "$ROOTFS_IMAGE")"
fc_api -X PUT http://localhost/drives/workspace \
  -d "$(printf '{\"drive_id\":\"workspace\",\"path_on_host\":\"%s\",\"is_root_device\":false,\"is_read_only\":false}' "$WORKSPACE_IMAGE")"

# Optional vsock setup. The guest must run a matching vsock service that
# executes only the declared workload and returns declared artifacts.
if [[ "${ENABLE_VSOCK:-0}" == 1 ]]; then
  [[ -n "${VSOCK_SOCKET:-}" ]] || { echo 'ERROR: VSOCK_SOCKET required.' >&2; exit 64; }
  fc_api -X PUT http://localhost/vsock \
    -d "$(printf '{\"vsock_id\":\"%s\",\"guest_cid\":%s,\"uds_path\":\"%s\"}' \
      demo-vsock "$VSOCK_CID" "$VSOCK_SOCKET")"
fi

fc_api -X PUT http://localhost/actions -d '{"action_type":"InstanceStart"}'

cat > "$RESULTS_DIR/metadata.txt" <<EOF
backend=firecracker-microvm
kernel=$KERNEL_IMAGE
rootfs=$ROOTFS_IMAGE
workspace=$WORKSPACE_IMAGE
vcpu=1
memory_mib=1024
network=disabled
vsock_cid=$VSOCK_CID
EOF

echo 'Firecracker instance started.'
echo 'Run the guest workload through the approved vsock/serial protocol.'
echo 'Do not copy arbitrary host paths into the guest.'
