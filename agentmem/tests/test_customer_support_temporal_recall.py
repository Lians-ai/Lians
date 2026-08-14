"""Regression contract for the synthetic support temporal-recall fixture."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

BENCHMARK_DIR = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "customer_support_temporal_recall"
)


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "customer_support_temporal_recall",
        BENCHMARK_DIR / "run_benchmark.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return datetime.fromisoformat(value)


def test_fixture_has_explicit_ordered_event_and_ingestion_times():
    fixture = json.loads((BENCHMARK_DIR / "fixture.json").read_text(encoding="utf-8"))
    ingestion_times = []
    for record in fixture["records"]:
        event_time = _parse_time(record["event_time"])
        ingestion_time = _parse_time(record["ingestion_time"])
        assert event_time.tzinfo is not None
        assert ingestion_time.tzinfo is not None
        ingestion_times.append(ingestion_time)

    assert ingestion_times == sorted(ingestion_times)
    late_records = [
        record
        for record in fixture["records"]
        if record["id"] == "ticket-late-arrival"
    ]
    assert len(late_records) == 1
    assert late_records[0]["event_time"] < late_records[0]["ingestion_time"]


def test_current_and_point_in_time_support_recall_match_contract():
    receipt = _runner_module().run_benchmark()
    assert receipt["passed"], receipt
    assert len(receipt["fixture_sha256"]) == 64
    assert all(len(check["lians_receipt_sha256"]) == 64 for check in receipt["checks"])
