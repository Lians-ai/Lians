"""Deterministic verification receipts for agent-generated repository work.

Lians does not generate code here and it does not ask a model to grade itself.
It binds a durable task contract to an explicit file scope, inspects the local
Git diff with fixed commands, checks traceability and current state, and signs
the resulting evidence. External test results remain clearly marked as caller
attestations until a sandboxed runner or trusted CI attestation is available.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .formal_proof import (
    FiniteModelProofChecker,
    FormalProofError,
    PythonFiniteFunctionProofChecker,
)
from .project import Project
from .state_integrity import StateIntegrityService
from .store import MemoryStore
from .task_contract import TaskContractService

_CHECK_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@{}^~:+-]{0,199}$")
_HEX_256 = re.compile(r"^[a-f0-9]{64}$")
_MAX_GIT_OUTPUT = 12 * 1024 * 1024
_MAX_UNTRACKED_BYTES = 2 * 1024 * 1024
_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("anthropic_key", re.compile(r"sk-ant-api[0-9A-Za-z_-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[0-9A-Za-z_-]{24,}")),
    ("github_token", re.compile(r"(?:ghp_|github_pat_)[0-9A-Za-z_]{24,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    (
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
            r"\s*[:=]\s*['\"][^'\"\r\n]{12,}['\"]"
        ),
    ),
)
_RISK_PATTERNS = (
    ("shell_execution", re.compile(r"\bshell\s*=\s*True\b")),
    ("dynamic_eval", re.compile(r"\b(?:eval|exec)\s*\(")),
    ("tls_verification_disabled", re.compile(r"\bverify\s*=\s*False\b")),
    ("unsafe_html", re.compile(r"\bdangerouslySetInnerHTML\b")),
)
_ADVISORY_PATTERNS = (
    re.compile(r"(?i)\b(?:did not|didn't) touch\b"),
    re.compile(r"(?i)\bleaving (?:it |this |these )?untouched\b"),
    re.compile(r"(?i)\bone thing (?:i noticed|still on your side)\b"),
    re.compile(r"(?i)\byou should be aware\b"),
    re.compile(r"(?i)\b(?:caught|noticed),? but\b"),
    re.compile(r"(?i)\bwon't want to leave hanging\b"),
)


class VerificationError(ValueError):
    """A verification contract or repository could not be inspected safely."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _clean_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    rendered = " ".join(value.strip().split())
    if not rendered:
        raise VerificationError(f"{field} cannot be blank")
    if len(rendered) > maximum:
        raise VerificationError(f"{field} must be {maximum} characters or fewer")
    return rendered


def _clean_check_id(value: Any, *, field: str) -> str:
    rendered = _clean_text(value, field=field, maximum=64).casefold()
    if not _CHECK_ID.fullmatch(rendered):
        raise VerificationError(f"{field} must use lowercase letters, numbers, _ or -")
    return rendered


def _clean_glob(value: Any, *, field: str) -> str:
    rendered = _clean_text(value, field=field, maximum=500).replace("\\", "/")
    if (
        rendered.startswith(("/", "!"))
        or re.match(r"^[A-Za-z]:", rendered)
        or any(part == ".." for part in rendered.split("/"))
        or "\x00" in rendered
    ):
        raise VerificationError(f"{field} must be a repository-relative glob")
    return rendered.removeprefix("./")


def _clean_repo_path(value: str) -> str:
    rendered = value.replace("\\", "/")
    if (
        not rendered
        or rendered.startswith("/")
        or re.match(r"^[A-Za-z]:", rendered)
        or any(part == ".." for part in rendered.split("/"))
        or "\x00" in rendered
    ):
        raise VerificationError("Git returned an unsafe repository path")
    return rendered


def _matches(path: str, patterns: list[str]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.endswith("/**") and path == pattern[:-3].rstrip("/"))
        for pattern in patterns
    )


def _policy_key(task_id: str) -> str:
    return f"tasks/{task_id}/verification-policy"


def _receipt_key(task_id: str) -> str:
    return f"tasks/{task_id}/verification-receipt"


def _safe_git_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return environment


def _git(root: Path, arguments: list[str], *, timeout: float = 15.0) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", *arguments],
            cwd=root,
            env=_safe_git_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError("Git inspection could not finish safely") from exc
    if len(result.stdout) > _MAX_GIT_OUTPUT or len(result.stderr) > 256_000:
        raise VerificationError("Git inspection output exceeded its safety limit")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise VerificationError(f"Git inspection failed: {detail or 'unknown error'}")
    return result.stdout


def _repository_root(project: Project) -> Path:
    root = project.trusted_root
    if root is None:
        raise VerificationError("Verification requires the trusted launched repository")
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VerificationError("Repository root is unavailable") from exc
    if not resolved.is_dir() or not (resolved / ".git").exists():
        raise VerificationError("Verification requires a Git repository")
    return resolved


def _base_commit(root: Path, base_ref: str) -> str:
    if not _SAFE_REF.fullmatch(base_ref) or base_ref.startswith("-"):
        raise VerificationError("base_ref is invalid")
    return _git(root, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])[
        :80
    ].decode("ascii", errors="strict").strip()


def _name_status(root: Path, base_ref: str) -> list[dict[str, Any]]:
    raw = _git(root, ["diff", "--name-status", "-z", "--find-renames", base_ref, "--"])
    fields = raw.decode("utf-8", errors="replace").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if index >= len(fields):
            raise VerificationError("Git returned an incomplete name-status record")
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise VerificationError("Git returned an incomplete rename record")
            previous, path = fields[index], fields[index + 1]
            index += 2
        else:
            previous, path = None, fields[index]
            index += 1
        normalized = _clean_repo_path(path)
        changes.append(
            {
                "path": normalized,
                "previous_path": _clean_repo_path(previous) if previous else None,
                "status": status,
                "tracked": True,
            }
        )
    return changes


def _untracked(root: Path) -> list[dict[str, Any]]:
    raw = _git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    paths = raw.decode("utf-8", errors="replace").split("\0")
    return [
        {
            "path": _clean_repo_path(path),
            "previous_path": None,
            "status": "?",
            "tracked": False,
        }
        for path in paths
        if path
    ]


def _numstat(root: Path, base_ref: str) -> dict[str, tuple[int | None, int | None]]:
    raw = _git(root, ["diff", "--numstat", "--no-renames", base_ref, "--"])
    result: dict[str, tuple[int | None, int | None]] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        pieces = line.split("\t", 2)
        if len(pieces) != 3:
            continue
        added, deleted, path = pieces
        result[path.replace("\\", "/")] = (
            int(added) if added.isdigit() else None,
            int(deleted) if deleted.isdigit() else None,
        )
    return result


def _tracked_added_text(root: Path, base_ref: str) -> tuple[str, dict[str, str]]:
    raw = _git(
        root,
        ["diff", "--no-ext-diff", "--no-color", "--unified=0", base_ref, "--"],
    )
    rendered = raw.decode("utf-8", errors="replace")
    by_file: dict[str, list[str]] = {}
    current: str | None = None
    for line in rendered.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:].replace("\\", "/")
            by_file.setdefault(current, [])
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            by_file[current].append(line[1:])
    return rendered, {path: "\n".join(lines) for path, lines in by_file.items()}


def _untracked_text(root: Path, paths: list[str]) -> tuple[dict[str, str], list[str]]:
    texts: dict[str, str] = {}
    skipped: list[str] = []
    total = 0
    for relative in paths:
        candidate = root / relative
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            size = resolved.stat().st_size
        except (OSError, RuntimeError, ValueError):
            skipped.append(relative)
            continue
        if resolved.is_symlink() or not resolved.is_file() or size > 1_000_000:
            skipped.append(relative)
            continue
        total += size
        if total > _MAX_UNTRACKED_BYTES:
            skipped.append(relative)
            continue
        try:
            texts[relative] = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped.append(relative)
    return texts, skipped


def _scan_findings(text_by_file: dict[str, str]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    secrets: list[dict[str, str]] = []
    risks: list[dict[str, str]] = []
    for path, text in text_by_file.items():
        for detector, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                secrets.append({"file": path, "detector": detector})
        for detector, pattern in _RISK_PATTERNS:
            if pattern.search(text):
                risks.append({"file": path, "detector": detector})
    return secrets, risks


def _advisory_count(value: str) -> int:
    return sum(len(pattern.findall(value)) for pattern in _ADVISORY_PATTERNS)


class VerificationService:
    """Bind task intent to repository evidence and signed review receipts."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.tasks = TaskContractService(store)

    def _current_memory(self, key: str, *, project_id: str) -> dict[str, Any] | None:
        history = self.store.memory_history(
            key,
            scope="project",
            project_id=project_id,
            limit=500,
        )
        return next((item for item in reversed(history) if item["is_current"]), None)

    def configure(
        self,
        task_id: str,
        *,
        project_id: str,
        allowed_paths: list[str],
        criterion_paths: dict[str, list[str]],
        required_checks: list[str] | None = None,
        forbidden_terms: list[str] | None = None,
        formal_proofs: list[dict[str, Any]] | None = None,
        max_changed_files: int = 500,
        max_advisories: int = 1,
        client: str = "mcp",
    ) -> dict[str, Any]:
        task = self.tasks.status(task_id, project_id=project_id)
        criterion_ids = {item["id"] for item in task["contract"]["success_criteria"]}
        if not isinstance(allowed_paths, list) or not allowed_paths or len(allowed_paths) > 100:
            raise VerificationError("allowed_paths must contain 1-100 repository globs")
        clean_allowed = list(
            dict.fromkeys(
                _clean_glob(value, field=f"allowed_paths[{index}]")
                for index, value in enumerate(allowed_paths)
            )
        )
        if not isinstance(criterion_paths, dict) or not criterion_paths:
            raise VerificationError("criterion_paths must map criteria to repository globs")
        unknown = sorted(set(criterion_paths) - criterion_ids)
        if unknown:
            raise VerificationError(f"Unknown criterion path mappings: {', '.join(unknown)}")
        clean_mapping: dict[str, list[str]] = {}
        total_patterns = 0
        for criterion_id, patterns in criterion_paths.items():
            if not isinstance(patterns, list) or not patterns or len(patterns) > 50:
                raise VerificationError(f"criterion_paths.{criterion_id} must contain 1-50 globs")
            clean_mapping[criterion_id] = list(
                dict.fromkeys(
                    _clean_glob(value, field=f"criterion_paths.{criterion_id}[{index}]")
                    for index, value in enumerate(patterns)
                )
            )
            total_patterns += len(clean_mapping[criterion_id])
        if total_patterns > 200:
            raise VerificationError("criterion path mappings are too large")

        checks = []
        for index, value in enumerate(required_checks or []):
            check_id = _clean_check_id(value, field=f"required_checks[{index}]")
            if check_id not in checks:
                checks.append(check_id)
        if len(checks) > 20:
            raise VerificationError("required_checks must contain 20 items or fewer")
        terms = []
        for index, value in enumerate(forbidden_terms or []):
            term = _clean_text(value, field=f"forbidden_terms[{index}]", maximum=80).casefold()
            if term not in terms:
                terms.append(term)
        if len(terms) > 50:
            raise VerificationError("forbidden_terms must contain 50 items or fewer")
        proof_obligations: list[dict[str, str]] = []
        proof_ids: set[str] = set()
        if not isinstance(formal_proofs or [], list) or len(formal_proofs or []) > 10:
            raise VerificationError("formal_proofs must contain 10 items or fewer")
        for index, value in enumerate(formal_proofs or []):
            if not isinstance(value, dict) or set(value) != {"id", "backend", "manifest"}:
                raise VerificationError(
                    f"formal_proofs[{index}] must contain only id, backend, and manifest"
                )
            proof_id = _clean_check_id(value["id"], field=f"formal_proofs[{index}].id")
            if proof_id in proof_ids:
                raise VerificationError(f"Duplicate formal proof: {proof_id}")
            proof_ids.add(proof_id)
            backend = _clean_text(
                value["backend"], field=f"formal_proofs[{index}].backend", maximum=80
            ).casefold()
            if backend not in {
                FiniteModelProofChecker.backend,
                PythonFiniteFunctionProofChecker.backend,
            }:
                raise VerificationError("Unsupported formal proof backend")
            manifest = _clean_glob(
                value["manifest"], field=f"formal_proofs[{index}].manifest"
            )
            if any(character in manifest for character in "*?[]{}"):
                raise VerificationError("Formal proof manifests must be concrete files")
            proof_obligations.append(
                {"id": proof_id, "backend": backend, "manifest": manifest}
            )
        if type(max_changed_files) is not int or not 1 <= max_changed_files <= 2_000:
            raise VerificationError("max_changed_files must be an integer from 1 to 2000")
        if type(max_advisories) is not int or not 0 <= max_advisories <= 5:
            raise VerificationError("max_advisories must be an integer from 0 to 5")

        policy = {
            "schema": "https://lians.ai/schemas/verification-policy/v0.1",
            "type": "verification_policy",
            "task_id": task["task_id"],
            "allowed_paths": clean_allowed,
            "criterion_paths": clean_mapping,
            "required_checks": checks,
            "forbidden_terms": terms,
            "formal_proofs": proof_obligations,
            "max_changed_files": max_changed_files,
            "max_advisories": max_advisories,
            "external_check_trust": "caller_attested",
            "updated_at": _now(),
        }
        item = self.store.set_current(
            _policy_key(task["task_id"]),
            json.dumps(policy, ensure_ascii=False, sort_keys=True),
            source="verification-policy",
            topic=f"task:{task['task_id']}",
            metadata={"lians_type": "verification_policy", "task_id": task["task_id"]},
            kind="verification_policy",
            scope="project",
            project_id=project_id,
            source_client=_clean_text(client, field="client", maximum=80),
            reason="verification policy configured",
        )
        StateIntegrityService(self.store).link(
            task["lineage"]["contract_memory_id"],
            item["id"],
            dependent_type="memory",
            downstream_memory_id=item["id"],
            project_id=project_id,
            label="Verification policy",
            relation="governs",
            provenance="verification-policy",
        )
        return {"policy": policy, "memory_id": item["id"], "task": task}

    def policy(self, task_id: str, *, project_id: str) -> dict[str, Any]:
        task = self.tasks.status(task_id, project_id=project_id)
        item = self._current_memory(_policy_key(task["task_id"]), project_id=project_id)
        if item is None:
            raise LookupError("Verification policy was not configured for this task")
        try:
            policy = json.loads(item["content"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise VerificationError("Stored verification policy is invalid") from exc
        if not isinstance(policy, dict) or policy.get("type") != "verification_policy":
            raise VerificationError("Stored verification policy is invalid")
        return {"policy": policy, "memory_id": item["id"], "task": task}

    @staticmethod
    def _check_results(values: Any) -> list[dict[str, Any]]:
        if values is None:
            return []
        if not isinstance(values, list) or len(values) > 20:
            raise VerificationError("check_results must be a list of 20 items or fewer")
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise TypeError(f"check_results[{index}] must be an object")
            unknown = set(value) - {
                "name",
                "status",
                "evidence",
                "command",
                "exit_code",
                "output_sha256",
            }
            if unknown:
                raise VerificationError(
                    f"check_results[{index}] has unknown fields: {', '.join(sorted(unknown))}"
                )
            name = _clean_check_id(value.get("name"), field=f"check_results[{index}].name")
            if name in seen:
                raise VerificationError(f"Duplicate check result: {name}")
            seen.add(name)
            status = str(value.get("status") or "").strip().casefold()
            if status not in {"passed", "failed"}:
                raise VerificationError("check result status must be passed or failed")
            evidence = _clean_text(
                value.get("evidence"),
                field=f"check_results[{index}].evidence",
                maximum=2_000,
            )
            command = value.get("command")
            if command is not None:
                command = _clean_text(
                    command,
                    field=f"check_results[{index}].command",
                    maximum=1_000,
                )
            exit_code = value.get("exit_code")
            if exit_code is not None and type(exit_code) is not int:
                raise TypeError("check result exit_code must be an integer")
            if status == "passed" and exit_code not in {None, 0}:
                raise VerificationError("A passed check cannot have a nonzero exit code")
            output_sha256 = value.get("output_sha256")
            if output_sha256 is not None:
                output_sha256 = str(output_sha256).strip().casefold()
                if not _HEX_256.fullmatch(output_sha256):
                    raise VerificationError("check result output_sha256 must be lowercase SHA-256")
            secret_findings, _ = _scan_findings(
                {"check_result": "\n".join(part for part in (command, evidence) if part)}
            )
            if secret_findings:
                raise VerificationError(
                    "check result appears to contain credential material; supply only a redacted "
                    "summary and an output hash"
                )
            results.append(
                {
                    "name": name,
                    "status": status,
                    "evidence": evidence,
                    "command": command,
                    "exit_code": exit_code,
                    "output_sha256": output_sha256,
                    "trust": "caller_attested",
                }
            )
        return results

    def verify(
        self,
        task_id: str,
        *,
        project: Project,
        base_ref: str = "HEAD",
        agent_summary: str,
        check_results: list[dict[str, Any]] | None = None,
        client: str = "mcp",
    ) -> dict[str, Any]:
        configured = self.policy(task_id, project_id=project.id)
        policy = configured["policy"]
        task = configured["task"]
        root = _repository_root(project)
        base = _clean_text(base_ref, field="base_ref", maximum=200)
        base_commit = _base_commit(root, base)
        summary = _clean_text(agent_summary, field="agent_summary", maximum=20_000)
        checks = self._check_results(check_results)

        changes = _name_status(root, base)
        tracked_paths = {item["path"] for item in changes}
        for item in _untracked(root):
            if item["path"] not in tracked_paths:
                changes.append(item)
        if len(changes) > policy["max_changed_files"]:
            raise VerificationError("Changed file count exceeds the verification policy")
        statistics = _numstat(root, base)
        tracked_diff, text_by_file = _tracked_added_text(root, base)
        untracked_paths = [item["path"] for item in changes if not item["tracked"]]
        untracked_text, skipped_untracked = _untracked_text(root, untracked_paths)
        text_by_file.update(untracked_text)
        try:
            diff_check = subprocess.run(
                ["git", "-c", "core.quotepath=false", "diff", "--check", base, "--"],
                cwd=root,
                env=_safe_git_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VerificationError("Git whitespace inspection could not finish safely") from exc
        if len(diff_check.stdout) > 1_000_000 or len(diff_check.stderr) > 256_000:
            raise VerificationError("Git whitespace check output exceeded its safety limit")
        untracked_whitespace = [
            path
            for path, text in untracked_text.items()
            if any(line.endswith((" ", "\t")) for line in text.splitlines())
        ]

        mapping = policy["criterion_paths"]
        scope_violations: list[str] = []
        unmapped: list[str] = []
        conflicts: list[str] = []
        for item in changes:
            path = item["path"]
            item["additions"], item["deletions"] = statistics.get(path, (None, None))
            item["criteria"] = [
                criterion_id
                for criterion_id, patterns in mapping.items()
                if _matches(path, patterns)
            ]
            if not _matches(path, policy["allowed_paths"]):
                scope_violations.append(path)
            if not item["criteria"]:
                unmapped.append(path)
            if str(item["status"]).startswith("U"):
                conflicts.append(path)

        secret_findings, risk_findings = _scan_findings(text_by_file)
        forbidden_hits = [
            {"term": term, "location": "agent_summary"}
            for term in policy["forbidden_terms"]
            if term in summary.casefold()
        ]
        for path, text in text_by_file.items():
            lowered = text.casefold()
            forbidden_hits.extend(
                {"term": term, "location": path}
                for term in policy["forbidden_terms"]
                if term in lowered
            )
        advisory_count = _advisory_count(summary)
        assessment = task["assessment"]
        open_invalidations = StateIntegrityService(self.store).invalidation_count(
            project_id=project.id
        )
        supplied_checks = {item["name"]: item for item in checks}
        missing_checks = [
            name for name in policy["required_checks"] if name not in supplied_checks
        ]
        failed_checks = [
            item["name"] for item in checks if item["status"] == "failed"
        ]

        blockers: list[dict[str, Any]] = []

        def block(code: str, message: str, **details: Any) -> None:
            blockers.append({"code": code, "message": message, **details})

        proof_results: list[dict[str, Any]] = []
        configured_proofs = policy.get("formal_proofs") or []
        for obligation in configured_proofs:
            try:
                proof_checker = (
                    PythonFiniteFunctionProofChecker()
                    if obligation["backend"] == PythonFiniteFunctionProofChecker.backend
                    else FiniteModelProofChecker()
                )
                proof = proof_checker.verify(root, obligation["manifest"])
            except (FormalProofError, OSError, ValueError) as exc:
                proof_results.append(
                    {
                        "id": obligation["id"],
                        "backend": obligation["backend"],
                        "manifest": obligation["manifest"],
                        "status": "error",
                        "error": str(exc)[:500],
                    }
                )
                block(
                    "formal_proof_error",
                    "A configured formal proof could not be checked",
                    proof_id=obligation["id"],
                )
                continue
            proof["id"] = obligation["id"]
            proof_results.append(proof)
            if proof["status"] != "proved":
                block(
                    "formal_proof_disproved",
                    "A configured formal property has a counterexample",
                    proof_id=obligation["id"],
                )

        if not changes:
            block("no_changes", "No repository changes were available to verify")
        if len(changes) > policy["max_changed_files"]:
            block("change_limit", "Changed file count exceeds policy")
        if scope_violations:
            block(
                "scope_violation",
                "Changes extend beyond the approved file scope",
                file_count=len(scope_violations),
                files=scope_violations[:50],
            )
        if unmapped:
            block(
                "unmapped_changes",
                "Changes are not mapped to a success criterion",
                file_count=len(unmapped),
                files=unmapped[:50],
            )
        if conflicts:
            block(
                "merge_conflict",
                "Unmerged files remain",
                file_count=len(conflicts),
                files=conflicts[:50],
            )
        if diff_check.returncode != 0 or untracked_whitespace:
            block(
                "diff_integrity",
                "Git whitespace or conflict-marker checks failed",
                file_count=len(untracked_whitespace),
                files=untracked_whitespace[:50],
            )
        if skipped_untracked:
            block(
                "uninspected_untracked",
                "Some untracked files could not be inspected within safety limits",
                file_count=len(skipped_untracked),
                files=skipped_untracked[:50],
            )
        if secret_findings:
            block(
                "secret_detected",
                "Potential credential material appears in added content",
                finding_count=len(secret_findings),
                findings=secret_findings[:100],
            )
        if forbidden_hits:
            block(
                "forbidden_language",
                "The work violates the configured terminology policy",
                finding_count=len(forbidden_hits),
                findings=forbidden_hits[:100],
            )
        if advisory_count > policy["max_advisories"]:
            block(
                "advisory_overload",
                "The agent response exceeds the unresolved-advisory limit",
                count=advisory_count,
                maximum=policy["max_advisories"],
            )
        if assessment["missing_criteria"]:
            block(
                "missing_evidence",
                "Success criteria still lack recorded evidence",
                criteria=assessment["missing_criteria"],
            )
        if assessment["failed_constraints"]:
            block(
                "failed_constraint",
                "A task constraint failed",
                constraints=assessment["failed_constraints"],
            )
        if assessment["unknown_constraints"]:
            block(
                "unknown_constraint",
                "A task constraint has not been checked",
                constraints=assessment["unknown_constraints"],
            )
        if assessment["blockers"]:
            block("task_blocker", "The task still has explicit blockers", blockers=assessment["blockers"])
        if open_invalidations:
            block(
                "stale_state",
                "Current-state changes left dependent work requiring review",
                invalidation_count=open_invalidations,
            )
        if missing_checks:
            block(
                "missing_check",
                "Required verification checks were not supplied",
                checks=missing_checks,
            )
        if failed_checks:
            block("failed_check", "One or more verification checks failed", checks=failed_checks)

        changed_summary = [
            {
                "path": item["path"],
                "previous_path": item["previous_path"],
                "status": item["status"],
                "tracked": item["tracked"],
                "additions": item["additions"],
                "deletions": item["deletions"],
                "criteria": item["criteria"],
            }
            for item in changes
        ]
        untracked_hashes = {
            path: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for path, text in untracked_text.items()
        }
        diff_digest = hashlib.sha256(
            tracked_diff.encode("utf-8") + _canonical(untracked_hashes)
        ).hexdigest()
        changed_files_digest = hashlib.sha256(_canonical(changed_summary)).hexdigest()
        changed_file_sample = changed_summary[:50]
        contract_digest = hashlib.sha256(_canonical(task["contract"])).hexdigest()
        state_digest = (
            hashlib.sha256(_canonical(task["state"])).hexdigest()
            if task["state"] is not None
            else None
        )
        policy_digest = hashlib.sha256(_canonical(policy)).hexdigest()
        verdict = "blocked" if blockers else "ready_for_human_ship_review"
        receipt_id = str(uuid.uuid4())
        payload = {
            "schema": "https://lians.ai/schemas/verification-receipt/v0.1",
            "type": "verification_receipt",
            "id": receipt_id,
            "task_id": task["task_id"],
            "project_id": project.id,
            "repository": {
                "name": project.name,
                "origin": project.origin,
                "base_ref": base,
                "base_commit": base_commit,
                "diff_sha256": diff_digest,
                "changed_files_sha256": changed_files_digest,
            },
            "verdict": verdict,
            "intent": {
                "task_contract_sha256": contract_digest,
                "task_state_sha256": state_digest,
                "verification_policy_sha256": policy_digest,
                "contract_memory_id": task["lineage"]["contract_memory_id"],
                "state_memory_id": task["lineage"]["state_memory_id"],
                "verification_policy_memory_id": configured["memory_id"],
            },
            "changed_file_count": len(changed_summary),
            "changed_files": changed_file_sample,
            "omitted_changed_file_count": max(
                0, len(changed_summary) - len(changed_file_sample)
            ),
            "task_assessment": {
                "status": assessment["status"],
                "missing_criteria": assessment["missing_criteria"],
                "failed_constraints": assessment["failed_constraints"],
                "unknown_constraints": assessment["unknown_constraints"],
            },
            "checks": checks,
            "formal_proofs": proof_results,
            "deterministic_checks": {
                "scope": not scope_violations,
                "criterion_traceability": not unmapped,
                "diff_integrity": diff_check.returncode == 0 and not untracked_whitespace,
                "secret_scan": not secret_findings,
                "current_state": open_invalidations == 0,
                "communication_contract": not forbidden_hits
                and advisory_count <= policy["max_advisories"],
                "formal_proofs": (
                    all(item.get("status") == "proved" for item in proof_results)
                    if configured_proofs
                    else None
                ),
            },
            "risk_finding_count": len(risk_findings),
            "risk_findings": risk_findings[:100],
            "blockers": blockers,
            "advisory_count": advisory_count,
            "agent_summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest(),
            "trust": {
                "git_inspection": "lians_measured",
                "task_state": "lians_stored",
                "external_checks": "caller_attested",
                "formal_model": (
                    "proved_by_exhaustive_enumeration"
                    if configured_proofs
                    and all(item.get("status") == "proved" for item in proof_results)
                    else "not_proved"
                    if configured_proofs
                    else "not_configured"
                ),
                "source_to_model_refinement_proven": bool(proof_results)
                and all(
                    item.get("trust", {}).get("source_to_model_refinement_proven") is True
                    for item in proof_results
                ),
                "bounded_implementation_correctness_proven": bool(proof_results)
                and all(
                    item.get("trust", {}).get("bounded_implementation_correctness_proven")
                    is True
                    for item in proof_results
                ),
                "implementation_correctness_formally_proven": False,
                "semantic_correctness_formally_proven": False,
                "human_ship_decision_required": True,
            },
            "created_at": _now(),
            "client": _clean_text(client, field="client", maximum=80),
        }
        payload["signature"] = self.store.cipher.sign(_canonical(payload))
        memory = self.store.set_current(
            _receipt_key(task["task_id"]),
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            source="verification-receipt",
            topic=f"task:{task['task_id']}",
            metadata={
                "lians_type": "verification_receipt",
                "task_id": task["task_id"],
                "verdict": verdict,
            },
            kind="verification_receipt",
            scope="project",
            project_id=project.id,
            source_client=client,
            reason="new repository verification receipt",
        )
        integrity = StateIntegrityService(self.store)
        for upstream_id, label in (
            (task["lineage"]["contract_memory_id"], "Task intent"),
            (configured["memory_id"], "Verification policy"),
        ):
            integrity.link(
                upstream_id,
                memory["id"],
                dependent_type="memory",
                downstream_memory_id=memory["id"],
                project_id=project.id,
                label=f"Verification receipt from {label}",
                relation="verifies",
                provenance="verification-engine",
            )
        return {
            "verdict": verdict,
            "may_claim_completion": verdict == "ready_for_human_ship_review",
            "may_claim_safe_to_ship": False,
            "may_claim_declared_model_proved": bool(configured_proofs)
            and all(item.get("status") == "proved" for item in proof_results),
            "may_claim_bounded_implementation_proved": bool(proof_results)
            and all(
                item.get("trust", {}).get("bounded_implementation_correctness_proven")
                is True
                for item in proof_results
            ),
            "may_claim_implementation_correct": False,
            "blockers": blockers,
            "risk_findings": risk_findings,
            "receipt": payload,
            "memory_id": memory["id"],
            "message": (
                "Ready for human ship review with signed evidence."
                if verdict == "ready_for_human_ship_review"
                else f"Blocked by {len(blockers)} verification gate(s)."
            ),
        }

    def status(self, task_id: str, *, project_id: str) -> dict[str, Any]:
        task = self.tasks.status(task_id, project_id=project_id)
        policy = self._current_memory(_policy_key(task["task_id"]), project_id=project_id)
        receipt = self._current_memory(_receipt_key(task["task_id"]), project_id=project_id)
        document = None
        if receipt is not None:
            try:
                document = json.loads(receipt["content"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise VerificationError("Stored verification receipt is invalid") from exc
            if not isinstance(document, dict) or document.get("type") != "verification_receipt":
                raise VerificationError("Stored verification receipt is invalid")
        return {
            "task_id": task["task_id"],
            "project_id": project_id,
            "configured": policy is not None,
            "policy_memory_id": policy["id"] if policy else None,
            "receipt": document,
            "receipt_memory_id": receipt["id"] if receipt else None,
            "task_assessment": task["assessment"],
        }
