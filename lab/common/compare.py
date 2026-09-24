#!/usr/bin/env python3
"""Print security and cost tables across backends and write
comparison.md/.json. Uses the latest run per backend.
"""
import glob
import json
import os
import sys

ORDER = ["baseline", "process-sandbox", "rootless-container", "firecracker"]
SYM = {"as-expected": "ok", "UNEXPECTED": "!!", "info": "· "}


def latest_per_backend(results_dir):
    runs = {}
    for probes in glob.glob(os.path.join(results_dir, "*", "collected", "probes.json")):
        run_dir = os.path.dirname(os.path.dirname(probes))
        try:
            data = json.load(open(probes))
        except Exception:
            continue
        backend = data.get("backend", "unknown")
        col = os.path.join(run_dir, "collected")
        meta = json.load(open(os.path.join(col, "metadata.json"))) \
            if os.path.exists(os.path.join(col, "metadata.json")) else {}
        fp = json.load(open(os.path.join(col, "footprint.json"))) \
            if os.path.exists(os.path.join(col, "footprint.json")) else {}
        key = os.path.basename(run_dir)
        prev = runs.get(backend)
        if not prev or key > prev["key"]:
            runs[backend] = {"key": key, "data": data, "meta": meta, "fp": fp}
    return runs


def cost_rows(runs, backends):
    """Return (header, rows) for the operational-cost matrix."""
    def g(b, *path, default="—"):
        if runs[b]["data"].get("status") == "not-executed":
            return "not-exec"
        cur = runs[b]["fp"]
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur

    metrics = [
        ("total time (ms)",     lambda b: g(b, "timing_ms", "total")),
        ("  prepare (ms)",      lambda b: g(b, "timing_ms", "prepare")),
        ("  run (ms)",          lambda b: g(b, "timing_ms", "run")),
        ("  teardown (ms)",     lambda b: g(b, "timing_ms", "teardown")),
        ("peak RSS (MB)",       lambda b: g(b, "peak_rss_mb")),
        ("peak procs",          lambda b: g(b, "peak_proc_count")),
        ("boundary disk (MB)",  lambda b: g(b, "boundary_disk_mb")),
        ("host helpers",        lambda b: (lambda v: v if isinstance(v, str) else (",".join(v) or "none"))(g(b, "host_helpers", default=[]))),
        ("workspace transfer",  lambda b: g(b, "workspace_transfer")),
        ("agent tokens",        lambda b: g(b, "agent_cost", "total_tokens",
                                            default=g(b, "agent_cost", "est_extra_tokens"))),
        ("agent calls",         lambda b: g(b, "agent_cost", "llm_requests",
                                            default=g(b, "agent_cost", "est_extra_tool_calls"))),
        ("token basis",         lambda b: g(b, "agent_cost", "mode")),
    ]
    return metrics


def main(results_dir):
    runs = latest_per_backend(results_dir)
    if not runs:
        sys.exit(f"no results under {results_dir}")
    backends = [b for b in ORDER if b in runs] + [b for b in runs if b not in ORDER]

    names = []
    for b in backends:
        for p in runs[b]["data"].get("probes", []):
            if p["probe"] not in names:
                names.append(p["probe"])

    def cell(b, name):
        for p in runs[b]["data"].get("probes", []):
            if p["probe"] == name:
                v = p.get("observed", "?")
                flag = SYM.get(p.get("verdict", ""), "  ")
                return f"{v} {flag}"
        st = runs[b]["data"].get("status")
        return "not-exec" if st == "not-executed" else "—"

    w = max(len("probe"), max(len(n) for n in names)) + 1
    cw = 20
    header = "probe".ljust(w) + "".join(b[:cw-1].ljust(cw) for b in backends)
    lines = [header, "-" * len(header)]
    for name in names:
        row = name.ljust(w) + "".join(cell(b, name).ljust(cw) for b in backends)
        lines.append(row)

    print("SECURITY — observed outcome per backend")
    print("\n".join(lines))

    metrics = cost_rows(runs, backends)
    cw2 = 20
    cheader = "metric".ljust(w) + "".join(b[:cw2-1].ljust(cw2) for b in backends)
    print("\nCOST / FOOTPRINT")
    print(cheader)
    print("-" * len(cheader))
    for label, fn in metrics:
        row = label.ljust(w) + "".join(str(fn(b)).ljust(cw2) for b in backends)
        print(row)

    print()
    for b in backends:
        m = runs[b]["meta"]
        print(f"[{b}] policy={m.get('policy_digest','?')[:22]} "
              f"net={m.get('network_mode','?')} "
              f"kernel={m.get('host_kernel', m.get('kernel_digest','?'))} "
              f"unexpected={runs[b]['data'].get('unexpected_count','?')}")

    md = ["# Sandbox comparison", "",
          "Observed probe outcome per backend (`ok`=as expected, `!!`=unexpected, `·`=info).",
          "", "| probe | " + " | ".join(backends) + " |",
          "|" + "---|" * (len(backends) + 1)]
    for name in names:
        md.append("| " + name + " | " + " | ".join(cell(b, name).strip() for b in backends) + " |")
    md += ["", "## Cost / footprint", "",
           "| metric | " + " | ".join(backends) + " |",
           "|" + "---|" * (len(backends) + 1)]
    for label, fn in metrics:
        md.append("| " + label.strip() + " | "
                  + " | ".join(str(fn(b)) for b in backends) + " |")
    md += ["", "## Backend metadata", ""]
    for b in backends:
        m = runs[b]["meta"]
        md.append(f"- **{b}** — net `{m.get('network_mode','?')}`, "
                  f"policy `{m.get('policy_digest','?')[:22]}`, "
                  f"unexpected `{runs[b]['data'].get('unexpected_count','?')}`, "
                  f"transfer `{m.get('workspace_transfer','bind')}`")
    md += ["", "> A `not-executed` Firecracker cell is a skipped run, not a pass.",
           "> Agent tokens/calls are **measured** in `--agent` mode and **estimated** "
           "from blocked-op friction in emulate mode; compare like-for-like."]
    out_md = os.path.join(results_dir, "comparison.md")
    open(out_md, "w").write("\n".join(md) + "\n")
    json.dump({b: runs[b]["data"] for b in backends},
              open(os.path.join(results_dir, "comparison.json"), "w"), indent=2)
    print(f"\nwrote {out_md} and comparison.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results")
