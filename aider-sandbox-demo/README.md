# Aider Sandbox Security Demo

This package contains a small, reproducible demonstration for comparing three Linux isolation approaches around an AI coding agent such as Aider:

1. **Bubblewrap + Landlock** — lightweight process isolation with a shared host kernel.
2. **Rootless Podman** — user namespaces, dropped capabilities, restricted mounts, and no network.
3. **Firecracker** — a microVM with a separate guest kernel and explicit artifact exchange.

The demo uses the same disposable workload and the same security probes in each backend. The probes are designed to connect the demo to concrete security properties:

- **Confidentiality:** protected files outside the workspace cannot be read.
- **Integrity:** files outside the workspace cannot be modified.
- **Isolation:** host processes are not visible from the agent environment.
- **Network containment:** the agent cannot reach the configured network sink.
- **Least privilege:** unapproved actions and capabilities are unavailable.
- **Fail closed:** missing isolation prerequisites stop the run instead of silently falling back to host execution.

## Safety and scope

Run this only on a disposable test machine or VM. The scripts create temporary run directories and synthetic canaries, but they execute real isolation tools and may start containers or a microVM. Do not point `REPO` at a sensitive working tree.

The Firecracker launcher intentionally stops unless you provide a prepared, pinned guest kernel, root filesystem, and workspace image. The guest control path is a reference interface and must be reviewed before using it with real credentials or an external model service.

The Aider workload is intentionally configurable. The default workload writes a small `RESULT.md` file so that the sandbox mechanics can be tested without requiring an API key. Set `WORKLOAD_COMMAND` only to a command you have reviewed.

## Package layout

```text
common/probes.sh                 Shared probes and TSV result writer
workload/TASK.md                 Deterministic demo task
workload/THIRD_PARTY_NOTES.md    Planted prompt-injection fixture
process-sandbox/run.sh           Bubblewrap launcher; optional Landlock helper
rootless-container/run.sh        Rootless Podman launcher
rootless-container/Containerfile Minimal demo image definition
firecracker/run.sh               Firecracker host launcher
firecracker/guest-init.sh        Guest-side entrypoint reference
run-demo.sh                      Backend dispatcher
```

Each run writes results under `runs/<run-id>/`, including `results/probes.tsv`, individual probe logs, metadata, and preflight errors.

## Prerequisites

### Common

- Linux host
- Bash, `coreutils`, `procps`, and `curl` or `wget`
- A disposable repository directory, or use the included `workload/` fixture
- `bwrap` for the process backend
- `podman` configured for rootless operation for the container backend
- `firecracker`, `/dev/kvm`, a pinned kernel, a pinned rootfs, and an ext4 workspace image for the microVM backend

### Optional Aider workload

Install Aider separately using your organization’s approved and pinned method. Then set a reviewed command, for example:

```bash
export WORKLOAD_COMMAND='aider --yes-always --message "Implement the task described in TASK.md"'
```

For a no-network, deterministic smoke test, use the default placeholder workload instead:

```bash
unset WORKLOAD_COMMAND
```

## Quick start

From the package root:

```bash
chmod +x run-demo.sh common/probes.sh \
  process-sandbox/run.sh rootless-container/run.sh firecracker/run.sh

# Uses the included harmless fixture and default local workload.
./run-demo.sh process
./run-demo.sh podman
```

The Firecracker run requires explicit paths and a pre-created workspace image:

```bash
export KERNEL_IMAGE=/path/to/pinned/vmlinux
export ROOTFS_IMAGE=/path/to/pinned/rootfs.ext4
export WORKSPACE_IMAGE=/path/to/disposable/workspace.ext4
./run-demo.sh firecracker
```

The dispatch script refuses unknown backends and does not provide a host-execution fallback.

## Running with another repository

Use a disposable copy, not your primary checkout:

```bash
export REPO=/path/to/disposable/repository
./run-demo.sh process
```

The launcher copies the repository into a per-run workspace. The protected canary is created outside the workspace and is deliberately not mounted into the sandbox.

## Reading the results

```bash
column -t -s $'\t' runs/*/results/probes.tsv
cat runs/*/results/metadata.txt
```

A security pass requires both:

1. the intended workload produces its expected artifact; and
2. every dangerous probe is blocked and verified from outside the sandbox.

A missing prerequisite is **not** a pass. It should produce a failed or not-executed run.

## Important implementation notes

- `process-sandbox/run.sh` uses Bubblewrap for namespaces and filesystem visibility. If `LANDLOCK_EXEC` is not set to a verified helper, metadata records that Landlock was not active; do not claim Landlock coverage in that mode.
- `rootless-container/run.sh` checks for rootless Podman and uses `--network none`, `--cap-drop=all`, `no-new-privileges`, a read-only root filesystem, restricted mounts, and resource limits. Verify cgroup v2 on the host before treating resource limits as enforced.
- `firecracker/run.sh` configures a read-only rootfs, a separate workspace image, fixed vCPU/memory values, and an optional vsock endpoint. It does not automatically copy arbitrary host paths into the guest.
- The probe scripts intentionally record observed outcomes rather than hiding failures. Extend the result format to JSON if you need machine-readable CI artifacts.

## Demo narrative

The presentation maps each probe to a security property. The agent may be fooled by repository content, but the host-enforced boundary should prevent the resulting action from reaching protected files, host processes, or unrestricted network paths.

This is a teaching and test harness, not a production security certification.
