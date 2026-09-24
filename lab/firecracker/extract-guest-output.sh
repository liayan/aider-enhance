#!/usr/bin/env bash
# Pull declared outputs off the writable work ext4 image after the guest halts.
# Uses debugfs (no mount/root needed). Only the allow-listed names are extracted.
set -euo pipefail
IMG="$1"; DEST="$2"
mkdir -p "$DEST"
for name in RESULT.md probes.json agent.log stdout.log stderr.log; do
  if debugfs -R "dump /$name $DEST/$name" "$IMG" >/dev/null 2>&1 && [ -s "$DEST/$name" ]; then
    echo "  extracted $name"
  fi
done
