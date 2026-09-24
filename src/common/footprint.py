#!/usr/bin/env python3
"""Write collected/footprint.json: what a run cost.

- phase timings from timings.env
- peak RSS, process count and host helpers from the sampler
- disk used by the boundary (image, or kernel + rootfs; 0 for the process sandbox)
- model requests and tokens from gateway.log in agent mode, otherwise an
  estimate based on blocked operations

Usage: footprint.py <run_dir>
"""
import glob
import json
import os
import sys


def load_env(path):
    cfg = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k] = v
    return cfg


def dir_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if os.path.isfile(fp) and not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


def boundary_disk_bytes(backend, meta, repo_root):
    """Disk used by the boundary on top of the host userland."""
    if backend == "process-sandbox":
        return 0  # uses host /usr, /lib, /bin
    if backend == "rootless-container":
        try:
            return int(meta.get("image_size_bytes", 0))
        except (TypeError, ValueError):
            return 0
    if backend == "firecracker":
        # Kernel + the rootfs that was booted, as recorded by the launcher.
        if meta.get("boundary_disk_bytes"):
            return int(meta["boundary_disk_bytes"])
        assets = os.path.join(repo_root, "src", "firecracker", "assets")
        b = 0
        for name in ("vmlinux", "rootfs.ext4"):
            p = os.path.join(assets, name)
            if os.path.isfile(p):
                b += os.path.getsize(p)
        return b
    return 0


def gateway_cost(run_dir):
    """Request count and token usage from gateway.log."""
    # hostside/ is gone after teardown; prefer the copy in collected/.
    for log in (os.path.join(run_dir, "collected", "gateway.log"),
                os.path.join(run_dir, "hostside", "gateway.log")):
        if os.path.exists(log):
            break
    else:
        return None
    reqs = prompt = completion = 0
    for line in open(log):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("event") == "forward":
            reqs += 1
        if rec.get("event") == "usage":
            prompt += rec.get("prompt_tokens", 0)
            completion += rec.get("completion_tokens", 0)
    return {"llm_requests": reqs, "prompt_tokens": prompt,
            "completion_tokens": completion, "total_tokens": prompt + completion}


def friction_estimate(run_dir):
    """Rough agent cost for emulate mode.

    Assumes one extra tool call per blocked operation. An estimate only; use
    --agent for real numbers.
    """
    probes_path = os.path.join(run_dir, "collected", "probes.json")
    if not os.path.exists(probes_path):
        return None
    data = json.load(open(probes_path))
    blocked = [p["probe"] for p in data.get("probes", [])
               if p.get("observed") == "blocked"
               and p["probe"] not in ("prompt-injection-marker",)]
    EST_TOKENS_PER_RETRY = 350
    return {"blocked_ops": len(blocked),
            "est_extra_tool_calls": len(blocked),
            "est_extra_tokens": len(blocked) * EST_TOKENS_PER_RETRY,
            "basis": f"{EST_TOKENS_PER_RETRY} tokens/blocked-op (estimate)"}


def main(run_dir):
    collected = os.path.join(run_dir, "collected")
    meta_path = os.path.join(collected, "metadata.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    backend = meta.get("backend", "unknown")
    repo_root = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))

    timings = load_env(os.path.join(run_dir, "timings.env"))
    # hostside/ is gone after teardown; prefer the copy in collected/.
    sample = {}
    for cand in (os.path.join(collected, "footprint-sample.json"),
                 os.path.join(run_dir, "hostside", "footprint-sample.json")):
        if os.path.exists(cand):
            sample = json.load(open(cand))
            break

    fp = {
        "backend": backend,
        "timing_ms": {
            "prepare": int(timings.get("PREP_MS", 0)),
            "run": int(timings.get("RUN_MS", 0)),
            "teardown": int(timings.get("TEARDOWN_MS", 0)),
            "total": int(timings.get("PREP_MS", 0)) + int(timings.get("RUN_MS", 0))
                     + int(timings.get("TEARDOWN_MS", 0)),
        },
        "peak_proc_count": sample.get("peak_proc_count"),
        "peak_rss_mb": sample.get("peak_rss_mb"),
        "host_helpers": sample.get("host_helpers", []),
        "boundary_disk_mb": round(boundary_disk_bytes(backend, meta, repo_root) / 1024 / 1024, 1),
        "workspace_transfer": meta.get("workspace_transfer", "bind-mount"),
        "network_mode": meta.get("network_mode"),
    }
    cost = gateway_cost(run_dir)
    if cost:
        fp["agent_cost"] = cost
        fp["agent_cost"]["mode"] = "measured (agent)"
    else:
        est = friction_estimate(run_dir)
        if est:
            fp["agent_cost"] = est
            fp["agent_cost"]["mode"] = "estimated (emulate)"

    json.dump(fp, open(os.path.join(collected, "footprint.json"), "w"), indent=2)

    t = fp["timing_ms"]
    ac = fp.get("agent_cost", {})
    tok = ac.get("total_tokens", ac.get("est_extra_tokens", "—"))
    print(f"  footprint: total {t['total']}ms "
          f"(prep {t['prepare']}/run {t['run']}/teardown {t['teardown']}) | "
          f"peak {fp['peak_rss_mb']}MB / {fp['peak_proc_count']} procs | "
          f"boundary {fp['boundary_disk_mb']}MB disk | "
          f"helpers {fp['host_helpers']} | agent tokens {tok}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
