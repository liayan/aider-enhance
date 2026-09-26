# Local-editing benchmarks

The harness also supports the [HTTP-client refactoring workload](../http_client_refactor/README.md).
Pass `--workload http-client-refactor` to `prepare`, `generate` or `run`.
The default workload remains `cli-refactor`.

This workload applies the useful idea from the supplied CLI-refactoring article:
move five commands out of one Python file, preserve behavior, and measure the
work with a fixed prompt and starting repository. All fixture code here is new;
the article's token counts and provider claims are not benchmark inputs.

There are two independent measurements:

- `generate` runs **aider-local**, which edits a fresh local checkout and tests in
  a pinned sandbox. It records real API response usage and total generation time.
- `run` freezes a completed candidate and repeatedly runs the same acceptance
  checks in process-sandbox, Podman (`container`), and Firecracker (`microvm`).
  This makes **no model calls**. Without `--candidate`, it uses the checked-in
  reference refactor; this is explicitly labeled `offline-reference`.

Run these commands from this repository with the Python interpreter used to
install the project. Replay needs only Python 3.10+ and backend dependencies;
generation also needs the pinned aider dependency. Output directories must be
new. Files under `results/` are Git-ignored.

## Offline sandbox comparison

First provision the backends using the [runbook](../../runbook.md). Then:

```bash
python benchmarks/cli_refactor/benchmark.py run \
  --kernel src/firecracker/assets/vmlinux \
  --rootfs src/firecracker/assets/rootfs.ext4 \
  --repeats 3 --warmups 1 --seed 0 \
  --output results/cli-refactor-reference
```

Use `--backends process-sandbox container` to select a subset. There is no fallback:
an unavailable requested backend is recorded as an error, and the command returns
nonzero. Warmup failures also return nonzero. Each backend runs with the limits
provided by `aider-local`; tests have no network access. Container/guest Python
versions can differ from the host and are recorded, not assumed identical.

`report.md` displays passing counts, failures, errors and median times.
`runs.jsonl` retains every measurement (including warmups), with per-run logs.
`metadata.json` records source hashes, prompt/checker/harness/runner hashes,
project commit and dirty status, settings, image ID, and kernel/rootfs SHA-256s.
No credentials or source text are included in metadata. `file_scope` records
changes anywhere in the candidate, including Git-ignored and untracked paths.
Changes outside the workload allowance fail the run before snapshot exclusions
can hide them. Known Git/Aider bookkeeping and Python bytecode caches are exempt. Image/asset hashing
happens outside measured intervals; it can warm the filesystem cache.

Wall time includes readiness checks, snapshot creation, setup/boot, tests, output
collection, and cleanup. Checker time includes running **both** the baseline and
candidate CLI for each case. It is not pure candidate execution time. Run order
is shuffled with a recorded seed. Summaries exclude warmups and failed runs,
while preserving their counts. Results are warm-cache observations on this host,
not proof that one backend is universally faster or safer.

## Generate a candidate with Aider

This command intentionally makes model requests. Set `OPENAI_API_KEY` in your
shell or secret manager. Supply the provider's exact documented API base and
model ID; `/v1` is needed only when the provider's endpoint requires it. The
recorder supports non-streaming OpenAI-compatible chat completions.

```bash
python benchmarks/cli_refactor/benchmark.py generate \
  --model openai/YOUR_MODEL_ID \
  --api-base https://YOUR_PROVIDER/v1 \
  --backend container --map-tokens 1024 \
  --output results/cli-refactor-model-01

python benchmarks/cli_refactor/benchmark.py run \
  --candidate results/cli-refactor-model-01/work \
  --kernel src/firecracker/assets/vmlinux \
  --rootfs src/firecracker/assets/rootfs.ext4 \
  --output results/cli-refactor-model-01-replay
```

Generation uses a fresh Git baseline and the checked-in `prompt.txt`, with
`--no-auto-commits`, `--no-gitignore`, `--no-dirty-commits`,
`--no-auto-lint`, `--no-stream`,
`--test-cmd 'python3 _bench/check.py'` and `--auto-test`. Config files, dotenv
loading and inherited `AIDER_*` settings are disabled. Main and weak models are
pinned to the same explicit model. The edit format defaults to `diff`; `whole`
is also available. The full argv and configuration are recorded. The repository
map budget is a requested budget, not a measured token count for map content.

`generation.json` records the starting commit, changed project files, file-scope
result, candidate
hash, exit code, total generation duration and usage summary. `requests.jsonl`
records each forwarded attempt's model, request hash, status, elapsed time, and
unmodified usage object (including cached/reasoning details if supplied). It does
not store prompts, responses or keys. Failed attempts remain in the trace;
missing usage yields `null` totals, not zero. Repeated identical request hashes
are evidence of repeated requests, not a reliable retry count. No dollar costs
or repo-map token attribution are invented. Aider's own `aider.log` and histories
can contain source and model output.

The loopback usage proxy is a local benchmark helper, not an authentication
boundary against other users on a shared host. It forwards the key only to the
configured provider. Request time includes network/provider response time;
generation duration also includes Aider, local editing, automatic tests and
cleanup. These clocks must not be compared as if both measured sandbox startup.
No successful Aider exit is treated as acceptance: run the frozen replay and
compare its `candidate_sha256` with `generation.json` before associating costs.

For a model or map-budget comparison, repeat `generate` into a fresh directory
for every sample, keep the prompt/baseline/edit format/test backend fixed, and
change only the intended variable. Use multiple samples and retain failures.
A read-only question and a refactoring request are different tasks and should
not be used to claim token savings. If checking provider connectivity first,
record that preflight separately rather than counting it as workload usage.

For manual editing without any model request:

```bash
python benchmarks/cli_refactor/benchmark.py prepare \
  --output results/cli-refactor-manual
# Edit results/cli-refactor-manual/work/mycli, then replay that candidate.
```

## Acceptance contract

The CLI workload’s 26 cases compare stdout, stderr, exit status and file contents against the
original CLI using the **same interpreter inside each sandbox**. Cases cover
all help screens, normal commands, Unicode, missing/invalid arguments, missing
files, invalid UTF-8, copying, refusing overwrite, and forced overwrite. The
checker also verifies the five modules, callable exports, the public entrypoint,
and ordinary imports remaining in the standard library. An unchanged baseline
fails the structural checks.

Before replay, the harness replaces `_bench` with its own checker and baseline
oracle, so accidental model changes to tests do not redefine acceptance. These
are regression checks, not adversarial proof or a complete semantic-equivalence
check. Review the resulting architecture and dynamic imports manually. The
tiny workload is meant to exercise the workflow; it may be too small to expose
meaningful repository-map effects.
