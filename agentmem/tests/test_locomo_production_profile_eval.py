"""Paid-call-free contracts for the full LOCOMO production-profile evaluator."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import tiktoken
import pytest


AGENTMEM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = AGENTMEM_ROOT.parent
if str(AGENTMEM_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTMEM_ROOT))

from benchmarks.locomo_production_profile_eval import (  # noqa: E402
    DEFAULT_HOOK,
    content_to_dia_ids,
    evaluate_prompt,
    load_production_renderer,
    nearest_rank,
    policy_minimum_score,
    production_settings,
    summarize,
)


def test_nearest_rank_is_deterministic_at_requested_percentiles() -> None:
    values = list(range(1, 101))
    assert nearest_rank(values, 0.50) == 50
    assert nearest_rank(values, 0.95) == 95
    assert nearest_rank(values, 0.99) == 99
    assert nearest_rank([], 0.95) is None


def test_threshold_policies_support_fixed_and_score_relative_modes() -> None:
    ranked = [{"score": value} for value in (0.8, 0.7, 0.6, 0.5)]
    assert policy_minimum_score({"kind": "fixed", "minimum_score": 0.45}, ranked) == 0.45
    assert policy_minimum_score({"kind": "top_n", "n": 3}, ranked) == 0.6
    assert policy_minimum_score({"kind": "top_margin", "margin": 0.1}, ranked) == pytest.approx(0.7)
    assert policy_minimum_score({"kind": "top_ratio", "ratio": 0.75}, ranked) == pytest.approx(0.6)
    calibrated = {
        "kind": "provider_quantile_top_n",
        "n": 3,
        "calibrated_top_score_gate": 0.75,
    }
    assert policy_minimum_score(calibrated, ranked) == 0.6
    assert policy_minimum_score(calibrated, [{"score": 0.7}]) > 0.7


def test_content_mapping_matches_photo_rendering_and_dialogue_ids() -> None:
    conversation = {
        "session_1": [
            {"speaker": "A", "text": "Hello", "dia_id": "D1:1"},
            {
                "speaker": "B",
                "text": "Look",
                "blip_caption": "a blue bowl",
                "dia_id": "D1:2",
            },
        ],
        "session_1_date_time": "8 May 2023",
    }
    assert content_to_dia_ids(conversation) == {
        "A: Hello": ("D1:1",),
        "B: Look [shared a photo: a blue bowl]": ("D1:2",),
    }


def test_prompt_evaluation_tracks_threshold_and_char_cap_boundaries() -> None:
    renderer = load_production_renderer(DEFAULT_HOOK)
    settings = replace(production_settings(renderer), max_tokens=64, min_score=0.45)
    long_answer = "target " + "x" * 500
    rows = [
        {
            "content": "first evidence",
            "score": 0.9,
            "event_time": "2023-01-01",
            "dia_id": "D1:1",
        },
        {
            "content": long_answer,
            "score": 0.8,
            "event_time": "2023-01-02",
            "dia_id": "D1:2",
        },
        {
            "content": "below threshold",
            "score": 0.44,
            "event_time": "2023-01-03",
            "dia_id": "D1:3",
        },
    ]
    result = evaluate_prompt(
        renderer=renderer,
        settings=settings,
        encoder=tiktoken.get_encoding("o200k_base"),
        question_id="fixture",
        conversation_idx=0,
        qa_index=0,
        qa={
            "question": "fixture?",
            "answer": "target",
            "evidence": ["D1:1", "D1:2"],
            "category": 1,
        },
        source="fixture",
        ranked_items=rows,
    )

    assert result["status"] == "injected"
    assert result["raw_topk_count"] == 3
    assert result["eligible_before_char_cap_count"] == 2
    assert result["rendered_after_char_cap_count"] in {1, 2}
    assert result["evidence_any_before_char_cap"] is True
    assert result["evidence_all_before_char_cap"] is True
    assert result["returned_chars"] <= 64 * 4
    assert result["truncated"] is True


def test_summary_keeps_missing_answer_denominators_out_of_rates() -> None:
    base = {
        "status": "injected",
        "injected": True,
        "truncated": False,
        "returned_tokens_o200k": 10,
        "returned_token_estimate_char_div_4": 12,
        "evidence_any_raw_topk": True,
        "evidence_all_raw_topk": True,
        "evidence_any_before_char_cap": True,
        "evidence_all_before_char_cap": True,
        "evidence_any_after_char_cap": True,
        "evidence_all_after_char_cap": True,
        "answer_string_raw_topk": True,
        "answer_string_before_char_cap": True,
        "answer_string_after_char_cap": True,
        "adversarial_answer_string_raw_topk": None,
        "adversarial_answer_string_before_char_cap": None,
        "adversarial_answer_string_after_char_cap": None,
    }
    missing = dict(base)
    missing.update(
        answer_string_raw_topk=None,
        answer_string_before_char_cap=None,
        answer_string_after_char_cap=None,
    )
    result = summarize([base, missing])

    assert result["answer_string_coverage"]["answer_string_after_char_cap"] == {
        "n": 1,
        "count": 1,
        "rate": 1.0,
    }
    assert (
        result["adversarial_answer_string_coverage"]["adversarial_answer_string_after_char_cap"][
            "n"
        ]
        == 0
    )
    assert result["returned_context_tokens_injected_only"]["p95"] == 10
