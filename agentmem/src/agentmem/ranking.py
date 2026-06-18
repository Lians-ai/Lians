"""
Hybrid retrieval and temporal ranking.

score = w_sem * cosine_similarity
      + w_lex * lexical_match   (simple token overlap; replace with BM25 in prod)
      + w_rec * recency_decay
      + w_imp * importance

When as_of is set, temporal filter is applied FIRST (pre-filter, not post-filter).
"""
from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, and_, or_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory
from .crypto import decrypt_content

# When the ANN pre-filter succeeds (Postgres + pgvector), fetch this many
# approximate nearest-neighbours before re-ranking with the hybrid scorer.
# This lets the HNSW index do the heavy lifting for large collections while
# keeping the Python-side scoring accurate for the final top-k.
_ANN_PREFETCH_MULTIPLIER = 20

# Default weights — tune against finance_bench
W_SEM = 0.50
W_LEX = 0.20
W_REC = 0.15
W_IMP = 0.15

RECENCY_HALF_LIFE_DAYS = 30.0


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


def _lexical_score(query: str, content: str) -> float:
    """Token overlap (Jaccard-ish). Replace with BM25 in production."""
    q_tokens = set(query.lower().split())
    c_tokens = set(content.lower().split())
    if not q_tokens:
        return 0.0
    return len(q_tokens & c_tokens) / len(q_tokens | c_tokens)


def _recency_decay(event_time: datetime) -> float:
    now = datetime.now(timezone.utc)
    # SQLite returns naive datetimes; treat as UTC
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    age_days = (now - event_time).total_seconds() / 86400
    return math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE_DAYS)


def _validity_score(mem: Memory, as_of: Optional[datetime]) -> float:
    """
    In present-time mode (no as_of): currently-valid fact gets 1.0,
    superseded gets 0.1 (not zero — might still be historically relevant).
    In as_of mode: caller has already filtered by validity window.
    """
    if as_of is not None:
        return 1.0
    if mem.valid_to is None:
        return 1.0
    return 0.1


async def _fetch_candidates(
    db: AsyncSession,
    conditions: list,
    query_embedding: list[float],
    k: int,
) -> list:
    """
    Retrieve candidate memories from the database.

    On PostgreSQL with pgvector: use the HNSW index via the `<=>` cosine-
    distance operator to pre-filter to the most promising candidates before
    Python-side hybrid scoring.  This keeps latency sub-millisecond for
    collections with millions of vectors.

    On SQLite (tests / local mode): the `::vector` cast fails; we catch it
    and fall back to a full table scan.  Correctness is identical; only
    performance degrades at scale.
    """
    base_stmt = select(Memory).where(and_(*conditions))

    if query_embedding:
        try:
            pre_k = max(k * _ANN_PREFETCH_MULTIPLIER, 100)
            # Format as Postgres vector literal — safe: values are model floats
            vec_lit = "[" + ",".join(f"{x:.8f}" for x in query_embedding) + "]"
            ann_stmt = (
                base_stmt
                .order_by(text(f"embedding <=> '{vec_lit}'::vector"))
                .limit(pre_k)
            )
            result = await db.execute(ann_stmt)
            return result.scalars().all()
        except Exception:
            pass  # Not pgvector — fall through to full scan

    result = await db.execute(base_stmt)
    return result.scalars().all()


async def hybrid_recall(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    query: str,
    query_embedding: list[float],
    k: int = 5,
    as_of: Optional[datetime] = None,
    filters: Optional[dict[str, Any]] = None,
    subject_keys: Optional[dict[str, bytes]] = None,  # subject_id -> plaintext key
) -> list[tuple[Memory, float, Optional[str]]]:
    """
    Returns list of (Memory, score, decrypted_content).
    """
    conditions = [
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
        Memory.erased_at.is_(None),
    ]

    if as_of is not None:
        # Temporal filter: memory must be valid at the as_of point
        conditions.append(Memory.valid_from <= as_of)
        conditions.append(or_(Memory.valid_to.is_(None), Memory.valid_to > as_of))
        conditions.append(Memory.event_time <= as_of)
    else:
        # Present-time: only return currently valid memories (not superseded)
        conditions.append(Memory.valid_to.is_(None))

    # Metadata filters
    if filters:
        for key, val in filters.items():
            conditions.append(Memory.metadata_[key].as_string() == str(val))

    candidates = await _fetch_candidates(db, conditions, query_embedding, k)

    scored: list[tuple[Memory, float, Optional[str]]] = []
    for mem in candidates:
        # Decrypt content if possible
        content: Optional[str] = None
        if mem.content_encrypted and subject_keys and mem.subject_id:
            key = subject_keys.get(mem.subject_id)
            if key:
                try:
                    content = decrypt_content(bytes(mem.content_encrypted), key)
                except Exception:
                    content = None
        elif mem.content_encrypted and not mem.subject_id:
            # Non-PII: stored unencrypted as bytes-encoded string
            try:
                content = bytes(mem.content_encrypted).decode()
            except Exception:
                content = None

        emb = list(mem.embedding) if mem.embedding is not None else None
        sem = _cosine(query_embedding, emb) if emb else 0.0
        lex = _lexical_score(query, content or "") if content else 0.0
        rec = _recency_decay(mem.event_time)
        val = _validity_score(mem, as_of)

        score = val * (
            W_SEM * sem
            + W_LEX * lex
            + W_REC * rec
            + W_IMP * mem.importance
        )
        scored.append((mem, score, content))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
