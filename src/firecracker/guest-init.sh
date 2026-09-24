#!/bin/sh
# PID 1 in the guest. Mount the work and input drives, run inside.sh as an
# unprivileged user, then reboot, which ends the Firecracker process
# (reboot=k). Outputs stay on the work drive for the host to extract.
set -u
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin

mount -t proc proc /proc 2>/dev/null
mount -t sysfs sys /sys 2>/dev/null
mount -t devtmpfs dev /dev 2>/dev/null
mount -t tmpfs -o mode=1777,nosuid,nodev tmp /tmp 2>/dev/null
mkdir -p /tmp/home
chown 1000:1000 /tmp/home

# vda rootfs (ro), vdb work (rw), vdc input + src (ro). The rootfs is
# read-only, so /input and /src are symlinks made by build-guest.sh.
mount -o rw,nosuid,nodev /dev/vdb /work 2>/dev/null || echo "guest: work mount failed"
mount -o ro,nosuid,nodev /dev/vdc /mnt/input 2>/dev/null || echo "guest: input mount failed"
chown -R 1000:1000 /work

export DEMO_BACKEND=firecracker
export DEMO_WORK=/work
export DEMO_RUN_ENV=/input/run.env
export HOME=/tmp/home

# Same uid as the container backend, with no capabilities, so the probes
# measure the VM boundary rather than root inside it.
echo "guest: starting inside.sh"
setpriv --reuid=1000 --regid=1000 --clear-groups --inh-caps=-all \
  --bounding-set=-all --no-new-privs \
  /bin/bash /src/common/inside.sh > /work/stdout.log 2> /work/stderr.log
echo "guest: inside.sh rc=$? (outputs on /work)"

sync
umount /work 2>/dev/null
echo b > /proc/sysrq-trigger
