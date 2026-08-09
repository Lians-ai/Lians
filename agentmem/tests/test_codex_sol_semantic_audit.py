"""Paid-call-free contracts for the secondary Codex Sol semantic audit."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.codex_sol_semantic_audit import (  # noqa: E402
    AUDIT_LABEL,
    FORBIDDEN_BLIND_KEYS,
    JudgmentUnit,
    SemanticAuditError,
    build_claude_request,
    build_rubric,
    call_claude_judge,
    collect_observations,
    deterministic_decision,
    freeze_rubric,
    load_and_verify_rubric,
    run_audit,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _raw_answer(path: Path, answer: str) -> None:
    events = [
        {"type": "thread.started", "thread_id": "fixture"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 2,
                "reasoning_output_tokens": 0,
            },
        },
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> dict[str, Path]:
    dataset = tmp_path / "locomo.json"
    _write_json(
        dataset,
        [
            {
                "qa": [
                    {
                        "question": "What did Ada research?",
                        "answer": "Adoption agencies",
                        "evidence": ["D1:1"],
                        "category": 1,
                    },
                    {
                        "question": "What did Bea research?",
                        "evidence": ["D1:1"],
                        "category": 5,
                        "adversarial_answer": "Adoption agencies",
                    },
                ],
                "conversation": {
                    "session_1_date_time": "8 May 2023",
                    "session_1": [
                        {
                            "speaker": "Ada",
                            "dia_id": "D1:1",
                            "text": "I researched adoption agencies.",
                        }
                    ],
                },
            }
        ],
    )
    manifest = tmp_path / "manifest.json"
    _write_json(
        manifest,
        {
            "schema_version": "lians.codex-sol-prompt-matrix-manifest.v1",
            "suite_id": "semantic-fixture",
            "contexts": [
                {
                    "id": "fixture",
                    "dataset_artifact": str(dataset),
                    "conversation_index": 0,
                }
            ],
            "dataset_prompts": [
                {
                    "id": "cat1",
                    "context_id": "fixture",
                    "qa_index": 0,
                    "category": 1,
                    "accepted_answers": ["Adoption agencies"],
                },
                {
                    "id": "cat5",
                    "context_id": "fixture",
                    "qa_index": 1,
                    "category": 5,
                    "accepted_answers": ["UNKNOWN"],
                    "denied_answers": ["Adoption agencies"],
                },
            ],
        },
    )
    raw_dir = tmp_path / "raw"
    answers = [
        ("run-1", "cat1", "baseline", "Adoption agencies", True),
        (
            "run-2",
            "cat1",
            "candidate",
            "Adoption agencies and counseling",
            False,
        ),
        ("run-3", "cat5", "baseline", "UNKNOWN", True),
        ("run-4", "cat5", "candidate", "Unknown", False),
    ]
    runs: list[dict[str, object]] = []
    for index, (run_id, prompt_id, mode, answer, exact) in enumerate(answers, start=1):
        raw_path = raw_dir / f"{index:05d}-{run_id}.stdout.jsonl"
        _raw_answer(raw_path, answer)
        runs.append(
            {
                "run_id": run_id,
                "prompt_id": prompt_id,
                "mode": mode,
                "profile_id": "hidden-profile",
                "repetition": 1,
                "answer": answer,
                "protected_quality_passed": exact,
                "raw_stdout_artifact": str(raw_path),
                "raw_stdout_sha256": _sha256(raw_path),
            }
        )
    report = tmp_path / "matrix-report.json"
    _write_json(
        report,
        {
            "schema_version": "lians.codex-sol-prompt-matrix-report.v1",
            "suite_id": "semantic-fixture",
            "runs": runs,
            "estimated_credit_budget": {
                "observed_from_completed_runs": 1.25,
                "provider_reported": False,
            },
            "verdict": {
                "status": "declared_matrix_not_qualified",
                "qualified": False,
                "statement": "Fixture primary verdict.",
            },
        },
    )
    return {"dataset": dataset, "manifest": manifest, "raw_dir": raw_dir, "report": report}


def _walk_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(map(str, value))
        for item in value.values():
            found.update(_walk_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_walk_keys(item))
    return found


def _unit(
    *,
    answer: str,
    question: str = "When did Ada go?",
    category: int = 2,
    accepted: tuple[str, ...] = ("2022",),
) -> JudgmentUnit:
    return JudgmentUnit(
        blind_case_id="blind-fixture",
        rubric_case_id="rubric-fixture",
        prompt_id="prompt-fixture",
        answer_id="answer-fixture",
        answer=answer,
        question=question,
        category=category,
        ground_truth=accepted[0],
        accepted_answers=accepted,
        denied_answers=(),
        evidence=(),
    )


def test_real_frozen_rubric_reconstructs_with_stable_hash() -> None:
    repo = ROOT.parent
    manifest = (
        repo / "docs" / "benchmarks" / "manifests" / "codex-sol-locomo-10-case-bge-onnx-v2.json"
    )
    dataset = repo / "agentmem" / "benchmarks" / "data" / "locomo10.json"
    frozen_path = (
        repo
        / "docs"
        / "benchmarks"
        / "codex-sol-matrix-bge-onnx-v2-semantic-rubric-2026-08-08.json"
    )

    frozen = load_and_verify_rubric(frozen_path, manifest, dataset)

    assert frozen == build_rubric(manifest, dataset)
    assert frozen["rubric_sha256"] == (
        "bd03bb21fa3bca7b007197a45c3c1d707fab954adc54ac2c5f2a8cfca4564a64"
    )
    assert frozen["rubric"]["source_policy"]["matrix_outputs_or_answer_strings_used"] is False
    assert len(frozen["rubric"]["cases"]) == 10


def test_category5_is_exact_and_surface_aliases_are_narrow() -> None:
    assert deterministic_decision(_unit(answer="In 2022"))["passed"] is True
    assert (
        deterministic_decision(
            _unit(
                answer="Ada is a trans woman",
                question="What is Ada's identity?",
                category=1,
                accepted=("Transgender woman",),
            )
        )["passed"]
        is True
    )
    assert (
        deterministic_decision(
            _unit(
                answer="Unclear because the record is incomplete",
                question="Would Ada still pursue counseling?",
                category=3,
                accepted=("Likely no",),
            )
        )["passed"]
        is False
    )
    assert deterministic_decision(_unit(answer="UNKNOWN", category=5))["passed"] is True
    assert deterministic_decision(_unit(answer="UNKNOWN.", category=5))["passed"] is False


def test_rubric_freeze_is_separate_and_detects_later_mutation(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    frozen_path = tmp_path / "rubric.json"
    frozen = freeze_rubric(paths["manifest"], paths["dataset"], frozen_path)
    assert frozen["freeze_status"] == "frozen_before_semantic_judging"

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    manifest["dataset_prompts"][0]["accepted_answers"] = ["Something else"]
    _write_json(paths["manifest"], manifest)

    with pytest.raises(SemanticAuditError, match="omit the dataset ground truth"):
        load_and_verify_rubric(frozen_path, paths["manifest"], paths["dataset"])


def test_raw_jsonl_hash_and_answer_are_verified(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    _, observations, integrity = collect_observations(paths["report"], paths["raw_dir"])
    assert len(observations) == 4
    assert integrity["all_declared_raw_sha256_verified"] is True

    first = sorted(paths["raw_dir"].glob("*.stdout.jsonl"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "{}\n", encoding="utf-8")
    with pytest.raises(SemanticAuditError, match="hash mismatch"):
        collect_observations(paths["report"], paths["raw_dir"])


def test_claude_contract_requires_pinned_model_and_small_cap() -> None:
    request = build_claude_request([], "a" * 64)
    with pytest.raises(SemanticAuditError, match="pinned version"):
        call_claude_judge(
            request,
            claude_exe="unused",
            model="haiku",
            max_budget_usd=Decimal("0.01"),
            timeout_seconds=1,
            auth_status={"logged_in": True},
            cli_version="fixture",
        )
    with pytest.raises(SemanticAuditError, match="no more than"):
        call_claude_judge(
            request,
            claude_exe="unused",
            model="claude-haiku-4-5-20251001",
            max_budget_usd=Decimal("0.11"),
            timeout_seconds=1,
            auth_status={"logged_in": True},
            cli_version="fixture",
        )


def test_end_to_end_audit_is_blind_deduplicated_and_preserves_failure(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    frozen_path = tmp_path / "rubric.json"
    freeze_rubric(paths["manifest"], paths["dataset"], frozen_path)
    primary_hash = _sha256(paths["report"])

    def fake_claude(
        command: list[str] | tuple[str, ...], prompt: str, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert "--tools" in command
        assert command[command.index("--tools") + 1] == ""
        assert "--safe-mode" in command
        assert "--max-budget-usd" in command
        assert timeout == 30
        payload = json.loads(prompt)
        assert FORBIDDEN_BLIND_KEYS.isdisjoint(_walk_keys(payload))
        assert len(payload["cases"]) == 1
        decisions = [
            {
                "blind_case_id": item["blind_case_id"],
                "passed": False,
                "reason": "unsupported_extra_claim",
                "rationale": "The extra counseling claim is not supported by the cited evidence.",
            }
            for item in payload["cases"]
        ]
        envelope = {
            "type": "result",
            "total_cost_usd": 0.00123,
            "usage": {"input_tokens": 100, "output_tokens": 20},
            "modelUsage": {
                "claude-haiku-4-5-20251001": {
                    "inputTokens": 100,
                    "outputTokens": 20,
                    "costUSD": 0.00123,
                }
            },
            "structured_output": {"decisions": decisions},
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    report = run_audit(
        rubric_path=frozen_path,
        manifest_path=paths["manifest"],
        dataset_path=paths["dataset"],
        matrix_report_path=paths["report"],
        raw_dir=paths["raw_dir"],
        output_json_path=tmp_path / "audit.json",
        output_markdown_path=tmp_path / "audit.md",
        raw_judge_path=tmp_path / "judge-raw.json",
        claude_model="claude-haiku-4-5-20251001",
        max_budget_usd=Decimal("0.01"),
        timeout_seconds=30,
        claude_runner=fake_claude,
        claude_auth_status={
            "logged_in": True,
            "auth_method": "fixture",
            "api_provider": "fixture",
        },
        claude_cli_version="fixture-cli",
    )

    assert _sha256(paths["report"]) == primary_hash
    assert report["primary_predeclared_verdict"] == {
        "status": "declared_matrix_not_qualified",
        "qualified": False,
        "statement": "Fixture primary verdict.",
    }
    assert report["primary_failure_preserved"] is True
    assert report["semantic_qualification"] == "not_applicable"
    assert report["secondary_semantic_summary"] == {
        "status": "complete",
        "total": 4,
        "resolved": 4,
        "unresolved": 0,
        "passed": 2,
        "failed": 2,
        "pass_rate": 0.5,
        "resolved_pass_rate": 0.5,
        "minimum_possible_pass_rate": 0.5,
        "maximum_possible_pass_rate": 0.5,
        "all_runs_passed": False,
    }
    assert report["judge"]["unique_answer_string_count"] == 4
    assert report["judge"]["unique_question_answer_unit_count"] == 4
    assert report["judge"]["external_unit_count"] == 1
    assert report["cost_accounting"]["total_posthoc_provider_reported_usd"] == "0.00123"
    raw_judge = json.loads((tmp_path / "judge-raw.json").read_text(encoding="utf-8"))
    assert FORBIDDEN_BLIND_KEYS.isdisjoint(_walk_keys(raw_judge["external_judge_request"]))
    assert FORBIDDEN_BLIND_KEYS.isdisjoint(_walk_keys(raw_judge["ordered_blind_units"]))
    assert raw_judge["audit_label"] == AUDIT_LABEL
    assert "Secondary/posthoc only" in (tmp_path / "audit.md").read_text(encoding="utf-8")


def test_rubric_only_audit_leaves_semantic_units_unresolved(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    frozen_path = tmp_path / "rubric.json"
    freeze_rubric(paths["manifest"], paths["dataset"], frozen_path)

    report = run_audit(
        rubric_path=frozen_path,
        manifest_path=paths["manifest"],
        dataset_path=paths["dataset"],
        matrix_report_path=paths["report"],
        raw_dir=paths["raw_dir"],
        output_json_path=tmp_path / "audit.json",
        output_markdown_path=tmp_path / "audit.md",
        raw_judge_path=tmp_path / "judge-raw.json",
        external_judge_enabled=False,
        external_attempt_disclosure={
            "attempt_count": 1,
            "exact_provider_reported_total_cost_usd": None,
            "aggregate_cost_known": False,
            "hard_cap_sum_upper_bound_usd": "0.05",
        },
    )

    assert report["secondary_semantic_summary"]["status"] == "incomplete"
    assert report["secondary_semantic_summary"]["resolved"] == 3
    assert report["secondary_semantic_summary"]["unresolved"] == 1
    assert report["secondary_semantic_summary"]["pass_rate"] is None
    assert report["judge"]["external_unit_count"] == 0
    assert report["judge"]["unresolved_unit_count"] == 1
    assert report["cost_accounting"]["total_posthoc_provider_reported_usd"] == "0"
    assert report["cost_accounting"]["accepted_audit_cost_is_exact"] is True
    assert report["cost_accounting"]["aggregate_experiment_cost_is_exact"] is False
