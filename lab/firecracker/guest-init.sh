#!/bin/sh
# PID 1 in the guest. Mount the work and input drives, run inside.sh, power
# off. Outputs stay on the work drive for the host to extract.
set -u
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

mount -t proc proc /proc 2>/dev/null
mount -t sysfs sys /sys 2>/dev/null
mount -t devtmpfs dev /dev 2>/dev/null
mount -t tmpfs tmp /tmp 2>/dev/null
mkdir -p /tmp/home

# vda rootfs (ro), vdb work (rw), vdc input + lab (ro)
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
poweroff -f 2>/dev/null || { echo o > /proc/sysrq-trigger; }
