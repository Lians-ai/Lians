from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from lians.memory_service import _fuse_recall_rankings, _retrieval_confidence
from lians.query_planner import QueryPlan


def _memory():
    return SimpleNamespace(id=uuid4())


def test_fusion_rewards_cross_facet_support_and_deduplicates():
    shared, only_a, only_b = _memory(), _memory(), _memory()
    rankings = [
        [(only_a, 0.9, "a"), (shared, 0.8, "shared")],
        [(only_b, 0.9, "b"), (shared, 0.8, "shared")],
    ]
    plan = QueryPlan(("original", "history"), ("episodic", "history"), True)

    fused = _fuse_recall_rankings(rankings, plan, limit=3)

    ids = [item[0].id for item in fused]
    assert len(ids) == len(set(ids)) == 3
    assert ids[0] == shared.id
    assert shared._retrieval_scopes == ["episodic", "history"]


def test_single_ranking_preserves_standard_order_and_score():
    first, second = _memory(), _memory()
    ranking = [(first, 0.8, "first"), (second, 0.7, "second")]
    plan = QueryPlan(("original",), ("episodic",), False)

    assert _fuse_recall_rankings([ranking], plan, limit=1) == ranking[:1]


def test_confidence_increases_with_cross_facet_support():
    broad, narrow = _memory(), _memory()
    broad._retrieval_scopes = ["episodic", "history"]
    narrow._retrieval_scopes = ["episodic"]

    broad_score = _retrieval_confidence([(broad, 0.8, "x")], 2)
    narrow_score = _retrieval_confidence([(narrow, 0.8, "x")], 2)

    assert 0.0 <= narrow_score < broad_score <= 1.0


def test_fusion_score_explanation_and_full_tie_order_stay_synchronized():
    feb = datetime(2026, 2, 1, tzinfo=timezone.utc)
    march = datetime(2026, 3, 1, tzinfo=timezone.utc)
    january = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def memory(suffix: int, event_time: datetime, ingestion_time: datetime):
        return SimpleNamespace(
            id=UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
            event_time=event_time,
            ingestion_time=ingestion_time,
            _score_breakdown={
                "final_score": 0.7,
                "quality_score": 0.6,
                "reasons": [],
            },
        )

    newest_event = memory(4, march, january)
    newest_ingestion = memory(3, feb, march)
    smaller_id = memory(1, feb, january)
    larger_id = memory(2, feb, january)

    # Each candidate appears first in exactly one facet. The original facet has
    # weight 1.25 while the other facets have weight 1.0, so compensate its raw
    # input score to create an exact public-score tie after six-place rounding.
    first_rrf = 1.25 / 4.25
    other_rrf = 1.0 / 4.25
    first_raw = 0.1
    other_raw = first_raw + 0.72 * (first_rrf - other_rrf) / 0.18
    rankings = [
        [(newest_event, first_raw, "event")],
        [(newest_ingestion, other_raw, "ingestion")],
        [(larger_id, other_raw, "larger")],
        [(smaller_id, other_raw, "smaller")],
    ]
    plan = QueryPlan(
        ("original", "history", "constraint", "timeline"),
        ("episodic", "history", "constraint", "timeline"),
        True,
    )

    fused = _fuse_recall_rankings(rankings, plan, limit=4)

    assert [entry[0].id for entry in fused] == [
        newest_event.id,
        newest_ingestion.id,
        smaller_id.id,
        larger_id.id,
    ]
    assert len({entry[1] for entry in fused}) == 1
    for memory_row, public_score, _content in fused:
        assert public_score == memory_row._score_breakdown["final_score"]
        assert memory_row._score_breakdown["pre_fusion_score"] == 0.7
        assert memory_row._score_breakdown["fusion"]["facet_count"] == 4
