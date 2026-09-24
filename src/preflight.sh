#!/usr/bin/env bash
# Report which backends this host can run. Read-only; always exits 0.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/common/lib.sh"

# lib.sh turns on set -e; a failed check here shouldn't abort.
set +e
check() { # <label> <cmd...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '  %-40s %sYES%s\n' "$label" "$c_grn" "$c_rst"
  else printf '  %-40s %sno%s\n'  "$label" "$c_yel" "$c_rst"; fi
  return 0
}

echo "== host =="
echo "  kernel: $(uname -r)   arch: $(uname -m)"
echo
echo "== common =="
check "python3 >= ${PYTHON_MIN}" python3 -c "import sys;exit(0 if sys.version_info[:2]>=tuple(map(int,'${PYTHON_MIN}'.split('.'))) else 1)"
check "git" command -v git

echo
echo "== process-sandbox (bwrap + landlock) =="
check "bwrap present" command -v bwrap
check "user namespaces work" bash -c 'bwrap --unshare-user --ro-bind /usr /usr $( [ -d /lib64 ] && echo --ro-bind /lib64 /lib64 ) --ro-bind /lib /lib --ro-bind /bin /bin -- /bin/true'
LL=$(python3 "$HERE/process-sandbox/landlock_guard.py" --abi 2>/dev/null || echo 0)
if [ "${LL:-0}" -ge 1 ]; then printf '  %-40s %sABI %s%s\n' "landlock" "$c_grn" "$LL" "$c_rst"
else printf '  %-40s %sno%s\n' "landlock" "$c_yel" "$c_rst"; fi

echo
echo "== rootless-container (podman) =="
check "podman present" command -v podman
check "podman rootless" bash -c '[ "$(podman info --format "{{.Host.Security.Rootless}}" 2>/dev/null)" = true ]'
check "cgroup v2 (limits enforced)" bash -c '[ "$(podman info --format "{{.Host.CgroupsVersion}}" 2>/dev/null)" = v2 ]'
check "demo image built" bash -c "podman image exists ${CONTAINER_IMAGE} 2>/dev/null"

echo
echo "== firecracker (microVM) =="
check "/dev/kvm" test -e /dev/kvm
check "firecracker binary" command -v firecracker
check "guest kernel" test -f "$HERE/firecracker/assets/vmlinux"
check "guest rootfs" test -f "$HERE/firecracker/assets/rootfs.ext4"

echo
echo "== agent mode (optional) =="
check "aider present" command -v aider
[ -n "${MODEL_API_KEY:-}" ] && printf '  %-40s %sset%s\n' "MODEL_API_KEY" "$c_grn" "$c_rst" \
  || printf '  %-40s %sunset (emulate mode still works)%s\n' "MODEL_API_KEY" "$c_yel" "$c_rst"

echo
ok "preflight complete. Backends marked 'no' will fail closed or record not-executed."
