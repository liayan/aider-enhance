#!/usr/bin/env python3
"""Cross-backend comparison matrix from results/*/collected/probes.json.

Prints a probe x backend table (observed value + expected/unexpected flag) plus
startup/teardown and identity notes, and writes comparison.md + comparison.json.
Uses the most recent run per backend.
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
        meta_path = os.path.join(run_dir, "collected", "metadata.json")
        meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
        key = os.path.basename(run_dir)
        prev = runs.get(backend)
        if not prev or key > prev["key"]:
            runs[backend] = {"key": key, "data": data, "meta": meta}
    return runs


def main(results_dir):
    runs = latest_per_backend(results_dir)
    if not runs:
        sys.exit(f"no results under {results_dir}")
    backends = [b for b in ORDER if b in runs] + [b for b in runs if b not in ORDER]

    # collect probe names in stable order
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

    print("\n".join(lines))
    print()
    for b in backends:
        m = runs[b]["meta"]
        print(f"[{b}] policy={m.get('policy_digest','?')[:22]} "
              f"net={m.get('network_mode','?')} "
              f"kernel={m.get('host_kernel', m.get('kernel_digest','?'))} "
              f"unexpected={runs[b]['data'].get('unexpected_count','?')}")

    # markdown
    md = ["# Sandbox comparison", "",
          "Observed probe outcome per backend (`ok`=as expected, `!!`=unexpected, `·`=info).",
          "", "| probe | " + " | ".join(backends) + " |",
          "|" + "---|" * (len(backends) + 1)]
    for name in names:
        md.append("| " + name + " | " + " | ".join(cell(b, name).strip() for b in backends) + " |")
    md += ["", "## Backend metadata", ""]
    for b in backends:
        m = runs[b]["meta"]
        md.append(f"- **{b}** — net `{m.get('network_mode','?')}`, "
                  f"policy `{m.get('policy_digest','?')[:22]}`, "
                  f"unexpected `{runs[b]['data'].get('unexpected_count','?')}`, "
                  f"transfer `{m.get('workspace_transfer','bind')}`")
    md += ["", "> A `not-executed` Firecracker cell is a skipped run, not a pass."]
    out_md = os.path.join(results_dir, "comparison.md")
    open(out_md, "w").write("\n".join(md) + "\n")
    json.dump({b: runs[b]["data"] for b in backends},
              open(os.path.join(results_dir, "comparison.json"), "w"), indent=2)
    print(f"\nwrote {out_md} and comparison.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results")
