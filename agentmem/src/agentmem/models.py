import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Text, DateTime, Float, Boolean,
    ForeignKey, Index, LargeBinary, JSON,
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
    scopes = Column(JSON, nullable=False, server_default='["read"]')
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
