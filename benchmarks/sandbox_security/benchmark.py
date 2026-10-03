#!/usr/bin/env python3
"""Systematic security + memory-footprint benchmark over the sandbox backends.

This turns the single probe run into a repeated, aggregated benchmark with three
questions per backend:

  1. Security: across N repeats, does every probe match its expected verdict on
     every run? A backend is "secure-stable" only if unexpected_count is 0 every
     time (flaky isolation is a failure, not noise).
  2. Memory footprint: peak RSS and peak process count per run, reported as
     median and spread across repeats, plus the phase memory when available.
  3. Boundary condition: sweep the memory limit (--mem-sweep) and confirm the
     resource-limit probe stays "blocked" (enforced) at each size. This checks
     the limit actually tracks its configured value rather than passing by luck
     at one setting.

It drives the existing `src/run.sh` per run (no reimplementation), one result
directory per run under a private results root, and reads each run's
`collected/probes.json` and `footprint.json`. It makes no model calls
(emulate mode); pass --agent-fake to exercise the real agent path with the
offline fake model.

Usage:
  python benchmarks/sandbox_security/benchmark.py \
      --backends process-sandbox baseline \
      --repeats 5 --warmups 1 --seed 0 \
      --mem-sweep 256M,512M,1G \
      --output results/sec-footprint

Exit code is nonzero if any requested backend is unavailable, any warmup fails,
or any measured (non-warmup) run is not secure-stable.
"""
import argparse
import hashlib
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time

REPO = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUN_SH = os.path.join(REPO, "src", "run.sh")
ALL_BACKENDS = ["baseline", "process-sandbox", "rootless-container", "firecracker"]


def sh(cmd, env, log_path):
    with open(log_path, "wb") as log:
        p = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
    return p.returncode


def one_run(backend, out_root, tag, extra_env, agent_fake, kernel, rootfs):
    """Run src/run.sh once into a private results root; return parsed results."""
    results_dir = os.path.join(out_root, "runs", tag)
    os.makedirs(results_dir, exist_ok=True)
    env = dict(os.environ)
    env["DEMO_RESULTS_DIR"] = results_dir
    env["KEEP_RUN"] = ""            # let run.sh wipe work/secrets; keep collected/
    env.update(extra_env)
    if kernel:
        env["FC_KERNEL"] = kernel
    if rootfs:
        env["FC_ROOTFS"] = rootfs
    cmd = ["bash", RUN_SH, backend]
    if agent_fake:
        cmd += ["--agent", "--fake"]
    log_path = os.path.join(results_dir, "run.log")
    t0 = time.monotonic()
    rc = sh(cmd, env, log_path)
    wall_ms = int((time.monotonic() - t0) * 1000)

    # run.sh writes results/<run-id>/collected/. Find the one run dir it made.
    run_dirs = [d for d in (os.path.join(results_dir, x)
                for x in os.listdir(results_dir))
                if os.path.isdir(d) and os.path.exists(os.path.join(d, "collected"))]
    rec = {"backend": backend, "tag": tag, "run_sh_rc": rc, "wall_ms": wall_ms,
           "extra_env": extra_env, "log": os.path.relpath(log_path, out_root)}
    if not run_dirs:
        rec["error"] = "no result directory produced"
        return rec
    collected = os.path.join(sorted(run_dirs)[-1], "collected")
    probes_p = os.path.join(collected, "probes.json")
    fp_p = os.path.join(collected, "footprint.json")
    meta_p = os.path.join(collected, "metadata.json")
    if os.path.exists(probes_p):
        probes = json.load(open(probes_p))
        rec["status"] = probes.get("status", "executed")
        rec["unexpected_count"] = probes.get("unexpected_count")
        rec["verdicts"] = {p["probe"]: p.get("verdict") for p in probes.get("probes", [])}
        rec["observed"] = {p["probe"]: p.get("observed") for p in probes.get("probes", [])}
    else:
        rec["error"] = "no probes.json"
    if os.path.exists(fp_p):
        fp = json.load(open(fp_p))
        rec["peak_rss_mb"] = fp.get("peak_rss_mb")
        rec["peak_proc_count"] = fp.get("peak_proc_count")
        rec["timing_ms"] = fp.get("timing_ms")
    if os.path.exists(meta_p):
        m = json.load(open(meta_p))
        rec["host_kernel"] = m.get("host_kernel")
        rec["memory_max"] = m.get("memory_max") or m.get("mem_mib")
    return rec


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 1) if xs else None


def spread(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(max(xs) - min(xs), 1) if len(xs) > 1 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backends", nargs="+", default=["process-sandbox", "baseline"])
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--warmups", type=int, default=1)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mem-sweep", default="",
                    help="comma-separated LIMIT_MEM values, e.g. 256M,512M,1G")
    ap.add_argument("--agent-fake", action="store_true",
                    help="exercise the real agent path with the offline fake model")
    ap.add_argument("--kernel", default="")
    ap.add_argument("--rootfs", default="")
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    out = os.path.realpath(args.output)
    if os.path.exists(out):
        sys.exit(f"output dir already exists: {out}")
    os.makedirs(out)
    for b in args.backends:
        if b not in ALL_BACKENDS:
            sys.exit(f"unknown backend: {b}")

    rng = random.Random(args.seed)
    runs = []           # every run, including warmups and sweep
    # Build the measured schedule (security + footprint), shuffled.
    schedule = []
    for b in args.backends:
        for i in range(args.warmups):
            schedule.append((b, {}, f"warmup-{b}-{i}", True))
        for i in range(args.repeats):
            schedule.append((b, {}, f"main-{b}-{i}", False))
    rng.shuffle(schedule)

    hard_fail = False
    for backend, env, tag, is_warmup in schedule:
        rec = one_run(backend, out, tag, env, args.agent_fake, args.kernel, args.rootfs)
        rec["warmup"] = is_warmup
        rec["phase"] = "schedule"
        runs.append(rec)
        note = rec.get("error") or f"unexpected={rec.get('unexpected_count')}"
        print(f"  [{tag}] {note} rss={rec.get('peak_rss_mb')}MB")
        if is_warmup and (rec.get("error") or rec.get("unexpected_count") not in (0, None)):
            print(f"  warmup failed for {backend}: {note}")
            hard_fail = True

    # Boundary condition: sweep the memory limit; resource-limit must stay blocked.
    sweep = [v.strip() for v in args.mem_sweep.split(",") if v.strip()]
    sweep_results = []
    for backend in args.backends:
        if backend == "baseline":
            continue  # no enforced limit by design
        for val in sweep:
            tag = f"memsweep-{backend}-{val}"
            rec = one_run(backend, out, tag, {"LIMIT_MEM": val}, args.agent_fake,
                          args.kernel, args.rootfs)
            rec["phase"] = "mem-sweep"
            rec["limit_mem"] = val
            rl = (rec.get("verdicts") or {}).get("resource-limit")
            rec["resource_limit_ok"] = (rl == "as-expected")
            runs.append(rec)
            sweep_results.append(rec)
            ok = "ok" if rec["resource_limit_ok"] else "FAIL"
            print(f"  [{tag}] resource-limit {rec.get('observed',{}).get('resource-limit')} -> {ok}")
            if not rec["resource_limit_ok"]:
                hard_fail = True

    # Aggregate per backend over measured (non-warmup, schedule) runs.
    summary = {}
    for backend in args.backends:
        mains = [r for r in runs if r["phase"] == "schedule" and not r["warmup"]
                 and r["backend"] == backend and "error" not in r]
        if not mains:
            summary[backend] = {"runs": 0, "error": "no successful runs"}
            hard_fail = True
            continue
        unexpected = [r.get("unexpected_count") for r in mains]
        secure_stable = all(u == 0 for u in unexpected)
        summary[backend] = {
            "runs": len(mains),
            "secure_stable": secure_stable,
            "unexpected_counts": unexpected,
            "peak_rss_mb_median": med([r.get("peak_rss_mb") for r in mains]),
            "peak_rss_mb_spread": spread([r.get("peak_rss_mb") for r in mains]),
            "peak_proc_median": med([r.get("peak_proc_count") for r in mains]),
            "run_ms_median": med([(r.get("timing_ms") or {}).get("run") for r in mains]),
            "host_kernel": mains[0].get("host_kernel"),
        }
        if not secure_stable:
            hard_fail = True

    meta = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backends": args.backends, "repeats": args.repeats, "warmups": args.warmups,
        "seed": args.seed, "mem_sweep": sweep, "agent_fake": args.agent_fake,
        "run_sh_sha256": hashlib.sha256(open(RUN_SH, "rb").read()).hexdigest(),
        "probe_runner_sha256": hashlib.sha256(
            open(os.path.join(REPO, "src", "common", "probe_runner.py"), "rb").read()).hexdigest(),
    }
    json.dump(meta, open(os.path.join(out, "metadata.json"), "w"), indent=2)
    with open(os.path.join(out, "runs.jsonl"), "w") as fh:
        for r in runs:
            fh.write(json.dumps(r) + "\n")
    write_report(out, summary, sweep_results, meta)
    print(f"\nwrote {out}/report.md")
    return 1 if hard_fail else 0


def write_report(out, summary, sweep_results, meta):
    L = ["# Sandbox security + footprint benchmark", "",
         f"Generated {meta['generated_utc']} · repeats={meta['repeats']} "
         f"warmups={meta['warmups']} seed={meta['seed']} "
         f"agent_fake={meta['agent_fake']}", "",
         "## Security stability and footprint (measured runs, warmups excluded)", "",
         "| backend | runs | secure-stable | peak RSS MB (median ± spread) | peak procs | run ms |",
         "|---|---|---|---|---|---|"]
    for b, s in summary.items():
        if s.get("runs", 0) == 0:
            L.append(f"| {b} | 0 | — | — | — | — |"); continue
        stable = "yes" if s["secure_stable"] else "**NO**"
        L.append(f"| {b} | {s['runs']} | {stable} | "
                 f"{s['peak_rss_mb_median']} ± {s['peak_rss_mb_spread']} | "
                 f"{s['peak_proc_median']} | {s['run_ms_median']} |")
    L += ["", "*secure-stable* = every measured run had 0 unexpected probe "
          "results. A single deviation across repeats makes it NO.", ""]
    if sweep_results:
        L += ["## Memory-limit boundary sweep", "",
              "resource-limit must stay enforced at every configured limit.", "",
              "| backend | LIMIT_MEM | resource-limit observed | verdict |",
              "|---|---|---|---|"]
        for r in sweep_results:
            L.append(f"| {r['backend']} | {r['limit_mem']} | "
                     f"{(r.get('observed') or {}).get('resource-limit')} | "
                     f"{'ok' if r.get('resource_limit_ok') else '**FAIL**'} |")
        L.append("")
    open(os.path.join(out, "report.md"), "w").write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
