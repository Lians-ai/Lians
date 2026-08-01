"""Stream, validate, and deterministically generate homelab NDJSON datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scenario import (
    DEIDENTIFIED_ACK,
    SAFE_NAME,
    SENSITIVE_KEY,
    SampleValidationError,
)
from scenario import _scan_text as scan_sample_text

SCHEMA = "https://lians.ai/schemas/homelab-dataset/v1"
CLASSIFICATIONS = frozenset({"synthetic", "deidentified"})
HEADER_FIELDS = frozenset({"$schema", "classification", "dataset_id", "agent_id"})
MEMORY_FIELDS = frozenset({"content", "event_time", "source", "metadata", "importance"})
MAX_METADATA_FIELDS = 100
MAX_CONTENT_CHARS = 100_000


class DatasetValidationError(ValueError):
    """A fail-closed dataset error that never includes rejected input values."""


@dataclass(frozen=True)
class DatasetLimits:
    """Hard streaming safety ceilings selected by the local resource profile."""

    max_records: int = 100_000
    max_bytes: int = 512 * 1024 * 1024
    max_line_bytes: int = 16 * 1024

    def __post_init__(self) -> None:
        for value, message in (
            (self.max_records, "maximum record count must be a positive integer"),
            (self.max_bytes, "maximum dataset bytes must be a positive integer"),
            (self.max_line_bytes, "maximum line bytes must be a positive integer"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DatasetValidationError(message)
        if self.max_line_bytes < 2:
            raise DatasetValidationError("maximum line bytes must be at least two")
        if self.max_line_bytes > self.max_bytes:
            raise DatasetValidationError("maximum line bytes cannot exceed maximum dataset bytes")


@dataclass(frozen=True)
class DatasetHeader:
    classification: str
    dataset_id: str
    agent_id: str


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class DatasetPreflight:
    header: DatasetHeader
    dataset_sha256: str
    total_bytes: int
    record_count: int
    limits: DatasetLimits
    file_identity: FileIdentity

    def sanitized_manifest(self) -> dict[str, Any]:
        """Return only value-free facts safe to print or persist."""

        return {
            "schema": SCHEMA,
            "classification": self.header.classification,
            "dataset_sha256": self.dataset_sha256,
            "total_bytes": self.total_bytes,
            "record_count": self.record_count,
            "limits": {
                "max_records": self.limits.max_records,
                "max_bytes": self.limits.max_bytes,
                "max_line_bytes": self.limits.max_line_bytes,
            },
        }


def resolve_launch_dataset(path: Path, lab_root: Path) -> Path:
    """Resolve links and reject commit-prone custom dataset paths in the repo."""

    try:
        resolved = path.resolve(strict=True)
        resolved_lab = lab_root.resolve(strict=True)
    except OSError as exc:
        raise DatasetValidationError("dataset file could not be resolved") from exc
    if not resolved.is_file():
        raise DatasetValidationError("dataset path must resolve to a file")

    repo_root = resolved_lab.parent
    datasets_dir = (resolved_lab / "datasets").resolve(strict=False)
    default_dataset = (datasets_dir / "default.ndjson").resolve(strict=False)
    if (
        resolved.is_relative_to(repo_root)
        and resolved != default_dataset
        and (resolved.parent != datasets_dir or not resolved.name.endswith(".local.ndjson"))
    ):
        raise DatasetValidationError(
            "custom datasets inside the repository must be direct children of "
            "homelab/datasets and end in .local.ndjson"
        )
    return resolved


def resolve_generation_target(path: Path, lab_root: Path) -> Path:
    """Resolve a future target and reject generated, commit-prone repo files."""

    try:
        resolved_lab = lab_root.resolve(strict=True)
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise DatasetValidationError("dataset target could not be resolved") from exc
    repo_root = resolved_lab.parent
    datasets_dir = (resolved_lab / "datasets").resolve(strict=False)
    if resolved.is_relative_to(repo_root) and (
        resolved.parent != datasets_dir or not resolved.name.endswith(".local.ndjson")
    ):
        raise DatasetValidationError(
            "generated datasets inside the repository must be direct children of "
            "homelab/datasets and end in .local.ndjson"
        )
    return resolved


def _identity(stat_result: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        size=stat_result.st_size,
        modified_ns=stat_result.st_mtime_ns,
    )


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetValidationError("dataset contains a duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise DatasetValidationError("dataset contains a non-finite JSON number")


def _parse_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise DatasetValidationError("dataset contains a non-finite JSON number")
    return parsed


def _parse_json_line(raw: bytes, line_number: int) -> dict[str, Any]:
    if not raw.strip():
        raise DatasetValidationError(f"line {line_number} must not be empty")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_parse_float,
        )
    except DatasetValidationError:
        raise
    except UnicodeDecodeError as exc:
        raise DatasetValidationError(f"line {line_number} must be UTF-8 JSON") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise DatasetValidationError(f"line {line_number} must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise DatasetValidationError(f"line {line_number} must contain a JSON object")
    return payload


def _string(value: Any, path: str, *, minimum: int = 1, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise DatasetValidationError(
            f"{path} must be a string of {minimum}..{maximum} characters"
        )
    return value


def _scan_text(value: str, path: str) -> None:
    try:
        scan_sample_text(value, path)
    except SampleValidationError as exc:
        raise DatasetValidationError(str(exc)) from exc


def _portable_identifier(value: Any, path: str, *, maximum: int) -> str:
    text = _string(value, path, maximum=maximum)
    if not SAFE_NAME.fullmatch(text):
        raise DatasetValidationError(f"{path} contains unsupported characters")
    _scan_text(text, path)
    return text


def _validate_header(
    payload: dict[str, Any], *, acknowledgement: str | None
) -> DatasetHeader:
    if set(payload) != HEADER_FIELDS:
        raise DatasetValidationError("dataset header must use only the documented fields")
    if payload["$schema"] != SCHEMA:
        raise DatasetValidationError("dataset schema is not supported")
    classification = payload["classification"]
    if not isinstance(classification, str) or classification not in CLASSIFICATIONS:
        raise DatasetValidationError("classification must be synthetic or deidentified")
    if classification == "deidentified" and acknowledgement != DEIDENTIFIED_ACK:
        raise DatasetValidationError(
            "deidentified datasets require the explicit local data-policy acknowledgement"
        )
    dataset_id = _portable_identifier(payload["dataset_id"], "dataset_id", maximum=64)
    agent_id = _portable_identifier(payload["agent_id"], "agent_id", maximum=255)
    return DatasetHeader(
        classification=classification,
        dataset_id=dataset_id,
        agent_id=agent_id,
    )


def _timestamp(value: Any, path: str) -> str:
    text = _string(value, path, maximum=64)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DatasetValidationError(f"{path} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise DatasetValidationError(f"{path} must include a UTC offset")
    return text


def _validate_metadata(value: Any, path: str) -> dict[str, str | int | float | bool]:
    if not isinstance(value, dict) or len(value) > MAX_METADATA_FIELDS:
        raise DatasetValidationError(
            f"{path} must contain 0..{MAX_METADATA_FIELDS} flat metadata fields"
        )
    validated: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 64 or not SAFE_NAME.fullmatch(key):
            raise DatasetValidationError(f"{path} contains an invalid field name")
        _scan_text(key, f"{path} field name")
        if SENSITIVE_KEY.search(key):
            raise DatasetValidationError(f"{path} uses a prohibited sensitive-data field")
        if isinstance(item, (bool, int)):
            if not isinstance(item, bool):
                _scan_text(str(item), f"{path} value")
            validated[key] = item
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise DatasetValidationError(f"{path} numbers must be finite")
            validated[key] = item
        elif isinstance(item, str):
            text = _string(item, f"{path} value", maximum=1_000)
            _scan_text(text, f"{path} value")
            validated[key] = text
        else:
            raise DatasetValidationError(f"{path} values must be flat JSON primitives")
    return validated


def _validate_memory(payload: dict[str, Any], line_number: int) -> dict[str, Any]:
    path = f"line {line_number} memory"
    if set(payload) != MEMORY_FIELDS:
        raise DatasetValidationError(f"{path} must use only the documented fields")
    content = _string(payload["content"], f"{path}.content", maximum=MAX_CONTENT_CHARS)
    _scan_text(content, f"{path}.content")
    event_time = _timestamp(payload["event_time"], f"{path}.event_time")
    source = _string(payload["source"], f"{path}.source", maximum=512)
    _scan_text(source, f"{path}.source")
    metadata = _validate_metadata(payload["metadata"], f"{path}.metadata")
    importance = payload["importance"]
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        raise DatasetValidationError(f"{path}.importance must be a number")
    if not math.isfinite(importance) or not 0 <= importance <= 1:
        raise DatasetValidationError(f"{path}.importance must be between 0 and 1")
    return {
        "content": content,
        "event_time": event_time,
        "source": source,
        "metadata": metadata,
        "importance": importance,
    }


def _read_bounded_line(handle: Any, limits: DatasetLimits, line_number: int) -> bytes:
    raw = handle.readline(limits.max_line_bytes + 1)
    if len(raw) > limits.max_line_bytes:
        raise DatasetValidationError(f"line {line_number} exceeds the per-line byte limit")
    return raw


def preflight_dataset(
    path: Path,
    *,
    acknowledgement: str | None = None,
    limits: DatasetLimits | None = None,
) -> DatasetPreflight:
    """Validate and hash the complete dataset without retaining its records."""

    active_limits = limits or DatasetLimits()
    try:
        initial_stat = path.stat()
        if not path.is_file():
            raise DatasetValidationError("dataset path must identify a file")
        if initial_stat.st_size < 1 or initial_stat.st_size > active_limits.max_bytes:
            raise DatasetValidationError("dataset size is outside the active byte limit")
        handle = path.open("rb")
    except DatasetValidationError:
        raise
    except OSError as exc:
        raise DatasetValidationError("dataset file could not be read") from exc

    digest = hashlib.sha256()
    total_bytes = 0
    record_count = 0
    header: DatasetHeader | None = None
    with handle:
        opened_identity = _identity(os.fstat(handle.fileno()))
        if opened_identity != _identity(initial_stat):
            raise DatasetValidationError("dataset changed before preflight could begin")
        line_number = 1
        while True:
            raw = _read_bounded_line(handle, active_limits, line_number)
            if not raw:
                break
            total_bytes += len(raw)
            if total_bytes > active_limits.max_bytes:
                raise DatasetValidationError("dataset exceeds the active total byte limit")
            digest.update(raw)
            payload = _parse_json_line(raw, line_number)
            if line_number == 1:
                header = _validate_header(payload, acknowledgement=acknowledgement)
            else:
                record_count += 1
                if record_count > active_limits.max_records:
                    raise DatasetValidationError("dataset exceeds the active record limit")
                _validate_memory(payload, line_number)
            line_number += 1
        final_identity = _identity(os.fstat(handle.fileno()))

    if opened_identity != final_identity:
        raise DatasetValidationError("dataset changed during preflight")
    if header is None:
        raise DatasetValidationError("dataset must contain a header")
    if record_count < 1:
        raise DatasetValidationError("dataset must contain at least one memory record")
    return DatasetPreflight(
        header=header,
        dataset_sha256=digest.hexdigest(),
        total_bytes=total_bytes,
        record_count=record_count,
        limits=active_limits,
        file_identity=final_identity,
    )


def iter_dataset_records(
    path: Path,
    preflight: DatasetPreflight,
    *,
    acknowledgement: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Revalidate and yield one record at a time after a complete preflight."""

    try:
        if _identity(path.stat()) != preflight.file_identity:
            raise DatasetValidationError("dataset changed after preflight")
        handle = path.open("rb")
    except DatasetValidationError:
        raise
    except OSError as exc:
        raise DatasetValidationError("dataset file could not be read") from exc

    digest = hashlib.sha256()
    total_bytes = 0
    record_count = 0
    with handle:
        if _identity(os.fstat(handle.fileno())) != preflight.file_identity:
            raise DatasetValidationError("dataset changed after preflight")
        line_number = 1
        while True:
            raw = _read_bounded_line(handle, preflight.limits, line_number)
            if not raw:
                break
            total_bytes += len(raw)
            if total_bytes > preflight.limits.max_bytes:
                raise DatasetValidationError("dataset exceeds the active total byte limit")
            digest.update(raw)
            payload = _parse_json_line(raw, line_number)
            if line_number == 1:
                header = _validate_header(payload, acknowledgement=acknowledgement)
                if header != preflight.header:
                    raise DatasetValidationError("dataset header changed after preflight")
            else:
                record_count += 1
                if record_count > preflight.limits.max_records:
                    raise DatasetValidationError("dataset exceeds the active record limit")
                yield _validate_memory(payload, line_number)
            line_number += 1

    if (
        digest.hexdigest() != preflight.dataset_sha256
        or total_bytes != preflight.total_bytes
        or record_count != preflight.record_count
    ):
        raise DatasetValidationError("dataset changed after preflight")


def _synthetic_marker(dataset_id: str, position: int) -> str:
    digest = hashlib.sha256(f"{dataset_id}:{position}".encode()).digest()
    return "".join(chr(ord("a") + (byte % 16)) for byte in digest[:12])


def iter_synthetic_lines(
    *,
    records: int,
    dataset_id: str,
    agent_id: str,
) -> Iterator[bytes]:
    """Yield a byte-identical synthetic dataset for identical arguments."""

    header_payload = {
        "$schema": SCHEMA,
        "classification": "synthetic",
        "dataset_id": dataset_id,
        "agent_id": agent_id,
    }
    _validate_header(header_payload, acknowledgement=None)
    if isinstance(records, bool) or not isinstance(records, int) or records < 1:
        raise DatasetValidationError("synthetic dataset record count must be positive")
    yield (json.dumps(header_payload, separators=(",", ":")) + "\n").encode("utf-8")

    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(records):
        position = index + 1
        marker = _synthetic_marker(dataset_id, position)
        payload = {
            "content": f"Synthetic memory record {position} carries test marker {marker}.",
            "event_time": (started_at + timedelta(milliseconds=index)).isoformat().replace(
                "+00:00", "Z"
            ),
            "source": "dataset://synthetic/generated",
            "metadata": {
                "sequence": position,
                "partition": index % 16,
                "marker": marker,
            },
            "importance": 0.5,
        }
        _validate_memory(payload, position + 1)
        yield (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def generate_synthetic_dataset(
    path: Path,
    *,
    records: int,
    dataset_id: str,
    agent_id: str,
    limits: DatasetLimits | None = None,
) -> DatasetPreflight:
    """Create a deterministic synthetic dataset without replacing an existing file."""

    active_limits = limits or DatasetLimits()
    if isinstance(records, bool) or not isinstance(records, int) or records < 1:
        raise DatasetValidationError("synthetic dataset record count must be positive")
    if records > active_limits.max_records:
        raise DatasetValidationError("synthetic dataset exceeds the active record limit")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("xb")
    except FileExistsError as exc:
        raise DatasetValidationError("synthetic dataset target already exists") from exc
    except OSError as exc:
        raise DatasetValidationError("synthetic dataset target could not be created") from exc

    total_bytes = 0
    try:
        with handle:
            for line_number, raw in enumerate(
                iter_synthetic_lines(
                    records=records,
                    dataset_id=dataset_id,
                    agent_id=agent_id,
                ),
                start=1,
            ):
                if len(raw) > active_limits.max_line_bytes:
                    raise DatasetValidationError(
                        f"line {line_number} exceeds the per-line byte limit"
                    )
                total_bytes += len(raw)
                if total_bytes > active_limits.max_bytes:
                    raise DatasetValidationError("synthetic dataset exceeds the active byte limit")
                handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return preflight_dataset(path, limits=active_limits)


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise DatasetValidationError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise DatasetValidationError(f"{name} must be a positive integer")
    return value


def limits_from_environment() -> DatasetLimits:
    return DatasetLimits(
        max_records=_env_positive_int("LAB_DATASET_MAX_RECORDS", 100_000),
        max_bytes=_env_positive_int("LAB_DATASET_MAX_BYTES", 512 * 1024 * 1024),
        max_line_bytes=_env_positive_int("LAB_DATASET_MAX_LINE_BYTES", 16 * 1024),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dataset.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("check", help="preflight one NDJSON dataset")
    check.add_argument("path", type=Path)
    generate = subparsers.add_parser("generate", help="create a deterministic dataset")
    generate.add_argument("path", type=Path)
    generate.add_argument("--records", type=int, required=True)
    generate.add_argument("--dataset-id", required=True)
    generate.add_argument("--agent-id", required=True)
    generate.add_argument(
        "--lab-root",
        type=Path,
        help="enforce the ignored repository-local generation policy",
    )
    return parser


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[1] == "--resolve-for-launch":
        try:
            print(resolve_launch_dataset(Path(argv[2]), Path(argv[3])))
        except DatasetValidationError as exc:
            print(f"dataset rejected: {exc}", file=sys.stderr)
            return 1
        return 0

    args = _parser().parse_args(argv[1:])
    try:
        limits = limits_from_environment()
        if args.command == "check":
            loaded = preflight_dataset(
                args.path,
                acknowledgement=os.getenv("LAB_DATASET_POLICY_ACK"),
                limits=limits,
            )
        else:
            target = (
                resolve_generation_target(args.path, args.lab_root)
                if args.lab_root is not None
                else args.path
            )
            loaded = generate_synthetic_dataset(
                target,
                records=args.records,
                dataset_id=args.dataset_id,
                agent_id=args.agent_id,
                limits=limits,
            )
    except DatasetValidationError as exc:
        print(f"dataset rejected: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(loaded.sanitized_manifest(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
