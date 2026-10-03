#!/usr/bin/env python3
"""Run one phase of the workload and record its memory use.

Usage: phase_mem.py <out.json> -- <command> [args...]

Writes peak RSS of the phase's largest child process (getrusage) and, where
the file is readable, the cgroup's memory.peak. Exits with the command's code.
"""
import json
import resource
import subprocess
import sys
import time


def read_mib(path):
    try:
        with open(path) as fh:
            return round(int(fh.read().strip()) / 1048576, 1)
    except (OSError, ValueError):
        return None


def main():
    if len(sys.argv) < 4 or sys.argv[2] != "--":
        sys.exit("usage: phase_mem.py <out.json> -- <command> [args...]")
    out, cmd = sys.argv[1], sys.argv[3:]
    t0 = time.monotonic()
    rc = subprocess.call(cmd)
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    rec = {
        "max_rss_mib": round(ru.ru_maxrss / 1024, 1),  # Linux reports KiB
        "cgroup_peak_mib": read_mib("/sys/fs/cgroup/memory.peak"),
        "wall_ms": int((time.monotonic() - t0) * 1000),
        "rc": rc,
    }
    with open(out, "w") as fh:
        json.dump(rec, fh)
    sys.exit(rc)


if __name__ == "__main__":
    main()
