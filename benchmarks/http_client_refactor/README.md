# HTTP-client refactoring benchmark

This workload tests a one-file refactor with real behavior checks. It was
inspired by the supplied Aider/Codex comparison article; its fixture, prompt,
oracle, and reference implementation are new. The article does not supply its
original file or measured results.

`baseline/src/http_client.py` calls `requests.get` and `requests.post` in three
public functions. The fixed [prompt](prompt.txt) asks Aider to introduce a shared
`request_json(method, url, **kwargs)`, preserve public signatures, URLs, payloads,
JSON return values, and the five-second override in `fetch_user`, while adding a
ten-second default timeout. It explicitly permits new error wrapping and retry
behavior. GET retries only on connection errors, timeouts, and HTTP 500–599; two
retries after the first attempt means three total. POST and other methods do not
retry. Backoff is 0.1 and 0.2 seconds, before the second and third attempts.

The [checker](check.py) runs 11 offline contract tests with a small `requests`
test double and mocked sleep. It checks signatures, dispatch, arguments,
return values, error causes, status handling, retry counts, logging, and
sensitive-value absence from logs. Neither external network nor the installed
`requests` package is needed for replay. A real application still needs its
existing `requests` dependency. The checks are regression tests, not proof that
all network or HTTP behavior is equivalent.

## Replay the reference implementation

Use the same backend prerequisites as the
[CLI benchmark](../cli_refactor/README.md). Output directories must be new.

```bash
python benchmarks/cli_refactor/benchmark.py run \
  --workload http-client-refactor \
  --kernel src/firecracker/assets/vmlinux \
  --rootfs src/firecracker/assets/rootfs.ext4 \
  --repeats 3 --warmups 1 \
  --output results/http-client-reference
```

The run reports each backend's result and timings. This reference replay makes
no model calls. Test execution is short, so VM/container setup and Python
startup dominate the wall-time result. Do not interpret these times as model
performance or standalone HTTP-client execution speed.

## Generate a candidate with Aider

Set `OPENAI_API_KEY` and specify the provider's actual OpenAI-compatible API
base and model. This step makes model requests; the exact base path depends on
the provider. See the [CLI benchmark guide](../cli_refactor/README.md) for API
usage recording details and the limits of model-versus-sandbox timing.

```bash
python benchmarks/cli_refactor/benchmark.py generate \
  --workload http-client-refactor \
  --model openai/YOUR_MODEL_ID \
  --api-base https://YOUR_PROVIDER/v1 \
  --backend container \
  --output results/http-client-model-01

python benchmarks/cli_refactor/benchmark.py run \
  --workload http-client-refactor \
  --candidate results/http-client-model-01/work \
  --kernel src/firecracker/assets/vmlinux \
  --rootfs src/firecracker/assets/rootfs.ext4 \
  --output results/http-client-model-01-replay
```

Generation records `file_scope` in `generation.json`; replay records it in
`metadata.json` and every run. **Only `src/http_client.py` may change.** The
scope check includes tracked, untracked, and Git-ignored files and executable
bits. It runs before the sandbox snapshot, so a new `.env`, key file, symlink,
or deleted caller fails even if the runner would omit it. The benchmark checks
its staged test and oracle files before replacing them for replay. Only known
Aider/Git bookkeeping and pure Python bytecode caches are exempt. Failed scope
checks remain failures even when the behavioral tests pass.

Each generation starts from a fresh Git commit. `--no-gitignore` prevents Aider
from creating `.gitignore` as a side effect. Model edits stay local; automatic
checks use the pinned sandbox. `generation.json` reports the exact changed file
list, exit code, and recorded model usage, so a task that needs another attempt
can be distinguished from a first-pass success.
