from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.lians import graph_service, ranking
from src.lians.memory_service import _rerank_by_proximity


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _memory(*, score: float, embedding=None, entity: str | None = None):
    metadata = {"ticker": entity} if entity is not None else {}
    return SimpleNamespace(
        id=uuid4(),
        event_time=NOW,
        ingestion_time=NOW,
        embedding=embedding,
        metadata_=metadata,
        _score_breakdown={
            "final_score": score,
            "quality_score": score,
            "eligible": True,
            "reasons": [],
        },
    )


def _assert_explained_order(results, stage: str):
    scores = [item[1] for item in results]
    assert scores == sorted(scores, reverse=True)
    for memory, score, _content in results:
        assert score == memory._score_breakdown["final_score"]
        assert memory._score_breakdown["ranking_stages"][-1]["stage"] == stage
        assert memory._score_breakdown["ranking_stages"][-1]["output_score"] == score


def test_cross_encoder_order_replaces_and_explains_public_rank(monkeypatch):
    high = _memory(score=0.9)
    low = _memory(score=0.1)

    class FakeCrossEncoder:
        def predict(self, _pairs, show_progress_bar=False):
            assert show_progress_bar is False
            return [0.0, 1.0]

    monkeypatch.setattr(ranking, "RERANKER_MODEL", "test/cross-encoder")
    monkeypatch.setattr(ranking, "_get_reranker", lambda: FakeCrossEncoder())
    results = ranking.rerank_cross_encoder(
        "query", [(high, 0.9, "high"), (low, 0.1, "low")], 2
    )

    assert [item[0].id for item in results] == [low.id, high.id]
    _assert_explained_order(results, "cross-encoder")
    assert results[0][0]._score_breakdown["ranking_stages"][-1]["raw_model_score"] == 1.0


def test_mmr_order_replaces_and_explains_public_rank():
    first = _memory(score=0.9, embedding=[1.0, 0.0])
    duplicate = _memory(score=0.85, embedding=[1.0, 0.0])
    diverse = _memory(score=0.5, embedding=[0.0, 1.0])
    results = ranking.mmr_rerank(
        [
            (first, 0.9, "first"),
            (duplicate, 0.85, "duplicate"),
            (diverse, 0.5, "diverse"),
        ],
        lambda_=0.5,
    )

    assert [item[0].id for item in results] == [first.id, diverse.id, duplicate.id]
    _assert_explained_order(results, "mmr-rerank")
    assert results[1][0]._score_breakdown["ranking_stages"][-1]["lambda"] == 0.5


@pytest.mark.asyncio
async def test_graph_order_replaces_and_explains_public_rank(monkeypatch):
    far = _memory(score=0.9, entity="far")
    near = _memory(score=0.3, entity="near")

    async def fake_distances(*_args, **_kwargs):
        return {"near": 0, "far": 3}

    monkeypatch.setattr(graph_service, "entity_distances", fake_distances)
    results = await _rerank_by_proximity(
        None,
        "tenant",
        "agent",
        "anchor",
        "ticker",
        [(far, 0.9, "far"), (near, 0.3, "near")],
        None,
    )

    assert [item[0].id for item in results] == [near.id, far.id]
    _assert_explained_order(results, "graph-proximity")
    stage = results[0][0]._score_breakdown["ranking_stages"][-1]
    assert stage["distance"] == 0
    assert stage["proximity_bonus"] == 1.0
