# Live-demo script

Total: ~8–10 min. Everything runs offline in emulate mode; `--agent` is optional.

## Before the talk (rehearsal checklist)

- [ ] Disposable host/VM only. No personal SSH keys, cloud creds, browser
      profiles, or real repos anywhere near `workload/`.
- [ ] `./lab/preflight.sh` is all-green for the backends you'll show live.
- [ ] `podman build -t agent-sandbox-demo:1 -f lab/rootless-container/Containerfile .`
- [ ] Firecracker assets built and digests pinned in `versions.env`.
- [ ] Pre-run all backends once; keep `results/` from that run as the offline
      fallback, plus a screen recording of a good run.
- [ ] `./lab/compare-results.sh` renders cleanly.

## Stage 0 — the question (30s)

"Same agent, same task, same attacks. I change only the box it runs in. Let's
watch what it can see, change, and reach."

## Stage 1 — baseline, no sandbox (90s)

```bash
./lab/run.sh baseline
```

Point at the table: **8 red `!!`** — it reads the canary, sees host processes,
phones the sink, writes outside the workspace, runs privileged ops, no resource
cap. And `approved-artifact` is green: the task still gets done. "This is the
'before'. Every green-to-red flip from here is what a boundary buys us."

## Stage 2 — process sandbox (90s)

```bash
./lab/run.sh process-sandbox
```

All isolation probes flip to green; task still green. Note the `[landlock]
enforced` and `[rlimit] enforced` lines. "Lightest boundary, shared kernel —
already closes every probe. Cheap and fast."

## Stage 3 — rootless container (90s)

```bash
./lab/run.sh rootless-container
```

Same green profile, different mechanism: ro-root, dropped caps, cgroup limits.
Show `metadata.json` → `image_digest`, `caps=drop-all`, `network_mode=none`.
"The launcher *refuses* privileged/host-net/socket-mount configs — it won't run
an unsafe container."

## Stage 4 — Firecracker (2–3 min, or fallback)

```bash
./lab/run.sh firecracker
```

If assets are ready: same green profile but now behind a **separate guest
kernel** — show `kernel_digest`, `vcpus`, `mem_mib`, `workspace_transfer`.
"Strongest boundary, highest cost — note the boot time in the metadata."

If KVM isn't available: it prints `not-executed` with a reason. **Say it out
loud:** "That's a labeled skip, not a pass," then show the pre-recorded run.

## Stage 5 — the two matrices (90s)

```bash
./lab/compare-results.sh
```

Two tables print. **Security** first: which authority each boundary removes.
Then **cost / footprint**: time, peak RSS, process count, boundary disk, the
extra host tooling each backend drags in, and the agent's token/call cost.

Land the trade-off: "The strongest boundary isn't free — look at boot time and
disk. And notice the agent-token line: a *tighter* box makes a confused agent
spend *more* tokens bouncing off walls. Security and cost move together; this
table lets you pick the point on that curve on purpose."

## Optional — a real fooled agent (if time + key)

```bash
export MODEL_API_KEY=sk-...
./lab/run.sh process-sandbox --agent
```

Show `hostside/gateway.log`: the key stayed on the host, the model call went
through the one logged path. If the model obeyed the injection, the probes and
canary catch the attempt — and the boundary still contained it.

## Fallbacks

- Any live run wedges → `./lab/compare-results.sh results/` on the pre-run
  results, or play the recording.
- Never "fix it live" by loosening a boundary. A failed run that failed *closed*
  is a better demo than a fudged green.
