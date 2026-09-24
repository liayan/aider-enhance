# Demo run-through

About 8-10 minutes. Everything runs offline in emulate mode; `--agent` is
optional.

## Before the talk

- [ ] Use a disposable host or VM. No SSH keys, cloud credentials, browser
      profiles or real repos on it.
- [ ] `./src/preflight.sh` shows YES for every backend you'll run live.
- [ ] `podman build -t agent-sandbox-demo:1 -f src/rootless-container/Containerfile .`
- [ ] Firecracker assets built, digests pinned in `versions.env`.
- [ ] Run every backend once and keep that `results/` as a fallback, plus a
      screen recording.
- [ ] `./src/compare-results.sh` output looks right.

## 1. Intro (30s)

Same agent, same task, same attacks; only the isolation changes.

## 2. Baseline (90s)

```bash
./src/run.sh baseline
```

Every isolation probe shows `!!`: it reads the canary, sees host processes,
reaches the sink, writes outside the workspace, runs privileged commands and
has no memory limit. `approved-artifact` still passes.

## 3. Process sandbox (90s)

```bash
./src/run.sh process-sandbox
```

All isolation probes are blocked and the task still passes. Point out the
`[landlock] enforced` line and the cgroup limits in `metadata.json`
(`memory_max`, `pids_max`). Shared kernel, cheapest
option.

## 4. Rootless container (90s)

```bash
./src/run.sh rootless-container
```

Same results via different mechanisms: read-only root, no capabilities, cgroup
limits. Show `image_digest`, `caps=drop-all` and `network_mode=none` in
`metadata.json`. The launcher refuses `--privileged`, host networking and
runtime socket mounts.

## 5. Firecracker (2-3 min)

```bash
./src/run.sh firecracker
```

Same results, with a separate guest kernel. Show `kernel_digest`, `vcpus`,
`mem_mib` and `workspace_transfer`, and the boot time.

Without KVM it prints `not-executed` with a reason. Point out that it's a skip,
not a pass, and show the recorded run.

## 6. Comparison (90s)

```bash
./src/compare-results.sh
```

Security table first, then cost: time, peak RSS, process count, disk, extra
host tools, agent tokens. The stronger boundaries cost more in boot time and
disk, and a tighter sandbox means a confused agent spends more tokens on
blocked operations.

## Optional: real model

```bash
export MODEL_API_KEY=sk-...
./src/run.sh process-sandbox --agent
```

Show `collected/gateway.log`: the key stayed on the host and all model traffic
went through the gateway. If the model followed the injection, the probes and
canary show the attempt and that it was contained.

## If something breaks

- Run `./src/compare-results.sh results/` on the saved results, or play the
  recording.
- Don't loosen a boundary on stage to get a green result.
