"""Build Recorder, erasure, and live-supersession indexes online.

Revision ID: 0058a_live_supersession_indexes
Revises: 0058_recorder_index_jobs

PostgreSQL builds are concurrent, definition-checked, and resumable after an
invalid interrupted index. Offline SQL is refused because it cannot provide
the autocommit boundary required by CREATE INDEX CONCURRENTLY.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import context, op

revision = "0058a_live_supersession_indexes"
down_revision = "0058_recorder_index_jobs"
branch_labels = None
depends_on = None

_SPECS = {
    "ix_recorder_event_decision_snapshot": {
        "table": "recorder_events",
        "keys": ("namespace", "decision_id", "recorded_at", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_recorder_event_decision_snapshot
                  ON public.recorder_events
                  (namespace, decision_id, recorded_at, id)""",
    },
    "ix_recorder_event_run_page": {
        "table": "recorder_events",
        "keys": ("namespace", "run_id", "recorded_at", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_recorder_event_run_page
                  ON public.recorder_events
                  (namespace, run_id, recorded_at, id)""",
    },
    "ix_memories_subject_erasure_page": {
        "table": "memories",
        "keys": ("namespace", "subject_id", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_memories_subject_erasure_page
                  ON public.memories
                  (namespace, subject_id, id)""",
    },
    "ix_live_facts_subject_erasure_page": {
        "table": "live_facts",
        "keys": ("namespace", "subject_id", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_live_facts_subject_erasure_page
                  ON public.live_facts
                  (namespace, subject_id, id)""",
    },
    "ix_relationships_subject_erasure_page": {
        "table": "relationships",
        "keys": ("namespace", "subject_id", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_relationships_subject_erasure_page
                  ON public.relationships
                  (namespace, subject_id, id)""",
    },
    "ix_pending_admissions_subject_erasure_page": {
        "table": "pending_admissions",
        "keys": ("namespace", "subject_id", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_pending_admissions_subject_erasure_page
                  ON public.pending_admissions
                  (namespace, subject_id, id)""",
    },
    "ix_event_log_recorder_binding": {
        "table": "event_log",
        "keys": ("namespace", "(payload ->> 'recorder_event_id'::text)"),
        "predicate": "op='recorder_ingest'::text",
        "sql": """CREATE INDEX CONCURRENTLY ix_event_log_recorder_binding
                  ON public.event_log
                  (namespace, ((payload ->> 'recorder_event_id')))
                  WHERE op = 'recorder_ingest'""",
    },
    "ix_ledger_event_scope_page": {
        "table": "ledger_events",
        "keys": ("namespace", "barrier_group", "occurred_at", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_ledger_event_scope_page
                  ON public.ledger_events
                  (namespace, barrier_group, occurred_at, id)""",
    },
    "ix_decision_record_scope_page": {
        "table": "decision_records",
        "keys": ("namespace", "barrier_group", "decided_at", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_decision_record_scope_page
                  ON public.decision_records
                  (namespace, barrier_group, decided_at, id)""",
    },
    "ix_evidence_artifact_scope_page": {
        "table": "evidence_artifacts",
        "keys": ("namespace", "barrier_group", "recorded_at", "id"),
        "predicate": "",
        "sql": """CREATE INDEX CONCURRENTLY ix_evidence_artifact_scope_page
                  ON public.evidence_artifacts
                  (namespace, barrier_group, recorded_at, id)""",
    },
    "ix_memories_supersession_live": {
        "table": "memories",
        "keys": (
            "namespace",
            "agent_id",
            "barrier_group",
            "valid_to",
            "erased_at",
            "event_time",
            "id",
        ),
        "predicate": "valid_toisnullanderased_atisnull",
        "sql": """CREATE INDEX CONCURRENTLY ix_memories_supersession_live
                  ON public.memories
                  (namespace, agent_id, barrier_group, valid_to, erased_at, event_time, id)
                  WHERE valid_to IS NULL AND erased_at IS NULL""",
    },
    "ix_relationships_exclusive_live": {
        "table": "relationships",
        "keys": (
            "namespace",
            "agent_id",
            "barrier_group",
            "src_entity",
            "rel_type",
            "valid_to",
            "id",
        ),
        "predicate": "valid_toisnull",
        "sql": """CREATE INDEX CONCURRENTLY ix_relationships_exclusive_live
                  ON public.relationships
                  (namespace, agent_id, barrier_group, src_entity, rel_type, valid_to, id)
                  WHERE valid_to IS NULL""",
    },
}


def _normalized(value: object) -> str:
    return "".join(str(value or "").lower().split()).replace("(", "").replace(")", "")


def _index_state(index_name: str) -> dict[str, Any] | None:
    row = op.get_bind().execute(
        sa.text(
            """SELECT index.indisvalid,
                      index.indisunique,
                      access_method.amname AS access_method,
                      base.relname AS table_name,
                      pg_get_expr(index.indpred, index.indrelid, true) AS predicate,
                      array_agg(
                          pg_get_indexdef(index.indexrelid, key.ordinality, true)
                          ORDER BY key.ordinality
                      ) AS keys
               FROM pg_catalog.pg_index AS index
               JOIN pg_catalog.pg_class AS relation
                 ON relation.oid = index.indexrelid
               JOIN pg_catalog.pg_namespace AS schema
                 ON schema.oid = relation.relnamespace
               JOIN pg_catalog.pg_am AS access_method
                 ON access_method.oid = relation.relam
               JOIN pg_catalog.pg_class AS base
                 ON base.oid = index.indrelid
               CROSS JOIN LATERAL generate_series(
                   1, index.indnkeyatts
               ) AS key(ordinality)
               WHERE schema.nspname = 'public'
                 AND relation.relname = :index_name
               GROUP BY index.indisvalid, index.indisunique, access_method.amname,
                        base.relname, index.indpred, index.indrelid"""
        ),
        {"index_name": index_name},
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _matches(index_name: str, state: dict[str, Any]) -> bool:
    spec = _SPECS[index_name]
    keys = tuple(str(value) for value in state.get("keys") or ())
    if index_name == "ix_event_log_recorder_binding":
        keys_match = len(keys) == 2 and keys[0] == "namespace" and (
            "payload->>'recorder_event_id'" in _normalized(keys[1])
            or "payload->>'recorder_event_id'::text" in _normalized(keys[1])
        )
        predicate_match = "op='recorder_ingest'" in _normalized(
            state.get("predicate")
        )
    else:
        keys_match = keys == spec["keys"]
        predicate_match = _normalized(state.get("predicate")) == spec["predicate"]
    return bool(
        not state.get("indisunique")
        and state.get("access_method") == "btree"
        and state.get("table_name") == spec["table"]
        and keys_match
        and predicate_match
    )


def _repair_or_create(index_name: str) -> None:
    state = _index_state(index_name)
    if state is not None:
        if not _matches(index_name, state):
            raise RuntimeError(f"{index_name} exists with an unexpected definition")
        if bool(state.get("indisvalid")):
            return
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
    op.execute(_SPECS[index_name]["sql"])


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0058a_live_supersession_indexes requires an online connection for "
            "resumable concurrent index builds"
        )
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name in _SPECS:
                _repair_or_create(index_name)
        return
    op.create_index(
        "ix_recorder_event_decision_snapshot",
        "recorder_events",
        ["namespace", "decision_id", "recorded_at", "id"],
    )
    op.create_index(
        "ix_recorder_event_run_page",
        "recorder_events",
        ["namespace", "run_id", "recorded_at", "id"],
    )
    op.create_index(
        "ix_memories_subject_erasure_page",
        "memories",
        ["namespace", "subject_id", "id"],
    )
    op.create_index(
        "ix_live_facts_subject_erasure_page",
        "live_facts",
        ["namespace", "subject_id", "id"],
    )
    op.create_index(
        "ix_relationships_subject_erasure_page",
        "relationships",
        ["namespace", "subject_id", "id"],
    )
    op.create_index(
        "ix_pending_admissions_subject_erasure_page",
        "pending_admissions",
        ["namespace", "subject_id", "id"],
    )
    op.execute(
        """CREATE INDEX ix_event_log_recorder_binding
        ON event_log (namespace, json_extract(payload, '$.recorder_event_id'))
        WHERE op = 'recorder_ingest'"""
    )
    op.create_index(
        "ix_ledger_event_scope_page",
        "ledger_events",
        ["namespace", "barrier_group", "occurred_at", "id"],
    )
    op.create_index(
        "ix_decision_record_scope_page",
        "decision_records",
        ["namespace", "barrier_group", "decided_at", "id"],
    )
    op.create_index(
        "ix_evidence_artifact_scope_page",
        "evidence_artifacts",
        ["namespace", "barrier_group", "recorded_at", "id"],
    )
    op.create_index(
        "ix_memories_supersession_live",
        "memories",
        [
            "namespace",
            "agent_id",
            "barrier_group",
            "valid_to",
            "erased_at",
            "event_time",
            "id",
        ],
        sqlite_where=sa.text("valid_to IS NULL AND erased_at IS NULL"),
    )
    op.create_index(
        "ix_relationships_exclusive_live",
        "relationships",
        [
            "namespace",
            "agent_id",
            "barrier_group",
            "src_entity",
            "rel_type",
            "valid_to",
            "id",
        ],
        sqlite_where=sa.text("valid_to IS NULL"),
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0058a_live_supersession_indexes downgrade requires an online connection"
        )
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name in reversed(tuple(_SPECS)):
                op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS public.{index_name}")
        return
    op.drop_index("ix_relationships_exclusive_live", table_name="relationships")
    op.drop_index("ix_memories_supersession_live", table_name="memories")
    op.drop_index(
        "ix_evidence_artifact_scope_page", table_name="evidence_artifacts"
    )
    op.drop_index("ix_decision_record_scope_page", table_name="decision_records")
    op.drop_index("ix_ledger_event_scope_page", table_name="ledger_events")
    op.execute("DROP INDEX IF EXISTS ix_event_log_recorder_binding")
    op.drop_index(
        "ix_pending_admissions_subject_erasure_page",
        table_name="pending_admissions",
    )
    op.drop_index(
        "ix_relationships_subject_erasure_page", table_name="relationships"
    )
    op.drop_index(
        "ix_live_facts_subject_erasure_page", table_name="live_facts"
    )
    op.drop_index(
        "ix_memories_subject_erasure_page", table_name="memories"
    )
    op.drop_index("ix_recorder_event_run_page", table_name="recorder_events")
    op.drop_index(
        "ix_recorder_event_decision_snapshot", table_name="recorder_events"
    )
