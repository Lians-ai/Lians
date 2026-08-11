import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, DateTime, Float, Boolean,
    ForeignKey, Index, LargeBinary, JSON, Integer,
    UniqueConstraint, Uuid as UUID, text, types as sa_types,
)
from sqlalchemy.engine import Dialect
from pgvector.sqlalchemy import Vector
from .db import Base
from .config import get_settings

# Use SQLAlchemy's cross-dialect type: native UUID on PostgreSQL and CHAR(32)
# on SQLite. PostgreSQL's dialect-only UUID type gives SQLite numeric affinity,
# which can coerce rare numeric-looking UUID hex strings into integers/floats.


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


class MemoryFeedback(Base):
    """Append-only outcome signal for a recalled memory."""
    __tablename__ = "memory_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("memories.id"), nullable=False, index=True)
    signal = Column(String, nullable=False, index=True)
    weight = Column(Float, nullable=False, default=1.0)
    outcome = Column(String, nullable=True, index=True)
    query_hash = Column(String(64), nullable=True)
    source = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    policy_action = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index("ix_memory_feedback_ns_memory", "namespace", "memory_id"),
        Index("ix_memory_feedback_ns_created", "namespace", "created_at"),
    )


class AgentExperience(Base):
    """A decision episode whose eventual outcome can inform future ranking."""

    __tablename__ = "agent_experiences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    task = Column(Text, nullable=False)
    task_key = Column(String(300), nullable=False, index=True)
    decision = Column(JSON, nullable=False)
    context_memory_ids = Column(JSON, nullable=False, server_default="[]")
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    outcome = Column(JSON, nullable=True)
    reward = Column(Float, nullable=True)
    reviewer_feedback = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="open", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_agent_experiences_ns_agent_created", "namespace", "agent_id", "created_at"),
    )


class ReflectionProposal(Base):
    """Review-gated proposal distilled from repeated successful experiences."""

    __tablename__ = "reflection_proposals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    task_key = Column(String(300), nullable=False, index=True)
    content = Column(Text, nullable=False)
    supporting_experience_ids = Column(JSON, nullable=False, server_default="[]")
    confidence = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)
    reviewer_note = Column(Text, nullable=True)
    promoted_memory_id = Column(UUID(as_uuid=True), ForeignKey("memories.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_reflection_proposals_ns_status", "namespace", "status", "created_at"),
        Index(
            "uq_reflection_pending_task",
            "namespace",
            "agent_id",
            "task_key",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )


class SubjectKey(Base):
    """Per-subject encryption keys — destroy to crypto-shred all their data.

    Keyed by (namespace, subject_id): subject_id is only unique *within* a
    tenant, so a bare subject_id PK would let two tenants share one DEK and let
    one tenant's erase crypto-shred another's data. See migration 0019.
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
    prev_hash = Column(String(64), nullable=True)   # row_hash of the preceding row in this namespace
    row_hash = Column(String(64), nullable=True)    # SHA-256(prev_hash || this row's canonical fields)
    # v1 excludes payload for backward compatibility; v2 hashes canonical JSON payload.
    hash_version = Column(Integer, nullable=False, default=1, server_default="1")


class DecisionEnvelope(Base):
    """Mutable capture window that becomes immutable when a decision is sealed.

    The envelope is the correlation boundary for every artifact that can shape
    an agent decision. Evidence links remain append-only after sealing so later
    reviews and outcomes can strengthen the record without rewriting history.
    """

    __tablename__ = "decision_envelopes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    decision_type = Column(String, nullable=False, index=True)
    regime = Column(String, nullable=True, index=True)
    subject_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    trace_id = Column(String(32), nullable=True, index=True)
    run_id = Column(String, nullable=True, index=True)
    knowledge_as_of = Column(DateTime(timezone=True), nullable=True)
    completeness_profile = Column(
        String, nullable=False, default="standard", server_default="standard"
    )
    requirements = Column(JSON, nullable=False, server_default="{}")
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    status = Column(String, nullable=False, default="open", server_default="open", index=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    sealed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_decision_envelope_ns_status", "namespace", "status", "created_at"),
        Index("ix_decision_envelope_ns_trace", "namespace", "trace_id"),
    )


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
    envelope_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_envelopes.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    namespace = Column(String, nullable=False, index=True)
    agent_id = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    decision_type = Column(String, nullable=False, index=True)
    outcome = Column(String, nullable=False)
    reason_codes = Column(JSON, nullable=False, server_default="[]")
    regime = Column(String, nullable=True, index=True)
    subject_id = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    trace_id = Column(String(32), nullable=True, index=True)
    run_id = Column(String, nullable=True, index=True)
    model_id = Column(String, nullable=True)
    model_version = Column(String, nullable=True)
    policy_version = Column(String, nullable=True)
    prompt_id = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    runtime_version = Column(String, nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=False, index=True)
    recorded_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    knowledge_as_of = Column(DateTime(timezone=True), nullable=False)
    evidence_memory_ids = Column(JSON, nullable=False, server_default="[]")
    input_hash = Column(String(64), nullable=True)
    output_hash = Column(String(64), nullable=True)
    replay_manifest_hash = Column(String(64), nullable=True)
    human_review_status = Column(String, nullable=False, default="not_requested")
    human_reviewer = Column(String, nullable=True)
    human_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    supersedes_id = Column(UUID(as_uuid=True), nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    record_hash = Column(String(64), nullable=False, index=True)

    __table_args__ = (
        Index("ix_decision_ns_decided", "namespace", "decided_at"),
        Index("ix_decision_ns_subject", "namespace", "subject_id"),
        Index("ix_decision_ns_trace", "namespace", "trace_id"),
    )


class DecisionEvidenceLink(Base):
    """Append-only edge from a decision envelope to a source artifact."""

    __tablename__ = "decision_evidence_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    envelope_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_envelopes.id"),
        nullable=False,
        index=True,
    )
    barrier_group = Column(String, nullable=True, index=True)
    evidence_type = Column(String, nullable=False, index=True)
    role = Column(String, nullable=False, index=True)
    source_id = Column(String(512), nullable=False, index=True)
    source_version = Column(String(255), nullable=True, index=True)
    artifact_hash = Column(String(64), nullable=True, index=True)
    occurred_at = Column(DateTime(timezone=True), nullable=True, index=True)
    metadata_ = Column("metadata", JSON, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        Index(
            "ix_decision_evidence_source",
            "namespace",
            "evidence_type",
            "source_id",
            "source_version",
        ),
        Index(
            "ix_decision_evidence_envelope_role",
            "envelope_id",
            "role",
            "created_at",
        ),
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

    __table_args__ = (Index("ix_ledger_event_ns_time", "namespace", "occurred_at"),)


class OTelSpan(Base):
    """Append-only copy of a span accepted through the OTLP/HTTP receiver."""
    __tablename__ = "otel_spans"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
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

    __table_args__ = (
        UniqueConstraint(
            "namespace", "trace_id", "span_id", name="uq_otel_span_ns_trace_span"
        ),
        Index("ix_otel_span_ns_received", "namespace", "received_at"),
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


class Workspace(Base):
    """Hosted or local tenant workspace metadata; namespace remains the boundary."""

    __tablename__ = "workspaces"

    namespace = Column(String, primary_key=True)
    display_name = Column(String(200), nullable=False)
    plan = Column(String(50), nullable=False, default="developer")
    region = Column(String(100), nullable=True)
    settings = Column(JSON, nullable=False, server_default="{}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class Connector(Base):
    """A workspace-scoped source feeding normalized events into governed memory."""

    __tablename__ = "connectors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    kind = Column(String(50), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    agent_id = Column(String(255), nullable=False, index=True)
    scope = Column(String(1000), nullable=True)
    status = Column(String(50), nullable=False, default="active", index=True)
    config = Column(JSON, nullable=False, server_default="{}")
    cursor = Column(String(1000), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_connector_namespace_name"),
        Index("ix_connectors_ns_kind_status", "namespace", "kind", "status"),
    )


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
    # is scoped to this barrier (Chinese wall). An SSO gateway selects the key from
    # the caller's IdP group, so the IdP group -> namespace/role/barrier chain is
    # enforced end to end. NULL = unbarriered (compliance / cross-desk).
    barrier_group = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)


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
    status = Column(String, nullable=False, default="open", index=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolver_note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_conflict_flags_ns_status", "namespace", "status"),
    )


class WebhookEndpoint(Base):
    """
    Registered webhook endpoint for a namespace.

    AgentMem will POST a signed JSON payload to `url` when any event in
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
    endpoint_id = Column(UUID(as_uuid=True), ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    attempt = Column(Integer, nullable=False, default=1)
    status_code = Column(Integer, nullable=True)   # NULL = not yet attempted / error before HTTP
    error = Column(Text, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("ix_webhook_deliveries_endpoint_created", "endpoint_id", "created_at"),)


class DurableJob(Base):
    """Database-backed work item for crash-safe external side effects."""

    __tablename__ = "durable_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False, server_default="{}")
    dedupe_key = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=8)
    available_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    leased_by = Column(String, nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("namespace", "kind", "dedupe_key", name="uq_durable_job_dedupe"),
        Index("ix_durable_jobs_claim", "status", "available_at", "lease_until"),
    )


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
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


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


class IdempotencyKey(Base):
    """
    Maps a client-supplied Idempotency-Key (per namespace) to the memory it
    created, so a retried write returns the original result instead of inserting
    a duplicate. The SDK sends the same key on automatic retries, giving
    exactly-once write semantics across network blips.
    """
    __tablename__ = "idempotency_keys"

    key = Column(String, primary_key=True)
    namespace = Column(String, primary_key=True)
    memory_id = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


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
    )
