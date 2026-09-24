# Firecracker backend

## Guest assets

```bash
# Firecracker-compatible uncompressed kernel
cp /path/to/vmlinux src/firecracker/assets/vmlinux

# rootfs from the python image; needs root for the loop mount
sudo env ROOTFS_MB=800 src/firecracker/build-guest.sh

# for --agent: rootfs-aider.ext4, built from the container image with aider
sudo env WITH_AIDER=1 src/firecracker/build-guest.sh
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
- Agent mode boots `rootfs-aider.ext4` and reaches the model gateway over
  vsock. Firecracker maps a guest connection to host port `P` onto the Unix
  socket `<uds_path>_P`, so the launcher links `fc-vsock.sock_1024` to the
  gateway socket. In the guest, `portfwd.py` bridges `127.0.0.1:8080` to
  `vsock:2:1024`. Only that one port has a host socket; the guest has no
  network interface besides loopback.

## metadata.json

Kernel and rootfs digests, vCPU count, memory, network mode, workspace
transfer method.
