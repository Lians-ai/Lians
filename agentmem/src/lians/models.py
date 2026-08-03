import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    types as sa_types,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import Dialect

from .config import get_settings
from .db import Base


class _FlexVector(sa_types.TypeDecorator):
    """Vector(dim) on PostgreSQL, JSON list on SQLite/other (for unit tests)."""
    impl = sa_types.Text
    cache_ok = True

    def __init__(self, dim: int):
        self.dim = dim
        super().__init__()

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        # Return value as-is; on PostgreSQL the Vector.bind_processor (applied
        # after this method by the TypeDecorator chain) converts the list to a
        # Postgres-literal string that asyncpg sends via the text protocol.
        # On SQLite, JSON serialises the list automatically.
        if value is None:
            return None
        return value

    def process_result_value(self, value, dialect):
        # On PostgreSQL: Vector.result_processor runs first and converts the
        # text-protocol string "[x,y,...]" → numpy array; we receive the array.
        # On SQLite: JSON deserialization returns a plain Python list.
        # In both cases, callers use list(mem.embedding) which handles both.
        if value is None:
            return None
        if isinstance(value, str):
            # Fallback: raw string (no result processor ran, e.g. direct text
            # SQL query bypassing the ORM type system).
            return [float(x) for x in value.strip("[]").split(",")]
        return value  # numpy ndarray or list — both are iterable as floats

EMBED_DIM = get_settings().embedding_dim  # 1024 — locked before first migration


def _now():
    return datetime.now(timezone.utc)


_LEGACY_IDEMPOTENCY_DIGEST = "0" * 64


class Memory(Base):
    """Content store — encrypted, erasable."""
    __tablename__ = "memories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)

    content_encrypted = Column(LargeBinary, nullable=True)   # null after erasure
    subject_id = Column(String, nullable=True, index=True)

    embedding = Column(_FlexVector(EMBED_DIM), nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")

    event_time = Column(DateTime(timezone=True), nullable=False, index=True)
    ingestion_time = Column(DateTime(timezone=True), nullable=False, default=_now)

    # Transaction/system-time axis. ``valid_from`` / ``valid_to`` below remain
    # the business-time interval for the fact. These columns record when that
    # interval state became visible to Lians, so a correction learned later
    # cannot rewrite an earlier decision's knowledge boundary.
    system_valid_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    system_valid_to = Column(DateTime(timezone=True), nullable=True)

    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)        # null = still valid

    superseded_by = Column(UUID(as_uuid=True), ForeignKey("memories.id"), nullable=True)
    supersession_confidence = Column(Float, nullable=True)

    # Information barrier group — only agents in the same group can recall this memory.
    # NULL means the memory is untagged (visible to all agents in the namespace, including
    # those with no barrier group assignment such as compliance officers).
    barrier_group = Column(String, nullable=True, index=True)

    importance = Column(Float, nullable=False, default=0.5)
    source = Column(String, nullable=True)
    content_hash = Column(String, nullable=False, index=True)
    erased_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_memories_ns_agent_event", "namespace", "agent_id", "event_time"),
        Index(
            "ix_memories_subject_erasure_page",
            "namespace",
            "subject_id",
            "id",
        ),
        Index(
            "ix_memories_ns_agent_system",
            "namespace",
            "agent_id",
            "system_valid_from",
            "system_valid_to",
        ),
        Index(
            "ix_memories_lineage_predecessor",
            "namespace",
            "agent_id",
            "superseded_by",
            "barrier_group",
            "id",
        ),
        Index(
            "ix_memories_supersession_live",
            "namespace",
            "agent_id",
            "barrier_group",
            "valid_to",
            "erased_at",
            "event_time",
            "id",
            postgresql_where=text("valid_to IS NULL AND erased_at IS NULL"),
            sqlite_where=text("valid_to IS NULL AND erased_at IS NULL"),
        ),
        # HNSW index — PostgreSQL/pgvector only; ignored on other dialects
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def embedding_as_list(self) -> list[float] | None:
        v = self.embedding
        if v is None:
            return None
        return list(v)

    def close_validity(self, *, valid_to: datetime, recorded_at: datetime) -> None:
        """Close the business-time interval and timestamp when Lians learned it."""
        self.valid_to = valid_to
        self.system_valid_to = recorded_at

    def reopen_validity(self) -> None:
        """Restore the current fact after a rejected supersession."""
        self.valid_to = None
        self.system_valid_to = None


class SubjectKey(Base):
    """Per-subject encryption keys — destroy to crypto-shred all their data.

    Keyed by ``(namespace, subject_id)``. New rows store the namespace-verifiable
    keyed subject reference in the historical ``subject_id`` column; legacy raw
    aliases remain readable only to support controlled erasure/upgrade continuity.
    The composite key prevents any DEK or tombstone from crossing tenants. See
    migration 0019.
    """
    __tablename__ = "subject_keys"

    namespace = Column(String, primary_key=True)
    subject_id = Column(String, primary_key=True)
    enc_key = Column(LargeBinary, nullable=True)   # null after destruction
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    destroyed_at = Column(DateTime(timezone=True), nullable=True)


class EventLog(Base):
    """Append-only audit trail — never updated, never deleted."""
    __tablename__ = "event_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False)
    op = Column(String, nullable=False)          # add | supersede | recall | erase
    memory_id = Column(UUID(as_uuid=True), nullable=True)
    content_hash = Column(String, nullable=True)
    payload = Column(JSON, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    # Hash chain for SEC 17a-4 tamper-evidence
    prev_hash = Column(String(64), nullable=False)  # row_hash of the namespace predecessor
    row_hash = Column(String(64), nullable=False)   # SHA-256 of versioned canonical fields
    # v1 excludes payload, v2 uses Python canonical JSON, and v3 is computed only
    # by the PostgreSQL security-definer append boundary.
    hash_version = Column(Integer, nullable=False, default=3, server_default="3")
    # Monotonic per namespace. Wall time is evidence, never chain-order authority.
    chain_position = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index(
            "ix_event_log_recorder_binding_lookup",
            "namespace",
            "op",
            "content_hash",
        ),
        Index(
            "ix_event_log_compliance_op_time",
            "namespace",
            "op",
            "created_at",
        ),
        Index(
            "ix_event_log_lineage_binding",
            "namespace",
            "op",
            "memory_id",
            "chain_position",
            "id",
        ),
        Index(
            "uq_event_log_namespace_prev_hash",
            "namespace",
            "prev_hash",
            unique=True,
        ),
        UniqueConstraint(
            "namespace",
            "chain_position",
            name="uq_event_log_namespace_chain_position",
        ),
        CheckConstraint(
            "prev_hash IS NOT NULL AND row_hash IS NOT NULL "
            "AND length(prev_hash) = 64 AND length(row_hash) = 64",
            name="ck_event_log_hash_lengths",
        ),
        CheckConstraint(
            "hash_version IN (1, 2, 3)",
            name="ck_event_log_hash_version",
        ),
    )


_DECISION_AUTHENTICATED_PROVENANCE_CHECK = """
    record_integrity_status = 'verified'
    AND recorded_by_principal_ref LIKE 'lians:principal:v1:%'
    AND recorded_by_principal_ref <> 'lians:principal:v1:legacy-unverified'
    AND length(recorded_by_principal_ref) > 20
    AND recorded_by_auth_method <> 'legacy_unverified'
    AND length(recorded_by_auth_method) > 0
    AND recorded_by_credential_ref LIKE 'lians:credential:v1:sha256:%'
    AND length(recorded_by_credential_ref) = 91
"""

_DECISION_POSTGRES_PROVENANCE_CHECK = f"""(
    record_hash_version = 1
    AND record_integrity_status = 'legacy_unverified'
    AND recorded_by_principal_ref = 'lians:principal:v1:legacy-unverified'
    AND recorded_by_auth_method = 'legacy_unverified'
    AND recorded_by_credential_ref IS NULL
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND recorded_by_scopes::jsonb = '[]'::jsonb
) OR (
    record_hash_version = 2
    AND {_DECISION_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND recorded_by_scopes::jsonb = '[]'::jsonb
) OR (
    record_hash_version = 3
    AND {_DECISION_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_credential_ref IS NOT NULL
    AND recorded_by_principal_type IS NOT NULL
    AND length(recorded_by_principal_type) BETWEEN 1 AND 32
    AND recorded_by_principal_type ~ '^[A-Za-z0-9_.:-]+$'
    AND (
        recorded_by_role IS NULL
        OR recorded_by_role IN ('owner', 'analyst', 'compliance', 'readonly')
    )
    AND public.lians_decision_authorization_scopes_valid(
        recorded_by_scopes::jsonb
    )
)"""

_DECISION_SQLITE_SCOPE_SHAPE = """(
    CASE
        WHEN json_valid(recorded_by_scopes) <> 1 THEN 0
        WHEN json_type(recorded_by_scopes) <> 'array' THEN 0
        WHEN record_hash_version IN (1, 2)
            THEN json_array_length(recorded_by_scopes) = 0
        WHEN record_hash_version = 3
            THEN json_array_length(recorded_by_scopes) BETWEEN 1 AND 50
        ELSE 0
    END
)"""

_DECISION_SQLITE_PROVENANCE_CHECK = f"""(
    record_hash_version = 1
    AND record_integrity_status = 'legacy_unverified'
    AND recorded_by_principal_ref = 'lians:principal:v1:legacy-unverified'
    AND recorded_by_auth_method = 'legacy_unverified'
    AND recorded_by_credential_ref IS NULL
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND {_DECISION_SQLITE_SCOPE_SHAPE}
) OR (
    record_hash_version = 2
    AND {_DECISION_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_principal_type IS NULL
    AND recorded_by_role IS NULL
    AND {_DECISION_SQLITE_SCOPE_SHAPE}
) OR (
    record_hash_version = 3
    AND {_DECISION_AUTHENTICATED_PROVENANCE_CHECK}
    AND recorded_by_credential_ref IS NOT NULL
    AND recorded_by_principal_type IS NOT NULL
    AND length(recorded_by_principal_type) BETWEEN 1 AND 32
    AND recorded_by_principal_type NOT GLOB '*[^-A-Za-z0-9_.:]*'
    AND (
        recorded_by_role IS NULL
        OR recorded_by_role IN ('owner', 'analyst', 'compliance', 'readonly')
    )
    AND {_DECISION_SQLITE_SCOPE_SHAPE}
)"""


class DecisionRecord(Base):
    """A consequential agent decision that may later be disputed.

    The schema is intentionally industry-neutral. Vertical requirements belong
    in ``regime`` and ``metadata``; the authoritative fields stay stable so one
    ledger can serve regulators, consumers, validators, courts, and auditors.
    Records are append-only at the API layer. Corrections create a new record
    linked through ``supersedes_id`` rather than rewriting history.
    """
    __tablename__ = "decision_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    # ``agent_id`` is a workload-supplied label.  These separate fields bind
    # the immutable record to the credential authenticated by Lians.
    agent_id = Column(String, nullable=False, index=True)
    recorded_by_principal_ref = Column(
        String,
        nullable=False,
        server_default="lians:principal:v1:legacy-unverified",
    )
    recorded_by_auth_method = Column(
        String(64), nullable=False, server_default="legacy_unverified"
    )
    recorded_by_credential_ref = Column(String, nullable=True)
    # v1/v2 rows must retain an empty authorization snapshot. v3 binds the
    # authenticated principal type, optional role, and effective write scopes.
    recorded_by_principal_type = Column(String(32), nullable=True)
    recorded_by_role = Column(String(64), nullable=True)
    recorded_by_scopes = Column(
        JSON,
        nullable=False,
        default=list,
        server_default="[]",
    )
    barrier_group = Column(String, nullable=True, index=True)
    decision_type = Column(String, nullable=False, index=True)
    outcome = Column(String, nullable=False)
    reason_codes = Column(JSON, nullable=False, server_default="[]")
    regime = Column(String, nullable=True, index=True)
    subject_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    model_id = Column(String, nullable=True)
    model_version = Column(String, nullable=True)
    policy_version = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    # Internal projection marker used by the resumable ValidMind inventory
    # backfill. Database triggers force every rolling-writer INSERT to ``true``;
    # nullable is retained in the ORM only for the expand/backfill window.
    validmind_inventory_counted = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    knowledge_as_of = Column(DateTime(timezone=True), nullable=False)
    # Transaction-time cutoff paired with ``knowledge_as_of``. Nullable for
    # legacy/imported records; receipt generation falls back to recorded_at.
    knowledge_recorded_as_of = Column(DateTime(timezone=True), nullable=True)
    evidence_memory_ids = Column(JSON, nullable=False, server_default="[]")
    input_hash = Column(String(64), nullable=True)
    output_hash = Column(String(64), nullable=True)
    human_review_status = Column(String, nullable=False, default="not_requested")
    human_reviewer = Column(String, nullable=True)
    human_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    supersedes_id = Column(UUID(as_uuid=True), nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    # Python writers explicitly choose v3. The database default deliberately
    # remains v1 for the 0.4.2 rolling-upgrade window, whose INSERT shape has no
    # authenticated provenance columns.
    record_hash_version = Column(Integer, nullable=False, default=3, server_default="1")
    record_integrity_status = Column(
        String(32),
        nullable=False,
        default="verified",
        server_default="legacy_unverified",
    )
    record_hash = Column(String(64), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "id",
            "namespace",
            name="uq_decision_record_id_namespace",
        ),
        Index("ix_decision_ns_decided", "namespace", "decided_at"),
        Index("ix_decision_ns_subject", "namespace", "subject_id"),
        Index(
            "ix_decision_validmind_model_inventory",
            "namespace",
            "model_id",
            "recorded_at",
        ),
        Index(
            "ix_decision_validmind_scope_bounds",
            "namespace",
            "barrier_group",
            "model_id",
            "recorded_at",
            "id",
            postgresql_where=text(
                "validmind_inventory_counted IS TRUE AND model_id IS NOT NULL"
            ),
            sqlite_where=text(
                "validmind_inventory_counted IS 1 AND model_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_decision_record_scope_page",
            "namespace",
            "barrier_group",
            "decided_at",
            "id",
        ),
        CheckConstraint(
            "record_hash_version IN (1, 2, 3)",
            name="ck_decision_record_hash_version",
        ),
        CheckConstraint(
            "record_integrity_status IN ('verified', 'legacy_unverified')",
            name="ck_decision_record_integrity_status",
        ),
        CheckConstraint(
            "length(record_hash) = 64 AND record_hash = lower(record_hash)",
            name="ck_decision_record_hash_length",
        ),
        CheckConstraint(
            _DECISION_POSTGRES_PROVENANCE_CHECK,
            name="ck_decision_record_provenance_state",
        ).ddl_if(dialect="postgresql"),
        CheckConstraint(
            _DECISION_SQLITE_PROVENANCE_CHECK,
            name="ck_decision_record_provenance_state",
        ).ddl_if(dialect="sqlite"),
    )


class LedgerEvent(Base):
    """First-class system-of-record event shared by every LIANS product."""
    __tablename__ = "ledger_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    subject_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    model_id = Column(String, nullable=True)
    model_version = Column(String, nullable=True)
    payload = Column(JSON, nullable=False, server_default="{}")
    artifact_hash = Column(String(64), nullable=True)
    event_hash = Column(String(64), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("id", "namespace", name="uq_ledger_event_id_namespace"),
        Index("ix_ledger_event_ns_time", "namespace", "occurred_at"),
        Index(
            "ix_ledger_event_scope_page",
            "namespace",
            "barrier_group",
            "occurred_at",
            "id",
        ),
    )


class OTelSpan(Base):
    """Append-only copy of a span accepted through the OTLP/HTTP receiver."""
    __tablename__ = "otel_spans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String(255), nullable=True)
    # Explicit provenance bit: historical rows and rolling legacy writers are
    # false/NULL during 0054 expand, while the barrier-aware writer always
    # supplies true (including for intentionally shared NULL barriers).
    barrier_scope_trusted = Column(Boolean, nullable=False)
    trace_id = Column(String(32), nullable=False)
    span_id = Column(String(16), nullable=False)
    parent_span_id = Column(String(16), nullable=True)
    name = Column(String, nullable=False)
    kind = Column(Integer, nullable=False, default=0)
    start_time_unix_nano = Column(String, nullable=False)
    end_time_unix_nano = Column(String, nullable=False)
    status_code = Column(Integer, nullable=False, default=0)
    status_message = Column(String, nullable=True)
    service_name = Column(String, nullable=True, index=True)
    scope_name = Column(String, nullable=True)
    scope_version = Column(String, nullable=True)
    resource_attributes = Column(JSON, nullable=False, server_default="{}")
    attributes = Column(JSON, nullable=False, server_default="{}")
    events = Column(JSON, nullable=False, server_default="[]")
    links = Column(JSON, nullable=False, server_default="[]")
    is_genai = Column(Boolean, nullable=False, default=False, index=True)
    model_id = Column(String, nullable=True, index=True)
    model_version = Column(String, nullable=True)
    payload_hash = Column(String(64), nullable=False)
    received_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    validmind_inventory_counted = Column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    __table_args__ = (
        CheckConstraint(
            "barrier_scope_trusted IS TRUE",
            name="ck_otel_barrier_scope_trusted",
        ),
        Index(
            "uq_otel_span_scope_trace_span",
            namespace,
            func.coalesce(barrier_group, ""),
            trace_id,
            span_id,
            unique=True,
        ),
        Index("ix_otel_span_ns_received", "namespace", "received_at"),
        Index(
            "ix_otel_span_scope_received",
            "namespace",
            "barrier_group",
            "received_at",
            "id",
        ),
        Index(
            "ix_otel_validmind_model_inventory",
            "namespace",
            "model_id",
            "received_at",
        ),
    )


class ValidMindBarrierScope(Base):
    """Private mapping from a raw information barrier to an opaque integration scope."""

    __tablename__ = "validmind_barrier_scopes"

    namespace = Column(String, primary_key=True)
    barrier_key = Column(String, primary_key=True)
    scope_id = Column(String(36), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "scope_id",
            name="uq_validmind_scope_namespace_id",
        ),
    )


class ValidMindModelInventory(Base):
    """Exact, trigger-maintained model inventory; public reads never scan events."""

    __tablename__ = "validmind_model_inventory"

    namespace = Column(String, primary_key=True)
    scope_id = Column(String(36), primary_key=True)
    model_id = Column(String, primary_key=True)
    external_id = Column(String(32), nullable=False)
    legacy_external_id = Column(String(32), nullable=False)
    decision_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    span_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    version_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    versions = Column(JSON, nullable=False, default=list, server_default="[]")
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace", "scope_id"],
            ["validmind_barrier_scopes.namespace", "validmind_barrier_scopes.scope_id"],
            name="fk_validmind_inventory_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "namespace",
            "external_id",
            name="uq_validmind_inventory_external_id",
        ),
        CheckConstraint(
            "decision_count >= 0 AND span_count >= 0 "
            "AND decision_count + span_count > 0",
            name="ck_validmind_inventory_activity",
        ),
        CheckConstraint(
            "version_count >= 0",
            name="ck_validmind_inventory_version_count",
        ),
        CheckConstraint(
            "json_array_length(versions) <= 100 "
            "AND json_array_length(versions) <= version_count",
            name="ck_validmind_inventory_versions",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_validmind_inventory_time_order",
        ),
        CheckConstraint(
            "length(external_id) = 32 AND length(legacy_external_id) = 32",
            name="ck_validmind_inventory_external_ids",
        ),
        Index(
            "ix_validmind_inventory_legacy_id",
            "namespace",
            "legacy_external_id",
        ),
        Index(
            "ix_validmind_inventory_llm_list",
            "namespace",
            "model_id",
            "scope_id",
            postgresql_where=text("span_count > 0"),
            sqlite_where=text("span_count > 0"),
        ),
        Index(
            "ix_validmind_inventory_ml_list",
            "namespace",
            "model_id",
            "scope_id",
            postgresql_where=text("span_count = 0 AND decision_count > 0"),
            sqlite_where=text("span_count = 0 AND decision_count > 0"),
        ),
    )


class ValidMindModelVersion(Base):
    """Reference counts supporting exact distinct-version maintenance."""

    __tablename__ = "validmind_model_versions"

    namespace = Column(String, primary_key=True)
    scope_id = Column(String(36), primary_key=True)
    model_id = Column(String, primary_key=True)
    model_version = Column(String, primary_key=True)
    decision_count = Column(BigInteger, nullable=False, default=0, server_default="0")
    span_count = Column(BigInteger, nullable=False, default=0, server_default="0")

    __table_args__ = (
        ForeignKeyConstraint(
            ["namespace", "scope_id", "model_id"],
            [
                "validmind_model_inventory.namespace",
                "validmind_model_inventory.scope_id",
                "validmind_model_inventory.model_id",
            ],
            name="fk_validmind_version_inventory",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "decision_count >= 0 AND span_count >= 0 "
            "AND decision_count + span_count > 0",
            name="ck_validmind_version_activity",
        ),
    )


class ValidMindLegacyModelAlias(Base):
    """Bounded resolver for the pre-0.5 namespace-wide model identifier."""

    __tablename__ = "validmind_legacy_model_aliases"

    namespace = Column(String, primary_key=True)
    legacy_external_id = Column(String(32), primary_key=True)
    target_count = Column(BigInteger, nullable=False)
    canonical_external_id = Column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "(target_count = 1 AND canonical_external_id IS NOT NULL) OR "
            "(target_count > 1 AND canonical_external_id IS NULL)",
            name="ck_validmind_legacy_alias_state",
        ),
    )


class ValidMindModelLink(Base):
    """Mutable synchronization metadata; source telemetry remains append-only."""
    __tablename__ = "validmind_model_links"

    namespace = Column(String, primary_key=True)
    external_id = Column(String, primary_key=True)
    vm_cuid = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class AgentBarrierGroup(Base):
    """
    Information barrier (Chinese wall) assignments.

    An agent assigned to a group can only recall memories tagged with that group
    OR memories with no barrier_group (public within the namespace).  Agents with
    no assignment (e.g. compliance officers) see everything in the namespace.

    Walls are enforced at recall time by hybrid_recall — they are NOT enforced at
    write time so that a memory can be tagged with any group by any writer.
    """
    __tablename__ = "agent_barrier_groups"

    namespace = Column(String, primary_key=True, index=True)
    agent_id = Column(String, primary_key=True)
    group_name = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class Agent(Base):
    __tablename__ = "agents"

    namespace = Column(String, primary_key=True, index=True)
    agent_id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    config = Column(JSON, nullable=False, server_default="{}")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hashed_key = Column(String, nullable=False, unique=True, index=True)
    namespace = Column(String, nullable=False)
    label = Column(String, nullable=True)
    scopes = Column(JSON, nullable=False, server_default='["read"]')
    # Optional named role (owner | analyst | compliance | readonly). When set, the
    # role's scope set is merged with any explicit `scopes` at auth time.
    role = Column(String, nullable=True)
    # Optional information-barrier group. When set, every read/write under this key
    # is scoped to this barrier (Chinese wall). Native OIDC tenant admins may
    # delegate only their own/same barrier; compatibility gateways must select a
    # pre-bound key. NULL = unbarriered (compliance / cross-desk).
    barrier_group = Column(String, nullable=True)
    # Credentials minted through the tenant OIDC lifecycle are distinguishable
    # from the compatibility/break-glass X-Admin-Secret provisioning path.
    provisioning_source = Column(
        String(32),
        nullable=False,
        default="breakglass_admin",
        server_default="breakglass_admin",
    )
    # A stable identity-binding reference, never the raw OIDC subject or token.
    created_by = Column(
        String(512),
        nullable=False,
        default="breakglass_admin:legacy",
        server_default="breakglass_admin:legacy",
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    rotated_from_id = Column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="RESTRICT"),
        nullable=True,
    )
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    # Lifecycle mutations use row locks plus this version for stale-client
    # detection. Authentication-time last-use updates intentionally do not bump it.
    version = Column(Integer, nullable=False, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "role IS NULL OR role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_api_key_role",
        ),
        CheckConstraint(
            "barrier_group IS NULL OR "
            "(length(barrier_group) BETWEEN 1 AND 255 "
            "AND barrier_group = trim(barrier_group))",
            name="ck_api_key_barrier_group",
        ),
        CheckConstraint(
            "provisioning_source IN ('breakglass_admin', 'tenant_oidc')",
            name="ck_api_key_provisioning_source",
        ),
        CheckConstraint("version >= 1", name="ck_api_key_version"),
        CheckConstraint(
            "provisioning_source <> 'tenant_oidc' OR expires_at IS NOT NULL",
            name="ck_api_key_tenant_expiry",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="ck_api_key_expiry_after_creation",
        ),
        CheckConstraint(
            "last_used_at IS NULL OR last_used_at >= created_at",
            name="ck_api_key_last_use_after_creation",
        ),
        CheckConstraint(
            "rotated_from_id IS NULL OR rotated_from_id <> id",
            name="ck_api_key_rotation_not_self",
        ),
        Index(
            "ix_api_keys_namespace_source_created",
            "namespace",
            "provisioning_source",
            "created_at",
        ),
        Index(
            "ix_api_keys_workload_inventory_page",
            "namespace",
            "provisioning_source",
            "barrier_group",
            "created_at",
            "id",
        ),
        Index("ix_api_keys_admin_inventory_page", "created_at", "id"),
        Index(
            "ix_api_keys_admin_namespace_page",
            "namespace",
            "created_at",
            "id",
        ),
        Index("uq_api_keys_rotated_from_id", "rotated_from_id", unique=True),
    )


class LiveFact(Base):
    """Compact read model: one row per live fact per agent.

    Maintained synchronously on the write path.  Recall queries this table
    instead of scanning ``memories WHERE valid_to IS NULL``, shrinking the
    search space 5–10×.  Keyed facts (predicate_key IS NOT NULL) have at most
    one row per (namespace, agent_id, predicate_key); unkeyed facts accumulate
    until explicitly superseded.

    Content and embedding are denormalized here so recall needs no join back
    to the memories table.
    """
    __tablename__ = "live_facts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("memories.id"), nullable=False, unique=True)

    # None for unkeyed memories; canonical "k=v|..." string for keyed ones.
    predicate_key = Column(String, nullable=True, index=True)
    subject_id = Column(String, nullable=True, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    event_time = Column(DateTime(timezone=True), nullable=False)
    importance = Column(Float, nullable=False, default=0.5)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")

    # Denormalized for zero-join recall
    content_encrypted = Column(LargeBinary, nullable=True)
    embedding = Column(_FlexVector(EMBED_DIM), nullable=True)

    __table_args__ = (
        Index("ix_live_facts_ns_agent", "namespace", "agent_id"),
        Index(
            "ix_live_facts_subject_erasure_page",
            "namespace",
            "subject_id",
            "id",
        ),
        Index("ix_live_facts_ns_agent_pred", "namespace", "agent_id", "predicate_key"),
        Index(
            "ix_live_facts_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MerkleAnchor(Base):
    """Periodic Merkle root anchors for the windowed audit-chain batcher.

    Each row covers a window of ``window_size`` EventLog rows whose leaf
    hashes form the Merkle tree.  ``root_hash`` is the Merkle root; the
    serial chain is continued by wiring ``prev_hash``/``row_hash`` exactly
    like a regular EventLog entry so existing verify_chain() logic still works.
    """
    __tablename__ = "merkle_anchors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    root_hash = Column(String(64), nullable=False)
    window_size = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    prev_anchor_id = Column(UUID(as_uuid=True), ForeignKey("merkle_anchors.id"), nullable=True)


class ConflictFlag(Base):
    """
    Flagged conflict between two memories that report different values for the
    same fact at the same (or ambiguous) point in time.

    Both memories remain valid and visible until a human resolves the conflict.
    Resolution options:
      accept_a — memory_a is authoritative; memory_b is invalidated
      accept_b — memory_b is authoritative; memory_a is invalidated
      dismiss   — both memories are left live (sources legitimately differ)

    A "conflict_detected" audit event is written at detection time.
    A "conflict_resolved" audit event is written at resolution time.
    """
    __tablename__ = "conflict_flags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False)

    # The two memories that disagree.  memory_a is the pre-existing memory;
    # memory_b is the newly ingested one that triggered the conflict detection.
    memory_a_id = Column(UUID(as_uuid=True), ForeignKey("memories.id"), nullable=False)
    memory_b_id = Column(UUID(as_uuid=True), ForeignKey("memories.id"), nullable=False)

    confidence = Column(Float, nullable=False)   # engine confidence that these conflict
    detected_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    # open | accept_a | accept_b | dismissed
    status = Column(String, nullable=False, default="open")
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolver_note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_conflict_flags_memory_a", "memory_a_id"),
        Index("ix_conflict_flags_memory_b", "memory_b_id"),
        Index("ix_conflict_flags_ns_status", "namespace", "status"),
        Index(
            "ix_conflict_validmind_ticket_list",
            "namespace",
            "detected_at",
            "id",
        ),
    )


class WebhookEndpoint(Base):
    """
    Registered webhook endpoint for a namespace.

    Lians will POST a signed JSON payload to `url` when any event in
    `events` occurs.  The payload is HMAC-SHA256-signed with `secret` so
    receivers can verify authenticity without trusting the network.

    Supported event types:
      memory.superseded       — a memory was invalidated by a newer fact
      memory.conflict         — a same-time contradiction was detected
      memory.erased           — a subject's DEK was destroyed (GDPR Art. 17)
      supersession.rejected   — a human reviewer rejected a supersession
    """
    __tablename__ = "webhook_endpoints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    url = Column(Text, nullable=False)
    secret = Column(String, nullable=False)
    events = Column(JSON, nullable=False)  # list[str]; JSONB on PostgreSQL via migration
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    description = Column(Text, nullable=True)

    __table_args__ = (Index("ix_webhook_endpoints_ns_enabled", "namespace", "enabled"),)


class WebhookDelivery(Base):
    """Delivery attempt log for a webhook event (used for retry and audit)."""
    __tablename__ = "webhook_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    endpoint_id = Column(
        UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    attempt = Column(Integer, nullable=False, default=1)
    status_code = Column(Integer, nullable=True)   # NULL = not yet attempted / error before HTTP
    error = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_webhook_deliveries_endpoint_created", "endpoint_id", "created_at"),)


class NamespacePolicy(Base):
    """
    Per-namespace retention and compliance policy.

    content_ttl_days  — days after ingestion_time before memory content is pruned.
                        NULL means retain forever.
    audit_retention_days — minimum days to keep event_log rows (SEC 17a-4 / CFTC default 5yr).
    legal_hold        — when True, prune is blocked regardless of ttl settings.
    stripe_customer_id — Stripe Customer ID for usage metering.  NULL = not billed.
    """
    __tablename__ = "namespace_policies"

    namespace = Column(String, primary_key=True)
    content_ttl_days = Column(sa_types.Integer, nullable=True)
    audit_retention_days = Column(sa_types.Integer, nullable=False, default=1825)
    legal_hold = Column(Boolean, nullable=False, default=False)
    stripe_customer_id = Column(String, nullable=True)
    # Governance remains opt-in for backward compatibility. Existing retention
    # or billing rows migrate as ``unconfigured`` and therefore stay unlimited.
    governance_status = Column(
        String(32),
        nullable=False,
        default="unconfigured",
        server_default="unconfigured",
    )
    allowed_processing_regions = Column(JSON, nullable=True)
    allowed_recorder_capture_modes = Column(JSON, nullable=True)
    recorder_events_daily_limit = Column(sa_types.BigInteger, nullable=True)
    decision_records_daily_limit = Column(sa_types.BigInteger, nullable=True)
    protected_actions_daily_limit = Column(sa_types.BigInteger, nullable=True)
    memory_writes_daily_limit = Column(sa_types.BigInteger, nullable=True)
    recalls_daily_limit = Column(sa_types.BigInteger, nullable=True)
    estimated_ingest_bytes_daily_limit = Column(sa_types.BigInteger, nullable=True)
    policy_version = Column(sa_types.BigInteger, nullable=False, default=0, server_default="0")
    governance_created_at = Column(DateTime(timezone=True), nullable=True)
    governance_created_by = Column(String(512), nullable=True)
    governance_updated_at = Column(DateTime(timezone=True), nullable=True)
    governance_updated_by = Column(String(512), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        CheckConstraint(
            "governance_status IN ('unconfigured', 'active', 'disabled')",
            name="ck_namespace_policy_governance_status",
        ),
        CheckConstraint("policy_version >= 0", name="ck_namespace_policy_version"),
        CheckConstraint(
            "recorder_events_daily_limit IS NULL OR recorder_events_daily_limit >= 0",
            name="ck_namespace_policy_recorder_quota",
        ),
        CheckConstraint(
            "decision_records_daily_limit IS NULL OR decision_records_daily_limit >= 0",
            name="ck_namespace_policy_decision_quota",
        ),
        CheckConstraint(
            "protected_actions_daily_limit IS NULL OR protected_actions_daily_limit >= 0",
            name="ck_namespace_policy_protected_action_quota",
        ),
        CheckConstraint(
            "memory_writes_daily_limit IS NULL OR memory_writes_daily_limit >= 0",
            name="ck_namespace_policy_memory_quota",
        ),
        CheckConstraint(
            "recalls_daily_limit IS NULL OR recalls_daily_limit >= 0",
            name="ck_namespace_policy_recall_quota",
        ),
        CheckConstraint(
            "estimated_ingest_bytes_daily_limit IS NULL "
            "OR estimated_ingest_bytes_daily_limit >= 0",
            name="ck_namespace_policy_ingest_bytes_quota",
        ),
    )


class RetentionSchedulerState(Base):
    """Singleton durable cursor for the leader-elected retention sweep."""

    __tablename__ = "retention_scheduler_state"

    id = Column(sa_types.Integer, primary_key=True, autoincrement=False)
    namespace_cursor = Column(String, nullable=True)
    sweep_generation = Column(
        sa_types.BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_retention_scheduler_state_singleton"),
        CheckConstraint(
            "sweep_generation >= 0",
            name="ck_retention_scheduler_state_generation",
        ),
    )


class PendingAdmission(Base):
    """
    A memory write held for human review by admission control (enforce mode).

    High-risk candidates (PII / PHI / MNPI) are parked here instead of being
    written live; an admin approves (→ the memory is created) or rejects them.
    Content is encrypted at rest and decrypted only for authorized reviewers.
    """
    __tablename__ = "pending_admissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False)
    barrier_group = Column(String, nullable=True, index=True)
    content = Column(Text, nullable=False)
    event_time = Column(DateTime(timezone=True), nullable=False)
    source = Column(String, nullable=True)
    subject_id = Column(String, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    importance = Column(Float, nullable=False, default=0.5)
    risk_tags = Column(JSON, nullable=False, server_default="[]")
    reasons = Column(JSON, nullable=False, server_default="[]")
    status = Column(String, nullable=False, default="pending", index=True)  # pending|approved|rejected
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolver_note = Column(Text, nullable=True)
    memory_id = Column(UUID(as_uuid=True), nullable=True)  # set when approved

    __table_args__ = (
        Index(
            "ix_pending_admission_ns_status_barrier_created_id",
            "namespace",
            "status",
            "barrier_group",
            "created_at",
            "id",
        ),
        Index(
            "ix_pending_admissions_subject_erasure_page",
            "namespace",
            "subject_id",
            "id",
        ),
    )


class LegacyMemoryIdempotency(Base):
    """Expand-phase 0.4.2 memory replay bridge; remove in a contract release."""

    __tablename__ = "idempotency_keys"

    key = Column(String, primary_key=True)
    namespace = Column(String, primary_key=True)
    memory_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_idempotency_keys_created_at", "created_at"),)


class OperationIdempotency(Base):
    """Immutable, payload-free completion ledger for authoritative mutations."""

    __tablename__ = "operation_idempotency"

    namespace = Column(String, primary_key=True)
    operation = Column(String(100), primary_key=True)
    key_hash = Column(String(64), primary_key=True)
    request_digest = Column(String(64), nullable=False)
    # Historical rows from the pre-0046 memory-only table had no request
    # digest. They remain duplicate-blocking, but cannot be served as verified
    # replays because the original principal/body/barrier binding is unknown.
    legacy_unverified_request = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    resource_kind = Column(String(64), nullable=False)
    resource_ids = Column(JSON, nullable=False, server_default="[]")
    response_status = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint(
            "length(operation) BETWEEN 1 AND 100",
            name="ck_operation_idempotency_operation_length",
        ),
        CheckConstraint(
            "length(key_hash) = 64 AND key_hash = lower(key_hash)",
            name="ck_operation_idempotency_key_hash",
        ),
        CheckConstraint(
            "length(request_digest) = 64 AND request_digest = lower(request_digest)",
            name="ck_operation_idempotency_request_digest",
        ),
        CheckConstraint(
            f"(legacy_unverified_request AND request_digest = "
            f"'{_LEGACY_IDEMPOTENCY_DIGEST}') OR "
            f"(NOT legacy_unverified_request AND request_digest <> "
            f"'{_LEGACY_IDEMPOTENCY_DIGEST}')",
            name="ck_operation_idempotency_legacy_digest",
        ),
        CheckConstraint(
            "length(resource_kind) BETWEEN 1 AND 64",
            name="ck_operation_idempotency_resource_kind",
        ),
        CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name="ck_operation_idempotency_response_status",
        ),
        Index("ix_operation_idempotency_created_at", "created_at"),
    )


class Relationship(Base):
    """
    Bitemporal relationship edge between two entities — the knowledge-graph layer.

    A directed triplet ``src_entity --rel_type--> dst_entity`` that inherits the
    same temporal, audit, and information-barrier machinery as ``memories``:

      valid_from / valid_to   — system-time window the edge was believed (Graphiti's
                                valid_at / invalid_at). NULL valid_to = currently live.
      event_time              — business time the relationship became true.
      invalidated_by          — the edge that superseded this one (exclusive rels).
      barrier_group           — RLS information-barrier tag, identical semantics to
                                memories: an edge in another barrier is invisible.
      subject_id              — optional data-subject link so crypto-shred reaches edges.

    Powers compliance graph queries that are inherently relational:
      legal      — conflict-of-interest reachability (ABA 1.7/1.9)
      finance    — related-party / beneficial-ownership within N hops (SEC, AML/KYC)
      healthcare — care-network and referral-pattern traversal (anti-kickback)
    """
    __tablename__ = "relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)

    src_entity = Column(String, nullable=False, index=True)
    rel_type = Column(String, nullable=False, index=True)
    dst_entity = Column(String, nullable=False, index=True)

    event_time = Column(DateTime(timezone=True), nullable=False)
    ingestion_time = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    invalidated_by = Column(UUID(as_uuid=True), ForeignKey("relationships.id"), nullable=True)

    barrier_group = Column(String, nullable=True, index=True)
    subject_id = Column(String, nullable=True, index=True)
    source = Column(String, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    content_hash = Column(String, nullable=False, index=True)

    __table_args__ = (
        Index("ix_rel_ns_agent_src", "namespace", "agent_id", "src_entity"),
        Index("ix_rel_ns_agent_dst", "namespace", "agent_id", "dst_entity"),
        Index(
            "ix_relationships_subject_erasure_page",
            "namespace",
            "subject_id",
            "id",
        ),
        Index(
            "ix_relationships_exclusive_live",
            "namespace",
            "agent_id",
            "barrier_group",
            "src_entity",
            "rel_type",
            "valid_to",
            "id",
            postgresql_where=text("valid_to IS NULL"),
            sqlite_where=text("valid_to IS NULL"),
        ),
    )


class MasterKeyRotationState(Base):
    """Counts-and-hashes-only checkpoint for the global offline rewrap."""

    __tablename__ = "master_key_rotation_state"

    singleton_id = Column(sa_types.SmallInteger, primary_key=True, default=1)
    run_id = Column(UUID(as_uuid=True), nullable=False)
    current_key_id = Column(String(64), nullable=False)
    previous_key_id = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False)
    total_values = Column(sa_types.BigInteger, nullable=False)
    rewritten_values = Column(sa_types.BigInteger, nullable=False)
    legacy_values_remaining = Column(sa_types.BigInteger, nullable=False)
    previous_values_remaining = Column(sa_types.BigInteger, nullable=False)
    unknown_values_remaining = Column(sa_types.BigInteger, nullable=False)
    plaintext_closures_remaining = Column(sa_types.BigInteger, nullable=False)
    inventory_sha256 = Column(String(64), nullable=False)
    backup_manifest_sha256 = Column(String(64), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_master_key_rotation_singleton"),
        CheckConstraint(
            "status IN ('verified', 'blocked')",
            name="ck_master_key_rotation_status",
        ),
        CheckConstraint(
            "length(current_key_id) BETWEEN 1 AND 64",
            name="ck_master_key_rotation_current_id",
        ),
        CheckConstraint(
            "previous_key_id IS NULL OR "
            "(length(previous_key_id) BETWEEN 1 AND 64 "
            "AND previous_key_id <> current_key_id)",
            name="ck_master_key_rotation_previous_id",
        ),
        CheckConstraint(
            "total_values >= 0 AND rewritten_values >= 0 AND "
            "legacy_values_remaining >= 0 AND previous_values_remaining >= 0 AND "
            "unknown_values_remaining >= 0 AND plaintext_closures_remaining >= 0",
            name="ck_master_key_rotation_counts",
        ),
        CheckConstraint(
            "length(inventory_sha256) = 64",
            name="ck_master_key_rotation_inventory_hash",
        ),
        CheckConstraint(
            "length(backup_manifest_sha256) = 64",
            name="ck_master_key_rotation_backup_hash",
        ),
    )


class MasterKeyWriteFenceState(Base):
    """Persistent bounded key-ID write policy installed by the offline operator."""

    __tablename__ = "master_key_write_fence_state"

    singleton_id = Column(sa_types.SmallInteger, primary_key=True, default=1)
    phase = Column(String(16), nullable=False)
    current_key_id = Column(String(64), nullable=False)
    previous_key_id = Column(String(64), nullable=True)
    generation = Column(sa_types.BigInteger, nullable=False)
    prepared_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    narrowed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1",
            name="ck_master_key_write_fence_singleton",
        ),
        CheckConstraint(
            "phase IN ('prepared', 'narrowed')",
            name="ck_master_key_write_fence_phase",
        ),
        CheckConstraint(
            "length(current_key_id) BETWEEN 1 AND 64",
            name="ck_master_key_write_fence_current_id",
        ),
        CheckConstraint(
            "previous_key_id IS NULL OR "
            "(length(previous_key_id) BETWEEN 1 AND 64 "
            "AND previous_key_id <> current_key_id)",
            name="ck_master_key_write_fence_previous_id",
        ),
        CheckConstraint(
            "generation >= 1",
            name="ck_master_key_write_fence_generation",
        ),
        CheckConstraint(
            "((phase = 'prepared' AND previous_key_id IS NOT NULL "
            "AND narrowed_at IS NULL) OR "
            "(phase = 'narrowed' AND previous_key_id IS NULL "
            "AND narrowed_at IS NOT NULL))",
            name="ck_master_key_write_fence_phase_storage",
        ),
    )


# Register additive model modules whenever callers import the historical
# ``lians.models.Base`` entry point (notably lightweight SQLite fixtures).
# Alembic imports these explicitly as well.
from . import integration_models as _integration_models  # noqa: E402,F401
from . import metering_models as _metering_models  # noqa: E402,F401
