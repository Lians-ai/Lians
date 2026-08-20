"""GitHub Actions evidence that can cross the Lians completion boundary.

The workflow artifact is useful only after GitHub CLI verifies its build
provenance, exact signer workflow, source ref, and source commit. Lians then
imports the selected passing checks into the local encrypted task state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .project import detect_project
from .store import MemoryStore
from .task_contract import TaskContractService, workspace_snapshot

_CHECK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"^[a-f0-9]{40}$")
_MAX_EVIDENCE_BYTES = 1_000_000


class CIEvidenceError(ValueError):
    """A CI evidence artifact is unsafe, invalid, or not trusted."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_id(value: str) -> str:
    rendered = str(value).strip().casefold()
    if not _CHECK_ID.fullmatch(rendered):
        raise CIEvidenceError("CI check ids must use lowercase letters, numbers, _ or -")
    return rendered


def emit_github_actions_evidence(
    output: Path,
    *,
    checks: Sequence[str],
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Write a bounded check record from inside a GitHub-hosted Actions run."""

    values = dict(os.environ if environment is None else environment)
    if values.get("GITHUB_ACTIONS") != "true":
        raise CIEvidenceError("CI evidence can only be emitted inside GitHub Actions")
    repository = values.get("GITHUB_REPOSITORY", "")
    commit_sha = values.get("GITHUB_SHA", "").casefold()
    if not _REPOSITORY.fullmatch(repository) or not _SHA.fullmatch(commit_sha):
        raise CIEvidenceError("GitHub repository or commit identity is invalid")
    normalized_checks = list(dict.fromkeys(_check_id(value) for value in checks))
    if not normalized_checks or len(normalized_checks) > 50:
        raise CIEvidenceError("CI evidence must contain between 1 and 50 checks")
    document = {
        "schema": "https://lians.ai/schemas/github-actions-evidence/v0.1",
        "type": "github_actions_evidence",
        "repository": repository,
        "commit_sha": commit_sha,
        "ref": values.get("GITHUB_REF", ""),
        "workflow_ref": values.get("GITHUB_WORKFLOW_REF", ""),
        "run_id": values.get("GITHUB_RUN_ID", ""),
        "run_attempt": values.get("GITHUB_RUN_ATTEMPT", ""),
        "event_name": values.get("GITHUB_EVENT_NAME", ""),
        "checks": [
            {"id": check_id, "status": "passed"} for check_id in normalized_checks
        ],
        "created_at": _now(),
    }
    encoded = (json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise CIEvidenceError("CI evidence exceeds its safety limit")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    return document


def _read_document(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        if path.is_symlink() or not path.is_file():
            raise CIEvidenceError("CI evidence must be a regular file")
        encoded = path.read_bytes()
    except OSError as exc:
        raise CIEvidenceError("CI evidence could not be read") from exc
    if not encoded or len(encoded) > _MAX_EVIDENCE_BYTES:
        raise CIEvidenceError("CI evidence is empty or exceeds its safety limit")
    try:
        document = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CIEvidenceError("CI evidence is not valid JSON") from exc
    if not isinstance(document, dict):
        raise CIEvidenceError("CI evidence must be a JSON object")
    required = {
        "schema",
        "type",
        "repository",
        "commit_sha",
        "ref",
        "workflow_ref",
        "run_id",
        "run_attempt",
        "event_name",
        "checks",
        "created_at",
    }
    if set(document) != required:
        raise CIEvidenceError("CI evidence fields do not match the supported schema")
    if (
        document.get("schema")
        != "https://lians.ai/schemas/github-actions-evidence/v0.1"
        or document.get("type") != "github_actions_evidence"
        or not _REPOSITORY.fullmatch(str(document.get("repository") or ""))
        or not _SHA.fullmatch(str(document.get("commit_sha") or "").casefold())
    ):
        raise CIEvidenceError("CI evidence identity is invalid")
    checks = document.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > 50:
        raise CIEvidenceError("CI evidence checks are invalid")
    for item in checks:
        if (
            not isinstance(item, dict)
            or set(item) != {"id", "status"}
            or item.get("status") != "passed"
        ):
            raise CIEvidenceError("CI evidence contains a non-passing or invalid check")
        _check_id(str(item.get("id") or ""))
    return document, encoded


def verify_github_attestation(
    path: Path,
    *,
    repository: str,
    signer_workflow: str,
    source_ref: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Verify provenance with GitHub CLI and return the bounded evidence record."""

    document, encoded = _read_document(path)
    if document["repository"].casefold() != repository.casefold():
        raise CIEvidenceError("CI evidence belongs to another repository")
    if not _REPOSITORY.fullmatch(repository):
        raise CIEvidenceError("repository must use the owner/name form")
    if not signer_workflow.startswith(f"{repository}/.github/workflows/"):
        raise CIEvidenceError("signer workflow must be an exact workflow in the repository")
    if document["ref"] != source_ref:
        raise CIEvidenceError("CI evidence source ref does not match the trusted ref")
    if document["workflow_ref"] != f"{signer_workflow}@{source_ref}":
        raise CIEvidenceError("CI evidence workflow identity does not match the trusted signer")
    try:
        result = runner(
            [
                "gh",
                "attestation",
                "verify",
                str(path),
                "--repo",
                repository,
                "--signer-workflow",
                signer_workflow,
                "--source-digest",
                document["commit_sha"],
                "--source-ref",
                source_ref,
                "--deny-self-hosted-runners",
                "--format",
                "json",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CIEvidenceError("GitHub attestation verification could not run") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise CIEvidenceError(
            f"GitHub rejected the CI attestation: {detail or 'verification failed'}"
        )
    try:
        verification = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CIEvidenceError("GitHub returned an invalid attestation result") from exc
    if not isinstance(verification, list) or not verification:
        raise CIEvidenceError("GitHub returned no verified attestation")
    return {
        "document": document,
        "artifact_sha256": hashlib.sha256(encoded).hexdigest(),
        "verification_count": len(verification),
        "signer_workflow": signer_workflow,
        "source_ref": source_ref,
    }


def import_github_actions_evidence(
    path: Path,
    *,
    repository: str,
    signer_workflow: str,
    source_ref: str,
    task_id: str,
    criterion_ids: Sequence[str],
    check_ids: Sequence[str],
    project_root: Path,
    store: MemoryStore,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Import selected attested checks as measured CI task evidence."""

    verified = verify_github_attestation(
        path,
        repository=repository,
        signer_workflow=signer_workflow,
        source_ref=source_ref,
        runner=runner,
    )
    document = verified["document"]
    selected_checks = list(dict.fromkeys(_check_id(value) for value in check_ids))
    available = {item["id"] for item in document["checks"]}
    missing = [value for value in selected_checks if value not in available]
    if not selected_checks or missing:
        raise CIEvidenceError(
            "Selected CI checks are missing: " + ", ".join(missing or ["none selected"])
        )
    project = detect_project(project_root)
    try:
        measured_root = project_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CIEvidenceError("project root could not be resolved") from exc
    workspace = workspace_snapshot(measured_root)
    if (
        workspace.get("status") != "measured_local"
        or workspace.get("head") != document["commit_sha"]
    ):
        raise CIEvidenceError("CI evidence does not match the current Git HEAD")
    source = f"github-actions:{repository}:{document['run_id']}"
    evidence = [
        {
            "criterion_id": str(criterion_id),
            "evidence": "GitHub Actions passed: " + ", ".join(selected_checks),
            "trust_class": "measured_ci",
            "source": source,
        }
        for criterion_id in dict.fromkeys(str(value) for value in criterion_ids)
    ]
    if not evidence:
        raise CIEvidenceError("At least one criterion id is required")
    result = TaskContractService(store)._checkpoint_trusted(
        task_id,
        "Imported attested GitHub Actions evidence",
        issuer="github_actions_attestation",
        receipt_sha256=verified["artifact_sha256"],
        project_id=project.id,
        evidence=evidence,
        client="github-actions",
        source_ref=source,
        event_time=document["created_at"],
        workspace=workspace,
    )
    return {"task": result, "attestation": verified}
