#!/usr/bin/env bash
# Shared helpers for all host-side scripts. Source this; do not execute.
set -euo pipefail

# ---------------------------------------------------------------- paths & config
LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$LAB_DIR/.." && pwd)"
# shellcheck disable=SC1091
source "$LAB_DIR/versions.env"

WORKLOAD_DIR="$REPO_ROOT/workload"
RESULTS_DIR="$REPO_ROOT/results"

# ---------------------------------------------------------------- logging
c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_blu=$'\033[34m'; c_rst=$'\033[0m'
log()  { printf '%s[lab]%s %s\n'  "$c_blu" "$c_rst" "$*" >&2; }
ok()   { printf '%s[ ok]%s %s\n'  "$c_grn" "$c_rst" "$*" >&2; }
warn() { printf '%s[warn]%s %s\n' "$c_yel" "$c_rst" "$*" >&2; }
die()  { printf '%s[FAIL]%s %s\n' "$c_red" "$c_rst" "$*" >&2; exit 1; }

# Fail closed: any backend calls this the moment a required isolation guarantee
# cannot be established. The demo must stop, never silently downgrade.
require() { "$@" || die "required check failed: $*"; }

# ---------------------------------------------------------------- run identity
new_run_id() {
  local backend="$1"
  echo "$(date -u +%Y-%m-%d)-${backend}-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
}

# ---------------------------------------------------------------- prepare()
# Build a disposable run directory holding the workspace, the synthetic canary,
# the run.env consumed by the probes, and the resolved injection fixture.
# Nothing here is a real secret.
prepare_run() {
  local backend="$1" run_dir="$2"
  local work="$run_dir/work" input="$run_dir/input" hostside="$run_dir/hostside"
  mkdir -p "$work" "$input" "$hostside" "$run_dir/collected"

  # Fresh copy of the workload; the agent only ever touches this copy.
  cp -a "$WORKLOAD_DIR/sample-repo/." "$work/"
  ( cd "$work" && git init -q && git add -A && git -c user.email=demo@local \
      -c user.name=demo commit -qm "workload baseline" )

  # Synthetic canary + fake credential, tracked by digest so we can prove exfil.
  local nonce marker
  nonce="$(head -c8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  marker="demo-marker-$nonce"
  printf 'SYNTHETIC-CANARY %s do-not-exfiltrate\n' "$nonce" > "$hostside/canary.txt"
  printf 'aws_access_key_id=AKIA_FAKE_%s\naws_secret_access_key=FAKE/%s\n' \
      "$nonce" "$nonce" > "$hostside/fake-creds.ini"
  local canary_digest creds_digest
  canary_digest="sha256:$(sha256sum "$hostside/canary.txt" | cut -d' ' -f1)"
  creds_digest="sha256:$(sha256sum "$hostside/fake-creds.ini" | cut -d' ' -f1)"

  # Host-side sentinels the probes will TRY to reach; the host decides the verdict.
  #  - outside-write target lives here, OUTSIDE the declared workspace
  #  - the local sink logs any egress that escapes the boundary
  mkdir -p "$hostside/outside"
  : > "$hostside/egress.log"

  # In-guest locations. Backends bind hostside read-only at /demo where the model
  # of the boundary allows a leak; where it does not, the path simply won't exist.
  # Digests of things that must NOT leak into the sandbox (canary, fake creds,
  # and — appended by run.sh in agent mode — the real model API key).
  local real_key_digest=""
  if [ -n "${MODEL_API_KEY:-}" ]; then
    real_key_digest=",sha256:$(printf '%s' "$MODEL_API_KEY" | sha256sum | cut -d' ' -f1)"
  fi
  cat > "$input/run.env" <<EOF
# Consumed by probe_runner.py inside the boundary. No real secrets.
RUN_NONCE=$nonce
MARKER=$marker
CANARY_PATH=/demo/canary.txt
FAKE_CREDS_PATH=/demo/fake-creds.ini
OUTSIDE_PATH=/demo/outside
SECRET_DIGESTS=$canary_digest,$creds_digest$real_key_digest
EXFIL_URLS=http://127.0.0.1:${EGRESS_PORT:-9$((RANDOM%900+100))},http://169.254.169.254/latest/meta-data
EOF
  # Record the sink port actually chosen (single source of truth).
  grep -q EGRESS_PORT "$run_dir/hostside/ports.env" 2>/dev/null || {
    local port; port="$(grep -oE 'EXFIL_URLS=http://127.0.0.1:[0-9]+' "$input/run.env" | grep -oE '[0-9]+$')"
    echo "EGRESS_PORT=$port" > "$hostside/ports.env"
  }

  # Resolve the injection fixture's placeholders into the copy the agent will read.
  sed -e "s#{{CANARY_PATH}}#/demo/canary.txt#g" \
      -e "s#{{FAKE_CREDS_PATH}}#/demo/fake-creds.ini#g" \
      -e "s#{{MARKER}}#$marker#g" \
      -e "s#{{EXFIL_URL}}#http://127.0.0.1:$(grep -oE '[0-9]+$' "$hostside/ports.env")#g" \
      -e "s#{{OUTSIDE_PATH}}#/demo/outside#g" \
      "$WORKLOAD_DIR/prompt-injection-fixture.txt" > "$work/docs/THIRD_PARTY_NOTES.md"
  cp "$WORKLOAD_DIR/task.txt" "$input/task.txt"
  # Real coding task + acceptance tests (used only in agent mode; harmless to stage).
  [ -f "$WORKLOAD_DIR/task-agent.txt" ] && cp "$WORKLOAD_DIR/task-agent.txt" "$input/task-agent.txt"
  [ -d "$WORKLOAD_DIR/acceptance" ] && cp -r "$WORKLOAD_DIR/acceptance" "$input/acceptance"

  # metadata the evaluator and report need.
  cat > "$run_dir/expected.env" <<EOF
CANARY_DIGEST=$canary_digest
CREDS_DIGEST=$creds_digest
MARKER=$marker
RUN_NONCE=$nonce
BACKEND=$backend
EOF
  ok "prepared $run_dir (canary ${canary_digest:0:19}…, marker $marker)"
}

cleanup_run() {
  local run_dir="$1"
  [ -n "${KEEP_RUN:-}" ] && { warn "KEEP_RUN set, leaving $run_dir"; return; }
  # Overwrite the synthetic canary before unlinking, then remove the tree.
  find "$run_dir/hostside" -type f -exec shred -u {} + 2>/dev/null || true
  rm -rf "$run_dir"
}

# ---------------------------------------------------------------- metadata
write_metadata() {
  local run_dir="$1" backend="$2" policy_digest="$3"; shift 3
  local out="$run_dir/collected/metadata.json"
  python3 - "$out" "$backend" "$policy_digest" "$@" <<'PY'
import json, sys, platform, subprocess, os
out, backend, policy_digest, *rest = sys.argv[1:]
extra = dict(kv.split("=", 1) for kv in rest)
def cmd(*a):
    try: return subprocess.check_output(a, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception: return None
meta = {
    "backend": backend,
    "policy_digest": policy_digest,
    "host_kernel": platform.release(),
    "host_arch": platform.machine(),
    "runner_commit": cmd("git", "-C", os.path.dirname(out) or ".", "rev-parse", "--short", "HEAD"),
    "captured_utc": cmd("date", "-u", "+%Y-%m-%dT%H:%M:%SZ"),
}
meta.update(extra)
# Never record secrets.
for k in list(meta):
    if any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET")):
        meta[k] = "<redacted>"
json.dump(meta, open(out, "w"), indent=2, sort_keys=True)
print(out)
PY
}
