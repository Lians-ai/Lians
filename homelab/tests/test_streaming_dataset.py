"""Focused contracts for streaming dataset validation and bounded ingestion."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "workload"))

import bulk_ingest
import dataset
from bulk_ingest import deterministic_idempotency_key, ingest_dataset
from dataset import (
    DEIDENTIFIED_ACK,
    SCHEMA,
    DatasetLimits,
    DatasetValidationError,
    generate_synthetic_dataset,
    iter_dataset_records,
    preflight_dataset,
    resolve_generation_target,
    resolve_launch_dataset,
)


class StreamingDatasetContract(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    @staticmethod
    def header(
        *, classification: str = "synthetic", dataset_id: str = "dataset-test"
    ) -> dict:
        return {
            "$schema": SCHEMA,
            "classification": classification,
            "dataset_id": dataset_id,
            "agent_id": "agent-test",
        }

    @staticmethod
    def record(position: int = 1, *, content: str | None = None) -> dict:
        return {
            "content": content or f"Synthetic compatibility memory {position}.",
            "event_time": f"2026-01-{position:02d}T00:00:00Z",
            "source": "dataset://unit-test",
            "metadata": {"sequence": position, "kind": "synthetic"},
            "importance": 0.5,
        }

    def write_dataset(
        self,
        *,
        header: dict | None = None,
        records: list[dict] | None = None,
        name: str = "input.ndjson",
    ) -> Path:
        path = self.root / name
        payloads = [header or self.header(), *(records or [self.record()])]
        path.write_text(
            "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in payloads),
            encoding="utf-8",
        )
        return path

    def test_preflight_streams_and_manifest_excludes_all_raw_record_values(self):
        path = self.write_dataset(records=[self.record(1), self.record(2)])
        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read")):
            loaded = preflight_dataset(path)
        self.assertEqual(loaded.record_count, 2)
        self.assertEqual(len(loaded.dataset_sha256), 64)
        records = list(iter_dataset_records(path, loaded))
        self.assertEqual([item["metadata"]["sequence"] for item in records], [1, 2])

        rendered = json.dumps(loaded.sanitized_manifest())
        self.assertNotIn("dataset-test", rendered)
        self.assertNotIn("agent-test", rendered)
        self.assertNotIn(records[0]["content"], rendered)
        self.assertNotIn("sequence", rendered)

    def test_deidentified_input_requires_the_existing_exact_acknowledgement(self):
        path = self.write_dataset(header=self.header(classification="deidentified"))
        with self.assertRaisesRegex(DatasetValidationError, "explicit local data-policy"):
            preflight_dataset(path)
        with self.assertRaises(DatasetValidationError):
            preflight_dataset(path, acknowledgement=DEIDENTIFIED_ACK + " ")
        loaded = preflight_dataset(path, acknowledgement=DEIDENTIFIED_ACK)
        self.assertEqual(loaded.header.classification, "deidentified")

    def test_duplicate_nonfinite_sensitive_nested_and_unknown_data_fail_closed(self):
        secret = "sk_live_abcdefghijklmnop"
        cases: list[tuple[str, bytes]] = []
        header = json.dumps(self.header(), separators=(",", ":")).encode("utf-8")
        valid = json.dumps(self.record(), separators=(",", ":"))
        duplicate = valid.replace(
            '"content":"Synthetic compatibility memory 1."',
            '"content":"Synthetic compatibility memory 1.","content":"duplicate"',
        )
        cases.append(("duplicate", header + b"\n" + duplicate.encode() + b"\n"))
        cases.append(
            (
                "nonfinite",
                header + b"\n" + valid.replace('"importance":0.5', '"importance":NaN').encode() + b"\n",
            )
        )
        cases.append(
            (
                "overflowing-float",
                header + b"\n" + valid.replace('"importance":0.5', '"importance":1e309').encode() + b"\n",
            )
        )
        for name, mutation in (
            ("secret", lambda item: item.__setitem__("content", secret)),
            ("email", lambda item: item.__setitem__("content", "person@example.com")),
            ("sensitive-key", lambda item: item["metadata"].__setitem__("api_key", "opaque")),
            ("numeric-card", lambda item: item["metadata"].__setitem__("number", 4242424242424242)),
            ("nested", lambda item: item["metadata"].__setitem__("nested", {"value": 1})),
            ("unknown", lambda item: item.__setitem__("extra", True)),
        ):
            item = self.record()
            mutation(item)
            raw = header + b"\n" + json.dumps(item, separators=(",", ":")).encode() + b"\n"
            cases.append((name, raw))
        bad_header = self.header()
        bad_header["classification"] = ["synthetic"]
        cases.append(
            (
                "bad-header-type",
                json.dumps(bad_header, separators=(",", ":")).encode()
                + b"\n"
                + valid.encode()
                + b"\n",
            )
        )

        for name, raw in cases:
            with self.subTest(name=name):
                path = self.root / f"{name}.ndjson"
                path.write_bytes(raw)
                with self.assertRaises(DatasetValidationError) as raised:
                    preflight_dataset(path)
                self.assertNotIn(secret, str(raised.exception))
                self.assertNotIn("person@example.com", str(raised.exception))

    def test_record_line_total_and_record_count_limits_are_enforced(self):
        two_records = self.write_dataset(records=[self.record(1), self.record(2)])
        with self.assertRaisesRegex(DatasetValidationError, "record limit"):
            preflight_dataset(
                two_records,
                limits=DatasetLimits(max_records=1, max_bytes=4_096, max_line_bytes=1_024),
            )

        long_line = self.write_dataset(
            records=[self.record(content="Synthetic " + "x" * 500)],
            name="long.ndjson",
        )
        with self.assertRaisesRegex(DatasetValidationError, "per-line byte limit"):
            preflight_dataset(
                long_line,
                limits=DatasetLimits(max_records=2, max_bytes=4_096, max_line_bytes=256),
            )

        with self.assertRaisesRegex(DatasetValidationError, "byte limit"):
            preflight_dataset(
                two_records,
                limits=DatasetLimits(max_records=2, max_bytes=300, max_line_bytes=256),
            )

    def test_generator_is_deterministic_streaming_and_exceeds_scenario_size(self):
        first = self.root / "first.ndjson"
        second = self.root / "second.ndjson"
        limits = DatasetLimits(max_records=50, max_bytes=100_000, max_line_bytes=2_048)
        first_loaded = generate_synthetic_dataset(
            first,
            records=25,
            dataset_id="deterministic-test",
            agent_id="agent-synthetic",
            limits=limits,
        )
        second_loaded = generate_synthetic_dataset(
            second,
            records=25,
            dataset_id="deterministic-test",
            agent_id="agent-synthetic",
            limits=limits,
        )
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first_loaded.dataset_sha256, second_loaded.dataset_sha256)
        self.assertEqual(first_loaded.record_count, 25)
        with self.assertRaisesRegex(DatasetValidationError, "already exists"):
            generate_synthetic_dataset(
                first,
                records=1,
                dataset_id="deterministic-test",
                agent_id="agent-synthetic",
                limits=limits,
            )

    def test_resolved_repo_paths_must_be_default_or_ignored_local_datasets(self):
        repo = self.root / "repo"
        lab = repo / "homelab"
        datasets = lab / "datasets"
        datasets.mkdir(parents=True)
        default = self.write_dataset(name="temporary-default.ndjson")
        (datasets / "default.ndjson").write_bytes(default.read_bytes())
        local = datasets / "customer.local.ndjson"
        local.write_bytes(default.read_bytes())
        unsafe = repo / "customer.ndjson"
        unsafe.write_bytes(default.read_bytes())
        external = self.root / "external.ndjson"
        external.write_bytes(default.read_bytes())

        self.assertEqual(resolve_launch_dataset(local, lab), local.resolve())
        self.assertEqual(resolve_launch_dataset(external, lab), external.resolve())
        generated_local = datasets / "generated.local.ndjson"
        self.assertEqual(
            resolve_generation_target(generated_local, lab), generated_local.resolve()
        )
        self.assertEqual(resolve_generation_target(external, lab), external.resolve())
        with self.assertRaisesRegex(DatasetValidationError, "inside the repository"):
            resolve_launch_dataset(unsafe, lab)
        with self.assertRaisesRegex(DatasetValidationError, "inside the repository"):
            resolve_generation_target(unsafe, lab)

    def test_file_change_after_preflight_is_rejected_before_iteration(self):
        path = self.write_dataset()
        loaded = preflight_dataset(path)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(DatasetValidationError, "changed after preflight"):
            next(iter_dataset_records(path, loaded))

    def test_bulk_preflight_rejects_a_bad_last_record_before_any_http_write(self):
        records = [self.record(1), self.record(2)]
        records[-1]["content"] = "person@example.com"
        path = self.write_dataset(records=records)
        calls = 0

        def request(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return 201, {}, b"{}"

        with self.assertRaises(DatasetValidationError):
            ingest_dataset(
                path,
                lians_url="http://lians.invalid",
                api_key="test-key",
                request_function=request,
            )
        self.assertEqual(calls, 0)

    def test_bulk_workers_are_bounded_and_receipt_is_sanitized(self):
        path = self.root / "bulk.ndjson"
        loaded = generate_synthetic_dataset(
            path,
            records=24,
            dataset_id="dataset-must-not-leak",
            agent_id="agent-must-not-leak",
            limits=DatasetLimits(max_records=24, max_bytes=100_000, max_line_bytes=2_048),
        )
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        calls: list[tuple[str, str, dict, dict]] = []

        def request(method, url, *, json_body, headers, timeout):
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                calls.append((method, url, json_body, headers))
            time.sleep(0.003)
            with lock:
                active -= 1
            return 201, {}, b'{"id":"synthetic"}'

        receipt = ingest_dataset(
            path,
            lians_url="http://lians.invalid/base",
            api_key="api-key-must-not-leak",
            limits=loaded.limits,
            concurrency=3,
            max_in_flight=4,
            scale_profile="unit",
            git_commit="abcdef1234567-dirty",
            request_function=request,
        )
        self.assertLessEqual(maximum_active, 3)
        self.assertEqual(len(calls), 24)
        self.assertTrue(all(call[0] == "POST" for call in calls))
        self.assertTrue(all(call[1].endswith("/v1/memories") for call in calls))
        self.assertTrue(all(call[2]["agent_id"] == "agent-must-not-leak" for call in calls))
        keys = [call[3]["Idempotency-Key"] for call in calls]
        self.assertEqual(len(set(keys)), 24)
        self.assertIn(deterministic_idempotency_key(loaded.dataset_sha256, 1), keys)

        self.assertEqual(receipt["requested_records"], 24)
        self.assertEqual(receipt["processed_records"], 24)
        self.assertEqual(receipt["succeeded_records"], 24)
        self.assertEqual(receipt["failed_records"], 0)
        self.assertEqual(receipt["git_commit"], "abcdef1234567-dirty")
        self.assertEqual(set(receipt["latency_ms"]), {"p50", "p95", "p99"})
        rendered = json.dumps(receipt)
        for prohibited in (
            "dataset-must-not-leak",
            "agent-must-not-leak",
            "api-key-must-not-leak",
            calls[0][2]["content"],
            "sequence",
            "dataset://synthetic/generated",
            "2026-01-01",
        ):
            self.assertNotIn(prohibited, rendered)

    def test_http_failures_are_counted_without_reflecting_exception_or_body(self):
        path = self.write_dataset(records=[self.record(1), self.record(2)])
        lock = threading.Lock()
        calls = 0
        secret = "response-secret-must-not-leak"

        def request(*_args, **_kwargs):
            nonlocal calls
            with lock:
                calls += 1
                current = calls
            if current == 1:
                raise RuntimeError(secret)
            return 201, {}, secret.encode("utf-8")

        receipt = ingest_dataset(
            path,
            lians_url="http://lians.invalid",
            api_key="test-key",
            concurrency=2,
            request_function=request,
        )
        self.assertEqual(receipt["failed_records"], 1)
        self.assertEqual(receipt["succeeded_records"], 1)
        self.assertNotIn(secret, json.dumps(receipt))

    def test_dataset_cli_and_bulk_environment_contracts(self):
        generated = self.root / "generated.ndjson"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            result = dataset.main(
                [
                    "dataset.py",
                    "generate",
                    str(generated),
                    "--records",
                    "12",
                    "--dataset-id",
                    "cli-dataset",
                    "--agent-id",
                    "cli-agent",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["record_count"], 12)
        self.assertNotIn("cli-agent", stdout.getvalue())

        state = self.root / "state"
        artifacts = self.root / "artifacts"
        state.mkdir()
        (state / "api-key").write_text("local-test-key", encoding="utf-8")
        fake_receipt = {"failed_records": 0, "processed_records": 12}
        environment = {
            "DATASET_PATH": str(generated),
            "ARTIFACTS_DIR": str(artifacts),
            "STATE_DIR": str(state),
            "LIANS_URL": "http://lians.internal:8000",
            "LAB_DATASET_POLICY_ACK": DEIDENTIFIED_ACK,
            "LAB_SCALE_PROFILE": "workstation",
            "LAB_BULK_CONCURRENCY": "7",
            "LAB_DATASET_MAX_RECORDS": "1000",
            "LAB_DATASET_MAX_BYTES": "1000000",
            "LAB_DATASET_MAX_LINE_BYTES": "4096",
            "LAB_BULK_REQUEST_TIMEOUT_SECONDS": "12",
            "LAB_GIT_COMMIT": "abcdef1234567",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(bulk_ingest, "ingest_dataset", return_value=fake_receipt) as mocked,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(bulk_ingest.main(), 0)
        written = artifacts / "latest-capacity-receipt.json"
        self.assertEqual(json.loads(written.read_text(encoding="utf-8")), fake_receipt)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["concurrency"], 7)
        self.assertEqual(kwargs["request_timeout"], 12.0)
        self.assertEqual(kwargs["scale_profile"], "workstation")
        self.assertEqual(kwargs["git_commit"], "abcdef1234567")


if __name__ == "__main__":
    unittest.main()
