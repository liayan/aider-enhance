#!/usr/bin/env python3
"""Assemble the full agent trajectory for a run into trajectory.json.

Sources (agent mode only):
  - hostside/gateway-trace.jsonl : per-turn request messages, completion, usage
  - collected/.aider.chat.history.md : aider's record of edits applied + commands
  - collected/acceptance.log + .acceptance_rc : did the real task actually pass
  - collected/probes.json : what the agent/probes could reach under the boundary

Produces a compact, presentable trajectory: an ordered list of turns with token
cost and the assistant's action, plus a summary (turns, tokens, task graded).

Usage: trajectory.py <run_dir>
"""
import json
import os
import re
import sys


def read_jsonl(path):
    out = []
    if os.path.exists(path):
        for line in open(path):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def summarize_message(content):
    if not content:
        return ""
    content = re.sub(r"\s+", " ", content).strip()
    return content[:200] + ("…" if len(content) > 200 else "")


def edits_from_chat_history(path):
    """Best-effort: count files aider said it edited + shell commands it ran."""
    edits, commands = [], []
    if not os.path.exists(path):
        return edits, commands
    text = open(path, encoding="utf-8", errors="replace").read()
    # aider marks applied edits like "Applied edit to src/app.py"
    edits = sorted(set(re.findall(r"Applied edit to (\S+)", text)))
    # and logs run commands after a "> " or "Running " prefix (version-dependent)
    commands = re.findall(r"^Running (.+)$", text, re.MULTILINE)
    return edits, commands


def main(run_dir):
    collected = os.path.join(run_dir, "collected")
    meta_path = os.path.join(collected, "metadata.json")
    meta = json.load(open(meta_path)) if os.path.exists(meta_path) else {}
    if meta.get("run_mode") != "agent":
        # nothing to assemble for emulate/baseline runs
        return 0

    trace = read_jsonl(os.path.join(run_dir, "hostside", "gateway-trace.jsonl"))
    if not trace:
        trace = read_jsonl(os.path.join(collected, "gateway-trace.jsonl"))

    turns = []
    tot_prompt = tot_completion = 0
    for rec in trace:
        usage = (rec.get("response") or {}).get("usage") or {}
        tot_prompt += usage.get("prompt_tokens", 0)
        tot_completion += usage.get("completion_tokens", 0)
        msgs = (rec.get("request") or {}).get("messages") or []
        last_user = next((m.get("content") for m in reversed(msgs)
                          if m.get("role") == "user"), "")
        choices = (rec.get("response") or {}).get("choices") or []
        assistant = ""
        if choices:
            assistant = (choices[0].get("message") or {}).get("content", "")
        turns.append({
            "turn": rec.get("turn"),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "user_preview": summarize_message(last_user),
            "assistant_preview": summarize_message(assistant),
        })

    edits, commands = edits_from_chat_history(
        os.path.join(collected, ".aider.chat.history.md"))

    acc_rc_path = os.path.join(collected, ".acceptance_rc")
    acc_rc = None
    if os.path.exists(acc_rc_path):
        acc_rc = int(open(acc_rc_path).read().strip() or "-1")

    model_name = next((t.get("request", {}).get("model") for t in trace
                       if t.get("request", {}).get("model")), meta.get("model"))
    traj = {
        "backend": meta.get("backend"),
        "model": model_name,
        "summary": {
            "turns": len(turns),
            "prompt_tokens": tot_prompt,
            "completion_tokens": tot_completion,
            "total_tokens": tot_prompt + tot_completion,
            "files_edited": edits,
            "commands_run": commands,
            "real_task_passed": (acc_rc == 0) if acc_rc is not None else None,
        },
        "turns": turns,
    }
    out = os.path.join(collected, "trajectory.json")
    json.dump(traj, open(out, "w"), indent=2)
    s = traj["summary"]
    print(f"  trajectory: {s['turns']} turns, {s['total_tokens']} tokens, "
          f"edited {s['files_edited'] or 'nothing'}, "
          f"real task passed: {s['real_task_passed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
