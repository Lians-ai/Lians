"""Paid-call-free contracts for the lexical + ONNX LOCOMO benchmark."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


AGENTMEM_ROOT = Path(__file__).resolve().parents[1]
if str(AGENTMEM_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTMEM_ROOT))

from benchmarks.locomo_onnx_lexical_rerank_eval import (  # noqa: E402
    Corpus,
    _tokens,
    aggregate,
    checked_out_product_candidates,
    lexical_candidates,
    nearest_rank,
    rendered_records,
)


def _corpus(contents: tuple[str, ...]) -> Corpus:
    frequencies: list[dict[str, int]] = []
    lengths: list[int] = []
    for content in contents:
        words = _tokens(content)
        values: dict[str, int] = {}
        for word in words:
            values[word] = values.get(word, 0) + 1
        frequencies.append(values)
        lengths.append(len(words))
    return Corpus(
        # Corpus rows are loaded in event_time/id order; stable benchmark ties
        # therefore implement the same explicit product tie contract.
        row_ids=("a", "b", "c", "d"),
        dia_ids=("D1:1", "D1:2", "D1:3", "D1:4"),
        contents=contents,
        event_times=("2023-01-01", "2023-01-01", "2023-01-02", "2023-01-03"),
        term_frequencies=tuple(frequencies),
        lengths=np.asarray(lengths, dtype=np.float64),
    )


def test_lexical_replica_matches_checked_out_product_order_and_ties() -> None:
    corpus = _corpus(
        (
            "Caroline planned the support group.",
            "Caroline plans a support group.",
            "A completely unrelated memory.",
            "Another unrelated memory.",
        )
    )
    query = "When was Caroline planning the support group?"

    assert lexical_candidates(corpus, query, 4) == checked_out_product_candidates(
        corpus, query, 4
    )


def test_nearest_rank_uses_nearest_rank_percentiles() -> None:
    assert nearest_rank(list(range(1, 101)), 0.50) == 50
    assert nearest_rank(list(range(1, 101)), 0.95) == 95
    assert nearest_rank([], 0.95) is None


def test_rendered_records_ignores_prefix_and_invalid_lines() -> None:
    context = "prefix\n{\"content\":\"one\"}\nnot-json\n{\"content\":\"two\"}"
    assert rendered_records(context) == [{"content": "one"}, {"content": "two"}]


def test_aggregate_selects_smallest_configuration_that_passes_every_gate(
    tmp_path: Path,
) -> None:
    archived = tmp_path / "archived"
    archived.mkdir()
    (archived / "conv_0.json").write_text(
        json.dumps(
            {
                "detail": [
                    {
                        "category": 1,
                        "question": "fixture question",
                        "hit_any": True,
                        "hit_all": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    common = {
        "hit_any": True,
        "hit_all": True,
        "rendered_hit_any": True,
        "rendered_hit_all": True,
        "context_chars": 100,
        "context_o200k_tokens": 25,
        "truncated": False,
        "rendered_adversarial_answer_exposed": False,
    }
    report = aggregate(
        [
            {
                "conversation_index": 0,
                "memories": 419,
                "details": [
                    {
                        "category": 1,
                        "question": "fixture question",
                        "configurations": {
                            "w100_k20": common,
                            "w30_k3": dict(common, rendered_hit_any=False),
                        },
                    }
                ],
            }
        ],
        archived,
        1,
    )

    assert report["smallest_passing_configuration"] == "w100_k20"
    assert report["non_inferiority_gate"]["passing_configurations"] == ["w100_k20"]
    assert report["configurations"]["w30_k3"]["non_inferiority"]["passed"] is False
