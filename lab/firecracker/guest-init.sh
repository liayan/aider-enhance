#!/bin/sh
# PID 1 inside the Firecracker guest. Minimal: mount essentials, mount the block
# devices carrying the workload and lab code, run the same inside.sh, then power
# off. Outputs are written back onto the writable /work block device, which the
# host extracts after shutdown.
set -u
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

mount -t proc proc /proc 2>/dev/null
mount -t sysfs sys /sys 2>/dev/null
mount -t devtmpfs dev /dev 2>/dev/null
mount -t tmpfs tmp /tmp 2>/dev/null
mkdir -p /tmp/home

# Block devices: vda=rootfs(ro), vdb=work(rw), vdc=input(ro, holds /input + /lab)
mkdir -p /work /mnt/input
mount -o rw  /dev/vdb /work      2>/dev/null || echo "guest: work mount failed"
mount -o ro  /dev/vdc /mnt/input 2>/dev/null || echo "guest: input mount failed"
ln -s /mnt/input/input /input 2>/dev/null || true
ln -s /mnt/input/lab   /lab   2>/dev/null || true

export DEMO_BACKEND=firecracker
export DEMO_WORK=/work
export DEMO_RUN_ENV=/input/run.env
export HOME=/tmp/home

echo "guest: starting inside.sh"
/bin/sh /lab/common/inside.sh > /work/stdout.log 2> /work/stderr.log
echo "guest: inside.sh rc=$? (outputs on /work)"

sync
# Clean power-off so the host loop returns.
poweroff -f 2>/dev/null || { echo o > /proc/sysrq-trigger; }
