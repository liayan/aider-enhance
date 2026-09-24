#!/usr/bin/env bash
# Build the Firecracker rootfs: export a python image with podman, add
# guest-init, and copy it into an ext4 image. Expects the kernel to already be
# at assets/vmlinux. Needs root for the loop mount.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../common/lib.sh"
ASSETS="$HERE/assets"; mkdir -p "$ASSETS"
SIZE_MB="${ROOTFS_MB:-800}"

if [ ! -f "$ASSETS/vmlinux" ]; then
  cat >&2 <<EOF
${c_yel}No kernel at $ASSETS/vmlinux.${c_rst}
Provide a Firecracker-compatible uncompressed kernel, e.g. from the
firecracker-microvm CI bucket, or build one with their kernel config:
  https://github.com/firecracker-microvm/firecracker/blob/main/docs/rootfs-and-kernel-setup.md
Place it at: $ASSETS/vmlinux
EOF
  die "kernel required"
fi

IMG="${GUEST_IMAGE:-docker.io/library/python:3.12-slim-bookworm}"
ROOTFS="$ASSETS/rootfs.ext4"
log "building rootfs from $IMG ($SIZE_MB MB)"

CID="$(podman create "$IMG" /bin/true)"
EXPORT="$(mktemp -d)"; chmod 0755 "$EXPORT"   # becomes the rootfs /
podman export "$CID" | tar -x -C "$EXPORT"
podman rm "$CID" >/dev/null

install -m0755 "$HERE/guest-init.sh" "$EXPORT/sbin/guest-init"
mkdir -p "$EXPORT/work" "$EXPORT/mnt/input" "$EXPORT/proc" "$EXPORT/sys" "$EXPORT/dev" "$EXPORT/tmp"
# The rootfs is mounted read-only, so these can't be made at boot.
ln -sfn /mnt/input/input "$EXPORT/input"
ln -sfn /mnt/input/src "$EXPORT/src"

dd if=/dev/zero of="$ROOTFS" bs=1M count="$SIZE_MB" status=none
mkfs.ext4 -q -F "$ROOTFS"
MNT="$(mktemp -d)"
if mount -o loop "$ROOTFS" "$MNT" 2>/dev/null; then
  cp -a "$EXPORT/." "$MNT/"
  umount "$MNT"
  ok "rootfs built at $ROOTFS"
else
  die "need privileges to loop-mount; run build-guest.sh as root on the demo host"
fi
rm -rf "$EXPORT" "$MNT"

echo "kernel : $(sha256sum "$ASSETS/vmlinux")"
echo "rootfs : $(sha256sum "$ROOTFS")"
echo "Pin these digests in versions.env / metadata before the talk."
