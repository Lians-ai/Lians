"""Static guards for bounded runtime queries and truthful completeness fields.

These tests intentionally inspect source contracts without opening a database.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIANS = ROOT / "src" / "lians"


def _source(relative: str) -> str:
    return (LIANS / relative).read_text(encoding="utf-8")


def test_recall_candidate_discovery_is_bounded_and_savepoint_recoverable() -> None:
    source = _source("ranking.py")
    assert ".limit(pre_k + 1)" in source
    assert ".limit(_CANDIDATE_WINDOW_LIMIT + 1)" in source
    assert "async with db.begin_nested()" in source
    assert "candidate_window_complete" in source
    assert "candidate_mode" in source


def test_graph_negative_is_unknown_when_the_search_budget_truncates() -> None:
    source = _source("graph_service.py")
    assert "True if found else False if search_complete else None" in source
    assert '"search_complete": search_complete' in source
    assert "return out, search_complete" in source
    assert "max_nodes" in source
    assert "max_edges" in source


def test_snapshots_and_receipts_never_label_a_truncated_boundary_complete() -> None:
    snapshot = _source("api/routes_snapshot.py")
    decisions = _source("api/routes_decisions.py")
    assert "limit + 1" in snapshot
    assert "recorded_as_of=effective_recorded_as_of" in snapshot
    assert "total == len(items)" in snapshot
    assert '"code": "knowledge_snapshot_requires_paged_export"' in decisions
    assert "snapshot_total > snapshot_limit" in decisions


def test_export_bytes_are_preflighted_before_plaintext_or_audit_side_effects() -> None:
    memory = _source("memory_service.py")
    snapshot = _source("api/routes_snapshot.py")
    decisions = _source("api/routes_decisions.py")
    audit = _source("audit_chain.py")
    assert "async def measure_knowledge_snapshot_bytes" in memory
    assert "if not include_content:" in memory
    assert '"snapshot_page_byte_capacity_exceeded"' in snapshot
    assert 'capacity_code = "decision_receipt_byte_capacity_exceeded"' in decisions
    assert 'capacity_code = "evidence_pack_byte_capacity_exceeded"' in decisions
    assert "bounded_bytes.c.estimated_bytes" in decisions
    assert '"audit_export_page_byte_capacity_exceeded"' in audit
    assert '"audit_verification_byte_capacity_exceeded"' in audit


def test_audit_export_total_and_collection_completion_are_cursor_independent() -> None:
    audit = _source("audit_chain.py")
    schemas = _source("schemas.py")
    assert "base_filters = [" in audit
    assert "EventLog.namespace == namespace" in audit
    assert "page_filters = list(base_filters)" in audit
    assert '"has_more": has_more' in audit
    assert '"complete": after_chain_position is None and not has_more' in audit
    assert '"snapshot_max_chain_position": snapshot_max_chain_position' in audit
    assert "EventLog.chain_position <= snapshot_max_chain_position" in audit
    assert "has_more: bool" in schemas


def test_backtest_cleanliness_uses_exact_counts_not_the_flag_page() -> None:
    source = _source("backtest.py")
    assert "select(func.count(Memory.id)).where(*contaminant_conditions)" in source
    assert ".limit(bounded_limit + 1)" in source
    assert "is_clean=flags_total == 0" in source
    assert "flags_complete=after_event_time is None and not has_more" in source


def test_recorder_and_impact_hydration_remain_bounded_without_prefix_claims() -> None:
    recorder = _source("recorder_service.py")
    worker = _source("recorder_index_service.py")
    decisions = _source("api/routes_decisions.py")
    assert "_DECISION_RECORDER_INDEX_LIMIT = 500" in recorder
    assert "_enqueue_recorder_evidence_index_job" in recorder
    assert "snapshot_max_event_id" in recorder
    assert "await assert_recorder_events_integrity(db, rows)" in recorder
    assert ".limit(_DECISION_RECORDER_INDEX_LIMIT + 1)" in recorder
    assert "with_for_update(skip_locked=" in worker
    assert "job.events_indexed == job.snapshot_event_count" in worker
    assert "state=\"completed\"" in worker
    assert "_MAX_IMPACT_LINKS_PER_DECISION = 2_000" in decisions
    assert "_MAX_IMPACT_LINKS_PER_PAGE = 50_000" in decisions
    assert "impact_link_hydration_limit_exceeded" in decisions


def test_recorder_run_events_are_exact_keyset_pages_with_bulk_integrity() -> None:
    recorder = _source("recorder_service.py")
    routes = _source("api/routes_recorder.py")
    assert "before_recorded_at: datetime | None" in recorder
    assert ".order_by(RecorderEvent.recorded_at.desc(), RecorderEvent.id.desc())" in recorder
    assert ".limit(limit + 1)" in recorder
    assert "select(func.count()).select_from(RecorderEvent)" in recorder
    assert 'total_subquery.label("collection_total")' in recorder
    assert "await assert_recorder_events_integrity(" in recorder
    assert "await assert_recorder_event_integrity(db, row)" not in recorder.split(
        "async def list_run_events", 1
    )[1].split("_ACTION_TEXT", 1)[0]
    assert 'response.headers["X-Lians-Total-Count"]' in routes
    assert 'response.headers["X-Lians-Collection-Complete"]' in routes
    assert '"recorder_event_integrity_failed"' in routes


def test_decision_evidence_normalization_is_bounded_bulk_and_atomic() -> None:
    evidence = _source("evidence_service.py")
    routes = _source("api/routes_decisions.py")
    config = _source("config.py")
    assert "class DecisionEvidenceCapacityExceeded" in evidence
    assert 'code = "decision_evidence_candidate_capacity_exceeded"' in evidence
    assert "candidate_bytes_limit" in evidence
    assert "ensure_artifacts_bulk(" in evidence
    assert "ensure_decision_links_bulk(" in evidence
    assert "_EVIDENCE_BULK_PAGE_SIZE = 500" in evidence
    assert "await _acquire_registration_fence(db, decision.namespace)" in evidence
    assert "DecisionEvidenceCapacityExceeded" in routes
    assert "load_only(" in routes
    mutation = routes.split("async def _create_decision_mutation", 1)[1]
    assert mutation.index("decision_artifact_specs(row, evidence_rows)") < mutation.index(
        "db.add(row)"
    )
    assert "decision_evidence_candidate_limit" in config
    assert "decision_evidence_candidate_bytes_limit" in config


def test_decision_graph_and_review_history_expose_page_completeness() -> None:
    decisions = _source("api/routes_decisions.py")
    schemas = _source("schemas.py")
    evidence_schemas = _source("evidence_schemas.py")
    assert "links_complete=after_relation is None and not has_more" in decisions
    assert "chain_scope_complete=collection_complete" in decisions
    assert "class DecisionReviewHistoryResult" in schemas
    assert "links_total: int" in evidence_schemas


def test_decision_projection_integrity_uses_one_bounded_immutable_head() -> None:
    source = _source("decision_record_integrity.py")
    block = source.split("async def _assert_review_projection", 1)[1].split(
        "async def assert_decision_record_integrity", 1
    )[0]
    assert ".limit(1)" in block
    assert ".outerjoin(latest_event, true())" in block
    assert ".all()" not in block
    assert "verify_decision_review_event(latest)" in block


def test_decision_compatibility_lists_have_exact_keyset_traversal_truth() -> None:
    decisions = _source("api/routes_decisions.py")
    assert "before_occurred_at: datetime | None" in decisions
    assert "before_decided_at: datetime | None" in decisions
    assert "before_recorded_at: datetime | None" in decisions
    assert decisions.count(".limit(limit + 1)") >= 3
    assert decisions.count("select(func.count()).select_from(") >= 3
    assert 'response.headers["X-Lians-Total-Count"]' in decisions
    assert 'response.headers["X-Lians-Collection-Complete"]' in decisions
    assert "not cursor_supplied and not has_more and total == returned" in decisions
    assert decisions.count("_require_paired_cursor(") >= 4


def test_retention_pruning_hydrates_only_one_candidate_batch() -> None:
    source = _source("memory_service.py")
    assert "async def prune_expired_content" in source
    assert ".limit(batch_limit)" in source
    assert "remaining=remaining" in source
    assert "complete=remaining == 0" in source


def test_supersession_candidates_are_complete_or_fail_closed() -> None:
    supersession = _source("supersession.py")
    memory_service = _source("memory_service.py")
    routes = _source("api/routes_memory.py")
    assert "async def _complete_candidate_set" in supersession
    assert "func.count(), func.coalesce(func.sum(row_bytes), 0)" in supersession
    assert ".limit(row_limit + 1)" in supersession
    assert '"supersession_candidate_capacity_exceeded"' in supersession
    assert "_exact_barrier_condition(Memory.barrier_group, barrier_group)" in supersession
    assert "barrier_group=barrier_group" in memory_service
    assert "SupersessionDecisionUnavailable" in routes


def test_exclusive_graph_invalidation_is_atomic_and_bounded() -> None:
    graph = _source("graph_service.py")
    routes = _source("api/routes_graph.py")
    assert ".limit(invalidation_limit + 1)" in graph
    assert '"graph_exclusive_invalidation_capacity_exceeded"' in graph
    assert '"graph_live_edge_invariant_violation"' in graph
    assert "commit=False" in graph
    assert "await db.commit()" in graph
    assert "GraphMutationDecisionUnavailable" in routes
