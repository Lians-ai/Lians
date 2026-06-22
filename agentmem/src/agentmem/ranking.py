"""
Hybrid retrieval and temporal ranking.

Change 1 (current-facts read model): present-time recall now queries
``live_facts`` instead of ``memories WHERE valid_to IS NULL``.  This is a
5–10× smaller table, eliminates temporal predicates from the hot path, and
keeps the barrier filter structural (live_facts.barrier_group) rather than
a post-scan.

Change 4 (partitioned vector index): ANN queries are restricted to a single
(namespace, agent_id) partition via the indexed columns on live_facts, so
the HNSW scan never touches other agents' vectors.

score = w_sem * cosine_similarity
      + w_lex * BM25_score
      + w_rec * recency_decay
      + w_imp * importance

Point-in-time queries (as_of set) still go to the ``memories`` table because
``live_facts`` only reflects the present state.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory, LiveFact
from .crypto import decrypt_content

_ANN_PREFETCH_MULTIPLIER = 20

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


_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_AVG_DOC_LEN = 50.0


def _bm25_score(query: str, content: str) -> float:
    q_tokens = set(query.lower().split())
    c_words = content.lower().split()
    if not q_tokens or not c_words:
        return 0.0
    doc_len = len(c_words)
    tf: dict[str, int] = {}
    for w in c_words:
        tf[w] = tf.get(w, 0) + 1
    score = 0.0
    for token in q_tokens:
        f = tf.get(token, 0)
        if f == 0:
            continue
        tf_norm = (f * (_BM25_K1 + 1)) / (
            f + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / _BM25_AVG_DOC_LEN)
        )
        score += tf_norm
    return score / len(q_tokens)


def _recency_decay(event_time: datetime) -> float:
    now = datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    age_days = (now - event_time).total_seconds() / 86400
    return math.exp(-math.log(2) * age_days / RECENCY_HALF_LIFE_DAYS)


# ── Change 1: present-time recall uses live_facts ────────────────────────────

async def _fetch_live_candidates(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    barrier_group: Optional[str],
    filters: Optional[dict],
    query_embedding: list[float],
    k: int,
) -> list[LiveFact]:
    """Fetch from live_facts — the compact present-time projection."""
    conditions = [
        LiveFact.namespace == namespace,
        LiveFact.agent_id == agent_id,
    ]
    # Change 4: barrier filter is structural — only the agent's partition is scanned
    if barrier_group is not None:
        conditions.append(
            or_(LiveFact.barrier_group == barrier_group, LiveFact.barrier_group.is_(None))
        )
    if filters:
        for key, val in filters.items():
            conditions.append(LiveFact.metadata_[key].as_string() == str(val))

    base_stmt = select(LiveFact).where(and_(*conditions))

    if query_embedding:
        try:
            pre_k = max(k * _ANN_PREFETCH_MULTIPLIER, 100)
            vec_lit = "[" + ",".join(f"{x:.8f}" for x in query_embedding) + "]"
            ann_stmt = (
                base_stmt
                .order_by(text(f"embedding <=> '{vec_lit}'::vector"))
                .limit(pre_k)
            )
            result = await db.execute(ann_stmt)
            return list(result.scalars().all())
        except Exception:
            pass

    result = await db.execute(base_stmt)
    return list(result.scalars().all())


async def _fetch_historical_candidates(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    barrier_group: Optional[str],
    filters: Optional[dict],
    query_embedding: list[float],
    k: int,
    as_of: datetime,
) -> list[Memory]:
    """Fetch from memories for point-in-time (as_of) recall."""
    conditions = [
        Memory.namespace == namespace,
        Memory.agent_id == agent_id,
        Memory.erased_at.is_(None),
        Memory.valid_from <= as_of,
        or_(Memory.valid_to.is_(None), Memory.valid_to > as_of),
        Memory.event_time <= as_of,
    ]
    if barrier_group is not None:
        conditions.append(
            or_(Memory.barrier_group == barrier_group, Memory.barrier_group.is_(None))
        )
    if filters:
        for key, val in filters.items():
            conditions.append(Memory.metadata_[key].as_string() == str(val))

    base_stmt = select(Memory).where(and_(*conditions))

    if query_embedding:
        try:
            pre_k = max(k * _ANN_PREFETCH_MULTIPLIER, 100)
            vec_lit = "[" + ",".join(f"{x:.8f}" for x in query_embedding) + "]"
            ann_stmt = (
                base_stmt
                .order_by(text(f"embedding <=> '{vec_lit}'::vector"))
                .limit(pre_k)
            )
            result = await db.execute(ann_stmt)
            return list(result.scalars().all())
        except Exception:
            pass

    result = await db.execute(base_stmt)
    return list(result.scalars().all())


def _decrypt(row: Any, subject_keys: dict[str, bytes]) -> Optional[str]:
    """Decrypt content from either a LiveFact or Memory row."""
    if row.content_encrypted is None:
        return None
    subject_id = getattr(row, "subject_id", None)
    if subject_id and subject_keys:
        key = subject_keys.get(subject_id)
        if key:
            try:
                return decrypt_content(bytes(row.content_encrypted), key)
            except Exception:
                return None
    if not subject_id:
        try:
            return bytes(row.content_encrypted).decode()
        except Exception:
            return None
    return None


def _score_live(
    fact: LiveFact,
    query: str,
    query_embedding: list[float],
    subject_keys: dict[str, bytes],
) -> tuple[float, Optional[str]]:
    content = _decrypt(fact, subject_keys)
    emb = list(fact.embedding) if fact.embedding is not None else None
    sem = _cosine(query_embedding, emb) if emb else 0.0
    lex = _bm25_score(query, content or "") if content else 0.0
    rec = _recency_decay(fact.event_time)
    score = W_SEM * sem + W_LEX * lex + W_REC * rec + W_IMP * fact.importance
    return score, content


def _score_historical(
    mem: Memory,
    query: str,
    query_embedding: list[float],
    subject_keys: dict[str, bytes],
) -> tuple[float, Optional[str]]:
    content = _decrypt(mem, subject_keys)
    emb = list(mem.embedding) if mem.embedding is not None else None
    sem = _cosine(query_embedding, emb) if emb else 0.0
    lex = _bm25_score(query, content or "") if content else 0.0
    rec = _recency_decay(mem.event_time)
    score = W_SEM * sem + W_LEX * lex + W_REC * rec + W_IMP * mem.importance
    return score, content


# ── Public API ────────────────────────────────────────────────────────────────

async def hybrid_recall(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    query: str,
    query_embedding: list[float],
    k: int = 5,
    as_of: Optional[datetime] = None,
    filters: Optional[dict[str, Any]] = None,
    subject_keys: Optional[dict[str, bytes]] = None,
    barrier_group: Optional[str] = None,
    live_facts_override: Optional[list] = None,
) -> list[tuple[Any, float, Optional[str]]]:
    """Return list of (row, score, decrypted_content).

    present-time (no as_of): queries ``live_facts`` — compact, fast, no
    temporal predicates.  ``live_facts_override`` allows the session cache
    (Change 7) to supply pre-fetched rows without a DB round-trip.

    point-in-time (as_of set): queries ``memories`` with the full temporal
    filter — as_of recall always hits the bitemporal log.
    """
    subject_keys = subject_keys or {}

    if as_of is not None:
        # Point-in-time: must go to the bitemporal log
        candidates = await _fetch_historical_candidates(
            db, namespace, agent_id, barrier_group, filters, query_embedding, k, as_of
        )
        scored: list[tuple[Memory, float, Optional[str]]] = []
        for mem in candidates:
            score, content = _score_historical(mem, query, query_embedding, subject_keys)
            scored.append((mem, score, content))
    else:
        # Present-time: use live_facts (Change 1)
        if live_facts_override is not None:
            facts = live_facts_override
            if barrier_group is not None:
                facts = [f for f in facts if f.barrier_group is None or f.barrier_group == barrier_group]
            if filters:
                facts = [
                    f for f in facts
                    if all((dict(f.metadata_ or {})).get(key) == str(val) for key, val in filters.items())
                ]
        else:
            facts = await _fetch_live_candidates(
                db, namespace, agent_id, barrier_group, filters, query_embedding, k
            )

        # Always return Memory objects for API consistency — fetch the canonical
        # Memory row so callers can use .id, .valid_to, .erased_at, etc.
        scored = []
        for fact in facts:
            score, content = _score_live(fact, query, query_embedding, subject_keys)
            mem = await db.get(Memory, fact.memory_id)
            if mem is not None:
                scored.append((mem, score, content))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
