"""Privacy-bounded evidence exports for the external proof-gate beta."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

BETA_REPORT_SCHEMA = "lians.finish.beta-report.v1"
BETA_IDENTITY_SCHEMA = "lians.finish.beta-identity.v1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _identity_path(data_dir: Path) -> Path:
    return data_dir.resolve() / "beta-identity.json"


def _valid_identity(value: Any) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("schema") == BETA_IDENTITY_SCHEMA
        and isinstance(value.get("participant_id"), str)
        and re.fullmatch(r"beta-[0-9a-f]{12}", value["participant_id"])
        and isinstance(value.get("pair_key"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["pair_key"])
    )


def load_or_create_beta_identity(data_dir: Path) -> dict[str, str]:
    """Return a random local study identity without collecting a real identity."""

    path = _identity_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not _valid_identity(value):
            raise ValueError("the local beta identity file is invalid")
        return value

    value = {
        "schema": BETA_IDENTITY_SCHEMA,
        "participant_id": f"beta-{secrets.token_hex(6)}",
        "pair_key": secrets.token_hex(32),
    }
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except FileExistsError:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if not _valid_identity(existing):
            raise ValueError("the local beta identity file is invalid")
        return existing


def repository_snapshot(repository: Path) -> dict[str, Any]:
    """Capture a non-content Git fingerprint for matched-run validation."""

    repository = repository.resolve()
    try:
        head = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--verify", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain=v1", "-z"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"git_head": None, "git_state_sha256": None, "git_clean": None}
    candidate = head.stdout.strip().lower()
    if head.returncode != 0 or re.fullmatch(r"[0-9a-f]{40,64}", candidate) is None:
        return {"git_head": None, "git_state_sha256": None, "git_clean": None}
    if status.returncode != 0:
        return {"git_head": candidate, "git_state_sha256": None, "git_clean": None}
    return {
        "git_head": candidate,
        "git_state_sha256": hashlib.sha256(status.stdout).hexdigest(),
        "git_clean": not bool(status.stdout),
    }


def create_beta_context(
    data_dir: Path,
    repository: Path,
    *,
    task: str,
    constraints: Sequence[str],
    definition_of_done: str,
    verifier_command: Sequence[str],
    product_version: str,
) -> dict[str, Any]:
    """Bind a run to a private matched-task key and its initial Git state."""

    identity = load_or_create_beta_identity(data_dir)
    snapshot = repository_snapshot(repository)
    material = {
        "task": " ".join(task.split()),
        "constraints": [" ".join(constraint.split()) for constraint in constraints],
        "definition_of_done": " ".join(definition_of_done.split()),
        "verifier_command": list(verifier_command),
        "product_version": product_version,
        **snapshot,
    }
    pair_sha256 = hmac.new(
        bytes.fromhex(identity["pair_key"]),
        _canonical(material),
        hashlib.sha256,
    ).hexdigest()
    return {
        "participant_id": identity["participant_id"],
        "task_pair_sha256": pair_sha256,
        "product_version": product_version,
        **snapshot,
    }


def _private_run_id(pair_key: str, job_id: str) -> str:
    return hmac.new(
        bytes.fromhex(pair_key),
        job_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:20]


def _safe_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: amount
        for key, amount in sorted(value.items())
        if isinstance(key, str) and type(amount) is int and amount >= 0
    }


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        by_pair.setdefault(run["task_pair_sha256"], []).append(run)

    matched: list[dict[str, Any]] = []
    incomparable_pairs = 0
    incomplete_usage_pairs = 0
    for pair_id, items in sorted(by_pair.items()):
        protect = [item for item in items if item["mode"] == "protect"]
        maximum = [item for item in items if item["mode"] == "maximum"]
        if len(protect) != 1 or len(maximum) != 1:
            continue
        low = protect[0]
        high = maximum[0]
        if (
            low["execution_bridge"] == "unknown"
            or low["execution_bridge"] != high["execution_bridge"]
        ):
            incomparable_pairs += 1
            continue
        if not low["usage_complete"] or not high["usage_complete"]:
            incomplete_usage_pairs += 1
            continue
        matched.append(
            {
                "task_pair_sha256": pair_id,
                "execution_bridge": low["execution_bridge"],
                "protect_status": low["status"],
                "maximum_status": high["status"],
                "protect_premium_calls": low["premium_calls"],
                "maximum_premium_calls": high["premium_calls"],
                "protect_model_calls": low["model_calls"],
                "maximum_model_calls": high["model_calls"],
                "both_verified": low["status"] == high["status"] == "PASS",
            }
        )

    protect_premium = sum(item["protect_premium_calls"] for item in matched)
    maximum_premium = sum(item["maximum_premium_calls"] for item in matched)
    reduction = None
    if maximum_premium:
        reduction = round((maximum_premium - protect_premium) / maximum_premium * 100, 2)
    return {
        "eligible_runs": len(runs),
        "matched_pairs": len(matched),
        "incomparable_bridge_pairs": incomparable_pairs,
        "incomplete_usage_pairs": incomplete_usage_pairs,
        "verified_matched_pairs": sum(item["both_verified"] for item in matched),
        "protect_premium_calls": protect_premium,
        "maximum_premium_calls": maximum_premium,
        "premium_call_reduction_percent": reduction,
        "pairs": matched,
    }


def build_beta_report(
    data_dir: Path,
    jobs: list[dict[str, Any]],
    *,
    product_version: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Create a shareable report with no task, path, prompt, or model output."""

    identity = load_or_create_beta_identity(data_dir)
    runs: list[dict[str, Any]] = []
    excluded = 0
    for job in jobs:
        receipt = job.get("receipt")
        context = job.get("beta_context")
        route = job.get("route")
        if not (
            isinstance(receipt, dict)
            and receipt.get("status") in {"PASS", "BLOCK"}
            and isinstance(context, dict)
            and context.get("participant_id") == identity["participant_id"]
            and isinstance(context.get("task_pair_sha256"), str)
            and isinstance(route, dict)
        ):
            excluded += 1
            continue
        stages = receipt.get("stages") if isinstance(receipt.get("stages"), list) else []
        runs.append(
            {
                "run_id": _private_run_id(identity["pair_key"], str(job.get("id", ""))),
                "task_pair_sha256": context["task_pair_sha256"],
                "product_version": context.get("product_version", product_version),
                "mode": route.get("mode"),
                "risk": route.get("risk"),
                "status": receipt["status"],
                "execution_bridge": (
                    job.get("execution_bridge")
                    if job.get("execution_bridge") in {"codex_app_server", "codex_exec"}
                    else "unknown"
                ),
                "started_at": receipt.get("started_at"),
                "finished_at": receipt.get("finished_at"),
                "duration_seconds": job.get("duration_seconds"),
                "git_head": context.get("git_head"),
                "git_state_sha256": context.get("git_state_sha256"),
                "git_clean": context.get("git_clean"),
                "model_calls": int(receipt.get("model_calls", 0)),
                "premium_calls": int(receipt.get("premium_calls", 0)),
                "premium_attempts": int(receipt.get("premium_attempts", 0)),
                "max_premium_calls": int(receipt.get("max_premium_calls", 0)),
                "models": [
                    stage.get("model")
                    for stage in stages
                    if isinstance(stage, dict) and isinstance(stage.get("model"), str)
                ],
                "usage_complete": bool(stages)
                and all(
                    isinstance(stage, dict) and bool(_safe_usage(stage.get("usage")))
                    for stage in stages
                ),
                "usage_observed": _safe_usage(receipt.get("usage_observed")),
                "verification_attempts": len(receipt.get("verification", [])),
                "verifier_command_sha256": receipt.get("verifier_command_sha256"),
                "receipt_sha256": receipt.get("receipt_sha256"),
            }
        )
    runs.sort(key=lambda item: (str(item.get("started_at")), item["run_id"]))
    report: dict[str, Any] = {
        "schema": BETA_REPORT_SCHEMA,
        "generated_at": generated_at or _now_iso(),
        "product_version": product_version,
        "participant_id": identity["participant_id"],
        "privacy": (
            "Contains no task text, constraints, repository path, prompt, model output, "
            "verification output, provider credential, local job or Codex thread id, "
            "or provider billing data."
        ),
        "claim_boundary": (
            "This report summarizes observed local receipts. Matched external results are "
            "required before claiming quality parity or usage savings."
        ),
        "excluded_legacy_or_incomplete_runs": excluded,
        "runs": runs,
        "aggregate": _aggregate(runs),
    }
    report["report_sha256"] = _sha256(report)
    return report


def beta_report_filename(participant_id: str) -> str:
    if re.fullmatch(r"beta-[0-9a-f]{12}", participant_id) is None:
        raise ValueError("invalid beta participant id")
    return f"lians-proof-gate-{participant_id}.json"
