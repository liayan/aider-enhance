#!/bin/sh
# Invoked by the existing demo rootfs's /sbin/guest-init, as uid 1000.
set -eu
cd /work
exec python3 - <<'PY'
import json
import subprocess
from pathlib import Path

command = json.loads(Path('/input/command.json').read_text())
result = subprocess.run(['/bin/sh', '-c', command], stdin=subprocess.DEVNULL)
Path('/work/.aider-test-exit').write_text(str(result.returncode))
PY
