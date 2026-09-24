# Firecracker backend

## Guest assets

```bash
# Firecracker-compatible uncompressed kernel
cp /path/to/vmlinux src/firecracker/assets/vmlinux

# rootfs from the python image; needs root for the loop mount
sudo ROOTFS_MB=800 src/firecracker/build-guest.sh
```

Pin the printed digests in `versions.env`.

## Run

```bash
./src/run.sh firecracker
```

If `/dev/kvm`, the `firecracker` binary, the kernel or the rootfs is missing,
the run is recorded as `not-executed` with the reason and exits 0.

## How it works

- The workspace, `/input` and `src/` are packed into ext4 images with
  `mkfs.ext4 -d` and attached as drives.
- `guest-init` runs as PID 1, mounts the drives and runs `src/common/inside.sh`
  as uid 1000 with no capabilities (`setpriv`), like the container backend.
  It then reboots the guest, which ends the Firecracker process.
- Outputs are written to the work drive, read back into `work/` with
  `debugfs` after the guest exits, and collected with the same allow-list as
  the other backends. The guest's serial console goes to
  `collected/console.log` and Firecracker's own log to
  `collected/firecracker.log`.
- Agent mode isn't supported yet: the config defines a vsock device, but
  nothing on the host forwards it to the model gateway.

## metadata.json

Kernel and rootfs digests, vCPU count, memory, network mode, workspace
transfer method.
