import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from benchmarks.codex_sol_ultra_checkout_hook_usage_evidence import (
    ABBA_RUNS,
    GOLD_ANSWER,
    PUBLIC_QUESTION,
    build_report,
    calculate_economics,
    estimated_sol_credits,
    main,
    publication_safety_violations,
    render_report,
)

REPORT_PATH = (
    Path(__file__).parents[2]
    / "docs"
    / "benchmarks"
    / "codex-sol-ultra-checkout-hook-usage-evidence-2026-08-08.json"
)


def _all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def test_four_frozen_runs_preserve_abba_usage_and_exact_answers():
    assert [run["arm"] for run in ABBA_RUNS] == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]
    assert [run["answer"] for run in ABBA_RUNS] == [GOLD_ANSWER] * 4
    assert [run["usage"]["input_tokens"] for run in ABBA_RUNS] == [
        25938,
        12718,
        12718,
        25938,
    ]
    assert build_report()["quality_gate"] == {
        "exact_matches": 4,
        "runs": 4,
        "passed": True,
        "candidate_receipts_injected": True,
    }


def test_fixed_rates_recompute_each_estimated_credit_value_exactly():
    credits = [estimated_sol_credits(run["usage"]) for run in ABBA_RUNS]

    assert credits == [
        Decimal("3.249"),
        Decimal("1.5965"),
        Decimal("1.5965"),
        Decimal("3.249"),
    ]


def test_pooled_abba_economics_recompute_publication_values():
    assert calculate_economics(ABBA_RUNS) == {
        "baseline_pooled_estimated_sol_credits": 6.498,
        "candidate_pooled_estimated_sol_credits": 3.193,
        "same_budget_usage_multiplier": 2.03507673,
        "same_budget_usage_extension_percent": 103.507673035,
        "publication_display": "2.035x / +103.51%",
    }


def test_checkout_hook_receipts_and_slower_candidate_wall_are_explicit():
    report = build_report()
    receipts = [run["hook_receipt"] for run in report["runs"]]

    assert receipts == [
        None,
        {"injected": True, "retrieval_degraded": False, "elapsed_ms": 18834},
        {"injected": True, "retrieval_degraded": False, "elapsed_ms": 15474},
        None,
    ]
    assert report["selected_second_repeat_wall"] == {
        "baseline_ms": 3466.147,
        "candidate_ms": 20458.282,
        "candidate_over_baseline_ratio": 5.902312279,
        "candidate_end_to_end_wall_slower": True,
    }


def test_installed_plugin_ab_is_an_explicit_rejected_non_result():
    report = build_report()

    assert report["current_installed_plugin_paid_ab"] == {
        "accepted": False,
        "status": "rejected before baseline",
        "reason": "the host failed to dispatch the trusted installed-plugin hook",
        "accepted_installed_plugin_economics_result_exists": False,
    }
    assert report["claim_boundary"]["installed_public_plugin_end_to_end_result"] is False
    assert report["claim_boundary"]["universal_usage_extension_supported"] is False
    assert report["claim_boundary"]["estimated_credits_not_provider_debits"] is True


def test_report_contains_no_local_paths_identifiers_extra_prompts_or_raw_traces():
    report = build_report()
    rendered = render_report()
    keys = set(_all_keys(report))

    assert report["public_locomo_item"]["question"] == PUBLIC_QUESTION
    assert publication_safety_violations(report) == []
    assert not re.search(r"(?i)\b[a-z]:[\\/]", rendered)
    assert not re.search(r"(?i)(?:/home/|/users/|%userprofile%)", rendered)
    assert not re.search(r"\b[a-f0-9]{64}\b", rendered)
    assert {
        "question_id",
        "thread_id",
        "prompt",
        "events",
        "source_artifacts",
        "raw_stdout",
        "raw_stderr",
        "sha256",
    }.isdisjoint(keys)


def test_checked_in_report_is_exact_deterministic_generator_output():
    assert REPORT_PATH.read_text(encoding="utf-8") == render_report()
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8")) == build_report()


def test_cli_writes_the_same_report_and_bad_accounting_fails(tmp_path):
    output = tmp_path / "usage-evidence.json"
    bad_usage = dict(ABBA_RUNS[0]["usage"])
    bad_usage["input_tokens"] += 1
    cache_write_usage = dict(ABBA_RUNS[0]["usage"])
    cache_write_usage["uncached_input_tokens"] -= 1
    cache_write_usage["cache_write_input_tokens"] = 1

    assert main(["--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == render_report()
    with pytest.raises(ValueError, match="does not balance"):
        estimated_sol_credits(bad_usage)
    with pytest.raises(ValueError, match="cache-write pricing is undocumented"):
        estimated_sol_credits(cache_write_usage)
