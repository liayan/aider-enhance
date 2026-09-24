#!/usr/bin/env python3
"""Copy declared outputs out of a finished run, safely.

Collection is an attack surface: a compromised workload can plant symlinks,
huge files, or extra paths to smuggle data past the boundary at copy time.
This collector therefore:
  - copies ONLY an explicit allow-list of names,
  - refuses symlinks and anything that is not a regular file,
  - caps each file's size,
  - never follows a path outside the source workspace.

Usage: collect.py <work_dir> <dest_dir>
"""
import os
import shutil
import sys

# Declared outputs only. Anything else the workload wrote stays behind.
ALLOW = {
    "RESULT.md": 256 * 1024,
    "probes.json": 4 * 1024 * 1024,
    "agent.log": 8 * 1024 * 1024,
    "stdout.log": 8 * 1024 * 1024,
    "stderr.log": 8 * 1024 * 1024,
}


def safe_copy(work_dir, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    work_real = os.path.realpath(work_dir)
    report = []
    for name, cap in ALLOW.items():
        src = os.path.join(work_dir, name)
        try:
            st = os.lstat(src)
        except FileNotFoundError:
            report.append((name, "absent"))
            continue
        if os.path.islink(src):
            report.append((name, "REJECTED: symlink"))
            continue
        if not os.path.isfile(src) or not stat_is_reg(st):
            report.append((name, "REJECTED: not a regular file"))
            continue
        # Realpath must still live inside the workspace.
        if not os.path.realpath(src).startswith(work_real + os.sep):
            report.append((name, "REJECTED: escapes workspace"))
            continue
        if st.st_size > cap:
            report.append((name, f"REJECTED: {st.st_size}B > cap {cap}B"))
            continue
        shutil.copyfile(src, os.path.join(dest_dir, name), follow_symlinks=False)
        report.append((name, f"copied {st.st_size}B"))
    return report


def stat_is_reg(st):
    import stat as _s
    return _s.S_ISREG(st.st_mode)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: collect.py <work_dir> <dest_dir>")
    for name, status in safe_copy(sys.argv[1], sys.argv[2]):
        print(f"  {name:14s} {status}")
