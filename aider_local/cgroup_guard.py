"""Check the actual scope limits before starting bubblewrap and any project code.

Invoked with /usr/bin/python3 -I; deliberately uses only the standard library.
"""

import os
from pathlib import Path
import sys


def verify_limits(cgroup_file=Path('/proc/self/cgroup'), root=Path('/sys/fs/cgroup')):
    entries = [line[3:] for line in cgroup_file.read_text().splitlines() if line.startswith('0::')]
    if len(entries) != 1 or '..' in Path(entries[0]).parts:
        raise ValueError('a cgroup v2 scope is required')
    scope = root / entries[0].lstrip('/')
    for name, expected in (('memory.max', 1024**3), ('memory.swap.max', 0), ('pids.max', 128)):
        value = (scope / name).read_text().strip()
        if value == 'max' or not 0 <= int(value) <= expected:
            raise ValueError(f'{name} does not enforce the requested limit')
    quota, period = (scope / 'cpu.max').read_text().split()
    if quota == 'max' or not 0 < int(quota) <= int(period):
        raise ValueError('cpu.max does not enforce the requested limit')


def main(argv=None):
    command = sys.argv[1:] if argv is None else argv
    try:
        verify_limits()
        if not command:
            raise ValueError('missing sandbox command')
        os.execvp(command[0], command)
    except (OSError, ValueError) as exc:
        print(f'Process sandbox setup failed: {exc}', file=sys.stderr)
        return 125


if __name__ == '__main__':
    sys.exit(main())
