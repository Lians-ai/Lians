"""
Supersession engine — decides what a new memory supersedes.

Phase 1: Stage 1 (candidate generation) + Stage 2 (rule-based classification).
Phase 2 adds: Stage 3 (LLM adjudication) for ambiguous pairs.

Change 3 (performance roadmap): keyed facts supersede deterministically by
event_time — no model call, no candidate scoring, no latency.  LLM adjudication
for unkeyed free-text is moved to an async worker (off the write path) when
``config.llm_adjudication_async`` is True.

Relations:
  SUPERSEDES           — same entity+attribute, newer event_time, values differ
  CONFIRMS             — same entity+attribute, same value
  ADDS                 — related topic, distinct attribute
  CONTRADICTS_SAME_TIME — conflicting values, no clear temporal ordering
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timezone as _tz
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory
from .schemas import SupersessionResult
from .crypto import decrypt_content
from .config import get_settings
from .llm_adjudication import llm_adjudicate  # module-level so tests can patch it

logger = logging.getLogger("agentmem.supersession")

# Threshold: cosine similarity above this is considered "same topic"
_SIM_THRESHOLD = 0.82

def _get_structured_keys() -> frozenset[str]:
    """Read structured keys from the active domain adapter — never hardcoded."""
    from .adapters import get_adapter
    return get_adapter().structured_keys


# Module-level alias for the common case (finance adapter at startup).
# supersession.py is hot-path code; we resolve once and cache the result.
# If the adapter changes at runtime (tests), callers that need the live value
# should call _get_structured_keys() directly.
_STRUCTURED_KEYS: frozenset[str] = frozenset({"ticker", "metric", "entity", "instrument", "cusip", "isin", "field"})


# ── Async LLM adjudication queue (Change 3) ──────────────────────────────────

_llm_queue: asyncio.Queue | None = None

AdjudicationTask = tuple[
    str,    # namespace
    str,    # agent_id
    UUID,   # superseded_memory_id (old)
    UUID,   # new_memory_id
    str,    # old_content
    str,    # new_content
    dict,   # metadata
]


def get_llm_queue() -> asyncio.Queue:
    global _llm_queue
    if _llm_queue is None:
        _llm_queue = asyncio.Queue(maxsize=1000)
    return _llm_queue


async def run_llm_adjudication_worker(session_factory) -> None:
    """Background worker: drain the LLM adjudication queue.

    For each task, re-runs Stage 3.  If the LLM downgrades SUPERSEDES →
    CONFIRMS (paraphrase detected), the supersession is rejected: the old
    memory is restored to live status and an audit event is appended.

    Runs until cancelled.
    """
    from .audit_chain import chain_log

    queue = get_llm_queue()
    logger.info("LLM adjudication worker started")

    while True:
        try:
            task: AdjudicationTask = await asyncio.wait_for(queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            logger.info("LLM adjudication worker stopping")
            return

        namespace, agent_id, old_id, new_id, old_content, new_content, meta = task
        try:
            relation, confidence, rationale = await llm_adjudicate(old_content, new_content, meta)
            if relation == "CONFIRMS":
                # Verdict: paraphrase — restore the superseded memory
                async with session_factory() as db:
                    old_mem = await db.get(Memory, old_id)
                    if old_mem and old_mem.valid_to is not None:
                        old_mem.valid_to = None
                        old_mem.superseded_by = None
                        old_mem.supersession_confidence = None
                        await chain_log(
                            db, namespace=namespace, agent_id=agent_id,
                            op="supersession_async_rejected",
                            memory_id=old_id,
                            content_hash=old_mem.content_hash,
                            payload={
                                "new_memory_id": str(new_id),
                                "llm_relation": relation,
                                "confidence": confidence,
                                "rationale": rationale,
                            },
                        )
                        await db.commit()
                        logger.info(
                            "Async LLM: restored superseded memory %s (CONFIRMS paraphrase)", old_id
                        )
            else:
                logger.debug(
                    "Async LLM: confirmed supersession %s → %s (%s %.2f)",
                    old_id, new_id, relation, confidence,
                )
        except Exception as exc:
            logger.warning("LLM adjudication worker error: %s", exc)
        finally:
            queue.task_done()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm_meta(meta: dict) -> dict[str, str]:
    """Return structured-key subset with values normalized through the domain adapter."""
    from .adapters import get_adapter
    adapter = get_adapter()
    sk = adapter.structured_keys
    return {k: adapter.normalize(k, str(meta[k])) for k in meta if k in sk}


def _metadata_overlap(old_meta: dict, new_meta: dict) -> set[str]:
    old_n = _norm_meta(old_meta)
    new_n = _norm_meta(new_meta)
    shared = set(old_n.keys()) & set(new_n.keys())
    return {k for k in shared if old_n[k] == new_n[k]}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz.utc)


# ── Change 3: deterministic keyed supersession ────────────────────────────────

def _is_full_structured_match(old_meta: dict, new_meta: dict) -> bool:
    """True when both memories share the same complete structured key set.

    Values are normalized via the domain adapter so AAPL == Apple == US0378331005.
    """
    old_s = _norm_meta(old_meta)
    new_s = _norm_meta(new_meta)
    return bool(old_s) and old_s == new_s


async def _keyed_supersession(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    new_meta: dict,
    new_event_time: datetime,
    new_content_hash: Optional[str] = None,
) -> SupersessionResult:
    """Fast path for keyed facts: supersede strictly by event_time, no model call.

    Fetches currently-live memories with the identical structured key set and
    supersedes those whose event_time is older than new_event_time.  O(small)
    DB fetch; zero LLM cost; result is fully deterministic.
    """
    stmt = select(Memory).where(
        and_(
            Memory.namespace == namespace,
            Memory.agent_id == agent_id,
            Memory.valid_to.is_(None),
            Memory.erased_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    superseded_ids: list[UUID] = []
    conflict_ids: list[UUID] = []
    confirms_ids: list[UUID] = []
    new_et = _utc(new_event_time)

    for mem in candidates:
        if mem.metadata_ is None:
            continue
        old_meta = dict(mem.metadata_)
        if not _is_full_structured_match(old_meta, new_meta):
            continue
        old_et = _utc(mem.event_time)
        if old_et < new_et:
            superseded_ids.append(mem.id)
        elif old_et == new_et:
            # Same structured key, same point in time.
            # If the content hashes match it's the same fact from a different source
            # (CONFIRMS) — a duplicate ingestion, not a conflict.  Only flag as
            # CONTRADICTS_SAME_TIME when the values are demonstrably different.
            if new_content_hash and mem.content_hash and new_content_hash == mem.content_hash:
                confirms_ids.append(mem.id)
            else:
                conflict_ids.append(mem.id)
        # old_et > new_et: a newer fact already exists — out-of-order ingestion,
        # treat as ADDS so we don't overwrite newer data.

    if superseded_ids:
        return SupersessionResult(
            relation="SUPERSEDES",
            confidence=1.0,
            superseded_ids=superseded_ids,
        )
    if conflict_ids:
        return SupersessionResult(
            relation="CONTRADICTS_SAME_TIME",
            confidence=0.9,
            conflict_ids=conflict_ids,
        )
    if confirms_ids:
        return SupersessionResult(
            relation="CONFIRMS",
            confidence=1.0,
        )
    return SupersessionResult(relation="ADDS", confidence=1.0)


# ── Stage 1: candidate generation ────────────────────────────────────────────

async def find_supersession_candidates(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    new_meta: dict[str, Any],
    new_embedding: list[float],
    new_event_time: datetime,
) -> list[Memory]:
    """Stage 1: find prior valid memories sharing structured keys + high cosine sim."""
    stmt = select(Memory).where(
        and_(
            Memory.namespace == namespace,
            Memory.agent_id == agent_id,
            Memory.valid_to.is_(None),
            Memory.erased_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    filtered = []
    for mem in candidates:
        if not new_meta or mem.metadata_ is None:
            continue
        old_meta = dict(mem.metadata_)
        overlap = _metadata_overlap(old_meta, new_meta)
        if not overlap:
            continue

        new_structured = _norm_meta(new_meta)
        old_structured = _norm_meta(old_meta)
        full_match = bool(new_structured) and new_structured == old_structured

        if full_match:
            filtered.append(mem)
            continue

        if mem.embedding is None:
            continue
        emb = mem.embedding if isinstance(mem.embedding, list) else list(mem.embedding)
        if _cosine(emb, new_embedding) >= _SIM_THRESHOLD:
            filtered.append(mem)

    return filtered


# ── Stage 2: rule-based classification ───────────────────────────────────────

def classify_relation(
    old_content: Optional[str],
    new_content: str,
    old_event_time: datetime,
    new_event_time: datetime,
    old_meta: dict,
    new_meta: dict,
) -> tuple[str, float]:
    """Stage 2: rule-based relation classification. Returns (relation, confidence)."""
    old_metric = old_meta.get("metric") or old_meta.get("field")
    new_metric = new_meta.get("metric") or new_meta.get("field")
    if old_metric and new_metric and old_metric != new_metric:
        return "ADDS", 0.9

    def _norm(s: Optional[str]) -> str:
        return (s or "").strip().lower()

    same_value = _norm(old_content) == _norm(new_content)
    old_et = _utc(old_event_time)
    new_et = _utc(new_event_time)

    if old_et < new_et:
        temporal_order = "new_is_later"
    elif old_et > new_et:
        temporal_order = "old_is_later"
    else:
        temporal_order = "same_time"

    if same_value:
        return "CONFIRMS", 0.9
    if temporal_order == "new_is_later":
        return "SUPERSEDES", 0.85
    if temporal_order == "same_time":
        return "CONTRADICTS_SAME_TIME", 0.7
    return "ADDS", 0.6


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_supersession(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    new_content: str,
    new_meta: dict[str, Any],
    new_embedding: list[float],
    new_event_time: datetime,
    subject_key: Optional[bytes] = None,
    new_memory_id: Optional[UUID] = None,
) -> SupersessionResult:
    """Full supersession funnel.

    Change 3 fast path: if the new memory has a full structured key match,
    supersede strictly by event_time (deterministic, zero LLM cost).

    Slow path (unkeyed or partial overlap): Stage 1 + Stage 2 rule-based.
    LLM adjudication (Stage 3) for unkeyed SUPERSEDES is either awaited
    synchronously (llm_adjudication_async=False) or enqueued for async
    processing off the write path (llm_adjudication_async=True).
    """
    settings = get_settings()

    # Change 3: keyed fast path — structured keys from domain adapter, not hardcoded
    import hashlib as _hl
    new_content_hash = _hl.sha256(new_content.encode()).hexdigest()
    from .adapters import get_adapter as _get_adapter
    _sk = _get_adapter().structured_keys
    new_structured = {k: new_meta[k] for k in new_meta if k in _sk and new_meta.get(k)}
    if new_structured:
        return await _keyed_supersession(
            db, namespace, agent_id, new_meta, new_event_time, new_content_hash
        )

    # Unkeyed path: Stage 1 + Stage 2
    candidates = await find_supersession_candidates(
        db, namespace, agent_id, new_meta, new_embedding, new_event_time
    )
    if not candidates:
        return SupersessionResult(relation="ADDS", confidence=1.0)

    superseded_ids: list[UUID] = []
    conflict_ids: list[UUID] = []
    best_relation = "ADDS"
    best_confidence = 1.0
    best_rationale: Optional[str] = None

    for candidate in candidates:
        old_content: Optional[str] = None
        if subject_key and candidate.content_encrypted:
            try:
                old_content = decrypt_content(bytes(candidate.content_encrypted), subject_key)
            except Exception:
                old_content = None
        elif candidate.content_encrypted and not candidate.subject_id:
            try:
                old_content = bytes(candidate.content_encrypted).decode()
            except Exception:
                old_content = None

        relation, confidence = classify_relation(
            old_content=old_content,
            new_content=new_content,
            old_event_time=candidate.event_time,
            new_event_time=new_event_time,
            old_meta=dict(candidate.metadata_ or {}),
            new_meta=new_meta,
        )

        rationale: Optional[str] = None
        if (
            relation == "SUPERSEDES"
            and settings.supersession_llm_stage
            and old_content is not None
        ):
            if settings.llm_adjudication_async and new_memory_id is not None:
                # Change 3: enqueue — don't block the write path
                try:
                    get_llm_queue().put_nowait((
                        namespace, agent_id,
                        candidate.id, new_memory_id,
                        old_content, new_content,
                        dict(candidate.metadata_ or {}),
                    ))
                except asyncio.QueueFull:
                    logger.warning("LLM adjudication queue full — skipping async Stage 3")
                # Proceed with Stage-2 SUPERSEDES verdict; worker may later refine
            else:
                # Synchronous Stage 3 (legacy / llm_adjudication_async=False)
                relation, confidence, rationale = await llm_adjudicate(
                    old_content=old_content,
                    new_content=new_content,
                    meta=new_meta,
                )

        if relation == "SUPERSEDES":
            superseded_ids.append(candidate.id)
            best_relation = "SUPERSEDES"
            best_confidence = confidence
            best_rationale = rationale
        elif relation == "CONTRADICTS_SAME_TIME" and best_relation != "SUPERSEDES":
            conflict_ids.append(candidate.id)
            best_relation = "CONTRADICTS_SAME_TIME"
            best_confidence = confidence
        elif relation == "CONFIRMS" and best_relation not in ("SUPERSEDES", "CONTRADICTS_SAME_TIME"):
            best_relation = "CONFIRMS"
            best_confidence = confidence
            best_rationale = rationale

    return SupersessionResult(
        relation=best_relation,
        confidence=best_confidence,
        superseded_ids=superseded_ids,
        conflict_ids=conflict_ids,
        rationale=best_rationale,
    )
