from __future__ import annotations

import json

from lians.cli import main
from lians.evaluation import REPORT_SCHEMA, load_dataset, render_summary, run_evaluation


class _EvaluationClient:
    def __init__(self) -> None:
        self.contents: list[str] = []

    def add_batch(self, agent_id: str, items: list[dict]) -> list[dict]:
        self.contents.extend(str(item["content"]) for item in items)
        return []

    def recall(self, **kwargs) -> dict:
        query = str(kwargs["query"]).casefold()
        if "salary" in query:
            content = "user: My salary is now 140000 dollars per year."
        elif "language" in query:
            content = "user: My favorite programming language is Python."
        else:
            content = self.contents[-1]
        return {
            "memories": [{"id": "memory-1", "content": content}],
            "latency_ms": 12.5,
            "token_estimate": 24,
            "deadline_exceeded": False,
            "receipt_sha256": "receipt",
        }


def _dataset() -> dict:
    return {
        "name": "product-cli-fixture",
        "samples": [
            {
                "agent_id": "developer",
                "sessions": [
                    {
                        "date": "2026-01-01",
                        "turns": [
                            {"speaker": "user", "text": "My salary was 120000."},
                            {"speaker": "user", "text": "My salary is now 140000."},
                            {"speaker": "user", "text": "I prefer Python."},
                        ],
                    }
                ],
                "questions": [
                    {
                        "question": "What is the current salary?",
                        "answer": "140000",
                        "stale": "120000",
                        "category": "temporal",
                    },
                    {
                        "question": "What language is preferred?",
                        "answer": "Python",
                        "category": "preference",
                    },
                ],
            }
        ],
    }


def test_evaluation_report_measures_quality_latency_and_staleness():
    report = run_evaluation(
        _EvaluationClient(),
        _dataset(),
        min_recall=1.0,
        max_stale_leak_rate=0.0,
        max_p95_latency_ms=20.0,
    )

    assert report["schema"] == REPORT_SCHEMA
    assert report["evaluation_passed"] is True
    assert report["answer_recall_at_k"] == 1.0
    assert report["stale_leak_rate"] == 0.0
    assert report["latency_ms"]["p95"] == 12.5
    assert report["average_token_estimate"] == 24.0
    assert report["by_category"] == {"preference": 1.0, "temporal": 1.0}
    assert "PASS" in render_summary(report)


def test_evaluation_report_fails_enforced_threshold():
    report = run_evaluation(
        _EvaluationClient(),
        _dataset(),
        min_recall=1.0,
        max_p95_latency_ms=10.0,
    )

    assert report["evaluation_passed"] is False
    assert report["threshold_checks"]["p95_latency_ms"] is False
    assert "failed thresholds" in render_summary(report)


def test_sample_evaluation_dataset_is_packaged():
    dataset = load_dataset()

    assert dataset["name"] == "lians-sample-memory-eval"
    assert len(dataset["samples"]) >= 2


def test_eval_command_writes_machine_readable_report(tmp_path, monkeypatch):
    dataset_path = tmp_path / "dataset.json"
    report_path = tmp_path / "report.json"
    dataset_path.write_text(json.dumps(_dataset()), encoding="utf-8")

    class _ContextClient(_EvaluationClient):
        def __init__(self, **kwargs) -> None:
            super().__init__()

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    monkeypatch.setattr("lians.local_client.LocalLiansClient", _ContextClient)
    exit_code = main([
        "eval",
        str(dataset_path),
        "--min-recall",
        "1",
        "--max-p95-latency-ms",
        "20",
        "--output",
        str(report_path),
    ])

    assert exit_code == 0
    assert json.loads(report_path.read_text(encoding="utf-8"))["evaluation_passed"] is True


def test_doctor_reports_required_local_runtime(capsys):
    assert main(["doctor"]) == 0
    assert "Lians local runtime" in capsys.readouterr().out
