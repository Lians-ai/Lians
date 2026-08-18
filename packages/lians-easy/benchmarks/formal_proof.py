"""Correctness and scale pressure test for the finite-model proof checker."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from lians_easy.formal_proof import (
    FiniteModelProofChecker,
    FormalProofError,
    PythonFiniteFunctionProofChecker,
)


def _write_model(root: Path, *, values: list[int], delta: int) -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "proofs").mkdir(parents=True, exist_ok=True)
    (root / "src" / "successor.py").write_text(
        "def successor(value):\n    return value + 1\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "schema": "https://lians.ai/schemas/finite-proof/v0.1",
        "scope": "The modeled successor is strictly greater than every bounded input.",
        "variables": {"input": values},
        "definitions": {
            "output": {
                "op": "add",
                "args": [{"var": "input"}, {"const": delta}],
            }
        },
        "assumptions": {"const": True},
        "claims": [
            {
                "id": "successor-increases",
                "description": "Output is greater than input.",
                "expression": {
                    "op": "gt",
                    "left": {"var": "output"},
                    "right": {"var": "input"},
                },
            }
        ],
        "source_bindings": ["src/successor.py"],
    }
    path = root / "proofs" / "successor.proof.json"
    path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def run_benchmark(*, scenarios: int = 40, scale_domain: int = 50) -> dict[str, Any]:
    checker = FiniteModelProofChecker()
    valid = 0
    invalid = 0
    invalid_detected = 0
    counterexamples = 0
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="lians-formal-proof-") as temporary:
        root = Path(temporary)
        for index in range(scenarios):
            scenario = root / f"scenario-{index}"
            should_prove = index % 2 == 0
            _write_model(
                scenario,
                values=list(range(-10, 11)),
                delta=1 if should_prove else -1,
            )
            result = checker.verify(scenario, "proofs/successor.proof.json")
            if should_prove:
                valid += result["status"] == "proved"
            else:
                invalid += 1
                if result["status"] == "disproved":
                    invalid_detected += 1
                    if result["claims"][0]["counterexample"] is not None:
                        counterexamples += 1

        vacuous_root = root / "vacuous"
        path = _write_model(vacuous_root, values=list(range(10)), delta=1)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["assumptions"] = {
            "op": "lt",
            "left": {"var": "input"},
            "right": {"const": 0},
        }
        path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        vacuous_rejected = False
        try:
            checker.verify(vacuous_root, "proofs/successor.proof.json")
        except FormalProofError:
            vacuous_rejected = True

        scale_root = root / "scale"
        (scale_root / "src").mkdir(parents=True)
        (scale_root / "proofs").mkdir()
        (scale_root / "src" / "sum.py").write_text(
            "def total(x, y, z):\n    return x + y + z\n",
            encoding="utf-8",
            newline="\n",
        )
        domain = list(range(scale_domain))
        scale_manifest = {
            "schema": "https://lians.ai/schemas/finite-proof/v0.1",
            "scope": "The sum of three nonnegative bounded integers is nonnegative.",
            "variables": {"x": domain, "y": domain, "z": domain},
            "definitions": {
                "total": {
                    "op": "add",
                    "args": [{"var": "x"}, {"var": "y"}, {"var": "z"}],
                }
            },
            "claims": [
                {
                    "id": "sum-nonnegative",
                    "description": "The modeled sum is nonnegative.",
                    "expression": {
                        "op": "ge",
                        "left": {"var": "total"},
                        "right": {"const": 0},
                    },
                }
            ],
            "source_bindings": ["src/sum.py"],
        }
        (scale_root / "proofs" / "sum.proof.json").write_text(
            json.dumps(scale_manifest), encoding="utf-8", newline="\n"
        )
        scale_started = time.perf_counter()
        scale = checker.verify(scale_root, "proofs/sum.proof.json")
        scale_ms = (time.perf_counter() - scale_started) * 1_000

        first_hash = scale["source_bindings"][0]["sha256"]
        (scale_root / "src" / "sum.py").write_text(
            "def total(x, y, z):\n    return x + y - z\n",
            encoding="utf-8",
            newline="\n",
        )
        rebound = checker.verify(scale_root, "proofs/sum.proof.json")
        source_mutation_rebound = rebound["source_bindings"][0]["sha256"] != first_hash

        python_root = root / "python-scale"
        (python_root / "src").mkdir(parents=True)
        (python_root / "proofs").mkdir()
        python_source = python_root / "src" / "sum.py"
        python_source.write_text(
            "def total(x, y, z):\n    return x + y + z\n",
            encoding="utf-8",
            newline="\n",
        )
        python_manifest = {
            "schema": "https://lians.ai/schemas/python-function-proof/v0.1",
            "scope": "The actual restricted sum function returns a nonnegative value.",
            "source": "src/sum.py",
            "function": "total",
            "variables": {"x": domain, "y": domain, "z": domain},
            "assumptions": {"const": True},
            "claims": [
                {
                    "id": "sum-nonnegative",
                    "description": "The actual function result is nonnegative.",
                    "expression": {
                        "op": "ge",
                        "left": {"var": "result"},
                        "right": {"const": 0},
                    },
                }
            ],
        }
        (python_root / "proofs" / "sum.python-proof.json").write_text(
            json.dumps(python_manifest), encoding="utf-8", newline="\n"
        )
        python_checker = PythonFiniteFunctionProofChecker()
        python_started = time.perf_counter()
        python_scale = python_checker.verify(
            python_root, "proofs/sum.python-proof.json"
        )
        python_ms = (time.perf_counter() - python_started) * 1_000
        python_source.write_text(
            "def total(x, y, z):\n    return x + y - z\n",
            encoding="utf-8",
            newline="\n",
        )
        python_regression = python_checker.verify(
            python_root, "proofs/sum.python-proof.json"
        )
        python_counterexample_found = python_regression["status"] == "disproved"
        python_source.write_text(
            "def total(x, y, z):\n    return dangerous(x, y, z)\n",
            encoding="utf-8",
            newline="\n",
        )
        unsafe_source_rejected = False
        try:
            python_checker.verify(python_root, "proofs/sum.python-proof.json")
        except FormalProofError:
            unsafe_source_rejected = True

    valid_scenarios = (scenarios + 1) // 2
    report = {
        "schema": "https://lians.ai/schemas/formal-proof-benchmark/v0.1",
        "correctness": {
            "scenarios": scenarios,
            "valid_proof_rate": valid / max(1, valid_scenarios),
            "false_claim_detection_recall": invalid_detected / max(1, invalid),
            "counterexample_rate": counterexamples / max(1, invalid),
            "vacuous_proof_rejected": vacuous_rejected,
            "source_mutation_changes_binding": source_mutation_rebound,
            "python_source_counterexample_found": python_counterexample_found,
            "unsafe_python_source_rejected": unsafe_source_rejected,
        },
        "scale": {
            "state_space": scale["model"]["state_space"],
            "checked_assignments": scale["model"]["satisfying_assignments"],
            "enumeration_complete": scale["model"]["enumeration_complete"],
            "verification_ms": round(scale_ms, 3),
            "single_digit_seconds": scale_ms < 9_000,
            "project_code_executed": scale["trust"]["project_code_executed"],
        },
        "python_scale": {
            "state_space": python_scale["model"]["state_space"],
            "checked_assignments": python_scale["model"]["satisfying_assignments"],
            "enumeration_complete": python_scale["model"]["enumeration_complete"],
            "verification_ms": round(python_ms, 3),
            "single_digit_seconds": python_ms < 9_000,
            "project_code_executed": python_scale["trust"]["project_code_executed"],
            "bounded_implementation_correctness_proven": python_scale["trust"][
                "bounded_implementation_correctness_proven"
            ],
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1_000, 3),
    }
    report["pass"] = bool(
        report["correctness"]["valid_proof_rate"] == 1.0
        and report["correctness"]["false_claim_detection_recall"] == 1.0
        and report["correctness"]["counterexample_rate"] == 1.0
        and vacuous_rejected
        and source_mutation_rebound
        and python_counterexample_found
        and unsafe_source_rejected
        and scale["status"] == "proved"
        and report["scale"]["single_digit_seconds"]
        and not report["scale"]["project_code_executed"]
        and python_scale["status"] == "proved"
        and report["python_scale"]["single_digit_seconds"]
        and not report["python_scale"]["project_code_executed"]
        and report["python_scale"]["bounded_implementation_correctness_proven"]
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=int, default=40)
    parser.add_argument("--scale-domain", type=int, default=50)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = run_benchmark(
        scenarios=max(2, arguments.scenarios),
        scale_domain=max(2, min(arguments.scale_domain, 50)),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
