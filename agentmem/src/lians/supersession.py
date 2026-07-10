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
  REFINES              — new fact narrows the old (contains everything the old
                         said, plus detail); old validity closes like SUPERSEDES
                         but the audit trail records a narrowing, not a stale value
  CONFIRMS             — same entity+attribute, same value
  ADDS                 — related topic, distinct attribute
  CONTRADICTS_SAME_TIME — conflicting values, no clear temporal ordering

REFINES is harvested from the Memory Governor's proposal vocabulary
(docs/governor-integration.md Phase 3) so Governor proposals and engine
relations stay interoperable.
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
# When the incoming free text explicitly announces a revision (see
# _REVISION_CUE_RE), same-topic candidacy is admitted at a lower bar: natural
# restatements ("I'm vegan" → "I eat fish now, call me pescatarian") land in
# the 0.6-0.76 cosine range on doc-doc embeddings, well below 0.82. Calibrated
# against should-not-supersede controls (additive facts, cross-topic
# interjections), which measure ≤0.59 on the same model.
_CUE_SIM_THRESHOLD = 0.60

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


def _has_structured_key(meta: dict) -> bool:
    """True if *meta* carries any of the domain adapter's structured keys.

    Distinguishes keyed facts (ticker/metric/entity/…) — which may supersede —
    from unkeyed free text (chat turns, notes), which never auto-supersedes.
    """
    from .adapters import get_adapter
    sk = get_adapter().structured_keys
    return any(k in sk for k in (meta or {}))


def _non_structured_meta(meta: dict) -> dict:
    """Remaining discriminating metadata: everything outside the structured key
    set and provenance/system keys (leading underscore)."""
    from .adapters import get_adapter
    sk = get_adapter().structured_keys
    return {k: v for k, v in (meta or {}).items()
            if k not in sk and not str(k).startswith("_")}


def _metadata_overlap(old_meta: dict, new_meta: dict) -> set[str]:
    old_n = _norm_meta(old_meta)
    new_n = _norm_meta(new_meta)
    shared = set(old_n.keys()) & set(new_n.keys())
    return {k for k in shared if old_n[k] == new_n[k]}


def _narrows(old_content: Optional[str], new_content: str) -> bool:
    """True when NEW restates everything OLD said and adds detail (a narrowing).

    Token-set containment, same tokenizer as the Governor's similarity(): the
    old fact's tokens must be a *proper* subset of the new fact's. A changed
    value breaks containment (the old value's token is missing), so genuine
    updates still classify as SUPERSEDES.
    """
    if not old_content:
        return False
    import re as _re
    old_tokens = set(_re.findall(r"[a-z0-9]+", old_content.lower()))
    new_tokens = set(_re.findall(r"[a-z0-9]+", new_content.lower()))
    return bool(old_tokens) and old_tokens < new_tokens


# Deterministic revision-cue lexicon for unkeyed free text. Without structured
# keys, two differing statements are DISTINCT by default (see classify_relation's
# guard) — but a statement that *announces itself* as a revision ("I eat fish
# now", "switched to Pacific Time", "rate adjusted to $175") is the one case
# where free text can supersede: high Stage-1 similarity (same topic) plus an
# explicit change marker plus a later event_time. Rule-based, reproducible, no
# model call — and the verdict lands at moderate confidence so it is visible in
# review_supersessions and eligible for Stage-3 LLM adjudication when enabled.
import re as _re_mod

_REVISION_CUE_RE = _re_mod.compile(
    r"\b(?:"
    r"no longer|not\s+\w+\s+anymore|anymore|now|instead|actually|wait|"
    r"correction|corrected|updated?|revised?|changed?|switch(?:ed)?|moved?|"
    r"relocat\w+|renamed?|adjust(?:ed)?|raised?|lowered?|increas\w+|decreas\w+|"
    r"went (?:up|down)|left|quit|resigned|started (?:as|at)|promoted|demoted|"
    r"taken over|took over|replace[sd]?|renewal|renewed|restated|amend\w+|"
    r"effective|from now on|these days"
    r")\b",
    _re_mod.IGNORECASE,
)


def _has_revision_cue(text: Optional[str]) -> bool:
    return bool(text) and bool(_REVISION_CUE_RE.search(text))


try:
    import numpy as _np
except ImportError:  # pragma: no cover - numpy ships with the embedding stack
    _np = None


def _cosine(a: list[float], b: list[float]) -> float:
    # Vectorized: candidate generation computes this against every live memory
    # on each unkeyed write, so the pure-Python loop dominated ingest latency.
    if _np is not None:
        va = _np.asarray(a, dtype=_np.float32)
        vb = _np.asarray(b, dtype=_np.float32)
        denom = float(_np.linalg.norm(va)) * float(_np.linalg.norm(vb)) + 1e-9
        return float(va @ vb) / denom
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


def _candidate_content(mem: Memory, subject_key: Optional[bytes]) -> Optional[str]:
    """Best-effort plaintext of a candidate memory (same rules as the slow path):
    subject-keyed rows decrypt with the caller's DEK, unkeyed rows are raw bytes.
    Returns None when the content is unavailable — callers must degrade safely."""
    if mem.content_encrypted is None:
        return None
    if mem.subject_id:
        if not subject_key:
            return None
        try:
            return decrypt_content(bytes(mem.content_encrypted), subject_key)
        except Exception:
            return None
    try:
        return bytes(mem.content_encrypted).decode()
    except Exception:
        return None


async def _keyed_supersession(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    new_meta: dict,
    new_event_time: datetime,
    new_content_hash: Optional[str] = None,
    new_content: Optional[str] = None,
    subject_key: Optional[bytes] = None,
) -> SupersessionResult:
    """Fast path for keyed facts: supersede strictly by event_time, no model call.

    Fetches currently-live memories with the identical structured key set and
    supersedes those whose event_time is older than new_event_time.  O(small)
    DB fetch; zero LLM cost; result is fully deterministic.

    The relation label is still classified, deterministically: an identical
    value re-stated later is CONFIRMS (duplicate ingestion, nothing to close),
    a later value that restates the old and adds detail is REFINES (0.8 — same
    window-close as SUPERSEDES, different audit label), and only a genuinely
    changed value is SUPERSEDES (1.0). Without this, REFINES/CONFIRMS were
    unreachable for keyed facts — every keyed rewrite audited as a 1.0
    supersession even when nothing changed.
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
    refines_ids: list[UUID] = []
    conflict_ids: list[UUID] = []
    confirms_close_ids: list[UUID] = []   # identical value re-stated LATER: close old window
    confirms_dupe_ids: list[UUID] = []    # identical value at the SAME time: coexisting duplicate
    newer_existing: Optional[Memory] = None  # live same-key fact with LATER event_time
    new_et = _utc(new_event_time)

    for mem in candidates:
        if mem.metadata_ is None:
            continue
        old_meta = dict(mem.metadata_)
        if not _is_full_structured_match(old_meta, new_meta):
            continue
        old_et = _utc(mem.event_time)
        if old_et < new_et:
            # Identical value observed again later: a re-confirmation. The new
            # observation becomes the live copy (old window closes at the new
            # event_time — validity is continuous since the value is unchanged),
            # but the audit label must say CONFIRMS, not SUPERSEDES: nothing
            # actually changed.
            if new_content_hash and mem.content_hash and new_content_hash == mem.content_hash:
                confirms_close_ids.append(mem.id)
            elif new_content and _narrows(_candidate_content(mem, subject_key), new_content):
                refines_ids.append(mem.id)
            else:
                superseded_ids.append(mem.id)
        elif old_et == new_et:
            # Same structured key, same point in time.
            # If the content hashes match it's the same fact from a different source
            # (CONFIRMS) — a duplicate ingestion, not a conflict; closing the old
            # window here would create a zero-width validity window, so both stay.
            # Only flag as CONTRADICTS_SAME_TIME when the values are demonstrably
            # different.
            if new_content_hash and mem.content_hash and new_content_hash == mem.content_hash:
                confirms_dupe_ids.append(mem.id)
            else:
                conflict_ids.append(mem.id)
        else:
            # old_et > new_et: a newer same-key fact already exists — out-of-order
            # ingestion. The incoming memory is historical on arrival: it must not
            # stay live alongside the newer value. Track the IMMEDIATE successor
            # (smallest event_time > new_et) so the incoming validity window
            # closes exactly where the next value takes over.
            #
            # Closure demands FULL metadata equivalence, not just structured-key
            # match: {ticker, metric} alone would let a Q2 figure close a
            # late-arriving Q1 *correction* (period is a discriminating key even
            # though it isn't structured). Forward supersession stays coarse —
            # its behavior is long-established — but closing an incoming fact is
            # only safe when nothing distinguishes the two.
            if _non_structured_meta(old_meta) == _non_structured_meta(new_meta):
                if newer_existing is None or _utc(mem.event_time) < _utc(newer_existing.event_time):
                    newer_existing = mem

    superseded_by_id = newer_existing.id if newer_existing is not None else None

    if superseded_ids:
        # A mixed batch still closes every old window (narrowed and re-confirmed
        # versions included); the stronger label wins the audit record.
        return SupersessionResult(
            relation="SUPERSEDES",
            confidence=1.0,
            superseded_ids=superseded_ids + refines_ids + confirms_close_ids,
            superseded_by_id=superseded_by_id,
        )
    if refines_ids:
        return SupersessionResult(
            relation="REFINES",
            confidence=0.8,
            superseded_ids=refines_ids + confirms_close_ids,
            superseded_by_id=superseded_by_id,
        )
    if conflict_ids:
        return SupersessionResult(
            relation="CONTRADICTS_SAME_TIME",
            confidence=0.9,
            conflict_ids=conflict_ids,
            superseded_by_id=superseded_by_id,
        )
    if confirms_close_ids:
        return SupersessionResult(
            relation="CONFIRMS",
            confidence=1.0,
            superseded_ids=confirms_close_ids,
            superseded_by_id=superseded_by_id,
        )
    if confirms_dupe_ids:
        return SupersessionResult(
            relation="CONFIRMS",
            confidence=1.0,
            superseded_by_id=superseded_by_id,
        )
    return SupersessionResult(relation="ADDS", confidence=1.0, superseded_by_id=superseded_by_id)


# ── Stage 1: candidate generation ────────────────────────────────────────────

async def find_supersession_candidates(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    new_meta: dict[str, Any],
    new_embedding: list[float],
    new_event_time: datetime,
    new_content: Optional[str] = None,
) -> list[Memory]:
    """Stage 1: find prior valid memories sharing structured keys + high cosine sim.

    Keyed facts require structured-key overlap. Unkeyed (free-text) facts fall
    back to pure embedding similarity — without this, free-text memories could
    never supersede or refine each other, because run_supersession only routes
    here when the new fact has no structured keys.
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

    new_structured = _norm_meta(new_meta or {})
    # A cued unkeyed revision qualifies for candidacy at the lower bar; the
    # final supersession decision stays top-1-only in run_supersession.
    unkeyed_threshold = (
        _CUE_SIM_THRESHOLD
        if not new_structured and _has_revision_cue(new_content)
        else _SIM_THRESHOLD
    )
    filtered = []
    for mem in candidates:
        old_meta = dict(mem.metadata_ or {})

        if new_structured:
            overlap = _metadata_overlap(old_meta, new_meta)
            if not overlap:
                continue
            if new_structured == _norm_meta(old_meta):
                filtered.append(mem)
                continue

        if mem.embedding is None:
            continue
        emb = mem.embedding if isinstance(mem.embedding, list) else list(mem.embedding)
        threshold = _SIM_THRESHOLD if new_structured else unkeyed_threshold
        if _cosine(emb, new_embedding) >= threshold:
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
    # Narrowing: the new fact restates the old and adds detail. Not a stale
    # value (SUPERSEDES) and not a disagreement (CONTRADICTS) — the Governor's
    # REFINE, now first-class here. Only when the new fact isn't older: an
    # earlier narrowing can't refine the current state.
    if temporal_order != "old_is_later" and _narrows(old_content, new_content):
        return "REFINES", 0.8
    # Unkeyed free-text guard: without a structured entity+attribute, two facts
    # that merely differ are DISTINCT statements, not a supersession. Otherwise
    # every successive chat message (all unkeyed) would supersede the prior one.
    # SUPERSEDES / CONTRADICTS require a structured key; identity (CONFIRMS) and
    # containment (REFINES) are handled above and are safe for free text.
    if not (_has_structured_key(old_meta) or _has_structured_key(new_meta)):
        return "ADDS", 0.6
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
            db, namespace, agent_id, new_meta, new_event_time, new_content_hash,
            new_content=new_content, subject_key=subject_key,
        )

    # Unkeyed path: Stage 1 + Stage 2
    candidates = await find_supersession_candidates(
        db, namespace, agent_id, new_meta, new_embedding, new_event_time,
        new_content=new_content,
    )
    if not candidates:
        return SupersessionResult(relation="ADDS", confidence=1.0)

    superseded_ids: list[UUID] = []
    conflict_ids: list[UUID] = []
    best_relation = "ADDS"
    best_confidence = 1.0
    best_rationale: Optional[str] = None
    # Unkeyed revision handling (see _REVISION_CUE_RE): a cued update supersedes
    # only its single most-similar candidate — real revisions target one fact,
    # and top-1 keeps a multi-fact utterance from mowing down its whole topic.
    cue_candidates: list[tuple[float, Any, str]] = []   # (sim, candidate, old_content)
    newer_closer: Optional[Any] = None                  # live newer revision → closes incoming
    new_et = _utc(new_event_time)

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

        if relation == "ADDS" and not (
            _has_structured_key(dict(candidate.metadata_ or {})) or _has_structured_key(new_meta)
        ):
            old_et = _utc(candidate.event_time)
            if old_et < new_et and _has_revision_cue(new_content) and old_content:
                emb = candidate.embedding if isinstance(candidate.embedding, list) \
                    else list(candidate.embedding or [])
                if emb:
                    cue_candidates.append((_cosine(emb, new_embedding), candidate, old_content))
            elif old_et > new_et and _has_revision_cue(old_content):
                # A live, later revision of this topic already exists: the
                # incoming (backdated) statement is historical on arrival.
                if newer_closer is None or old_et < _utc(newer_closer.event_time):
                    newer_closer = candidate

        rationale: Optional[str] = None
        if (
            relation in ("SUPERSEDES", "REFINES")
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
        elif relation == "REFINES":
            # Narrowing closes the old validity window exactly like SUPERSEDES;
            # only the audit label differs.
            superseded_ids.append(candidate.id)
            if best_relation != "SUPERSEDES":
                best_relation = "REFINES"
                best_confidence = confidence
                best_rationale = rationale
        elif relation == "CONTRADICTS_SAME_TIME" and best_relation not in ("SUPERSEDES", "REFINES"):
            conflict_ids.append(candidate.id)
            best_relation = "CONTRADICTS_SAME_TIME"
            best_confidence = confidence
        elif relation == "CONFIRMS" and best_relation not in ("SUPERSEDES", "REFINES", "CONTRADICTS_SAME_TIME"):
            best_relation = "CONFIRMS"
            best_confidence = confidence
            best_rationale = rationale

    # Resolve the cued unkeyed revision: supersede the single most-similar
    # candidate at moderate confidence (reviewable; Stage-3 eligible).
    if cue_candidates:
        _, chosen, chosen_old = max(cue_candidates, key=lambda t: t[0])
        if chosen.id not in superseded_ids:
            if settings.supersession_llm_stage:
                if settings.llm_adjudication_async and new_memory_id is not None:
                    try:
                        get_llm_queue().put_nowait((
                            namespace, agent_id,
                            chosen.id, new_memory_id,
                            chosen_old, new_content,
                            dict(chosen.metadata_ or {}),
                        ))
                    except asyncio.QueueFull:
                        logger.warning("LLM adjudication queue full — skipping async Stage 3")
                    superseded_ids.append(chosen.id)
                    if best_relation not in ("SUPERSEDES",):
                        best_relation, best_confidence = "SUPERSEDES", 0.7
                else:
                    relation, confidence, rationale = await llm_adjudicate(
                        old_content=chosen_old,
                        new_content=new_content,
                        meta=new_meta,
                    )
                    if relation in ("SUPERSEDES", "REFINES"):
                        superseded_ids.append(chosen.id)
                        best_relation, best_confidence, best_rationale = relation, confidence, rationale
            else:
                superseded_ids.append(chosen.id)
                if best_relation not in ("SUPERSEDES",):
                    best_relation, best_confidence = "SUPERSEDES", 0.7

    return SupersessionResult(
        relation=best_relation,
        confidence=best_confidence,
        superseded_ids=superseded_ids,
        conflict_ids=conflict_ids,
        superseded_by_id=newer_closer.id if newer_closer is not None else None,
        rationale=best_rationale,
    )
