#!/usr/bin/env bash
# Show what each probe found in the latest run of a backend, one line each.
# Usage: ./src/show-probes.sh <backend> [--exposure]
#   --exposure   only the residual-exposure probes
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/common/lib.sh"
BACKEND="${1:?usage: show-probes.sh <backend> [--exposure]}"
python3 - "$RESULTS_DIR" "$BACKEND" "${2:-}" <<'PY'
import glob, json, os, shutil, sys
results, backend, flag = sys.argv[1:]
runs = []
for p in glob.glob(os.path.join(results, f"*-{backend}-*", "collected", "probes.json")):
    d = json.load(open(p))
    runs.append((d.get("captured_utc") or "", os.path.getmtime(p), p, d))
if not runs:
    sys.exit(f"no {backend} run under {results}")
data = max(runs)[3]
width = shutil.get_terminal_size((160, 20)).columns
for r in data.get("probes", []):
    if flag == "--exposure" and r.get("kind") != "exposure":
        continue
    line = f"{r['probe']:24s} {r.get('observed', '?'):10s} {r.get('evidence', '')}"
    print(line if len(line) <= width else line[:width - 1] + "…")
PY
