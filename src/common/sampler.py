#!/usr/bin/env python3
"""Sample RSS and process count for the backend's process tree.

Usage: sampler.py <interval_sec> <out_json> <root_pid>

On SIGTERM/SIGINT writes the peaks and the process names seen. Only the tree
under <root_pid> counts, so the sink and gateway are left out.
"""
import json
import os
import signal
import sys
import time

PAGE = os.sysconf("SC_PAGE_SIZE")


def ppid_of(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read()
        # comm may contain spaces/parens; split after the last ')'
        rest = data[data.rindex(b")") + 2:].split()
        return int(rest[1])  # ppid is field 4 -> index 1 after state
    except (OSError, ValueError, IndexError):
        return None


def comm_of(pid):
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip()
    except OSError:
        return None


def rss_bytes(pid):
    try:
        with open(f"/proc/{pid}/statm") as fh:
            return int(fh.read().split()[1]) * PAGE  # resident pages
    except (OSError, ValueError, IndexError):
        return 0


def descendants(root):
    """All live pids in the tree rooted at `root` (inclusive)."""
    all_pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    parent = {p: ppid_of(p) for p in all_pids}
    tree = set()
    for p in all_pids:
        cur, seen = p, 0
        while cur and cur not in (0, 1) and seen < 64:
            if cur == root:
                tree.add(p)
                break
            cur = parent.get(cur)
            seen += 1
    tree.add(root)
    return tree


def main():
    interval, out, root = float(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    peak = {"peak_proc_count": 0, "peak_rss_bytes": 0, "samples": 0}
    comms = {}
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))

    while not stop["v"]:
        pids = descendants(root)
        total_rss = 0
        live = 0
        for p in pids:
            if not os.path.exists(f"/proc/{p}"):
                continue
            live += 1
            total_rss += rss_bytes(p)
            c = comm_of(p)
            if c:
                comms[c] = comms.get(c, 0) + 1
        peak["peak_proc_count"] = max(peak["peak_proc_count"], live)
        peak["peak_rss_bytes"] = max(peak["peak_rss_bytes"], total_rss)
        peak["samples"] += 1
        time.sleep(interval)

    peak["peak_rss_mb"] = round(peak["peak_rss_bytes"] / 1024 / 1024, 1)
    peak["host_helpers"] = sorted(k for k in comms
                                  if k not in ("sampler.py", "python3", "bash", "sh", "sleep", "sleep"))
    peak["all_comms"] = comms
    json.dump(peak, open(out, "w"), indent=2)


if __name__ == "__main__":
    main()
