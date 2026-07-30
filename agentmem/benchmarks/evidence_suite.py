"""Run the offline Lians evidence gates and emit one reproducible receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

AGENTMEM_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AGENTMEM_ROOT.parent

GATES = {
    "supersession": {
        "module": "benchmarks.supersession_eval",
        "claim": "Rule-based relation classification stays correct on the frozen pair set.",
        "metric": "accuracy",
    },
    "point_in_time": {
        "module": "benchmarks.finance_bench",
        "claim": "As-of recall returns the value that was valid at the requested time.",
        "metric": "accuracy",
    },
    "regulated_invariants": {
        "module": "benchmarks.regulated_eval",
        "claim": "The local engine satisfies the five published regulated-memory invariants.",
        "metric": "checks",
    },
    "riad_1": {
        "module": "benchmarks.decision_reconstruction_eval",
        "claim": "Decision reconstruction, provenance, evidence hashing, OTLP, and tamper detection execute end to end.",
        "metric": "checks",
    },
    "memory_engine": {
        "module": "benchmarks.memory_engine_eval",
        "claim": "Typed compilation, serving modes, temporal recall, and content-addressed receipts execute end to end.",
        "metric": "checks",
    },
}


def _git_value(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value or None


def _parse_metrics(name: str, stdout: str) -> dict:
    if name == "supersession":
        match = re.search(r"overall_accuracy\s*=\s*([0-9.]+)\s*\((\d+)/(\d+)\)", stdout)
        return {
            "accuracy": float(match.group(1)),
            "passed": int(match.group(2)),
            "total": int(match.group(3)),
        } if match else {}
    if name == "point_in_time":
        match = re.search(r"point_in_time_accuracy\s*=\s*([0-9.]+)\s*\((\d+)/(\d+)\)", stdout)
        return {
            "accuracy": float(match.group(1)),
            "passed": int(match.group(2)),
            "total": int(match.group(3)),
        } if match else {}
    if name == "regulated_invariants":
        match = re.search(r"Regulated memory eval:\s*(\d+)/(\d+)", stdout)
        return {
            "passed": int(match.group(1)),
            "total": int(match.group(2)),
        } if match else {}
    if name == "riad_1":
        start = stdout.find("{")
        if start >= 0:
            report = json.loads(stdout[start:])
            return {
                "passed": report["passed"],
                "total": report["total"],
                **report.get("metrics", {}),
            }
    if name == "memory_engine":
        start = stdout.find("{")
        if start >= 0:
            report = json.loads(stdout[start:])
            return {
                "passed": report["passed"],
                "total": report["total"],
                **report.get("metrics", {}),
            }
    return {}


def _run_gate(name: str, definition: dict) -> dict:
    env = os.environ.copy()
    for secret in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "VOYAGE_API_KEY",
        "STRIPE_API_KEY",
    ):
        env.pop(secret, None)
    env.update({
        "PYTHONPATH": os.pathsep.join([
            str(AGENTMEM_ROOT / "src"),
            str(AGENTMEM_ROOT / "sdk" / "python"),
        ]),
        "EMBEDDING_PROVIDER": "local",
        "AGENTMEM_ALLOW_UNENCRYPTED": "true",
        "KMS_PROVIDER": "env",
        "MASTER_ENCRYPTION_KEY": "",
    })
    command = [sys.executable, "-m", definition["module"]]
    started = time.perf_counter()
    result = subprocess.run(
        command,
        cwd=AGENTMEM_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    combined = result.stdout + result.stderr
    return {
        "name": name,
        "claim": definition["claim"],
        "command": command,
        "status": "passed" if result.returncode == 0 else "failed",
        "return_code": result.returncode,
        "duration_ms": duration_ms,
        "metrics": _parse_metrics(name, result.stdout),
        "stdout_sha256": hashlib.sha256(combined.encode()).hexdigest(),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(GATES),
        help="Run only the named gate. Repeat to select more than one.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=AGENTMEM_ROOT / "results" / "evidence-suite-latest.json",
    )
    parser.add_argument("--list", action="store_true", help="List gate names and exit.")
    args = parser.parse_args()
    if args.list:
        for name, definition in GATES.items():
            print(f"{name}: {definition['claim']}")
        return 0

    selected = args.only or list(GATES)
    gates = [_run_gate(name, GATES[name]) for name in selected]
    receipt = {
        "suite": "lians-offline-evidence-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "commit": _git_value("rev-parse", "HEAD"),
        "worktree_dirty": bool(_git_value("status", "--porcelain")),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "network_or_paid_judges": False,
            "embedding_provider": "local-test-stub",
        },
        "methodology": {
            "gold_answer_tuning": False,
            "baseline_correctness_tuning": False,
            "scoring": "deterministic",
            "scope": "local functional gates, not a production load test",
        },
        "gates": gates,
        "passed": sum(gate["status"] == "passed" for gate in gates),
        "total": len(gates),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(f"Offline evidence suite: {receipt['passed']}/{receipt['total']} gates passed")
    print(f"Receipt: {args.out}")
    for gate in gates:
        print(f"  {gate['status'].upper():6} {gate['name']} {gate['metrics']}")
    return 0 if receipt["passed"] == receipt["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
