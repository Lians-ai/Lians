"""Load a bounded synthetic or explicitly de-identified homelab scenario."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = "https://lians.ai/schemas/homelab-sample/v1"
DEIDENTIFIED_ACK = "I_CONFIRM_THIS_SAMPLE_IS_DEIDENTIFIED"
MAX_FILE_BYTES = 64 * 1024
MAX_MEMORIES = 10
MAX_METADATA_FIELDS = 20

TOP_LEVEL_FIELDS = {
    "$schema",
    "classification",
    "scenario_id",
    "agent_id",
    "decision_type",
    "subject_id",
    "query",
    "outcome",
    "reason_codes",
    "recall_filters",
    "expected_marker",
    "memories",
}
MEMORY_FIELDS = {
    "idempotency_key",
    "content",
    "event_time",
    "source",
    "metadata",
    "importance",
}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(email|phone|address|ssn|social_security|account_number|routing_number|"
    r"api_key|password|secret|token|credit_card|card_number|customer_name|borrower_name)(?:$|[_-])",
    re.IGNORECASE,
)
SENSITIVE_VALUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email address", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("US Social Security number", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    (
        "phone number",
        re.compile(
            r"(?<!\d)(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}(?!\d)"
        ),
    ),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Stripe-style secret", re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{12,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("bearer credential", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*", re.IGNORECASE)),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("password assignment", re.compile(r"\bpass(?:word)?\s*[:=]\s*\S+", re.IGNORECASE)),
)


class SampleValidationError(ValueError):
    """A fail-closed sample error that never includes the rejected value."""


@dataclass(frozen=True)
class LoadedScenario:
    data: dict[str, Any]
    sample_sha256: str
    manifest: dict[str, Any]


def resolve_launch_sample(path: Path, lab_root: Path) -> Path:
    """Resolve every link and reject commit-prone custom paths inside the repo."""

    try:
        resolved = path.resolve(strict=True)
        resolved_lab = lab_root.resolve(strict=True)
        default_sample = (resolved_lab / "samples" / "default.json").resolve(strict=True)
    except OSError as exc:
        raise SampleValidationError("sample file could not be resolved") from exc
    if not resolved.is_file():
        raise SampleValidationError("sample path must resolve to a file")
    repo_root = resolved_lab.parent
    if resolved.is_relative_to(repo_root) and resolved != default_sample:
        allowed_parent = (resolved_lab / "samples").resolve(strict=True)
        if resolved.parent != allowed_parent or not resolved.name.endswith(".local.json"):
            raise SampleValidationError(
                "custom samples inside the repository must be direct children of "
                "homelab/samples and end in .local.json"
            )
    return resolved


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SampleValidationError("sample contains a duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise SampleValidationError(f"non-finite JSON number {value} is not supported")


def _string(value: Any, path: str, *, minimum: int = 1, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise SampleValidationError(f"{path} must be a string of {minimum}..{maximum} characters")
    return value


def _timestamp(value: Any, path: str) -> str:
    text = _string(value, path, maximum=64)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SampleValidationError(f"{path} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise SampleValidationError(f"{path} must include a UTC offset")
    return text


def _looks_like_card_number(text: str) -> bool:
    for candidate in re.findall(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", text):
        digits = "".join(character for character in candidate if character.isdigit())
        if not 13 <= len(digits) <= 19:
            continue
        checksum = 0
        parity = len(digits) % 2
        for index, character in enumerate(digits):
            digit = int(character)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            checksum += digit
        if checksum % 10 == 0:
            return True
    return False


def _scan_text(value: str, path: str) -> None:
    for label, pattern in SENSITIVE_VALUES:
        if pattern.search(value):
            raise SampleValidationError(f"{path} appears to contain a {label}")
    if _looks_like_card_number(value):
        raise SampleValidationError(f"{path} appears to contain a payment-card number")


def _validate_metadata(value: Any, path: str) -> dict[str, str | int | float | bool]:
    if not isinstance(value, dict) or not 1 <= len(value) <= MAX_METADATA_FIELDS:
        raise SampleValidationError(
            f"{path} must contain 1..{MAX_METADATA_FIELDS} flat metadata fields"
        )
    validated: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not SAFE_NAME.fullmatch(key) or len(key) > 64:
            raise SampleValidationError(f"{path} contains an invalid field name")
        _scan_text(key, f"{path} field name")
        if SENSITIVE_KEY.search(key):
            raise SampleValidationError(f"{path} uses a prohibited sensitive-data field")
        if not isinstance(item, (str, int, float, bool)) or item is None:
            raise SampleValidationError(f"{path} values must be strings, numbers, or booleans")
        if isinstance(item, str):
            _string(item, f"{path} value", maximum=256)
            _scan_text(item, f"{path} value")
        elif isinstance(item, float) and not math.isfinite(item):
            raise SampleValidationError(f"{path} numbers must be finite")
        validated[key] = item
    return validated


def load_scenario(path: Path, *, acknowledgement: str | None = None) -> LoadedScenario:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SampleValidationError("sample file could not be read") from exc
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise SampleValidationError(f"sample file must be 1..{MAX_FILE_BYTES} bytes")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SampleValidationError("sample file must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise SampleValidationError("sample file must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise SampleValidationError("sample root must be a JSON object")
    unknown = set(payload) - TOP_LEVEL_FIELDS
    missing = TOP_LEVEL_FIELDS - set(payload)
    if unknown:
        raise SampleValidationError("sample contains one or more unsupported fields")
    if missing:
        raise SampleValidationError(f"sample is missing fields: {', '.join(sorted(missing))}")
    if payload["$schema"] != SCHEMA:
        raise SampleValidationError("sample schema is not supported")

    classification = payload["classification"]
    if classification not in {"synthetic", "deidentified"}:
        raise SampleValidationError("classification must be synthetic or deidentified")
    if classification == "deidentified" and acknowledgement != DEIDENTIFIED_ACK:
        raise SampleValidationError(
            "deidentified samples require the explicit local data-policy acknowledgement"
        )

    scenario_id = _string(payload["scenario_id"], "scenario_id", maximum=64)
    agent_id = _string(payload["agent_id"], "agent_id", maximum=255)
    decision_type = _string(payload["decision_type"], "decision_type", maximum=100)
    subject_id = _string(payload["subject_id"], "subject_id", maximum=255)
    for path_name, value in (
        ("scenario_id", scenario_id),
        ("agent_id", agent_id),
        ("decision_type", decision_type),
        ("subject_id", subject_id),
    ):
        if not SAFE_NAME.fullmatch(value):
            raise SampleValidationError(f"{path_name} contains unsupported characters")
        _scan_text(value, path_name)
    required_prefix = "SYNTHETIC-" if classification == "synthetic" else "DEIDENTIFIED-"
    if not subject_id.startswith(required_prefix):
        raise SampleValidationError(f"subject_id must start with {required_prefix}")

    query = _string(payload["query"], "query", maximum=1000)
    outcome = _string(payload["outcome"], "outcome", maximum=500)
    expected_marker = _string(payload["expected_marker"], "expected_marker", maximum=200)
    for path_name, value in (("query", query), ("outcome", outcome), ("expected_marker", expected_marker)):
        _scan_text(value, path_name)

    reason_codes = payload["reason_codes"]
    if not isinstance(reason_codes, list) or not 1 <= len(reason_codes) <= 10:
        raise SampleValidationError("reason_codes must contain 1..10 entries")
    for index, code in enumerate(reason_codes):
        text = _string(code, f"reason_codes[{index}]", maximum=100)
        if not SAFE_NAME.fullmatch(text):
            raise SampleValidationError(f"reason_codes[{index}] contains unsupported characters")
        _scan_text(text, f"reason_codes[{index}]")

    recall_filters = _validate_metadata(payload["recall_filters"], "recall_filters")
    memories = payload["memories"]
    if not isinstance(memories, list) or not 1 <= len(memories) <= MAX_MEMORIES:
        raise SampleValidationError(f"memories must contain 1..{MAX_MEMORIES} entries")
    ids: set[str] = set()
    marker_found = False
    for index, memory in enumerate(memories):
        path_name = f"memories[{index}]"
        if not isinstance(memory, dict):
            raise SampleValidationError(f"{path_name} must be an object")
        unknown_memory = set(memory) - MEMORY_FIELDS
        missing_memory = MEMORY_FIELDS - set(memory)
        if unknown_memory or missing_memory:
            raise SampleValidationError(f"{path_name} must use the documented memory fields")
        idempotency_key = _string(
            memory["idempotency_key"], f"{path_name}.idempotency_key", maximum=255
        )
        if not SAFE_NAME.fullmatch(idempotency_key) or idempotency_key in ids:
            raise SampleValidationError(f"{path_name}.idempotency_key must be unique and portable")
        _scan_text(idempotency_key, f"{path_name}.idempotency_key")
        ids.add(idempotency_key)
        content = _string(memory["content"], f"{path_name}.content", maximum=4000)
        _scan_text(content, f"{path_name}.content")
        marker_found = marker_found or expected_marker in content
        _timestamp(memory["event_time"], f"{path_name}.event_time")
        source = _string(memory["source"], f"{path_name}.source", maximum=512)
        if not source.startswith("sample://"):
            raise SampleValidationError(f"{path_name}.source must use the sample:// scheme")
        _scan_text(source, f"{path_name}.source")
        metadata = _validate_metadata(memory["metadata"], f"{path_name}.metadata")
        for key, expected in recall_filters.items():
            if metadata.get(key) != expected:
                raise SampleValidationError(f"{path_name}.metadata must match recall_filters")
        importance = memory["importance"]
        if isinstance(importance, bool) or not isinstance(importance, (int, float)):
            raise SampleValidationError(f"{path_name}.importance must be a number")
        if not 0 <= importance <= 1:
            raise SampleValidationError(f"{path_name}.importance must be between 0 and 1")
    if not marker_found:
        raise SampleValidationError("expected_marker must occur in at least one memory content value")

    sample_sha256 = hashlib.sha256(raw).hexdigest()
    manifest = {
        "schema": SCHEMA,
        "classification": classification,
        "scenario_id": scenario_id,
        "sample_sha256": sample_sha256,
        "memory_count": len(memories),
        "decision_type": decision_type,
        "limits": {
            "max_file_bytes": MAX_FILE_BYTES,
            "max_memories": MAX_MEMORIES,
            "max_metadata_fields": MAX_METADATA_FIELDS,
        },
    }
    return LoadedScenario(data=payload, sample_sha256=sample_sha256, manifest=manifest)


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--resolve-for-launch":
        try:
            print(resolve_launch_sample(Path(argv[2]), Path(argv[3])))
        except SampleValidationError as exc:
            print(f"sample rejected: {exc}", file=sys.stderr)
            return 1
        return 0
    if len(argv) != 2:
        print(
            "usage: python scenario.py SAMPLE.json | "
            "python scenario.py --resolve-for-launch SAMPLE.json LAB_ROOT",
            file=sys.stderr,
        )
        return 2
    try:
        loaded = load_scenario(
            Path(argv[1]), acknowledgement=os.getenv("LAB_SAMPLE_POLICY_ACK")
        )
    except SampleValidationError as exc:
        print(f"sample rejected: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(loaded.manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
