#!/usr/bin/env python3
"""
Crestbrick agent eval harness.

Reads evals/<agent>/cases.jsonl, invokes each case via the `claude` CLI in
bypass-permissions mode, validates the response against expected_contains /
expected_excludes / max_duration_ms, and writes
evals/results/<YYYY-MM-DD>.jsonl plus a Telegram summary.

Exits non-zero if any case fails.

Usage:
    python3 evals/run.py                  # all targets
    python3 evals/run.py developer        # one target
    python3 evals/run.py developer afford # subset
    python3 evals/run.py --no-telegram    # skip Telegram summary
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"
LOGS_DIR = EVALS_DIR / "_logs"

# Targets are either agents (kind=agent, default) or skills (kind=skill).
# A directory under evals/<name>/ holds cases.jsonl. Each case may carry a
# "kind" field; if absent it defaults to "agent" — except for the `afford`
# folder which is the skill replacement for the deprecated affordability-modeler.
TARGETS = [
    "chief-of-staff",
    "chief-operating-officer",
    "developer",
    "security",
    "financial-advisor",
    "seo-content-agent",
    "competitor-scanner",
    "market-scout",
    "property-researcher",
    "afford",  # skill (replaces affordability-modeler agent)
]

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
PER_CASE_HARD_TIMEOUT_S = 240  # walltime ceiling regardless of case max


def build_invocation_prompt(target: str, case: dict) -> str:
    """
    Build the prompt sent to claude -p.

    For agents we ask Claude to use the named subagent. For skills (kind=skill,
    e.g. /afford), we let the case input speak for itself — it should already
    contain the slash command.
    """
    kind = case.get("kind", "agent")
    user_input = case["input"]
    if kind == "skill":
        return user_input
    return f"Use the {target} agent: {user_input}"


def invoke_agent(target: str, case: dict) -> dict[str, Any]:
    """
    Run `claude -p --permission-mode bypassPermissions <prompt>` and return a
    dict with rc, stdout, stderr, duration_ms. On timeout, rc is set to 124.
    """
    prompt = build_invocation_prompt(target, case)
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--permission-mode", "bypassPermissions",
        prompt,
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PER_CASE_HARD_TIMEOUT_S,
        )
        rc = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as e:
        rc = 124
        stdout = (e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or "")
        stderr = (e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or "")
        stderr = (stderr or "") + f"\n[harness] timed out after {PER_CASE_HARD_TIMEOUT_S}s"
    duration_ms = int((time.monotonic() - t0) * 1000)
    return {"rc": rc, "stdout": stdout, "stderr": stderr, "duration_ms": duration_ms}


def evaluate(case: dict, result: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if result["rc"] != 0:
        failures.append(f"non-zero rc: {result['rc']}")
    stdout = result["stdout"]
    lower = stdout.lower()
    for kw in case.get("expected_contains", []):
        if kw.lower() not in lower:
            failures.append(f"missing expected_contains: {kw!r}")
    for kw in case.get("expected_excludes", []):
        if kw.lower() in lower:
            failures.append(f"hit expected_excludes: {kw!r}")
    max_ms = case.get("max_duration_ms")
    if max_ms is not None and result["duration_ms"] > max_ms:
        failures.append(f"duration {result['duration_ms']}ms > max_duration_ms {max_ms}ms")
    return (len(failures) == 0, failures)


def load_cases(target: str) -> list[dict]:
    path = EVALS_DIR / target / "cases.jsonl"
    if not path.exists():
        return []
    cases = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as e:
            print(f"  ! {target}/cases.jsonl line {i}: invalid JSON: {e}", file=sys.stderr)
    return cases


def post_telegram_summary(total: int, passed: int, regressions: list[str]) -> None:
    """
    Best-effort Telegram summary via the local bot daemon helper.
    Looks for ~/.claude/bin/tg-send.sh; silently skips if absent.
    """
    helper = Path.home() / ".claude" / "bin" / "tg-send.sh"
    if not helper.exists():
        print("[telegram] tg-send.sh not found; skipping summary")
        return
    regressions_str = ", ".join(regressions) if regressions else "none"
    msg = f"Agent evals: pass={passed}/{total}, regressions: [{regressions_str}]"
    try:
        subprocess.run([str(helper), msg], check=False, timeout=15)
    except Exception as e:
        print(f"[telegram] send failed: {e}", file=sys.stderr)


def main(argv: list[str]) -> int:
    args = argv[1:]
    send_telegram = True
    if "--no-telegram" in args:
        send_telegram = False
        args = [a for a in args if a != "--no-telegram"]

    selected = args if args else TARGETS
    unknown = [a for a in selected if a not in TARGETS]
    if unknown:
        print(f"Unknown target(s): {unknown}. Known: {TARGETS}", file=sys.stderr)
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results_path = RESULTS_DIR / f"{today}.jsonl"

    total = 0
    failed = 0
    regressions: list[str] = []

    with results_path.open("w") as out:
        for target in selected:
            cases = load_cases(target)
            if not cases:
                print(f"[skip] {target}: no cases")
                continue
            print(f"[run]  {target}: {len(cases)} case(s)")
            for case in cases:
                total += 1
                case_id = case.get("id", "?")
                try:
                    result = invoke_agent(target, case)
                    invoke_error = None
                except Exception as e:
                    result = {"rc": -1, "stdout": "", "stderr": repr(e), "duration_ms": 0}
                    invoke_error = repr(e)

                if invoke_error:
                    passed_case, failures = False, [f"invoke error: {invoke_error}"]
                else:
                    passed_case, failures = evaluate(case, result)

                if not passed_case:
                    failed += 1
                    regressions.append(f"{target}/{case_id}")

                # Persist full stdout/stderr to a log sidecar for debugging.
                log_file = LOGS_DIR / f"{today}-{target}-{case_id}.log"
                log_file.write_text(
                    f"=== prompt ===\n{build_invocation_prompt(target, case)}\n\n"
                    f"=== rc ===\n{result['rc']}\n\n"
                    f"=== duration_ms ===\n{result['duration_ms']}\n\n"
                    f"=== stdout ===\n{result['stdout']}\n\n"
                    f"=== stderr ===\n{result['stderr']}\n"
                )

                record = {
                    "agent": target,
                    "kind": case.get("kind", "agent"),
                    "id": case_id,
                    "passed": passed_case,
                    "rc": result["rc"],
                    "duration_ms": result["duration_ms"],
                    "failures": failures,
                    "response_preview": result["stdout"][:500],
                    "log_file": str(log_file),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                out.write(json.dumps(record) + "\n")
                out.flush()

                marker = "PASS" if passed_case else "FAIL"
                detail = "" if passed_case else f" :: {'; '.join(failures)}"
                print(f"  {marker} {target}/{case_id} ({result['duration_ms']}ms){detail}")

    print(f"\nResults: {total - failed}/{total} passed -> {results_path}")
    if send_telegram:
        post_telegram_summary(total, total - failed, regressions)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
