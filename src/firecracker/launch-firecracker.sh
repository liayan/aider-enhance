#!/usr/bin/env bash
# Firecracker microVM with its own guest kernel. Workspace, inputs and src/ go
# in as ext4 drives; guest-init runs inside.sh and the outputs are read back
# off the work image. Agent mode has no model path here yet (nothing forwards
# the vsock socket).
#
# Missing KVM, binary, kernel or rootfs is recorded as not-executed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/../common/lib.sh"

RUN_DIR="$1"
source "$RUN_DIR/expected.env"
source "$RUN_DIR/hostside/ports.env"

KERNEL="${FC_KERNEL:-$HERE/assets/vmlinux}"
ROOTFS="${FC_ROOTFS:-$HERE/assets/rootfs.ext4}"
FC_BIN="${FC_BIN:-firecracker}"

skip() {
  warn "firecracker prerequisite missing: $1"
  mkdir -p "$RUN_DIR/collected"
  cat > "$RUN_DIR/collected/probes.json" <<EOF
{"backend":"firecracker","status":"not-executed","reason":"$1","probes":[]}
EOF
  write_metadata "$RUN_DIR" "firecracker" "sha256:none" \
    "status=not-executed" "reason=$1" "run_mode=${RUN_MODE:-emulate}"
  echo 3 > "$RUN_DIR/collected/.inside_rc"
  exit 0
}
[ -e /dev/kvm ] || skip "no /dev/kvm"
command -v "$FC_BIN" >/dev/null || skip "firecracker binary not found"
[ -f "$KERNEL" ] || skip "kernel image missing ($KERNEL) — run build-guest.sh"
[ -f "$ROOTFS" ] || skip "rootfs missing ($ROOTFS) — run build-guest.sh"

KERNEL_SHA="sha256:$(sha256sum "$KERNEL" | cut -d' ' -f1)"
ROOTFS_SHA="sha256:$(sha256sum "$ROOTFS" | cut -d' ' -f1)"
POLICY_DIGEST="sha256:$(sha256sum "$HERE/launch-firecracker.sh" "$HERE/guest-init.sh" | sha256sum | cut -d' ' -f1)"

API_SOCK="$RUN_DIR/hostside/fc-api.sock"
WORK_IMG="$RUN_DIR/hostside/work.ext4"
INPUT_IMG="$RUN_DIR/hostside/input.ext4"
rm -f "$API_SOCK"

make_ext4() { # <img> <size_mb> <srcdir>
  local img="$1" mb="$2" src="$3"
  dd if=/dev/zero of="$img" bs=1M count="$mb" status=none
  # -d fills the fs from a directory (e2fsprogs >= 1.43); no mount or root.
  mkfs.ext4 -q -F -d "$src" "$img" || die "mkfs.ext4 -d failed for $src"
}

STAGE="$(mktemp -d)"; chmod 0755 "$STAGE"   # becomes the input drive root
mkdir -p "$STAGE/work" "$STAGE/input" "$STAGE/src"
cp -a "$RUN_DIR/work/." "$STAGE/work/"
cp -a "$RUN_DIR/input/." "$STAGE/input/"
# assets/ holds the kernel and rootfs; the guest doesn't need them.
tar -C "$REPO_ROOT/src" --exclude=./firecracker/assets -cf - . | tar -C "$STAGE/src" -xf -
make_ext4 "$WORK_IMG" 256 "$STAGE/work"
make_ext4 "$INPUT_IMG" 64 "$STAGE"   # holds /input and /src
rm -rf "$STAGE"

NET_MODE=none
BOOT_ARGS="console=ttyS0 reboot=k panic=1 pci=off init=/sbin/guest-init"
CFG="$RUN_DIR/hostside/fc-config.json"
cat > "$CFG" <<EOF
{
  "boot-source": { "kernel_image_path": "$KERNEL", "boot_args": "$BOOT_ARGS" },
  "drives": [
    { "drive_id": "rootfs", "path_on_host": "$ROOTFS", "is_root_device": true,  "is_read_only": true },
    { "drive_id": "work",   "path_on_host": "$WORK_IMG", "is_root_device": false, "is_read_only": false },
    { "drive_id": "input",  "path_on_host": "$INPUT_IMG","is_root_device": false, "is_read_only": true }
  ],
  "machine-config": { "vcpu_count": ${FC_VCPUS}, "mem_size_mib": ${FC_MEM_MIB} },
  "vsock": { "guest_cid": 3, "uds_path": "$RUN_DIR/hostside/fc-vsock.sock" }
}
EOF

ok "booting firecracker (vcpus=${FC_VCPUS} mem=${FC_MEM_MIB}MiB net=$NET_MODE)"
set +e
timeout "${RUN_TIMEOUT_SEC}" "$FC_BIN" --no-api --config-file "$CFG" \
  > "$RUN_DIR/collected/console.log" 2> "$RUN_DIR/collected/firecracker.log"
RC=$?
set -e

# Copy the guest's outputs back into work/, where run.sh's collect step reads
# them with the same allow-list and size caps as the other backends.
"$HERE/extract-guest-output.sh" "$WORK_IMG" "$RUN_DIR/work" || \
  warn "could not extract guest outputs"
[ -f "$RUN_DIR/work/probes.json" ] && cp "$RUN_DIR/work/probes.json" "$RUN_DIR/collected/probes.json"

write_metadata "$RUN_DIR" "firecracker" "$POLICY_DIGEST" \
  "kernel_digest=$KERNEL_SHA" "rootfs_digest=$ROOTFS_SHA" \
  "vcpus=${FC_VCPUS}" "mem_mib=${FC_MEM_MIB}" "network_mode=$NET_MODE" \
  "workspace_transfer=ext4-block" "run_mode=${RUN_MODE:-emulate}" "inside_rc=$RC"

echo "$RC" > "$RUN_DIR/collected/.inside_rc"
ok "firecracker run finished (rc=$RC)"
