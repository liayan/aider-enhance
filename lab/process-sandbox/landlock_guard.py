#!/usr/bin/env python3
"""Apply Landlock filesystem rules, then exec the workload. Fails closed.

Landlock is a kernel LSM that lets an unprivileged process drop its own
filesystem authority. We use it as defence-in-depth *behind* bwrap's mounts:
even if a bind mount is looser than intended, Landlock restricts the process to
an allow-list of paths.

Modes:
  --abi            print the supported Landlock ABI version (0 if unavailable) and exit
  --enforce -- CMD apply the ruleset, then execvp CMD

Allow-list (read-write): /work, /tmp
Allow-list (read-only) : /usr /bin /lib /lib64 /etc /input /lab /proc /dev
Everything else (notably /demo/*, host home, /root) becomes inaccessible.

If the kernel supports Landlock but rule installation fails, this exits nonzero
so the launcher aborts rather than running under-restricted. If the kernel has no
Landlock at all, it warns and continues (bwrap mounts remain the boundary).
"""
import ctypes
import ctypes.util
import os
import sys

LANDLOCK_CREATE_RULESET = 444
LANDLOCK_ADD_RULE = 445
LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_RULE_PATH_BENEATH = 1

# Access bits (ABI 1..). We request a broad set and mask to what the ABI supports.
ACCESS_FS = {
    "execute": 1 << 0, "write_file": 1 << 1, "read_file": 1 << 2,
    "read_dir": 1 << 3, "remove_dir": 1 << 4, "remove_file": 1 << 5,
    "make_char": 1 << 6, "make_dir": 1 << 7, "make_reg": 1 << 8,
    "make_sock": 1 << 9, "make_fifo": 1 << 10, "make_block": 1 << 11,
    "make_sym": 1 << 12, "refer": 1 << 13, "truncate": 1 << 14,
}
ALL = 0
for _v in ACCESS_FS.values():
    ALL |= _v
READ_ONLY = ACCESS_FS["execute"] | ACCESS_FS["read_file"] | ACCESS_FS["read_dir"]

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def abi():
    # create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION=1) -> ABI version
    v = libc.syscall(LANDLOCK_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    return v if v > 0 else 0


def cap_for_abi(a):
    """Mask access bits to those valid for the running ABI."""
    handled = ALL
    if a < 2:
        handled &= ~ACCESS_FS["refer"]
    if a < 3:
        handled &= ~ACCESS_FS["truncate"]
    # ABI 4+ adds net rules; we don't use them here.
    return handled


def enforce():
    a = abi()
    if a == 0:
        sys.stderr.write("[landlock] not supported by kernel; continuing on bwrap mounts\n")
        return
    handled = cap_for_abi(a)
    attr = RulesetAttr(handled_access_fs=handled, handled_access_net=0)
    rs = libc.syscall(LANDLOCK_CREATE_RULESET, ctypes.byref(attr),
                      ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0))
    if rs < 0:
        sys.exit(f"[landlock] create_ruleset failed: {os.strerror(ctypes.get_errno())}")

    ro = READ_ONLY & handled
    rw = handled  # full set for writable dirs
    rules = [("/usr", ro), ("/bin", ro), ("/lib", ro), ("/lib64", ro),
             ("/etc", ro), ("/input", ro), ("/lab", ro), ("/dev", ro),
             ("/proc", ro), ("/tmp", rw), ("/work", rw)]
    for path, access in rules:
        if not os.path.exists(path):
            continue
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        pb = PathBeneathAttr(allowed_access=access & handled, parent_fd=fd)
        r = libc.syscall(LANDLOCK_ADD_RULE, ctypes.c_int(rs),
                         ctypes.c_uint(LANDLOCK_RULE_PATH_BENEATH),
                         ctypes.byref(pb), ctypes.c_uint32(0))
        os.close(fd)
        if r < 0:
            sys.exit(f"[landlock] add_rule {path} failed: {os.strerror(ctypes.get_errno())}")

    # PR_SET_NO_NEW_PRIVS is required before restrict_self.
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS=38
        sys.exit("[landlock] prctl(NO_NEW_PRIVS) failed")
    if libc.syscall(LANDLOCK_RESTRICT_SELF, ctypes.c_int(rs), ctypes.c_uint32(0)) != 0:
        sys.exit(f"[landlock] restrict_self failed: {os.strerror(ctypes.get_errno())}")
    sys.stderr.write(f"[landlock] enforced (ABI {a})\n")


def apply_rlimits():
    """Enforce a resource budget with setrlimit, so the process sandbox has real
    memory/PID/CPU caps comparable to the container's cgroup limits. Values come
    from the environment (set by the launcher from versions.env)."""
    import resource as R

    def parse_bytes(s):
        s = s.strip().upper()
        mult = {"K": 1024, "M": 1024**2, "G": 1024**3}
        return int(float(s[:-1]) * mult[s[-1]]) if s and s[-1] in mult else int(s)

    applied = []
    mem = os.environ.get("LIMIT_MEM")
    if mem:
        b = parse_bytes(mem)
        R.setrlimit(R.RLIMIT_AS, (b, b))
        R.setrlimit(R.RLIMIT_DATA, (b, b))
        applied.append(f"AS={mem}")
    pids = os.environ.get("LIMIT_PIDS")
    if pids:
        R.setrlimit(R.RLIMIT_NPROC, (int(pids), int(pids)))
        applied.append(f"NPROC={pids}")
    cpu = os.environ.get("LIMIT_CPU_SEC")
    if cpu:
        R.setrlimit(R.RLIMIT_CPU, (int(cpu), int(cpu)))
        applied.append(f"CPU={cpu}s")
    R.setrlimit(R.RLIMIT_CORE, (0, 0))  # no core dumps
    if applied:
        sys.stderr.write("[rlimit] enforced " + ", ".join(applied) + "\n")


def main():
    args = sys.argv[1:]
    if args and args[0] == "--abi":
        print(abi())
        return
    if args and args[0] == "--enforce":
        rest = args[1:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        apply_rlimits()
        enforce()
        if not rest:
            sys.exit("[landlock] nothing to exec")
        os.execvp(rest[0], rest)
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
