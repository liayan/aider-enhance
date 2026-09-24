#!/usr/bin/env bash
# Backend 3: Firecracker microVM. Separate guest kernel => strongest boundary,
# highest setup cost. This is an ADAPTER around the same inside.sh contract.
#
# Coordination uses vsock: the guest runs a tiny agent that receives the run
# inputs, executes inside.sh, and streams back RESULT.md/probes.json/logs. The
# host never passes the real model key into the image; in agent mode a vsock
# forwarder on the host injects credentials (mirror of model_gateway.py).
#
# This script fails closed if KVM, the binary, kernel, or rootfs are missing, and
# records "not executed" rather than faking a pass.
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

# --- fail-closed preconditions; a miss is "not executed", never a pass ------
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

# --- per-run writable overlay + input disk ---------------------------------
API_SOCK="$RUN_DIR/hostside/fc-api.sock"
WORK_IMG="$RUN_DIR/hostside/work.ext4"
INPUT_IMG="$RUN_DIR/hostside/input.ext4"
rm -f "$API_SOCK"

# Pack workspace + inputs + lab code into small ext4 images the guest mounts.
make_ext4() { # <img> <size_mb> <srcdir>
  local img="$1" mb="$2" src="$3"
  dd if=/dev/zero of="$img" bs=1M count="$mb" status=none
  mkfs.ext4 -q -F "$img"
  local mnt; mnt="$(mktemp -d)"
  # Rootless-friendly population without mount: use debugfs write.
  # Simpluer path: require guestfish/mount if available; else fall back to debugfs.
  if command -v guestmount >/dev/null && guestmount -a "$img" -m /dev/sda "$mnt" 2>/dev/null; then
    cp -a "$src/." "$mnt/"; guestunmount "$mnt"
  else
    # debugfs population (no root mount needed)
    ( cd "$src" && find . -type d -printf '%P\n' | while read -r d; do
        [ -n "$d" ] && debugfs -w -R "mkdir /$d" "$img" >/dev/null 2>&1; done
      find . -type f -printf '%P\n' | while read -r f; do
        debugfs -w -R "cd /$(dirname "$f")" "$img" >/dev/null 2>&1
        debugfs -w -R "write $f $(basename "$f")" "$img" >/dev/null 2>&1 || \
        debugfs -w -R "write ./$f /$f" "$img" >/dev/null 2>&1; done )
  fi
  rmdir "$mnt" 2>/dev/null || true
}

STAGE="$(mktemp -d)"
mkdir -p "$STAGE/work" "$STAGE/input" "$STAGE/lab"
cp -a "$RUN_DIR/work/." "$STAGE/work/"
cp -a "$RUN_DIR/input/." "$STAGE/input/"
cp -a "$REPO_ROOT/lab/." "$STAGE/lab/"
make_ext4 "$WORK_IMG" 256 "$STAGE/work"
make_ext4 "$INPUT_IMG" 64 "$STAGE"   # holds /input and /lab
rm -rf "$STAGE"

# --- boot config ------------------------------------------------------------
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
  > "$RUN_DIR/collected/stdout.log" 2> "$RUN_DIR/collected/stderr.log"
RC=$?
set -e

# Guest writes outputs back onto the work image; extract them.
"$HERE/extract-guest-output.sh" "$WORK_IMG" "$RUN_DIR/collected" || \
  warn "could not extract guest outputs"

write_metadata "$RUN_DIR" "firecracker" "$POLICY_DIGEST" \
  "kernel_digest=$KERNEL_SHA" "rootfs_digest=$ROOTFS_SHA" \
  "vcpus=${FC_VCPUS}" "mem_mib=${FC_MEM_MIB}" "network_mode=$NET_MODE" \
  "workspace_transfer=ext4-block" "run_mode=${RUN_MODE:-emulate}" "inside_rc=$RC"

echo "$RC" > "$RUN_DIR/collected/.inside_rc"
ok "firecracker run finished (rc=$RC)"
