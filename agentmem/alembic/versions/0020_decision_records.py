"""Add the cross-industry dispute decision ledger.

Revision ID: 0020_decision_records
Revises: 0019_subject_keys_composite_pk
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020_decision_records"
down_revision = "0019_subject_keys_composite_pk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("decision_type", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("regime", sa.String(), nullable=True),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("policy_version", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_memory_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("human_review_status", sa.String(), nullable=False, server_default="not_requested"),
        sa.Column("human_reviewer", sa.String(), nullable=True),
        sa.Column("human_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("record_hash", sa.String(64), nullable=False),
    )
    for name, cols in (
        ("ix_decision_records_namespace", ["namespace"]),
        ("ix_decision_records_agent_id", ["agent_id"]),
        ("ix_decision_records_decision_type", ["decision_type"]),
        ("ix_decision_records_regime", ["regime"]),
        ("ix_decision_records_subject_id", ["subject_id"]),
        ("ix_decision_records_session_id", ["session_id"]),
        ("ix_decision_records_decided_at", ["decided_at"]),
        ("ix_decision_records_record_hash", ["record_hash"]),
        ("ix_decision_ns_decided", ["namespace", "decided_at"]),
        ("ix_decision_ns_subject", ["namespace", "subject_id"]),
    ):
        op.create_index(name, "decision_records", cols)

    op.create_table(
        "ledger_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("agent_id", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("artifact_hash", sa.String(64), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
    )
    for name, cols in (
        ("ix_ledger_events_namespace", ["namespace"]),
        ("ix_ledger_events_event_type", ["event_type"]),
        ("ix_ledger_events_agent_id", ["agent_id"]),
        ("ix_ledger_events_occurred_at", ["occurred_at"]),
        ("ix_ledger_events_subject_id", ["subject_id"]),
        ("ix_ledger_events_session_id", ["session_id"]),
        ("ix_ledger_events_decision_id", ["decision_id"]),
        ("ix_ledger_events_event_hash", ["event_hash"]),
        ("ix_ledger_event_ns_time", ["namespace", "occurred_at"]),
    ):
        op.create_index(name, "ledger_events", cols)


def downgrade() -> None:
    op.drop_table("ledger_events")
    op.drop_table("decision_records")
