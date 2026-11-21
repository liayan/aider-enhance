# Extras

Material that isn't part of the talk. The talk's harness is in [../src/](../src/).

| Path | What it is |
|---|---|
| [k8s/](k8s/) | The harness on Kubernetes with agent-sandbox (runc, gVisor, Kata). Not run on a cluster yet. |
| [benchmarks/sandbox_security/](benchmarks/sandbox_security/) | Repeated probe runs per backend, plus a memory-limit sweep. Run from the repo root. |
| [aider-local/](aider-local/) | Separate installable tool: aider edits on the host, tests run in a disposable sandbox. Includes the refactoring benchmarks and the Python test suite. |
