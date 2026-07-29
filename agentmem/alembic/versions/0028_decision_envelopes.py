"""Add Decision Envelopes, completeness evidence edges, and correlation fields.

Revision ID: 0028_decision_envelopes
Revises: 0027_agent_experiences
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_decision_envelopes"
down_revision = "0027_agent_experiences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_envelopes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("decision_type", sa.String(), nullable=False),
        sa.Column("regime", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("knowledge_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completeness_profile",
            sa.String(),
            nullable=False,
            server_default="standard",
        ),
        sa.Column("requirements", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, cols in (
        ("ix_decision_envelopes_namespace", ["namespace"]),
        ("ix_decision_envelopes_agent_id", ["agent_id"]),
        ("ix_decision_envelopes_barrier_group", ["barrier_group"]),
        ("ix_decision_envelopes_decision_type", ["decision_type"]),
        ("ix_decision_envelopes_regime", ["regime"]),
        ("ix_decision_envelopes_subject_id", ["subject_id"]),
        ("ix_decision_envelopes_session_id", ["session_id"]),
        ("ix_decision_envelopes_trace_id", ["trace_id"]),
        ("ix_decision_envelopes_run_id", ["run_id"]),
        ("ix_decision_envelopes_status", ["status"]),
        ("ix_decision_envelope_ns_status", ["namespace", "status", "created_at"]),
        ("ix_decision_envelope_ns_trace", ["namespace", "trace_id"]),
    ):
        op.create_index(name, "decision_envelopes", cols)

    for column in (
        sa.Column("envelope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("prompt_id", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("runtime_version", sa.String(), nullable=True),
        sa.Column("replay_manifest_hash", sa.String(64), nullable=True),
    ):
        op.add_column("decision_records", column)
    op.create_foreign_key(
        "fk_decision_records_envelope",
        "decision_records",
        "decision_envelopes",
        ["envelope_id"],
        ["id"],
    )
    op.create_index(
        "ix_decision_records_envelope_id",
        "decision_records",
        ["envelope_id"],
        unique=True,
    )
    op.create_index("ix_decision_records_trace_id", "decision_records", ["trace_id"])
    op.create_index("ix_decision_records_run_id", "decision_records", ["run_id"])
    op.create_index(
        "ix_decision_ns_trace",
        "decision_records",
        ["namespace", "trace_id"],
    )

    op.create_table(
        "decision_evidence_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column(
            "envelope_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("decision_envelopes.id"),
            nullable=False,
        ),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("evidence_type", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("source_version", sa.String(255), nullable=True),
        sa.Column("artifact_hash", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, cols in (
        ("ix_decision_evidence_links_namespace", ["namespace"]),
        ("ix_decision_evidence_links_envelope_id", ["envelope_id"]),
        ("ix_decision_evidence_links_barrier_group", ["barrier_group"]),
        ("ix_decision_evidence_links_evidence_type", ["evidence_type"]),
        ("ix_decision_evidence_links_role", ["role"]),
        ("ix_decision_evidence_links_source_id", ["source_id"]),
        ("ix_decision_evidence_links_source_version", ["source_version"]),
        ("ix_decision_evidence_links_artifact_hash", ["artifact_hash"]),
        ("ix_decision_evidence_links_occurred_at", ["occurred_at"]),
        (
            "ix_decision_evidence_source",
            ["namespace", "evidence_type", "source_id", "source_version"],
        ),
        (
            "ix_decision_evidence_envelope_role",
            ["envelope_id", "role", "created_at"],
        ),
    ):
        op.create_index(name, "decision_evidence_links", cols)

    _backfill_existing_decisions()
    _apply_rls()


def _backfill_existing_decisions() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Existing record tables use FORCE ROW LEVEL SECURITY. Migration roles
        # are not necessarily superusers, so enter the same explicit admin
        # context used by the application before scanning every namespace.
        # The settings are transaction-local and disappear after migration.
        bind.execute(
            sa.text(
                "SELECT set_config('app.current_namespace', '__admin__', true)"
            )
        )
        bind.execute(
            sa.text(
                "SELECT set_config('agentmem.barrier_group', '', true)"
            )
        )
    decisions = list(
        bind.execute(
            sa.text(
                """
                SELECT id, namespace, agent_id, barrier_group, decision_type,
                       regime, subject_id, session_id, knowledge_as_of,
                       decided_at, recorded_at, metadata, evidence_memory_ids
                FROM decision_records
                WHERE envelope_id IS NULL
                """
            )
        ).mappings()
    )
    for decision in decisions:
        envelope_id = uuid.uuid4()
        created_at = decision["recorded_at"] or decision["decided_at"]
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        bind.execute(
            sa.text(
                """
                INSERT INTO decision_envelopes (
                    id, namespace, agent_id, barrier_group, decision_type, regime,
                    subject_id, session_id, knowledge_as_of, completeness_profile,
                    requirements, metadata, status, version, created_at, sealed_at
                ) VALUES (
                    :id, :namespace, :agent_id, :barrier_group, :decision_type, :regime,
                    :subject_id, :session_id, :knowledge_as_of, 'standard',
                    :requirements, :metadata, 'sealed', 1, :created_at, :sealed_at
                )
                """
            ),
            {
                "id": envelope_id,
                "namespace": decision["namespace"],
                "agent_id": decision["agent_id"],
                "barrier_group": decision["barrier_group"],
                "decision_type": decision["decision_type"],
                "regime": decision["regime"],
                "subject_id": decision["subject_id"],
                "session_id": decision["session_id"],
                "knowledge_as_of": decision["knowledge_as_of"],
                "requirements": json.dumps({}),
                "metadata": json.dumps(decision["metadata"] or {}),
                "created_at": created_at,
                "sealed_at": created_at,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE decision_records SET envelope_id = :envelope_id WHERE id = :decision_id"
            ),
            {"envelope_id": envelope_id, "decision_id": decision["id"]},
        )
        for raw_memory_id in decision["evidence_memory_ids"] or []:
            try:
                memory_id = uuid.UUID(str(raw_memory_id))
            except ValueError:
                continue
            memory = bind.execute(
                sa.text(
                    """
                    SELECT content_hash, event_time, source
                    FROM memories
                    WHERE id = :memory_id AND namespace = :namespace
                    """
                ),
                {"memory_id": memory_id, "namespace": decision["namespace"]},
            ).mappings().first()
            if memory is None:
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO decision_evidence_links (
                        id, namespace, envelope_id, barrier_group, evidence_type,
                        role, source_id, artifact_hash, occurred_at, metadata, created_at
                    ) VALUES (
                        :id, :namespace, :envelope_id, :barrier_group, 'memory',
                        'used', :source_id, :artifact_hash, :occurred_at, :metadata, :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "namespace": decision["namespace"],
                    "envelope_id": envelope_id,
                    "barrier_group": decision["barrier_group"],
                    "source_id": str(memory_id),
                    "artifact_hash": memory["content_hash"],
                    "occurred_at": memory["event_time"],
                    "metadata": json.dumps(
                        {"source": memory["source"], "backfilled": True}
                    ),
                    "created_at": created_at,
                },
            )


def _apply_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("decision_envelopes", "decision_evidence_links"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY rls_{table}_namespace ON {table}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            """
        )
        op.execute(
            f"""
            CREATE POLICY barrier_isolation ON {table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("decision_envelopes", "decision_evidence_links"):
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.drop_table("decision_evidence_links")
    op.drop_index("ix_decision_ns_trace", table_name="decision_records")
    op.drop_index("ix_decision_records_run_id", table_name="decision_records")
    op.drop_index("ix_decision_records_trace_id", table_name="decision_records")
    op.drop_index("ix_decision_records_envelope_id", table_name="decision_records")
    op.drop_constraint(
        "fk_decision_records_envelope",
        "decision_records",
        type_="foreignkey",
    )
    for column in (
        "replay_manifest_hash",
        "runtime_version",
        "prompt_version",
        "prompt_id",
        "run_id",
        "trace_id",
        "envelope_id",
    ):
        op.drop_column("decision_records", column)
    op.drop_table("decision_envelopes")
