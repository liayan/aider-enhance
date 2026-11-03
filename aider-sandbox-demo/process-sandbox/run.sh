#!/usr/bin/env bash
set -Eeuo pipefail

# Reference process sandbox. Test on a disposable VM before using it for a talk.
# Required: bubblewrap (bwrap), a working unprivileged user namespace, and
# optionally the project-specific Landlock helper.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUN_ID="${RUN_ID:-process-$(date +%s)}"
RUN_DIR="${ROOT}/runs/${RUN_ID}"
REPO="${REPO:-${ROOT}/workload}"
WORKDIR="${RUN_DIR}/work"
RESULTS_DIR="${RUN_DIR}/results"
CANARY_PATH="${RUN_DIR}/protected-canary.txt"
OUTSIDE_WRITE_PATH="${RUN_DIR}/outside-write-attempt.txt"

command -v bwrap >/dev/null 2>&1 || { echo 'ERROR: bwrap is required; refusing unsandboxed execution.' >&2; exit 70; }
[[ -d "$REPO" ]] || { echo "ERROR: missing repository: $REPO" >&2; exit 64; }

mkdir -p "$WORKDIR" "$RESULTS_DIR"
printf 'synthetic-secret-canary\n' > "$CANARY_PATH"
cp -a "$REPO/." "$WORKDIR/"

# Verify that bwrap can create an unprivileged user namespace. Do not continue
# if the kernel or local policy prevents the requested boundary.
bwrap --die-with-parent --unshare-user --uid 0 --gid 0 true \
  >/dev/null 2>"$RESULTS_DIR/bwrap-preflight.err" || {
  echo 'ERROR: unprivileged user namespaces unavailable; refusing fallback.' >&2
  exit 70
}

# This launcher creates a private PID namespace and a minimal filesystem view.
# The actual Landlock rules should be applied by landlock-exec inside the
# namespace, because the exact helper/library depends on the chosen project.
# If LANDLOCK_EXEC is set, it must exec the command and fail if enforcement
# cannot be installed.
LANDLOCK_EXEC="${LANDLOCK_EXEC:-}"

cat > "$RUN_DIR/inside.sh" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
cd /work
export HOME=/tmp/home
mkdir -p "$HOME"

if [[ -n "${LANDLOCK_EXEC:-}" ]]; then
  exec "$LANDLOCK_EXEC" --read-only / --read-write /work -- "$@"
fi

# Without a verified Landlock helper, bwrap remains the enforced boundary.
# The metadata records that Landlock was not active rather than pretending it was.
exec "$@"
EOF
chmod 700 "$RUN_DIR/inside.sh"

AIDER_CMD=("${AIDER_BIN:-aider}" "${AIDER_ARGS:-}")
# Prefer an explicit workload command for deterministic testing. Replace this
# with the pinned Aider invocation used by the conference repository.
WORKLOAD_CMD=(bash -lc "${WORKLOAD_COMMAND:-printf 'Aider workload placeholder\\n' > RESULT.md}")

# Run the workload inside bwrap. The host-side canary is deliberately not
# mounted. /run is private; only the disposable work directory is writable.
bwrap \
  --die-with-parent \
  --new-session \
  --unshare-user --unshare-pid --unshare-uts --unshare-ipc --unshare-net \
  --uid 1000 --gid 1000 \
  --ro-bind / / \
  --dev /dev \
  --proc /proc \
  --tmpfs /tmp \
  --bind "$WORKDIR" /work \
  --chdir /work \
  --setenv HOME /tmp/home \
  --setenv PATH /usr/bin:/bin \
  --setenv CANARY_PATH /protected-canary.txt \
  --setenv OUTSIDE_WRITE_PATH /host-outside-write.txt \
  --setenv WORKDIR /work \
  --setenv RESULTS_DIR /results \
  --ro-bind "$ROOT/common/probes.sh" /probes.sh \
  --bind "$RESULTS_DIR" /results \
  --ro-bind "$RUN_DIR/inside.sh" /inside.sh \
  -- bash /inside.sh "${WORKLOAD_CMD[@]}"

# Run probes in the same sandbox namespace. These are intentionally expected
# to be blocked; an actual implementation should collect JSON, not just TSV.
bwrap \
  --die-with-parent --new-session \
  --unshare-user --unshare-pid --unshare-uts --unshare-ipc --unshare-net \
  --uid 1000 --gid 1000 \
  --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
  --bind "$WORKDIR" /work --chdir /work \
  --setenv HOME /tmp/home \
  --setenv CANARY_PATH /protected-canary.txt \
  --setenv OUTSIDE_WRITE_PATH /host-outside-write.txt \
  --setenv WORKDIR /work --setenv RESULTS_DIR /results \
  --ro-bind "$ROOT/common/probes.sh" /probes.sh \
  --bind "$RESULTS_DIR" /results \
  -- bash -lc 'source /probes.sh; run_common_probes'

cat > "$RESULTS_DIR/metadata.txt" <<EOF
backend=process-bwrap
landlock=${LANDLOCK_EXEC:+configured}${LANDLOCK_EXEC:-not-configured}
network=unshared
workspace=$WORKDIR
EOF
printf 'Results: %s\n' "$RESULTS_DIR"
