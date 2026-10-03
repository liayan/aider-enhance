# Sandbox security + footprint benchmark

Turns the single probe run into a repeated, aggregated benchmark that answers,
per backend, three questions:

1. **Security stability.** Across N repeats, every probe must match its expected
   verdict on every run. One deviation makes the backend *not* secure-stable.
   Flaky isolation is a failure, not noise.
2. **Memory footprint.** Peak RSS and peak process count per run, reported as
   median and spread across repeats, plus phase memory where the cgroup exposes
   it (`src/common/phase_mem.py`).
3. **Boundary condition.** `--mem-sweep` re-runs a backend at several
   `LIMIT_MEM` values and checks the `resource-limit` probe stays enforced at
   each one, so a limit isn't passing by luck at a single setting.

It drives the existing `src/run.sh` (no reimplementation), one result directory
per run under a private root, and reads each run's `collected/probes.json` and
`footprint.json`. No model calls by default (emulate mode); `--agent-fake`
exercises the real agent path with the offline fake model.

## Run

```bash
# provision backends first (see ../../runbook.md); process-sandbox needs a
# systemd user scope for its cgroup limits, so it won't run where that's absent.
python benchmarks/sandbox_security/benchmark.py \
  --backends process-sandbox rootless-container firecracker baseline \
  --repeats 5 --warmups 1 --seed 0 \
  --mem-sweep 256M,512M,1G \
  --kernel src/firecracker/assets/vmlinux \
  --rootfs src/firecracker/assets/rootfs.ext4 \
  --output results/sec-footprint
```

Subset with `--backends`. `baseline` is the unsandboxed reference and is
excluded from the memory sweep (it enforces no limit by design).

## Output

- `report.md` — the security-stability + footprint table and the sweep table.
- `runs.jsonl` — every run (warmups, main, sweep) with verdicts, observed
  values, footprint and a log path.
- `metadata.json` — settings and SHA-256 of `run.sh` and `probe_runner.py`.

## Exit code

Nonzero if a requested backend is unavailable, a warmup fails, any measured run
is not secure-stable, or any sweep point fails to enforce the limit. Suitable
for CI.

## What it does and doesn't show

It shows whether each backend is configured to enforce the declared contract,
repeatably, and at what memory cost. It does **not** show resistance to an agent
hunting zero-days in the runtime or kernel; that is the operations layer
(patching, ephemeral environments, monitoring). See
[Trail of Bits, 26 Aug 2026](https://blog.trailofbits.com/2026/08/26/vms-wont-contain-cyber-capable-agents/).
