"""Preflight and ingest a homelab dataset with bounded stdlib HTTP workers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import atomic_write_json, endpoint, http_request
from dataset import (
    DatasetLimits,
    DatasetPreflight,
    DatasetValidationError,
    iter_dataset_records,
    preflight_dataset,
)

RECEIPT_SCHEMA = "https://lians.ai/schemas/homelab-capacity-receipt/v1"
MAX_CONCURRENCY = 256
MAX_REQUEST_TIMEOUT_SECONDS = 300.0
LATENCY_BUCKETS = 1_024
MIN_TRACKED_LATENCY_MS = 0.001
MAX_TRACKED_LATENCY_MS = MAX_REQUEST_TIMEOUT_SECONDS * 1_000
SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
SAFE_GIT_COMMIT = re.compile(r"^(?:[0-9a-fA-F]{7,64}(?:-dirty)?|unrecorded)$")
RequestFunction = Callable[..., tuple[int, dict[str, str], bytes]]


class BulkIngestError(RuntimeError):
    """A sanitized bulk-ingest error that never includes dataset or response data."""


@dataclass(frozen=True)
class RequestResult:
    succeeded: bool
    latency_ms: float


class LatencyHistogram:
    """Fixed-memory, roughly two-percent-resolution latency percentiles."""

    def __init__(self) -> None:
        self._counts = [0] * LATENCY_BUCKETS
        self._total = 0
        self._log_step = math.log(
            MAX_TRACKED_LATENCY_MS / MIN_TRACKED_LATENCY_MS
        ) / (LATENCY_BUCKETS - 1)

    def add(self, value_ms: float) -> None:
        bounded = min(max(value_ms, MIN_TRACKED_LATENCY_MS), MAX_TRACKED_LATENCY_MS)
        index = math.ceil(
            math.log(bounded / MIN_TRACKED_LATENCY_MS) / self._log_step
        )
        index = min(max(index, 0), LATENCY_BUCKETS - 1)
        self._counts[index] += 1
        self._total += 1

    def percentile(self, quantile: float) -> float:
        if self._total == 0:
            return 0.0
        target = max(1, math.ceil(quantile * self._total))
        observed = 0
        for index, count in enumerate(self._counts):
            observed += count
            if observed >= target:
                return min(
                    MIN_TRACKED_LATENCY_MS * math.exp(index * self._log_step),
                    MAX_TRACKED_LATENCY_MS,
                )
        return MAX_TRACKED_LATENCY_MS


def deterministic_idempotency_key(dataset_sha256: str, position: int) -> str:
    material = f"{dataset_sha256}:{position}".encode("ascii")
    return f"lians-dataset-{hashlib.sha256(material).hexdigest()}"


def _send_memory(
    *,
    request_function: RequestFunction,
    url: str,
    api_key: str,
    agent_id: str,
    dataset_sha256: str,
    position: int,
    record: dict[str, Any],
    timeout: float,
) -> RequestResult:
    started = time.perf_counter()
    succeeded = False
    try:
        status, _, _ = request_function(
            "POST",
            url,
            json_body={"agent_id": agent_id, **record},
            headers={
                "X-API-Key": api_key,
                "Idempotency-Key": deterministic_idempotency_key(
                    dataset_sha256, position
                ),
            },
            timeout=timeout,
        )
        succeeded = 200 <= status < 300
    except Exception:  # noqa: BLE001 - arbitrary HTTP failures must be value-free.
        # Response bodies and exception messages may reflect submitted content.
        # Only the value-free failure count is allowed into the receipt.
        succeeded = False
    return RequestResult(
        succeeded=succeeded,
        latency_ms=(time.perf_counter() - started) * 1_000,
    )


def _receipt(
    *,
    preflight: DatasetPreflight,
    scale_profile: str,
    git_commit: str,
    concurrency: int,
    request_timeout: float,
    processed: int,
    succeeded: int,
    failed: int,
    duration_seconds: float,
    latencies_ms: LatencyHistogram,
) -> dict[str, Any]:
    elapsed = max(duration_seconds, 0.0)
    rate = processed / elapsed if elapsed > 0 else 0.0
    return {
        "schema": RECEIPT_SCHEMA,
        "classification": preflight.header.classification,
        "dataset_sha256": preflight.dataset_sha256,
        "scale_profile": scale_profile,
        "git_commit": git_commit,
        "total_bytes": preflight.total_bytes,
        "requested_records": preflight.record_count,
        "processed_records": processed,
        "succeeded_records": succeeded,
        "failed_records": failed,
        "duration_seconds": round(elapsed, 6),
        "records_per_second": round(rate, 3),
        "latency_ms": {
            "p50": round(latencies_ms.percentile(0.50), 3),
            "p95": round(latencies_ms.percentile(0.95), 3),
            "p99": round(latencies_ms.percentile(0.99), 3),
        },
        "concurrency": concurrency,
        "limits": {
            "max_records": preflight.limits.max_records,
            "max_bytes": preflight.limits.max_bytes,
            "max_line_bytes": preflight.limits.max_line_bytes,
            "request_timeout_seconds": request_timeout,
        },
    }


def ingest_dataset(
    path: Path,
    *,
    lians_url: str,
    api_key: str,
    acknowledgement: str | None = None,
    limits: DatasetLimits | None = None,
    concurrency: int = 4,
    max_in_flight: int | None = None,
    request_timeout: float = 15.0,
    scale_profile: str = "laptop",
    git_commit: str = "unrecorded",
    request_function: RequestFunction = http_request,
) -> dict[str, Any]:
    """Validate the complete file, then stream writes through a bounded queue."""

    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise BulkIngestError(f"concurrency must be between 1 and {MAX_CONCURRENCY}")
    queue_bound = concurrency * 2 if max_in_flight is None else max_in_flight
    if queue_bound < 1:
        raise BulkIngestError("max_in_flight must be positive")
    if not 0 < request_timeout <= MAX_REQUEST_TIMEOUT_SECONDS:
        raise BulkIngestError(
            f"request timeout must be between 0 and {MAX_REQUEST_TIMEOUT_SECONDS:g} seconds"
        )
    if not SAFE_LABEL.fullmatch(scale_profile):
        raise BulkIngestError("scale profile must be a portable label")
    if not SAFE_GIT_COMMIT.fullmatch(git_commit):
        raise BulkIngestError(
            "git commit must be a hexadecimal revision, optionally dirty, or unrecorded"
        )
    if not api_key or len(api_key) > 512:
        raise BulkIngestError("local API key is unavailable or invalid")

    # This complete pass is intentionally before constructing or submitting the
    # first request. Invalid data therefore cannot produce a partial write set.
    preflight = preflight_dataset(
        path,
        acknowledgement=acknowledgement,
        limits=limits,
    )

    processed = 0
    succeeded = 0
    failed = 0
    latencies_ms = LatencyHistogram()
    pending: set[Future[RequestResult]] = set()
    ingest_started = time.perf_counter()
    memory_url = endpoint(lians_url, "/v1/memories")

    def consume(done: set[Future[RequestResult]]) -> None:
        nonlocal processed, succeeded, failed
        for future in done:
            try:
                result = future.result()
            except Exception:  # noqa: BLE001 - keep worker failures out of receipts.
                result = RequestResult(succeeded=False, latency_ms=0.0)
            processed += 1
            if result.succeeded:
                succeeded += 1
            else:
                failed += 1
            latencies_ms.add(result.latency_ms)

    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="lians-dataset",
    ) as executor:
        for position, record in enumerate(
            iter_dataset_records(
                path,
                preflight,
                acknowledgement=acknowledgement,
            ),
            start=1,
        ):
            while len(pending) >= queue_bound:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                consume(done)
            pending.add(
                executor.submit(
                    _send_memory,
                    request_function=request_function,
                    url=memory_url,
                    api_key=api_key,
                    agent_id=preflight.header.agent_id,
                    dataset_sha256=preflight.dataset_sha256,
                    position=position,
                    record=record,
                    timeout=request_timeout,
                )
            )
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            consume(done)

    elapsed = time.perf_counter() - ingest_started
    if processed != preflight.record_count or succeeded + failed != processed:
        raise BulkIngestError("bulk ingest accounting did not reconcile")
    return _receipt(
        preflight=preflight,
        scale_profile=scale_profile,
        git_commit=git_commit,
        concurrency=concurrency,
        request_timeout=request_timeout,
        processed=processed,
        succeeded=succeeded,
        failed=failed,
        duration_seconds=elapsed,
        latencies_ms=latencies_ms,
    )


def _env_positive_int(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise BulkIngestError(f"{name} must be a positive integer") from exc
    if value < 1 or (maximum is not None and value > maximum):
        raise BulkIngestError(f"{name} is outside its supported range")
    return value


def _env_positive_float(name: str, default: float, *, maximum: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise BulkIngestError(f"{name} must be a positive number") from exc
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise BulkIngestError(f"{name} is outside its supported range")
    return value


def _read_api_key(state_dir: Path) -> str:
    path = state_dir / "api-key"
    try:
        if path.stat().st_size > 512:
            raise BulkIngestError("local API key is unavailable or invalid")
        value = path.read_text(encoding="utf-8").strip()
    except BulkIngestError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise BulkIngestError("local API key is unavailable or invalid") from exc
    if not value or len(value) > 512 or any(character.isspace() for character in value):
        raise BulkIngestError("local API key is unavailable or invalid")
    return value


def main() -> int:
    try:
        dataset_path = Path(os.getenv("DATASET_PATH", "/dataset/input.ndjson"))
        artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "/artifacts"))
        state_dir = Path(os.getenv("STATE_DIR", "/state"))
        limits = DatasetLimits(
            max_records=_env_positive_int("LAB_DATASET_MAX_RECORDS", 100_000),
            max_bytes=_env_positive_int("LAB_DATASET_MAX_BYTES", 512 * 1024 * 1024),
            max_line_bytes=_env_positive_int("LAB_DATASET_MAX_LINE_BYTES", 16 * 1024),
        )
        receipt = ingest_dataset(
            dataset_path,
            lians_url=os.getenv("LIANS_URL", "http://lians:8000"),
            api_key=_read_api_key(state_dir),
            acknowledgement=os.getenv("LAB_DATASET_POLICY_ACK"),
            limits=limits,
            concurrency=_env_positive_int(
                "LAB_BULK_CONCURRENCY", 4, maximum=MAX_CONCURRENCY
            ),
            request_timeout=_env_positive_float(
                "LAB_BULK_REQUEST_TIMEOUT_SECONDS",
                15.0,
                maximum=MAX_REQUEST_TIMEOUT_SECONDS,
            ),
            scale_profile=os.getenv("LAB_SCALE_PROFILE", "laptop"),
            git_commit=os.getenv("LAB_GIT_COMMIT", "unrecorded"),
        )
        receipt_path = artifacts_dir / "latest-capacity-receipt.json"
        atomic_write_json(receipt_path, receipt)
    except (BulkIngestError, DatasetValidationError) as exc:
        print(f"bulk ingest failed: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("bulk ingest failed: capacity receipt could not be written", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 1 if receipt["failed_records"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
