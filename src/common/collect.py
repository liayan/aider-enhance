#!/usr/bin/env python3
"""Copy allow-listed outputs out of the workspace.

The workload controls /work, so it could plant symlinks or huge files. Only
listed names are copied; symlinks, non-regular files, files over the size cap
and paths resolving outside the workspace are rejected.

Usage: collect.py <work_dir> <dest_dir>
"""
import os
import shutil
import sys

# name -> max size in bytes
ALLOW = {
    "RESULT.md": 256 * 1024,
    "probes.json": 4 * 1024 * 1024,
    "agent.log": 8 * 1024 * 1024,
    "acceptance.log": 2 * 1024 * 1024,
    ".acceptance_rc": 16,
    "stdout.log": 8 * 1024 * 1024,
    "stderr.log": 8 * 1024 * 1024,
    # agent mode
    ".aider.chat.history.md": 8 * 1024 * 1024,
    ".aider.llm.history": 16 * 1024 * 1024,
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
