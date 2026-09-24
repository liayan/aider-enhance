# Agent mode: how aider runs here

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
