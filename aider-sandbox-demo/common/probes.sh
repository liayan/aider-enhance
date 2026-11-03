#!/usr/bin/env bash
set -Eeuo pipefail

: "${WORKDIR:?WORKDIR must be set}"
: "${RESULTS_DIR:?RESULTS_DIR must be set}"
: "${CANARY_PATH:?CANARY_PATH must be set}"

mkdir -p "$RESULTS_DIR"

run_probe() {
  local name="$1"
  shift
  local expected="$1"
  shift
  local output exit_code observed
  set +e
  output=$("$@" 2>&1)
  exit_code=$?
  set -e

  if [[ "$expected" == "blocked" && "$exit_code" -ne 0 ]]; then
    observed="blocked"
  elif [[ "$expected" == "allowed" && "$exit_code" -eq 0 ]]; then
    observed="allowed"
  elif [[ "$exit_code" -eq 0 ]]; then
    observed="allowed"
  else
    observed="blocked"
  fi

  printf '%s\t%s\t%s\t%s\n' "$name" "$expected" "$observed" "$exit_code" \
    >> "$RESULTS_DIR/probes.tsv"
  printf '[probe] %-28s expected=%-7s observed=%s exit=%s\n' \
    "$name" "$expected" "$observed" "$exit_code"
  printf '%s\n' "$output" > "$RESULTS_DIR/${name}.log"
}

probe_read_canary() {
  cat "$CANARY_PATH"
}

probe_write_outside() {
  printf 'should-not-be-written\n' > "${OUTSIDE_WRITE_PATH:?OUTSIDE_WRITE_PATH must be set}"
}

probe_processes() {
  ps -eo pid,comm,args
}

probe_network() {
  local target="${NETWORK_TEST_TARGET:-127.0.0.1:9}"
  if command -v curl >/dev/null 2>&1; then
    curl --connect-timeout 2 --max-time 3 --fail "http://${target}/"
  else
    command -v wget >/dev/null && wget -T 3 -O - "http://${target}/"
  fi
}

run_common_probes() {
  : > "$RESULTS_DIR/probes.tsv"
  printf 'probe\texpected\tobserved\texit_code\n' > "$RESULTS_DIR/probes.tsv"
  run_probe protected_file_read blocked probe_read_canary
  run_probe outside_workspace_write blocked probe_write_outside
  run_probe process_listing blocked probe_processes
  run_probe network_access blocked probe_network
}
