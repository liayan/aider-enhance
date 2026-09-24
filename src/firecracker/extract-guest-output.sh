#!/usr/bin/env bash
# Copy allow-listed outputs off the work image after the guest halts.
# debugfs, so no mount or root needed.
set -euo pipefail
IMG="$1"; DEST="$2"
mkdir -p "$DEST"
for name in RESULT.md probes.json agent.log stdout.log stderr.log; do
  if debugfs -R "dump /$name $DEST/$name" "$IMG" >/dev/null 2>&1 && [ -s "$DEST/$name" ]; then
    echo "  extracted $name"
  fi
done
