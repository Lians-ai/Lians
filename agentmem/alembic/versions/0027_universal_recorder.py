"""Add provider-neutral Universal Recorder events and run boundaries.

Revision ID: 0027_universal_recorder
Revises: 0026_evidence_graph
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_universal_recorder"
down_revision = "0026a_evidence_graph_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recorder_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("barrier_scope", sa.String(), nullable=False),
        sa.Column("correlation_type", sa.String(32), nullable=False),
        sa.Column("correlation_value", sa.String(512), nullable=False),
        sa.Column("correlation_hash", sa.String(64), nullable=False),
        sa.Column("boundary_kind", sa.String(32), nullable=False, server_default="run"),
        sa.Column("agent_id", sa.String(255), nullable=True),
        sa.Column("subject_id", sa.String(512), nullable=True),
        sa.Column("session_id", sa.String(512), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(512), nullable=True),
        # Intentionally not a foreign key: recorder telemetry can precede
        # DecisionRecord promotion and still carry the future decision UUID.
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("first_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("protocols", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("capture_state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("readiness_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("receipt_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("completeness_gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("diagnostics", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("extension_attributes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "correlation_hash",
            name="uq_recorder_run_scope_correlation",
        ),
    )
    for name, columns in (
        ("ix_recorder_runs_namespace", ["namespace"]),
        ("ix_recorder_runs_barrier_group", ["barrier_group"]),
        ("ix_recorder_runs_agent_id", ["agent_id"]),
        ("ix_recorder_runs_subject_id", ["subject_id"]),
        ("ix_recorder_runs_session_id", ["session_id"]),
        ("ix_recorder_runs_trace_id", ["trace_id"]),
        ("ix_recorder_runs_task_id", ["task_id"]),
        ("ix_recorder_runs_decision_id", ["decision_id"]),
        ("ix_recorder_runs_status", ["status"]),
        ("ix_recorder_runs_ready_at", ["ready_at"]),
        ("ix_recorder_runs_receipt_ready", ["receipt_ready"]),
        ("ix_recorder_run_ns_updated", ["namespace", "updated_at"]),
        ("ix_recorder_run_ns_trace", ["namespace", "trace_id"]),
        ("ix_recorder_run_ns_task", ["namespace", "task_id"]),
    ):
        op.create_index(name, "recorder_runs", columns)

    op.create_table(
        "recorder_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recorder_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("barrier_group", sa.String(), nullable=True),
        sa.Column("barrier_scope", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False),
        sa.Column("event_kind", sa.String(128), nullable=False),
        sa.Column("event_name", sa.String(512), nullable=True),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("status", sa.String(64), nullable=True),
        sa.Column("source_event_id", sa.String(512), nullable=True),
        sa.Column("dedup_key", sa.String(64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=True),
        sa.Column("source_payload_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("agent_id", sa.String(255), nullable=True),
        sa.Column("subject_id", sa.String(512), nullable=True),
        sa.Column("session_id", sa.String(512), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("span_id", sa.String(64), nullable=True),
        sa.Column("parent_span_id", sa.String(64), nullable=True),
        sa.Column("task_id", sa.String(512), nullable=True),
        sa.Column("context_id", sa.String(512), nullable=True),
        sa.Column("message_id", sa.String(512), nullable=True),
        sa.Column("tool_call_id", sa.String(512), nullable=True),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.String(512), nullable=True),
        sa.Column("model_version", sa.String(512), nullable=True),
        sa.Column("policy_version", sa.String(512), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("capture_mode", sa.String(32), nullable=False),
        sa.Column("normalized_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("extension_attributes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("capture_gaps", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("diagnostics", sa.JSON(), nullable=False, server_default="[]"),
        sa.UniqueConstraint(
            "namespace",
            "barrier_scope",
            "dedup_key",
            name="uq_recorder_event_scope_dedup",
        ),
    )
    for name, columns in (
        ("ix_recorder_events_namespace", ["namespace"]),
        ("ix_recorder_events_run_id", ["run_id"]),
        ("ix_recorder_events_barrier_group", ["barrier_group"]),
        ("ix_recorder_events_protocol", ["protocol"]),
        ("ix_recorder_events_event_kind", ["event_kind"]),
        ("ix_recorder_events_event_hash", ["event_hash"]),
        ("ix_recorder_events_occurred_at", ["occurred_at"]),
        ("ix_recorder_events_recorded_at", ["recorded_at"]),
        ("ix_recorder_events_agent_id", ["agent_id"]),
        ("ix_recorder_events_subject_id", ["subject_id"]),
        ("ix_recorder_events_session_id", ["session_id"]),
        ("ix_recorder_events_trace_id", ["trace_id"]),
        ("ix_recorder_events_task_id", ["task_id"]),
        ("ix_recorder_events_decision_id", ["decision_id"]),
        ("ix_recorder_events_model_id", ["model_id"]),
        ("ix_recorder_event_run_time", ["run_id", "occurred_at"]),
        ("ix_recorder_event_ns_protocol_time", ["namespace", "protocol", "occurred_at"]),
        ("ix_recorder_event_ns_trace_span", ["namespace", "trace_id", "span_id"]),
        ("ix_recorder_event_ns_task", ["namespace", "task_id"]),
    ):
        op.create_index(name, "recorder_events", columns)

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("recorder_runs", "recorder_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""CREATE POLICY rls_{table}_namespace ON {table}
            USING (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )
            WITH CHECK (
                namespace = current_setting('app.current_namespace', true)
                OR current_setting('app.current_namespace', true) = '__admin__'
            )"""
        )
        op.execute(
            f"""CREATE POLICY barrier_isolation ON {table} AS RESTRICTIVE
            USING (
                barrier_group IS NULL
                OR current_setting('agentmem.barrier_group', true) IS NULL
                OR current_setting('agentmem.barrier_group', true) = ''
                OR barrier_group = current_setting('agentmem.barrier_group', true)
            )"""
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in ("recorder_events", "recorder_runs"):
            op.execute(f"DROP POLICY IF EXISTS barrier_isolation ON {table}")
            op.execute(f"DROP POLICY IF EXISTS rls_{table}_namespace ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.drop_table("recorder_events")
    op.drop_table("recorder_runs")
