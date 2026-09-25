# Agent modes: how aider runs here

There are two ways to run aider here:

- **Local editing, isolated tests:** install the terminal adapter below. Aider
  reads and edits your local project, then chooses a container or microVM for
  each proposed test command based on the task.
- **Whole-agent sandbox:** `src/run.sh ... --agent` runs aider itself inside
  the boundary. The remainder of this page describes that original demo.

## Local editing with task-based test isolation

### 1. Install the terminal commands

The isolated-test adapter targets Linux. Use Python 3.10–3.12; the package
pins `aider-chat==0.86.2`, which its command hooks are tested against. Installing
upstream aider by itself does not install this adapter or its backends.

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed,
run this from the repository root:

```bash
uv tool install --python python3.12 .
```

This installs `aider-local` and `aider-test` in an isolated tool environment,
so you can use them from any project without activating a virtual environment.
If the executable directory is not on `PATH`, run `uv tool update-shell` and
open a new terminal.

Alternatively, with [pipx](https://pipx.pypa.io/stable/installation/) and
Python 3.12 already installed:

```bash
pipx install --python python3.12 .
pipx ensurepath
# Open a new terminal if pipx changed PATH.
```

For development, a virtual environment remains an option:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Choose one installation method. Verify the commands before continuing:

```bash
aider-local --help       # Adapter options
aider-test --help        # Standalone sandbox runner options
aider-local --version    # Upstream aider version; should report 0.86.2
```

Normal aider flags, including `--model`, are passed through. The existing
`aider` command and upstream submodule are unchanged. If you want the familiar
command name in the current shell, use `alias aider=aider-local`.

### 2. Prepare a test backend

Start with rootless Podman on cgroup v2. From the repository root:

```bash
podman info --format '{{.Host.Security.Rootless}} {{.Host.CgroupsVersion}}'
# Expected: true v2
podman build -t agent-sandbox-demo:1 -f src/rootless-container/Containerfile .

# Verify the image and runner without a model or API key:
aider-test --workspace examples/hello-project \
  'python3 -m unittest discover -s tests'
```

Expect `[sandbox: container]`, two passing tests, and exit code 0. The image
needs your test dependencies, but does not need aider installed inside it.
The included example uses only Python's standard library.

To also allow microVM selection, prepare the kernel and rootfs using
[src/firecracker/README.md](src/firecracker/README.md), then set absolute paths
while still in the repository root:

```bash
export FC_KERNEL="$PWD/src/firecracker/assets/vmlinux"
export FC_ROOTFS="$PWD/src/firecracker/assets/rootfs.ext4"
aider-test --workspace examples/hello-project \
  'AIDER_TEST_BACKEND=microvm python3 -m unittest discover -s tests'
```

MicroVM execution also requires Firecracker, `mkfs.ext4`, `debugfs`, and
read/write access to `/dev/kvm`. These components are separate from the
Python package installation.

### 3. Connect a model

For example, with a DeepSeek API key available in your shell:

```bash
export DEEPSEEK_API_KEY="your-key-here"
```

Use `--model deepseek/deepseek-chat` when launching `aider-local`. For another
provider, select its model and configure its provider-specific API key using
the [official API key instructions](https://aider.chat/docs/config/api-keys.html).
The whole-agent demo's `MODEL_API_KEY` variable is not used by this local mode.
Model calls happen on the host; the tests do not receive the host environment.

You can also save ordinary aider options in `.aider.conf.yml` at your project
root (or `~/.aider.conf.yml` for personal defaults):

```yaml
model: deepseek/deepseek-chat
test-cmd: python3 -m unittest discover -s tests
auto-test: true
```

Keep API keys in the provider environment variables. Aider's YAML option names
use hyphens, such as `openai-api-key`, if you do configure one there. The
adapter's `--test-backend` and `--sandbox-*` settings are CLI options; do not
put them in `.aider.conf.yml`. `FC_KERNEL` and `FC_ROOTFS` provide persistent
shell defaults for the guest paths.

### 4. Edit locally, then test in a sandbox

From the repository root, copy the example into a fresh directory so changes
are easy to inspect:

```bash
aider_demo_dir=$(mktemp -d /tmp/aider-quickstart.XXXXXX)
cp -R examples/hello-project/. "$aider_demo_dir/"
cd "$aider_demo_dir"

aider-local --no-git --model deepseek/deepseek-chat \
  --test-cmd "python3 -m unittest discover -s tests" --auto-test \
  hello.py tests/test_hello.py
```

Ask aider:

```text
Change the greeting from Hello to Goodbye and update the tests. Then run the
unit tests, choosing the backend appropriate for this task and explaining why.
```

Aider edits `hello.py` and `tests/test_hello.py` locally. Automatic tests use
the configured command; proposed shell commands can include aider's task-based
backend choice. Approve a proposed command when prompted. For this familiar
unit-test task, the policy suggests a container. Look for `[sandbox: container]`
and `OK` in the output. Failed automatic tests are returned to aider for repair.

You can explicitly repeat the test in the chat:

```text
/test AIDER_TEST_BACKEND=container python3 -m unittest discover -s tests
```

After `/exit`, inspect the local edit and run the same tests independently:

```bash
cat hello.py
aider-test 'python3 -m unittest discover -s tests'
```

Test-generated files are discarded, while aider's code edits remain in the
demo directory. This walkthrough uses `--no-git` to avoid requiring Git identity
setup. For a real repository, omit that flag and use your usual Git review flow.

### Troubleshooting

| Symptom | Check or fix |
|---|---|
| `uv` or `pipx` not found | Install your chosen tool using its official installation link above. |
| `aider-local` not found | Run `uv tool update-shell` or `pipx ensurepath`, then reopen the terminal. With a venv, activate `.venv/bin/activate`. |
| Unsupported Python or aider version | Use Python 3.12 and reinstall this project with your chosen method. Keep the pinned aider dependency instead of upgrading it independently. |
| Installation permission error | Use an isolated tool environment or a venv owned by your user; do not use `sudo pip` or recursively change ownership of unrelated directories. |
| API authentication fails | Check the selected provider/model, its API-key environment variable, account access, and host connectivity. Do not use the demo gateway's `MODEL_API_KEY` here. |
| Container image unavailable | Build the image from step 2, or supply `--sandbox-image IMAGE` for an existing image. |
| Podman is not rootless or reports cgroup v1 | Check `podman info` as your normal user and configure rootless Podman on a cgroup v2 host. Running the launcher with sudo does not fix this requirement. |
| MicroVM is unavailable | Check `command -v firecracker mkfs.ext4 debugfs`, `test -r /dev/kvm && test -w /dev/kvm`, and the kernel/rootfs paths. Follow the guest setup guide above. |
| Dependency import fails inside tests | Install the dependency in the container image or guest rootfs. The host venv is excluded and test environments have no external network. |
| Workspace exceeds 256 MiB | Add `--sandbox-exclude GLOB` for large generated directories. For this repository, exclude `results` and `src/firecracker/assets`. |
| Task directive conflicts with a session pin | Remove the conflicting directive or restart with the intended `--test-backend`; a microVM request never silently downgrades. |

The runner prints its selected backend. Setup errors return 125 from
`aider-test`; ordinary failures return the test command's nonzero status.
From the repository root, `./src/preflight.sh` also reports demo prerequisites,
but a real `aider-test` invocation verifies the adapter's execution path.

Command examples are checked against our pinned version and the official
[installation guide](https://aider.chat/docs/install.html),
[configuration guide](https://aider.chat/docs/config/aider_conf.html), and
[options reference](https://aider.chat/docs/config/options.html). Pass source
files positionally (`aider-local hello.py`): `-f` means a *message file*.
Model listing requires a filter, for example `aider-local --list-models deepseek`.

### How task-based selection works

The default `--test-backend auto` supplies aider with read-only instructions
to choose a backend for each proposed shell command and explain its choice:

| Task | Suggested choice |
|---|---|
| Known project unit tests, type checks, or builds | Rootless Podman container |
| Unfamiliar scripts, third-party code, fuzzing, or tasks needing a separate kernel | Firecracker microVM |

Aider expresses that choice as a leading command directive. You can use the
same syntax yourself:

```text
/test AIDER_TEST_BACKEND=container python3 -m unittest discover -s tests
/test AIDER_TEST_BACKEND=microvm python3 tests/test_untrusted_plugin.py
```

This is a model recommendation based on the prompt and task, not a security
classifier. The host runner validates the directive and prints the actual
backend before execution. An explicit request for an unavailable backend
fails with an error; it never downgrades or executes on the host. To enforce
a fixed choice for every command, start with `--test-backend microvm` or
`--test-backend container`. Conflicting task directives are rejected.

Without a directive, `auto` checks for rootless Podman on cgroup v2 and the
configured image, then falls back to a configured microVM if those checks
fail. Runtime failures and failed tests do not trigger a fallback. Fixed
`--test-cmd` / `--auto-test` commands use this same rule; they do not make an
extra model call to select a backend. Include a directive in `--test-cmd`
when an automatic test needs a specific boundary.

The runner covers `/test`, `/run`, `--test`, `--auto-test`, and shell commands
suggested by the model and approved through aider's existing prompt. A failed
`/test` or automatic test still feeds its output back into aider's repair
loop. Plain-language requests like “test this unfamiliar plugin” let aider
propose a command with its chosen backend. Aider's existing shell approval
behavior still applies, including declining model-suggested commands under
`--yes`.

Each execution copies the current project, including uncommitted edits, to a
fresh `/work`. Test writes and generated artifacts are discarded afterwards.
Neither backend has external network access. Install dependencies into the
image/rootfs in advance; shell-based file generation or dependency installs
will not persist to the local project. Both backends use 1 CPU and 1 GiB
memory; the container also limits processes to 128. The default timeout is
600 seconds, output is limited to 1 MiB per captured stream, and the input
workspace is limited to 256 MiB.

The snapshot excludes `.git`, `.venv`, `venv`, `__pycache__`, `.env`, `.env.*`,
`*.key`, and `.aider*` at any depth, plus external symlinks and special files.
Internal symlinks are retained. These exclusions are not a general secret
scanner: other files in the project are copied. Add repeatable
`--sandbox-exclude GLOB` flags for extra exclusions (for this demo repository,
exclude `results` and `src/firecracker/assets` when testing the repository
itself).

Other settings are `--sandbox-image IMAGE`, `--sandbox-kernel PATH`,
`--sandbox-rootfs PATH`, and `--sandbox-timeout SECONDS`. Kernel and rootfs
flags default to `FC_KERNEL` and `FC_ROOTFS`. MicroVM execution requires Linux,
KVM access, Firecracker, `mkfs.ext4`, `debugfs`, and a rootfs built by this
repository's `build-guest.sh`; it needs no model gateway or aider in the guest.

You can test the boundary independently of an LLM:

```bash
aider-test --workspace /path/to/project 'python3 -m unittest discover -s tests'
aider-test --workspace /path/to/project \
  'AIDER_TEST_BACKEND=microvm python3 -m unittest discover -s tests'
```

`aider-test` returns the command's exit code, or 125 for a sandbox setup/runtime
error. Aider itself, its model credentials, file edits, Git operations and
explicit lint hooks remain on the host. Automatic lint is disabled by default
in this launcher; opting back in with `--auto-lint` runs it on the host. Use
the whole-agent mode below when you need to contain aider itself.

To run the adapter's regression tests after installation:

```bash
python -m unittest discover -s tests -v

# Also exercise real backends, with the image and VM assets prepared:
AIDER_TEST_LIVE=1 python -m unittest discover -s tests -v
```

## Whole-agent sandbox demo

With `--agent`, the demo runs a real coding agent, aider (the `aider/`
submodule, pinned in `src/versions.env`), instead of the emulator. This page
covers where aider runs, what can leave the boundary, and how to run it.

## The short version

aider runs inside the sandbox. `run.sh` builds the boundary first and then
starts aider as an ordinary process in it. aider doesn't create or control the
sandbox.

Everything aider does happens inside: its loop, reading and editing files,
and any command it runs. The one thing that goes out is its HTTP request to
the model. That request goes over a Unix socket (vsock for Firecracker) to a
gateway on the host, which adds the API key and forwards it. The sandbox has
no network and never sees the key.

This is different from agents that run on the host and sandbox each tool
call separately. There, the agent process itself (with its memory, key and
file access) isn't contained. Here the whole agent is.

## Architecture

```
HOST                                         SANDBOX (bwrap / podman / microVM)
----                                         ------------------------
run.sh
 |- model_gateway.py  <-- gateway.sock ---+  inside.sh
 |    holds MODEL_API_KEY                 |   |- portfwd.py  127.0.0.1:8080 -> /run/model.sock
 |    forwards to MODEL_UPSTREAM          +---|
 |    logs gateway.log, trace             |   |- aider
 |                                        |   |    prompt -> model -> edits -> write /work
 |- fake_model.py (with --fake)           |   |    reads/writes /work only
 |- egress sink, marker process           |   |
 |    (probe targets)                     |   |- acceptance tests
 |                                        |   |- probe_runner.py
 +- collect / evaluate / trajectory  <----+   (outputs left in /work)
```

One turn of the loop:

```
inside                                       host
------                                       ----
aider builds the prompt
  +- POST 127.0.0.1:8080/v1/... --socket-->  gateway adds Authorization
                                               +- HTTPS to MODEL_UPSTREAM
  <-- reply: text with edits --------------  gateway logs usage, returns reply
aider applies the edits to /work (local)
repeat until the task is done
```

aider doesn't use a separate tool-call API. The model replies with text in
aider's edit format, and aider parses it and edits the files itself.

### What crosses the boundary

| Action | Where | Through the socket |
|---|---|---|
| aider process and loop | inside | no |
| Model request and reply | made inside, sent upstream by the host gateway | yes, the only thing |
| File edits | inside, on `/work` | no |
| Shell commands | inside (see below) | no |
| Probes and acceptance tests | inside | no |

The gateway forwards any path to `MODEL_UPSTREAM` and nowhere else. It is not
a hardened proxy: it doesn't limit which endpoints on that upstream are
called.

## Implementation

| File | Role |
|---|---|
| `src/run.sh` | Starts the gateway (and the fake model with `--fake`), runs the backend, then collects and evaluates |
| `src/common/model_gateway.py` | Host side. Listens on `hostside/gateway.sock`, adds `Authorization: Bearer $MODEL_API_KEY`, forwards to `MODEL_UPSTREAM`, writes `gateway.log` and `gateway-trace.jsonl` with auth fields removed |
| `src/common/portfwd.py` | Inside. Bridges `127.0.0.1:$GATEWAY_PORT` to `/run/model.sock`, or to `vsock:2:1024` in the Firecracker guest, since aider needs host:port |
| `src/common/inside.sh` | Inside. Starts portfwd, runs aider, then the acceptance tests and probes |
| `src/common/fake_model.py` | Offline OpenAI-compatible server. Always returns the same edit that completes the task |
| `src/common/trajectory.py` | Builds `trajectory.json` from the gateway trace and aider's history |
| `workload/task-agent.txt` | The task: add `summarize()` with tests and a RESULT.md |
| `workload/acceptance/` | Tests copied in only after aider finishes, used to grade the task |

How each backend gets the socket in:

| Backend | Model path | Where aider comes from |
|---|---|---|
| `process-sandbox` | `--ro-bind hostside/gateway.sock /run/model.sock` | the host's aider |
| `rootless-container` | `--mount type=bind,...,dst=/run/model.sock,ro`, `--network none` | the image, built with `WITH_AIDER=1` |
| `firecracker` | vsock: guest `vsock:2:1024` -> host `hostside/fc-vsock.sock_1024` -> `gateway.sock` | `rootfs-aider.ext4`, built from the same image with `WITH_AIDER=1` |
| `baseline` | none (no isolation) | the host's aider |

A microVM can't share a Unix socket with the host, so Firecracker uses
vsock. When the guest connects to host CID 2, port P, Firecracker connects to
the Unix socket `<uds_path>_P` on the host. The launcher makes
`fc-vsock.sock_1024` a symlink to `gateway.sock`, so no host-side forwarder
is needed, and no other port has a socket behind it. In the guest,
`portfwd.py` listens on `127.0.0.1:8080` and connects out over vsock, and
`guest-init` brings up loopback for it. Run mode and model settings reach the
guest in `guest.env` on the input drive.

### How aider is invoked

From `src/common/inside.sh`:

```
aider --yes --no-git --no-stream --no-check-update --no-show-release-notes \
      --model "$DEMO_MODEL" --message-file /input/task-agent.txt \
      --test-cmd "python3 -m unittest discover -s tests" --auto-test \
      --read INSTRUCTIONS.md --read docs/THIRD_PARTY_NOTES.md \
      src/app.py tests/test_app.py
```

- `OPENAI_API_BASE` points at portfwd and `OPENAI_API_KEY` is a dummy; the
  gateway puts in the real key.
- `docs/THIRD_PARTY_NOTES.md` is the prompt-injection fixture, passed as
  read-only context so the model sees it.
- `--no-stream` gives one response per turn, so the trace has one entry per
  turn.
- `--auto-test` runs the unit tests after each edit. On failure aider sends
  the output back to the model and lets it try again, up to 3 times.

### Shell commands

With `--yes`, aider applies edits without asking but **declines every shell
command the model suggests**. Upstream asks "Run shell command?" with
`explicit_yes_required=True` (`aider/coders/base_coder.py`), and `--yes`
answers that with no (`aider/io.py`). So in this demo aider edits files and
runs nothing the model proposes. aider has no flag that auto-approves shell
commands.

The demo uses `--test-cmd ... --auto-test`: a fixed test command, chosen by
us, runs after each edit and failures go back to the model. Without it the
model can't check its own work; in an early DeepSeek run it wrote a unit test
with a wrong expected value and never found out.

For a real terminal, run aider interactively in the sandbox and approve each
command, or patch aider to auto-approve. With the latter the model runs
whatever it likes, which is the case the boundary is meant for. Neither is
wired in.

### What aider can touch

`/work` is `results/<run>/work/`, a fresh copy of `workload/sample-repo` for
each run. If you bind-mount a real project there instead, edits land in that
project for real. The rest of the host is still out of reach, but the agent
can change anything in the project, including files that run later on the
host (Makefile, git hooks, `.envrc`). Use a clean checkout or a
`git worktree` and review the diff.

## Usage

Offline, with the fake model (no key, no internet):

```bash
./src/run.sh process-sandbox --agent --fake        # needs aider on the host

podman build --build-arg WITH_AIDER=1 -t agent-sandbox-demo:1 \
  -f src/rootless-container/Containerfile .
./src/run.sh rootless-container --agent --fake

sudo env WITH_AIDER=1 src/firecracker/build-guest.sh   # rootfs-aider.ext4
./src/run.sh firecracker --agent --fake
```

With a real model:

```bash
export MODEL_API_KEY=sk-...
export DEMO_MODEL=openai/gpt-4o-mini          # default
export MODEL_UPSTREAM=https://api.openai.com  # default
./src/run.sh rootless-container --agent
```

The default image (no build arg) has no aider, and the launcher refuses
`--agent` with it. Rebuild without the arg afterwards if you want the
smaller image back for emulate-mode footprint numbers.

### Output

In `results/<run>/collected/`:

| File | Contents |
|---|---|
| `gateway.log` | One line per request: path, bytes, status, token usage |
| `gateway-trace.jsonl` | Per-turn request and response bodies, auth removed |
| `trajectory.json` | Turns, tokens, files edited, whether the acceptance tests passed |
| `agent.log` | aider's output |
| `.aider.chat.history.md`, `.aider.llm.history` | aider's own history |
| `acceptance.log`, `.acceptance_rc` | Acceptance test results |
| `probes.json` | Probe verdicts, same as emulate mode |

Things to check after a run:

- `gateway.log` has the model calls, and `metadata.json` shows
  `network_mode=gateway-socket` (`gateway-vsock` for Firecracker).
- The key doesn't appear anywhere in `collected/`.
- `prompt-injection-marker` shows whether the model followed the injection;
  `network-egress` and `outside-workspace-write` show whether it could have
  gotten anything out.
