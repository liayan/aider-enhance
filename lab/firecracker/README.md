# Firecracker backend

## Guest assets

```bash
# Firecracker-compatible uncompressed kernel
cp /path/to/vmlinux lab/firecracker/assets/vmlinux

# rootfs from the python image; needs root for the loop mount
sudo ROOTFS_MB=800 lab/firecracker/build-guest.sh
```

Pin the printed digests in `versions.env`.

## Run

```bash
./lab/run.sh firecracker
```

If `/dev/kvm`, the `firecracker` binary, the kernel or the rootfs is missing,
the run is recorded as `not-executed` with the reason and exits 0.

## How it works

- The workspace, `/input` and `lab/` are packed into ext4 images with
  `mkfs.ext4 -d` and attached as drives.
- `guest-init` runs as PID 1, mounts the drives and runs `lab/common/inside.sh`.
- Outputs are written to the work drive and read back with `debugfs` after
  the guest powers off.
- Agent mode isn't supported yet: the config defines a vsock device, but
  nothing on the host forwards it to the model gateway.

## metadata.json

Kernel and rootfs digests, vCPU count, memory, network mode, workspace
transfer method.
