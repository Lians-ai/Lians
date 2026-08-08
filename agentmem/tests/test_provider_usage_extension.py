"""Focused contracts for the provider-neutral usage-extension evaluator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.provider_usage_extension import (  # noqa: E402
    EvaluationError,
    evaluate_case,
    main,
)


def _case(*, baseline: dict, candidate: dict, quality_passed: bool = True) -> dict:
    return {
        "provider": "test-provider",
        "workload": "protected-recall-v1",
        "target_usage_extension_percent": 85,
        "quality_gate": {
            "protected_checks": [
                {"name": "answer_correct", "passed": quality_passed},
                {"name": "safety", "passed": True},
            ],
            "baseline_score": 0.90,
            "candidate_score": 0.89,
            "minimum_candidate_score": 0.85,
            "maximum_score_regression": 0.02,
        },
        "baseline": baseline,
        "candidate": candidate,
    }


def test_85_percent_extension_requires_about_45_95_percent_cost_reduction():
    report = evaluate_case(
        _case(
            baseline={"raw_input_tokens": 1000},
            candidate={"raw_input_tokens": 500},
        )
    )

    assert report["target"]["same_budget_usage_multiplier"] == 1.85
    assert report["target"]["maximum_candidate_cost_ratio"] == pytest.approx(1 / 1.85)
    assert report["target"]["minimum_task_cost_reduction_percent"] == pytest.approx(45.945946)
    assert "does not mean an 85% token reduction" in report["target"]["interpretation"]
    assert report["observed"]["same_budget_usage_multiplier"] == 2.0
    assert report["verdict"]["qualified_target_met"] is True


def test_protected_quality_failure_blocks_an_economically_passing_candidate():
    report = evaluate_case(
        _case(
            baseline={"raw_input_tokens": 1000},
            candidate={"raw_input_tokens": 100},
            quality_passed=False,
        )
    )

    assert report["evaluation_order"][0] == "protected_quality"
    assert report["verdict"]["economic_target_met"] is True
    assert report["verdict"]["protected_quality_passed"] is False
    assert report["verdict"]["qualified_target_met"] is False
    assert report["verdict"]["status"] == "quality_gate_failed"
    assert "diagnostic only" in report["verdict"]["statement"]


def test_85_percent_token_reduction_is_reported_as_distinct_from_extension():
    report = evaluate_case(
        _case(
            baseline={"raw_input_tokens": 1000},
            candidate={"raw_input_tokens": 150},
        )
    )

    raw_change = report["observed"]["token_changes"]["raw_input_tokens"]
    assert raw_change["reduction_percent"] == 85.0
    assert report["observed"]["same_budget_usage_multiplier"] == pytest.approx(6.666667)
    assert report["observed"]["same_budget_usage_extension_percent"] == pytest.approx(566.666667)


def test_comparable_provider_credits_are_the_economic_basis_and_tokens_are_retained():
    case = _case(
        baseline={
            "raw_input_tokens": 1000,
            "cached_input_tokens": 200,
            "uncached_input_tokens": 800,
            "output_tokens": 100,
            "reported_credits": 10,
        },
        candidate={
            "raw_input_tokens": 1200,
            "cached_input_tokens": 1000,
            "uncached_input_tokens": 200,
            "output_tokens": 100,
            "reported_credits": 5,
        },
    )
    report = evaluate_case(case)

    assert report["accounting"]["basis"] == "provider_reported_cost"
    assert report["accounting"]["unit"] == "credits"
    assert report["observed"]["candidate_cost_ratio"] == 0.5
    assert report["accounting"]["candidate"]["token_counts"] == {
        "raw_input_tokens": 1200,
        "cached_input_tokens": 1000,
        "uncached_input_tokens": 200,
        "output_tokens": 100,
    }
    assert report["verdict"]["qualified_target_met"] is True


def test_locally_estimated_credits_have_a_distinct_basis_and_provenance():
    case = _case(
        baseline={
            "raw_input_tokens": 1000,
            "output_tokens": 10,
            "estimated_credits": 10,
        },
        candidate={
            "raw_input_tokens": 500,
            "output_tokens": 10,
            "estimated_credits": 5,
        },
    )
    case["estimate_metadata"] = {
        "method": "published rates applied to measured tokens",
        "source": "provider pricing page",
        "source_url": "https://example.test/pricing",
        "as_of": "2026-08-08",
        "rates": {"input_credits_per_million": 5},
    }

    report = evaluate_case(case)

    assert report["accounting"]["basis"] == "estimated_cost"
    assert report["accounting"]["unit"] == "credits"
    assert report["accounting"]["baseline"]["reported_cost"] is None
    assert report["accounting"]["baseline"]["estimated_cost"] == 10
    assert report["accounting"]["estimate_metadata"] == case["estimate_metadata"]


def test_estimated_cost_requires_method_and_source_metadata():
    case = _case(
        baseline={"raw_input_tokens": 1000, "estimated_credits": 10},
        candidate={"raw_input_tokens": 500, "estimated_credits": 5},
    )

    with pytest.raises(EvaluationError, match="estimate_metadata with method and source"):
        evaluate_case(case)


def test_measurement_cannot_mix_reported_and_estimated_values():
    with pytest.raises(EvaluationError, match="cannot mix provider-reported and locally estimated"):
        evaluate_case(
            _case(
                baseline={
                    "raw_input_tokens": 1000,
                    "reported_credits": 10,
                    "estimated_credits": 10,
                },
                candidate={"raw_input_tokens": 500, "reported_credits": 5},
            )
        )


def test_complete_cached_components_are_weighted_without_double_counting_raw_input():
    case = _case(
        baseline={
            "raw_input_tokens": 1000,
            "cached_input_tokens": 800,
            "uncached_input_tokens": 200,
            "output_tokens": 10,
        },
        candidate={
            "raw_input_tokens": 900,
            "cached_input_tokens": 850,
            "uncached_input_tokens": 50,
            "output_tokens": 10,
        },
    )
    case["token_weights"] = {
        "raw_input": 1,
        "cached_input": 0.1,
        "uncached_input": 1,
        "output": 2,
    }
    report = evaluate_case(case)

    # 800*.1 + 200 + 10*2 = 300. Raw 1000 is display-only, not added again.
    assert report["accounting"]["baseline"]["weighted_token_cost"] == 300
    assert report["accounting"]["candidate"]["weighted_token_cost"] == 155
    assert report["accounting"]["candidate"]["token_counts"]["raw_input_tokens"] == 900


@pytest.mark.parametrize("missing_from", ["baseline", "candidate"])
def test_output_token_dimension_cannot_disappear_from_one_side(missing_from: str):
    baseline = {"raw_input_tokens": 1000, "output_tokens": 20}
    candidate = {"raw_input_tokens": 500, "output_tokens": 10}
    (baseline if missing_from == "baseline" else candidate).pop("output_tokens")

    with pytest.raises(EvaluationError, match="both provide output_tokens or both omit"):
        evaluate_case(_case(baseline=baseline, candidate=candidate))


def test_raw_only_and_cached_uncached_input_bases_cannot_be_compared():
    with pytest.raises(EvaluationError, match="token input accounting basis must match"):
        evaluate_case(
            _case(
                baseline={"raw_input_tokens": 1000, "output_tokens": 10},
                candidate={
                    "cached_input_tokens": 400,
                    "uncached_input_tokens": 100,
                    "output_tokens": 10,
                },
            )
        )


def test_same_input_basis_still_requires_symmetric_reported_dimensions():
    with pytest.raises(EvaluationError, match="same token accounting dimensions"):
        evaluate_case(
            _case(
                baseline={
                    "raw_input_tokens": 1000,
                    "cached_input_tokens": 800,
                    "uncached_input_tokens": 200,
                    "output_tokens": 10,
                },
                candidate={
                    "cached_input_tokens": 400,
                    "uncached_input_tokens": 100,
                    "output_tokens": 10,
                },
            )
        )


@pytest.mark.parametrize("inconsistent_side", ["baseline", "candidate"])
def test_raw_input_must_exactly_equal_cached_plus_uncached(inconsistent_side: str):
    baseline = {
        "raw_input_tokens": 1000,
        "cached_input_tokens": 800,
        "uncached_input_tokens": 200,
        "output_tokens": 10,
    }
    candidate = {
        "raw_input_tokens": 500,
        "cached_input_tokens": 400,
        "uncached_input_tokens": 100,
        "output_tokens": 10,
    }
    target = baseline if inconsistent_side == "baseline" else candidate
    target["raw_input_tokens"] += 1

    with pytest.raises(
        EvaluationError,
        match=r"raw_input_tokens must equal cached_input_tokens \+ uncached_input_tokens exactly",
    ):
        evaluate_case(_case(baseline=baseline, candidate=candidate))


@pytest.mark.parametrize(
    ("baseline_extra", "candidate_extra", "match"),
    [
        ({"reported_cost": 1, "reported_cost_unit": "usd"}, {}, "both provide"),
        (
            {"reported_cost": 1, "reported_cost_unit": "usd"},
            {"reported_cost": 0.5, "reported_cost_unit": "credits"},
            "units must match",
        ),
    ],
)
def test_provider_costs_cannot_be_mixed_or_compared_across_units(
    baseline_extra: dict, candidate_extra: dict, match: str
):
    baseline = {"raw_input_tokens": 1000, **baseline_extra}
    candidate = {"raw_input_tokens": 500, **candidate_extra}
    with pytest.raises(EvaluationError, match=match):
        evaluate_case(_case(baseline=baseline, candidate=candidate))


def test_cli_writes_a_machine_readable_report(tmp_path: Path):
    case_path = tmp_path / "case.json"
    report_path = tmp_path / "report.json"
    case_path.write_text(
        json.dumps(
            _case(
                baseline={"raw_input_tokens": 1000},
                candidate={"raw_input_tokens": 500},
            )
        ),
        encoding="utf-8",
    )

    assert main([str(case_path), "--out", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "lians.provider-usage-extension.v1"
    assert report["verdict"]["qualified_target_met"] is True
    assert "universal" in report["verdict"]["statement"]


def test_recorded_codex_pair_clears_usage_extension_target():
    case = json.loads(
        (
            ROOT.parent / "docs" / "benchmarks" / "codex-usage-extension-case-2026-08-08.json"
        ).read_text(encoding="utf-8")
    )
    report = evaluate_case(case)

    assert report["quality_gate"]["passed"] is True
    assert report["accounting"]["basis"] == "estimated_cost"
    assert report["accounting"]["estimate_metadata"]["rates"] == {
        "uncached_input_credits_per_million": 5.0,
        "cached_input_credits_per_million": 0.5,
        "output_credits_per_million": 30.0,
    }
    assert report["observed"]["same_budget_usage_multiplier"] == 2.100155
    assert report["verdict"]["qualified_target_met"] is True
