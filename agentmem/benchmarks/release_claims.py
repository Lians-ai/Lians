"""Evaluate which public claim level the available evidence permits."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "benchmarks" / "release_gate_policy.json"
DEFAULT_SUITE = ROOT / "results" / "evidence-suite-latest.json"

ORDER = ("foundation_verified", "production_validated", "competitive_leader")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMPETITIVE_EVIDENCE = {
    "agent_memory_benchmark_submission",
    "longmemeval_v2_lafs_submission",
    "independent_reproduction",
    # Short names keep this validator reusable in unit and downstream policies.
    "benchmark",
    "leaderboard",
    "independent",
}


def valid_external_evidence(name: str, value: object) -> bool:
    """Accept an external gate only when it points to an auditable artifact."""
    if not isinstance(value, dict):
        return False
    if value.get("schema") != "lians.evidence.v1" or value.get("passed") is not True:
        return False
    if not _SHA256.fullmatch(str(value.get("artifact_sha256", ""))):
        return False
    if not str(value.get("generated_at", "")).strip():
        return False
    if not str(value.get("methodology", "")).strip():
        return False
    if name in _COMPETITIVE_EVIDENCE:
        if not str(value.get("independent_party", "")).strip():
            return False
        if not str(value.get("source_url", "")).startswith("https://"):
            return False
    return True


def evaluate_claims(policy: dict, suite: dict, external: dict | None = None) -> dict:
    external = external or {}
    local = {
        gate["name"]: gate.get("status") == "passed"
        for gate in suite.get("gates", [])
    }
    evidence = {
        **local,
        **{
            name: valid_external_evidence(name, value)
            for name, value in external.items()
        },
    }

    foundation_requirements = policy["claim_levels"]["foundation_verified"][
        "required_local_gates"
    ]
    foundation = all(evidence.get(name, False) for name in foundation_requirements)
    production_requirements = [
        item for item in policy["claim_levels"]["production_validated"]["requires"]
        if item != "foundation_verified"
    ]
    production = foundation and all(
        evidence.get(name, False) for name in production_requirements
    )
    leader_requirements = [
        item for item in policy["claim_levels"]["competitive_leader"]["requires"]
        if item != "production_validated"
    ]
    leader = production and all(
        evidence.get(name, False) for name in leader_requirements
    )
    states = {
        "foundation_verified": foundation,
        "production_validated": production,
        "competitive_leader": leader,
    }
    achieved = "unverified"
    for level in ORDER:
        if states[level]:
            achieved = level

    missing = {
        "foundation_verified": [
            name for name in foundation_requirements if not evidence.get(name, False)
        ],
        "production_validated": [
            name for name in production_requirements if not evidence.get(name, False)
        ],
        "competitive_leader": [
            name for name in leader_requirements if not evidence.get(name, False)
        ],
    }
    public_language = {
        "unverified": "No performance or leadership claim is permitted.",
        "foundation_verified": (
            "Lians passes its reproducible local temporal, governance, and "
            "memory-engine contract suite."
        ),
        "production_validated": (
            "Lians has validated its governed memory engine under production-like "
            "load, isolation, recovery, and failure conditions."
        ),
        "competitive_leader": (
            "Lians leads the independently reproducible accuracy-latency-cost "
            "frontier covered by the attached benchmark submissions."
        ),
    }[achieved]
    return {
        "schema": "lians.claim-evidence.v1",
        "achieved_level": achieved,
        "states": states,
        "missing": missing,
        "invalid_external_evidence": sorted(
            name
            for name, value in external.items()
            if not valid_external_evidence(name, value)
        ),
        "permitted_public_language": public_language,
        "best_claim_permitted": leader,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--external-evidence", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--require", choices=ORDER)
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    external = (
        json.loads(args.external_evidence.read_text(encoding="utf-8"))
        if args.external_evidence
        else {}
    )
    result = evaluate_claims(policy, suite, external)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not args.require:
        return 0
    return 0 if result["states"][args.require] else 1


if __name__ == "__main__":
    raise SystemExit(main())
