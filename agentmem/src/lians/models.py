import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, DateTime, Float, Boolean,
    ForeignKey, Index, LargeBinary, JSON, Integer,
    types as sa_types,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.engine import Dialect
from pgvector.sqlalchemy import Vector
from .db import Base
from .config import get_settings


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


class SubjectKey(Base):
    """Per-subject encryption keys — destroy to crypto-shred all their data."""
    __tablename__ = "subject_keys"

    subject_id = Column(String, primary_key=True)
    namespace = Column(String, nullable=False)
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

    agent_id = Column(String, primary_key=True)
    namespace = Column(String, nullable=False, index=True)
    group_name = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)


class Agent(Base):
    __tablename__ = "agents"

    agent_id = Column(String, primary_key=True)
    namespace = Column(String, nullable=False, index=True)
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
