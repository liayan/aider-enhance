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

# Run mode and, in agent mode, the model settings, written by the launcher.
[ -f /mnt/input/guest.env ] && . /mnt/input/guest.env
export RUN_MODE="${RUN_MODE:-emulate}"
if [ "$RUN_MODE" = "agent" ]; then
  export DEMO_MODEL GATEWAY_PORT MODEL_VSOCK
  # portfwd listens on 127.0.0.1. There is no ip(8) in the image, so bring
  # lo up with SIOCGIFFLAGS/SIOCSIFFLAGS. No other interface exists.
  python3 -c '
import fcntl, socket, struct
s = socket.socket()
flags = struct.unpack("16sH", fcntl.ioctl(s, 0x8913, struct.pack("16sH", b"lo", 0))[:18])[1]
fcntl.ioctl(s, 0x8914, struct.pack("16sH", b"lo", flags | 1))
' || echo "guest: could not bring up lo"
fi

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
