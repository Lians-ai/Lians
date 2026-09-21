"""Durable mission contracts, proof discovery, and observed usage summaries."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .policy import contains_secret

MISSION_SCHEMA = "lians.mission.v1"
_MAX_CONSTRAINTS = 20
_MAX_CONSTRAINT_LENGTH = 500


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_time(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("valid_until must be an ISO 8601 date and time") from exc
    if parsed.tzinfo is None:
        raise ValueError("valid_until must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def normalize_constraints(value: str | list[str] | None) -> tuple[str, ...]:
    """Normalize line-oriented constraints without accepting nested structures."""

    if value is None:
        return ()
    if isinstance(value, str):
        items = value.splitlines()
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise ValueError("constraints must be text or a list of text values")
    normalized = tuple(dict.fromkeys(item.strip() for item in items if item.strip()))
    if len(normalized) > _MAX_CONSTRAINTS:
        raise ValueError(f"constraints cannot contain more than {_MAX_CONSTRAINTS} lines")
    if any(len(item) > _MAX_CONSTRAINT_LENGTH for item in normalized):
        raise ValueError(f"each constraint must be {_MAX_CONSTRAINT_LENGTH} characters or fewer")
    return normalized


def mission_status(mission: dict[str, Any], *, now: datetime | None = None) -> str:
    valid_until = mission.get("valid_until")
    if not valid_until:
        return "active"
    expires = datetime.fromisoformat(str(valid_until))
    return "expired" if expires <= (now or datetime.now(UTC)) else "active"


@dataclass(frozen=True)
class MissionInput:
    goal: str
    repository: Path
    constraints: tuple[str, ...] = ()
    definition_of_done: str = "The configured proof command passes."
    valid_until: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any], repository: Path) -> MissionInput:
        goal = payload.get("task")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("mission outcome is required")
        definition = payload.get("definition_of_done")
        if definition is None or (isinstance(definition, str) and not definition.strip()):
            definition = "The configured proof command passes."
        if not isinstance(definition, str):
            raise TypeError("definition_of_done must be text")
        definition = definition.strip()
        if len(definition) > 2_000:
            raise ValueError("definition_of_done exceeds 2,000 characters")
        goal = goal.strip()
        constraints = normalize_constraints(payload.get("constraints"))
        if any(contains_secret(text) for text in (goal, definition, *constraints)):
            raise ValueError(
                "mission contract appears to contain a secret; remove it before recording"
            )
        return cls(
            goal=goal,
            repository=repository.resolve(),
            constraints=constraints,
            definition_of_done=definition,
            valid_until=_parse_time(payload.get("valid_until")),
        )


class MissionLedger:
    """Append-only local mission revisions keyed by repository and stable goal."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @staticmethod
    def _mission_id(value: MissionInput) -> str:
        identity = {
            "repository": str(value.repository).casefold(),
            "goal": re.sub(r"\s+", " ", value.goal).casefold(),
        }
        return f"mission-{_sha256(identity)[:16]}"

    @staticmethod
    def _material(value: MissionInput) -> dict[str, Any]:
        return {
            "goal": value.goal,
            "repository": str(value.repository),
            "constraints": list(value.constraints),
            "definition_of_done": value.definition_of_done,
            "valid_until": value.valid_until,
        }

    def _path(self, mission_id: str) -> Path:
        return self.directory / f"{mission_id}.jsonl"

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        values: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                values.append(value)
        previous: str | None = None
        for revision, value in enumerate(values, start=1):
            digest = value.get("mission_sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("mission ledger contains an invalid digest")
            payload = {key: item for key, item in value.items() if key != "mission_sha256"}
            if _sha256(payload) != digest:
                raise ValueError("mission ledger integrity check failed")
            if value.get("revision") != revision or value.get("previous_sha256") != previous:
                raise ValueError("mission ledger revision chain is invalid")
            previous = digest
        return values

    def record(self, value: MissionInput, *, recorded_at: str | None = None) -> dict[str, Any]:
        mission_id = self._mission_id(value)
        path = self._path(mission_id)
        material = self._material(value)
        with self._lock:
            revisions = self._read(path)
            latest = revisions[-1] if revisions else None
            if latest and all(latest.get(key) == item for key, item in material.items()):
                return {**latest, "status": mission_status(latest)}
            now = recorded_at or _now_iso()
            record: dict[str, Any] = {
                "schema": MISSION_SCHEMA,
                "mission_id": mission_id,
                "revision": int(latest["revision"]) + 1 if latest else 1,
                **material,
                "created_at": latest["created_at"] if latest else now,
                "recorded_at": now,
                "as_of": now,
                "previous_sha256": latest.get("mission_sha256") if latest else None,
            }
            record["mission_sha256"] = _sha256(record)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            return {**record, "status": mission_status(record)}

    def list_latest(self, limit: int = 20) -> list[dict[str, Any]]:
        latest: list[dict[str, Any]] = []
        for path in self.directory.glob("mission-*.jsonl"):
            try:
                revisions = self._read(path)
                if revisions:
                    latest.append({**revisions[-1], "status": mission_status(revisions[-1])})
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return sorted(latest, key=lambda item: item["recorded_at"], reverse=True)[:limit]


@dataclass(frozen=True)
class ProofSuggestion:
    command: str
    label: str
    confidence: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "label": self.label,
            "confidence": self.confidence,
            "reason": self.reason,
        }


def discover_proof(repository: Path) -> list[ProofSuggestion]:
    """Suggest visible, user-confirmed verifier commands from repository markers."""

    repository = repository.resolve()
    suggestions: list[ProofSuggestion] = []
    pyproject = repository / "pyproject.toml"
    pytest_markers = (
        repository / "pytest.ini",
        repository / "conftest.py",
    )
    if any(path.is_file() for path in pytest_markers):
        suggestions.append(
            ProofSuggestion(
                "python -m pytest",
                "Python test suite",
                "high",
                "Pytest configuration was found.",
            )
        )
    elif pyproject.is_file() and (repository / "tests").is_dir():
        text = pyproject.read_text(encoding="utf-8", errors="ignore")
        command = (
            "python -m pytest"
            if "[tool.pytest" in text
            else "python -m unittest discover -s tests -v"
        )
        suggestions.append(
            ProofSuggestion(
                command,
                "Python test suite",
                "high",
                "A Python project and tests directory were found.",
            )
        )
    package_json = repository / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
            test_script = package.get("scripts", {}).get("test")
        except (OSError, ValueError, TypeError):
            test_script = None
        if (
            isinstance(test_script, str)
            and test_script.strip()
            and "no test specified" not in test_script
        ):
            manager = (
                "pnpm"
                if (repository / "pnpm-lock.yaml").is_file()
                else "yarn"
                if (repository / "yarn.lock").is_file()
                else "npm"
            )
            suggestions.append(
                ProofSuggestion(
                    f"{manager} test",
                    "JavaScript test script",
                    "high",
                    f"package.json defines a test script and {manager} matches the lockfile.",
                )
            )
    if (repository / "Cargo.toml").is_file():
        suggestions.append(
            ProofSuggestion("cargo test", "Rust test suite", "high", "Cargo.toml was found.")
        )
    if (repository / "go.mod").is_file():
        suggestions.append(
            ProofSuggestion("go test ./...", "Go test suite", "high", "go.mod was found.")
        )
    if any(repository.glob("*.sln")) or any(repository.glob("*.csproj")):
        suggestions.append(
            ProofSuggestion(
                "dotnet test", ".NET test suite", "medium", ".NET project files were found."
            )
        )
    return suggestions


def summarize_usage(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate only counters that were actually observed in local run receipts."""

    observed_runs = verified_tasks = blocked_tasks = model_calls = premium_calls = 0
    usage: dict[str, int] = {}
    seen: set[str] = set()
    for job in jobs:
        job_id = str(job.get("id", ""))
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        receipt = job.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("status") not in {"PASS", "BLOCK"}:
            continue
        observed_runs += 1
        verified_tasks += receipt["status"] == "PASS"
        blocked_tasks += receipt["status"] == "BLOCK"
        model_calls += int(receipt.get("model_calls", 0))
        premium_calls += int(receipt.get("premium_calls", 0))
        for key, amount in receipt.get("usage_observed", {}).items():
            if isinstance(key, str) and type(amount) is int and amount >= 0:
                usage[key] = usage.get(key, 0) + amount
    return {
        "observed_runs": observed_runs,
        "verified_tasks": verified_tasks,
        "blocked_tasks": blocked_tasks,
        "model_calls": model_calls,
        "premium_calls": premium_calls,
        "usage_observed": usage,
        "claim_boundary": "Observed local receipts only; this is not provider quota or billing data.",
    }
