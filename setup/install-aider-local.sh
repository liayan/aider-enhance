#!/usr/bin/env bash
# Install the aider-local and aider-test commands (pins aider 0.86.2).
# Uses uv if present, otherwise pipx. Needs Python 3.10-3.12.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-python3.12}"
if command -v uv >/dev/null; then
  exec uv tool install --python "$PY" "$ROOT"
elif command -v pipx >/dev/null; then
  exec pipx install --python "$PY" "$ROOT"
else
  echo "install uv (https://docs.astral.sh/uv/) or pipx first" >&2; exit 1
fi
