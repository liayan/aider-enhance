#!/usr/bin/env bash
# Copy allow-listed outputs off the work image after the guest halts.
# debugfs, so no mount or root needed.
set -euo pipefail
IMG="$1"; DEST="$2"
mkdir -p "$DEST"
for name in RESULT.md probes.json agent.log stdout.log stderr.log acceptance.log \
            .acceptance_rc .aider.chat.history.md .aider.llm.history; do
  rm -f "$DEST/$name"
  if debugfs -R "dump /$name $DEST/$name" "$IMG" >/dev/null 2>&1 && [ -s "$DEST/$name" ]; then
    echo "  extracted $name"
  fi
done
