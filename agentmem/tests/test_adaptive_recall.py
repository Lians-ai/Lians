from types import SimpleNamespace
from uuid import uuid4

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
