# Firecracker backend

This is the strongest boundary (separate guest kernel) and the most setup.

## One-time asset build

```bash
# 1. Provide a Firecracker-compatible uncompressed kernel:
cp /path/to/vmlinux lab/firecracker/assets/vmlinux

# 2. Build the rootfs (needs root for the loop-mount step):
sudo ROOTFS_MB=800 lab/firecracker/build-guest.sh

# 3. Pin the printed digests into versions.env before the talk.
```

## Run

```bash
./lab/run.sh firecracker
```

If `/dev/kvm`, the `firecracker` binary, the kernel, or the rootfs are missing,
the backend records `status: not-executed` with a reason and exits 0 — a skipped
Firecracker run is **labeled, never counted as a pass** (spec §10).

## How it works

- Workspace, `/input`, and `/lab` are packed into small ext4 images.
- `guest-init` (PID 1) mounts them and runs the same `lab/common/inside.sh`.
- Coordination channel is vsock; in agent mode a host-side vsock forwarder
  injects the model credential exactly like `model_gateway.py` (the key never
  enters the guest image).
- Outputs are written back onto the writable work image and extracted with
  `debugfs` (no host mount required).

## Reported in metadata.json

guest-image digest, kernel digest, vCPU count, memory limit, network mode,
workspace-transfer method.
