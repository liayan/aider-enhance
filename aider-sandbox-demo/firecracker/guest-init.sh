#!/bin/sh
set -eu

# This script runs inside the Firecracker guest, not on the host.
# The guest image must contain Aider, the pinned model client configuration,
# the common probes, and a small vsock/serial control service.

mountpoint -q /workspace || mount -t ext4 /dev/vdb /workspace
mountpoint -q /proc || mount -t proc proc /proc

cd /workspace
export HOME=/tmp/home
mkdir -p "$HOME" /workspace/results

# The control service should provide the workload command through a validated,
# fixed protocol. Do not eval arbitrary host-provided shell text here.
if [ "${RUN_WORKLOAD:-0}" = 1 ]; then
  aider --yes-always --message "Implement the task described in TASK.md" \
    > /workspace/results/aider.log 2>&1
fi

printf 'guest_complete\n' > /workspace/results/status
sync
