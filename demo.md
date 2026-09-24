# Demo run-through

About 12-15 minutes. Everything runs offline in emulate mode except the
optional real-model runs.

Thesis: isolation is what protects you from a coding agent, not the model's
behavior. Pick the boundary by who runs the agent and on whose code.

## Before the talk

- [ ] Use a disposable host or VM. No SSH keys, cloud credentials, browser
      profiles or real repos on it.
- [ ] `./src/preflight.sh` shows YES for every backend you'll run live.
- [ ] Give the baseline somewhere to put the canary and the outside-write
      target; without it two baseline probes show `blocked`:
      `sudo mkdir -p /demo/outside && sudo chown -R $USER /demo`
- [ ] `podman build -t agent-sandbox-demo:1 -f src/rootless-container/Containerfile .`
      (add `--build-arg WITH_AIDER=1` for the real-model run)
- [ ] Firecracker: kernel in `src/firecracker/assets/`, then
      `sudo src/firecracker/build-guest.sh` (and `sudo env WITH_AIDER=1 ...`
      for agent mode). Rebuild both after any change to `guest-init.sh`.
- [ ] Run every backend once and keep that `results/` as a fallback, plus a
      screen recording.
- [ ] `./src/compare-results.sh` output looks right.

## 1. The threat: agents act with your permissions (2 min)

```bash
./src/run.sh baseline
```

The repo contains a prompt injection (`docs/THIRD_PARTY_NOTES.md`). Running as
you, the workload reads the canary, sees host processes, reaches the sink,
writes outside the workspace and has no memory limit: every `!!` row.
`unapproved-command` only succeeds if the host has passwordless sudo; say so
if it shows `blocked`.

If you did a real-model run, show its `RESULT.md`: DeepSeek spotted the
injection and refused it. Say: *the model caught it this time; the boundary
is there so it doesn't have to.*

## 2. Measure what the sandbox blocks, not what the agent chooses (1 min)

No command; show `src/common/probe_runner.py` and `evaluate.py`.

- The probes attempt every escape on purpose, so results don't depend on
  whether a model gets fooled.
- The host decides the verdict from its own evidence: sink hits, the canary
  digest, files outside the workspace.
- The API key stays on the host; the sandbox reaches the model only through
  the gateway socket.

Point: if the sandbox grades itself, you've learned nothing.

## 3. The everyday developer: process sandbox (1.5 min)

```bash
./src/run.sh process-sandbox
```

bubblewrap hides home, other repos and the network; Landlock limits the
filesystem; a systemd user scope holds the cgroup limits. Every probe is
blocked and the task still passes, in under a second.

Point: the cheapest boundary removes most of the risk. Most people should
just use this.

## 4. Automation: --yes-always, CI, bots (2 min)

```bash
./src/run.sh rootless-container
```

Nobody reviews commands, and model-written tests run automatically. The
container adds cgroup limits, no capabilities, a read-only root and a fixed
image. Show `image_digest`, `caps=drop-all` and `network_mode=none` in
`metadata.json`.

Then the refusals:

- The launcher refuses `--privileged`, host networking and runtime socket
  mounts.
- It refuses cgroup v1, where rootless Podman silently drops the memory and
  pids limits.
- The process sandbox refuses to run if its scope doesn't get the limits;
  Firecracker refuses a rootfs whose `guest-init` is out of date.

Point: a boundary you can't verify isn't a boundary.

## 5. Running other people's agents: Firecracker (2 min)

```bash
./src/run.sh firecracker
```

Shared host, untrusted code, and a kernel exploit would reach every tenant.
A microVM gives each job its own kernel. Show `kernel_digest`, `vcpus`,
`mem_mib`, `workspace_transfer` and `backend-identity` (guest kernel, uid
1000).

The cost is setup, not speed: here it started faster than the container. It
needs KVM, a kernel, root to build the rootfs, and a rootfs per toolchain
(842 MB, 2 GB with aider).

Without KVM it prints `not-executed` with a reason. That's a skip, not a
pass.

Point: needed for a hosted service, overkill on a laptop.

## 6. The trade-off (1.5 min)

```bash
./src/compare-results.sh
```

Security table first, then cost, which is labeled by use case (developer /
automation / hosted service): time, peak RSS, disk, extra host tools.

Caveats to say out loud:

- In emulate mode, agent tokens are a fixed estimate, not a measurement.
- Peak RSS for the process sandbox includes the memory probe filling its
  1G cgroup; the container's number can't see inside the container.

Point: the right boundary depends on the threat.

## 7. A sandbox inside the agent isn't a boundary (1.5 min)

> TODO: two external incidents go here, with sources, before the talk:
> an agent that switched off its own confinement, and a sandbox that failed
> silently while the run still exited 0. Don't present them until verified.

Our own runs have examples of the same failure mode:

- Under the old `RLIMIT_AS` memory limit, aider couldn't start threads; the
  run just failed. The limit now lives in a cgroup and is checked before
  each run.
- The Firecracker `guest-init` is baked into the rootfs, so the image could
  quietly fall behind the repo. The launcher now compares them and refuses.

Point: the boundary has to sit outside the agent's control and fail closed.
Every backend here is external and aborts when it can't isolate.

## Closing (30s)

Assume the agent will be fooled, and build the sandbox so that it doesn't
matter.

## Optional: real model

```bash
export MODEL_API_KEY=sk-...
export MODEL_UPSTREAM=https://api.deepseek.com DEMO_MODEL=openai/deepseek-chat
./src/run.sh rootless-container --agent
```

Show `collected/gateway.log` (all model traffic went through the gateway, and
the key never entered the sandbox) and `trajectory.json`. With `--auto-test`,
in our DeepSeek runs the first tests failed and the model fixed them a turn
later.

## If something breaks

- Run `./src/compare-results.sh results/` on the saved results, or play the
  recording.
- Don't loosen a boundary on stage to get a green result.
