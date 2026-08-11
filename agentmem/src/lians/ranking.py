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

import asyncio
import logging
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory, LiveFact
from .crypto import decrypt_content
from .scoring import (
    _bounded_scoring_text,
    rank_position_score,
    record_ranking_stage,
    score_memory,
    stable_score_key,
    tokenize_for_scoring,
)

_ANN_PREFETCH_MULTIPLIER = 20
MAX_RECALL_SCORING_CANDIDATES = 400
logger = logging.getLogger("agentmem.ranking")

W_SEM = 0.50
# BM25 is unbounded while cosine lives in [-1, 1]; at 0.20 a single strong
# term overlap out-shouted real semantic matches (measured -3pts evidence
# retrieval on LOCOMO). 0.05 keeps lexical as a tie-breaker, not a driver.
W_LEX = 0.05
W_REC = 0.15
W_IMP = 0.15

# Maximal-marginal-relevance selection over the candidate pool. λ trades
# relevance against novelty: 1.0 = pure relevance (MMR off, the default —
# measured a wash-to-negative on evidence retrieval: diversity evicts the
# second gold turn of multi-fact questions more often than it rescues one),
# 0.0 = pure diversity. Deterministic either way.
MMR_LAMBDA = float(os.getenv("RECALL_MMR_LAMBDA", "1.0"))

# Optional cross-encoder second stage: rerank the blend's top candidates with
# a local reranker model before cutting to k. Probe-measured +8pts hit@10 on
# LOCOMO conv_0 (86.7 vs 78.7 blend-only). Opt-in because it costs real CPU
# (~50-150ms/pair without a GPU): set RECALL_RERANKER_MODEL to enable, e.g.
# "BAAI/bge-reranker-v2-m3". Deterministic for a fixed model.
RERANKER_MODEL = os.getenv("RECALL_RERANKER_MODEL", "")
RERANKER_PREFETCH = int(os.getenv("RECALL_RERANKER_PREFETCH", "30"))
RERANKER_TIMEOUT_MS = float(os.getenv("RECALL_RERANKER_TIMEOUT_MS", "300"))
RERANKER_MAX_CONCURRENCY = max(
    1, min(8, int(os.getenv("RECALL_RERANKER_MAX_CONCURRENCY", "1")))
)

_reranker = None
_reranker_slots = asyncio.Semaphore(RERANKER_MAX_CONCURRENCY)


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        _reranker = CrossEncoder(RERANKER_MODEL, max_length=384)
    return _reranker


def _stable_entry_key(entry: tuple[Any, float, Optional[str]]) -> tuple[Any, ...]:
    memory, score, _content = entry
    breakdown = getattr(memory, "_score_breakdown", None)
    if not isinstance(breakdown, dict):
        breakdown = {"final_score": score}
    return stable_score_key(
        getattr(memory, "id", ""),
        getattr(memory, "event_time", None),
        getattr(memory, "ingestion_time", None),
        breakdown,
    )


def apply_ordered_ranking_stage(
    ordered: list[tuple[Any, float, Optional[str]]],
    *,
    stage: str,
    details: Optional[list[dict[str, Any]]] = None,
    reason: str,
) -> list[tuple[Any, float, Optional[str]]]:
    """Assign explainable, strictly descending public scores to a new order."""
    total = len(ordered)
    if total == 0:
        return ordered
    explained: list[tuple[Any, float, Optional[str]]] = []
    detail_rows = details or [{} for _ in ordered]
    if len(detail_rows) != total:
        raise ValueError("ranking-stage details must match the ordered candidates")
    for position, ((memory, input_score, content), stage_details) in enumerate(
        zip(ordered, detail_rows)
    ):
        output_score = (
            input_score
            if total == 1
            else rank_position_score(position, total, input_score)
        )
        output_score = record_ranking_stage(
            memory,
            stage=stage,
            input_score=input_score,
            output_score=output_score,
            details={
                "method": "rank-calibration-v1",
                "position": position + 1,
                "candidate_count": total,
                **dict(stage_details),
            },
            reason=reason,
        )
        explained.append((memory, output_score, content))
    return explained


def _cross_encoder_plan(
    query: str,
    scored: list[tuple[Any, float, Optional[str]]],
    k: int,
) -> tuple[
    list[tuple[Any, float, Optional[str]]],
    list[dict[str, Any]],
]:
    """Compute an immutable rerank plan; never mutate public score evidence."""
    window = scored[:max(RERANKER_PREFETCH, k)]
    rest = scored[len(window):]
    ce = _get_reranker()
    pairs = [
        (_bounded_scoring_text(query), _bounded_scoring_text(content or ""))
        for _, _, content in window
    ]
    ce_scores = ce.predict(pairs, show_progress_bar=False)
    raw_scores = [float(value) for value in ce_scores]
    if len(raw_scores) != len(window) or any(
        not math.isfinite(value) for value in raw_scores
    ):
        raise ValueError("cross-encoder returned invalid scores")
    order = sorted(
        range(len(window)),
        key=lambda i: (-raw_scores[i], _stable_entry_key(window[i])),
    )
    ordered = ([window[i] for i in order] + rest)[:k]
    detail_rows = [{
        "model": RERANKER_MODEL,
        "evaluated": True,
        "raw_model_score": round(raw_scores[i], 6),
    } for i in order]
    detail_rows.extend({
        "model": RERANKER_MODEL,
        "evaluated": False,
    } for _entry in rest)
    return ordered, detail_rows[:len(ordered)]


def rerank_cross_encoder(
    query: str,
    scored: list[tuple[Any, float, Optional[str]]],
    k: int,
) -> list[tuple[Any, float, Optional[str]]]:
    """Synchronously rerank and apply evidence only after model completion."""
    if not RERANKER_MODEL or len(scored) <= 1:
        return scored[:k]
    try:
        ordered, detail_rows = _cross_encoder_plan(query, scored, k)
        return apply_ordered_ranking_stage(
            ordered,
            stage="cross-encoder",
            details=detail_rows,
            reason="cross-encoder relevance reranking applied",
        )
    except Exception:
        return scored[:k]


async def rerank_cross_encoder_async(
    query: str,
    scored: list[tuple[Any, float, Optional[str]]],
    k: int,
) -> list[tuple[Any, float, Optional[str]]]:
    """Run CPU/GPU reranking without blocking the FastAPI event loop.

    The timeout is a latency guard, not a correctness dependency. A timeout or
    model failure returns the deterministic hybrid order.
    """
    if not RERANKER_MODEL or len(scored) <= 1:
        return scored[:k]
    timeout = max(0.001, RERANKER_TIMEOUT_MS / 1000.0)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        await asyncio.wait_for(_reranker_slots.acquire(), timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        return scored[:k]

    remaining = max(0.001, deadline - loop.time())
    task = asyncio.create_task(
        asyncio.to_thread(_cross_encoder_plan, query, scored, k)
    )
    release_on_completion = False
    try:
        ordered, detail_rows = await asyncio.wait_for(
            asyncio.shield(task), timeout=remaining,
        )
    except asyncio.CancelledError:
        release_on_completion = True
        raise
    except Exception:
        release_on_completion = not task.done()
        return scored[:k]
    finally:
        if release_on_completion:
            task.add_done_callback(lambda _task: _reranker_slots.release())
        else:
            _reranker_slots.release()

    # Only the event-loop thread mutates Memory._score_breakdown, and only
    # after the model completed within the advertised deadline.
    return apply_ordered_ranking_stage(
        ordered,
        stage="cross-encoder",
        details=detail_rows,
        reason="cross-encoder relevance reranking applied",
    )

# Temporal-context smoothing: a memory inherits a fraction of its strongest
# temporally-adjacent neighbor's semantic match. Dialogue and event streams
# split one fact across neighboring entries (a question and its answer, a
# statement and its reply); the low-vocabulary half then never matches the
# query on its own. Only neighbors within CONTEXT_SMOOTHING_MAX_GAP_S count,
# so smoothing never bleeds across sessions or unrelated days. 0 disables.
CONTEXT_SMOOTHING = float(os.getenv("RECALL_CONTEXT_SMOOTHING", "0.3"))
CONTEXT_SMOOTHING_MAX_GAP_S = float(os.getenv("RECALL_CONTEXT_SMOOTHING_MAX_GAP_S", "3600"))

# Entity matching: a third retrieval signal alongside semantic and lexical —
# proper nouns and quoted spans in the query anchor memories that mention
# them. Deterministic (regex extraction, word-boundary matching), no model.
#
# DEFAULT OFF: measured on LOCOMO raw dialogue it is a small net negative
# (79.0% hit@10 without vs 78.6-78.8% with, even document-frequency-gated) —
# speaker-prefixed turns make names indiscriminate. It exists for fact-shaped
# corpora (distilled facts, tickets, CRM rows) where entities are sparse and
# discriminative; enable via RECALL_ENTITY_MATCH_BONUS there.
ENTITY_MATCH_BONUS = float(os.getenv("RECALL_ENTITY_MATCH_BONUS", "0.0"))
# Entities present in more than this fraction of the candidate pool are not
# discriminative and never boost (a speaker's own name matches half the pool).
ENTITY_MAX_DF = float(os.getenv("RECALL_ENTITY_MAX_DF", "0.1"))

_QUOTED_SPAN = re.compile(r"[\"'“‘]([^\"'”’]{2,60})[\"'”’]")
# Runs of adjacent capitalized words merge into one entity ("San Francisco").
_CAPITALIZED_RUN = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b")
_ENTITY_STOPWORDS = frozenset(
    "the this that what when where which who whom whose why how did does was "
    "were are is has have had will would could should monday tuesday wednesday "
    "thursday friday saturday sunday january february march april may june july "
    "august september october november december".split()
)


def query_entities(query: str) -> list[str]:
    """Extract quoted spans and mid-sentence capitalized-word runs from a
    query; capitalized words already inside a quoted span are not repeated."""
    ents: list[str] = []
    quoted_ranges: list[tuple[int, int]] = []
    for m in _QUOTED_SPAN.finditer(query):
        ents.append(m.group(1).strip())
        quoted_ranges.append((m.start(), m.end()))
    for m in _CAPITALIZED_RUN.finditer(query):
        if any(lo <= m.start() < hi for lo, hi in quoted_ranges):
            continue
        run = m.group(1)
        if run.lower() not in _ENTITY_STOPWORDS:
            ents.append(run)
    seen: set[str] = set()
    out = []
    for e in ents:
        key = e.lower()
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _entity_bonus(contents: list[Optional[str]], entities: list[str]) -> list[float]:
    """Per-row bonus: ENTITY_MATCH_BONUS × fraction of (discriminative) query
    entities present, word-boundary and case-insensitive. Entities appearing
    in more than ENTITY_MAX_DF of the pool are dropped before scoring."""
    if not entities or ENTITY_MATCH_BONUS <= 0 or not contents:
        return [0.0] * len(contents)
    pats = []
    for e in entities:
        p = re.compile(rf"\b{re.escape(e)}\b", re.IGNORECASE)
        df = sum(1 for c in contents if c and p.search(c)) / len(contents)
        if df <= ENTITY_MAX_DF:
            pats.append(p)
    if not pats:
        return [0.0] * len(contents)
    out = []
    for c in contents:
        if not c:
            out.append(0.0)
            continue
        hits = sum(1 for p in pats if p.search(c))
        out.append(ENTITY_MATCH_BONUS * hits / len(pats))
    return out


# Temporal query grounding: when the query itself names a calendar date or
# month ("what did she mention on 3 June, 2023?"), memories whose event_time
# falls inside that window get an additive bonus. Embeddings are nearly blind
# to dates, so without this a date-pinned query ranks purely on topic and
# retrieves the wrong instance. Deterministic — regex date parse, no model.
TEMPORAL_GROUNDING_BONUS = float(os.getenv("RECALL_TEMPORAL_GROUNDING_BONUS", "0.1"))

# Stale-clause demotion: a turn whose extracted interjection clause was later
# superseded still contains the stale text (interjection.py stores clauses as
# derived memories; supersession closes the clause, not the multi-fact parent).
# Each closure is timestamped on the parent (metadata._stale_clauses); parents
# are demoted per closure already effective at query time, so as_of recall
# before the revision is untouched. Additive on the blended score, capped at 2.
STALE_CLAUSE_PENALTY = float(os.getenv("RECALL_STALE_CLAUSE_PENALTY", "0.15"))

_MONTHS = ("january february march april may june july august september "
           "october november december").split()
_MONTH_RE = "|".join(_MONTHS)
# "3 June, 2023" / "June 3, 2023" / "3rd of June 2023"
_DAY_DATE = re.compile(
    rf"\b(?:(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+({_MONTH_RE})|({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?)"
    rf"\s*,?\s+(\d{{4}})\b", re.IGNORECASE)
# "June 2023" (month precision; only used when no day-precision date matched)
_MONTH_DATE = re.compile(rf"\b({_MONTH_RE})\s*,?\s+(\d{{4}})\b", re.IGNORECASE)

_DAY_WINDOW_S = 2 * 86400      # day-precision: date ±2 days
_MONTH_WINDOW_S = 86400        # month-precision: month edges padded a day


def query_time_windows(query: str) -> list[tuple[float, float]]:
    """Extract (start_ts, end_ts) windows for explicit dates in the query."""
    windows: list[tuple[float, float]] = []
    for m in _DAY_DATE.finditer(query):
        day = int(m.group(1) or m.group(4))
        month = _MONTHS.index((m.group(2) or m.group(3)).lower()) + 1
        year = int(m.group(5))
        try:
            t = datetime(year, month, day, tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        windows.append((t - _DAY_WINDOW_S, t + 2 * 86400 + _DAY_WINDOW_S))
    if windows:
        return windows
    for m in _MONTH_DATE.finditer(query):
        month = _MONTHS.index(m.group(1).lower()) + 1
        year = int(m.group(2))
        start = datetime(year, month, 1, tzinfo=timezone.utc).timestamp()
        end_month = datetime(year + (month == 12), month % 12 + 1, 1,
                             tzinfo=timezone.utc).timestamp()
        windows.append((start - _MONTH_WINDOW_S, end_month + _MONTH_WINDOW_S))
    return windows


def _temporal_bonus(rows: list[Any], windows: list[tuple[float, float]]) -> list[float]:
    if not windows or TEMPORAL_GROUNDING_BONUS <= 0:
        return [0.0] * len(rows)
    out = []
    for row in rows:
        t = _event_ts(row)
        out.append(TEMPORAL_GROUNDING_BONUS
                   if any(lo <= t <= hi for lo, hi in windows) else 0.0)
    return out

RECENCY_HALF_LIFE_DAYS = 30.0

# Materiality-weighted decay: a fact's retrieval half-life scales with its
# stated materiality, so a client instruction or compliance flag stays
# retrievable long after a passing preference has faded. This is a *ranking*
# policy only — storage is never decayed; facts persist until superseded or
# provably erased. The tag is deterministic caller/adapter metadata
# (``metadata.materiality``), never model-inferred at recall time, so the same
# query over the same corpus always ranks the same way.
MATERIALITY_HALF_LIFE_DAYS: dict[str, float] = {
    "low": 7.0,
    "standard": RECENCY_HALF_LIFE_DAYS,
    "high": 120.0,
    "critical": 365.0,
}


def _materiality_half_life(metadata: Optional[dict]) -> float:
    tag = (metadata or {}).get("materiality")
    if isinstance(tag, str):
        return MATERIALITY_HALF_LIFE_DAYS.get(tag.strip().lower(), RECENCY_HALF_LIFE_DAYS)
    return RECENCY_HALF_LIFE_DAYS


try:
    import numpy as _np
except ImportError:  # pragma: no cover - numpy ships with the embedding stack
    _np = None


def _cosine(a: list[float], b: list[float]) -> float:
    # Vectorized: this runs once per candidate row on every recall (and per
    # selected pair in MMR), so the pure-Python loop dominated recall latency
    # on local SQLite (~2ms per 1024-dim pair; ~30x faster under numpy).
    #
    # Length guard: degraded recall (embedding provider down) passes an empty
    # query vector — the python zip loop silently returned 0.0 there, but
    # numpy matmul raises on the shape mismatch and crashed the very outage
    # path that exists to keep recall alive.
    if len(a) != len(b) or not a:
        return 0.0
    if _np is not None:
        va = _np.asarray(a, dtype=_np.float32)
        vb = _np.asarray(b, dtype=_np.float32)
        denom = float(_np.linalg.norm(va)) * float(_np.linalg.norm(vb)) + 1e-9
        return float(va @ vb) / denom
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


def mmr_rerank(
    results: list[tuple[Any, float, Optional[str]]],
    lambda_: float = 0.5,
) -> list[tuple[Any, float, Optional[str]]]:
    """
    Maximal Marginal Relevance reorder of recall candidates.

    Greedily picks the item maximizing ``λ·relevance − (1−λ)·max_similarity_to_
    already_selected``, where relevance is the existing fusion score and
    similarity is cosine over the candidates' embeddings. This keeps the top-k
    from being dominated by near-duplicate restatements of the same fact —
    higher diversity at a small relevance cost. ``λ=1`` is pure relevance,
    ``λ=0`` is pure diversity.

    Items are reordered, never dropped; items without an embedding contribute
    zero similarity (treated as maximally diverse).
    """
    lambda_ = min(1.0, max(0.0, lambda_))
    if lambda_ >= 1.0 or len(results) <= 1:
        return results

    def _emb_or_none(row: Any) -> Optional[list[float]]:
        # Memory rows arrive with the embedding column deferred (recall
        # hydration skips it); touching it here would lazy-load outside the
        # async session. Treat unloaded as no-embedding (maximally diverse).
        try:
            e = getattr(row, "embedding", None)
        except Exception:
            return None
        return list(e) if e is not None else None

    embs: list[Optional[list[float]]] = [_emb_or_none(r[0]) for r in results]
    remaining = list(range(len(results)))
    order: list[int] = []
    selection: list[dict[str, Any]] = []
    while remaining:
        best_i: Optional[int] = None
        best_val: Optional[float] = None
        best_rel = 0.0
        best_sim = 0.0
        for i in remaining:
            rel = results[i][1]
            if order and embs[i] is not None:
                sim = max(
                    (_cosine(embs[i], embs[j]) for j in order if embs[j] is not None),
                    default=0.0,
                )
            else:
                sim = 0.0
            val = lambda_ * rel - (1.0 - lambda_) * sim
            if best_val is None or val > best_val:
                best_val, best_i = val, i
                best_rel, best_sim = float(rel), float(sim)
        order.append(best_i)  # type: ignore[arg-type]
        remaining.remove(best_i)
        selection.append({
            "lambda": round(lambda_, 6),
            "selection_objective": round(float(best_val or 0.0), 6),
            "input_relevance": round(best_rel, 6),
            "max_selected_similarity": round(best_sim, 6),
        })
    return apply_ordered_ranking_stage(
        [results[i] for i in order],
        stage="mmr-rerank",
        details=selection,
        reason="maximal marginal relevance diversity reranking applied",
    )


_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_AVG_DOC_LEN = 50.0

# Word runs (unicode-aware, so Cyrillic/Greek/Arabic/Devanagari words tokenize
# as words, and punctuation never glues onto a token the way naive
# str.split() left it: "revenue." must match a query for "revenue").
_BM25_WORD = re.compile(r"\w+", re.UNICODE)
_MAX_BM25_TOKENS = 1_024
# Scripts written without spaces between words (Han, Hiragana, Katakana,
# Hangul, Thai, Lao, Myanmar, Khmer). A whitespace tokenizer sees a whole
# sentence as one "word" there, so no query can ever match; index character
# bigrams instead — the standard dependency-free segmentation fallback.
_BM25_UNSEG_SPAN = re.compile(
    "["
    "฀-๿"  # Thai
    "຀-໿"  # Lao
    "က-႟"  # Myanmar
    "ក-៿"  # Khmer
    "぀-ヿ"  # Hiragana, Katakana
    "㐀-䶿"  # CJK Extension A
    "一-鿿"  # CJK Unified Ideographs
    "가-힯"  # Hangul syllables
    "豈-﫿"  # CJK Compatibility Ideographs
    "]+"
)


def _light_stem(token: str) -> str:
    """Suffix-strip English inflections so query and content conjugations
    meet at one form ("attended"/"attending"/"attends" → "attend"). Rule-based
    and deliberately conservative — short tokens and non-Latin scripts pass
    through untouched, so the CJK bigram path is unaffected."""
    if len(token) <= 4 or not token.isascii():
        return token
    for suffix in ("ing", "ies", "ied", "ed", "es", "s"):
        if token.endswith(suffix):
            stem = token[: -len(suffix)]
            if len(stem) >= 3:
                if suffix in ("ies", "ied"):
                    return stem + "y"
                # doubled final consonant from gerunds: "planning" → "plan"
                if suffix in ("ing", "ed") and len(stem) >= 4 and stem[-1] == stem[-2]:
                    return stem[:-1]
                return stem
    return token


def _bm25_tokens(text: str) -> list[str]:
    """Shared query/content tokenizer for the lexical half of hybrid recall."""
    tokens: list[str] = []
    bounded = _bounded_scoring_text(text).casefold()
    for word_match in _BM25_WORD.finditer(bounded):
        word = word_match.group(0)
        last = 0
        for m in _BM25_UNSEG_SPAN.finditer(word):
            if m.start() > last:
                tokens.append(_light_stem(word[last:m.start()]))
            span = m.group(0)
            if len(span) == 1:
                tokens.append(span)
            else:
                tokens.extend(span[i:i + 2] for i in range(len(span) - 1))
            last = m.end()
            if len(tokens) >= _MAX_BM25_TOKENS:
                return tokens[:_MAX_BM25_TOKENS]
        if last < len(word):
            tokens.append(_light_stem(word[last:]))
        if len(tokens) >= _MAX_BM25_TOKENS:
            break
    return tokens[:_MAX_BM25_TOKENS]


def _bm25_score(query: str, content: str) -> float:
    q_tokens = set(_bm25_tokens(query))
    c_words = _bm25_tokens(content)
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


def _recency_decay(
    event_time: datetime,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    anchor: Optional[datetime] = None,
) -> float:
    """``anchor`` re-bases the decay clock for point-in-time recall: under
    ``as_of``, "recent" means recent *relative to the pinned moment*, not to
    wall-clock now — otherwise every as_of query sees uniformly stale scores
    and the recency term deadens."""
    now = anchor or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    age_days = (now - event_time).total_seconds() / 86400
    return math.exp(-math.log(2) * age_days / half_life_days)


# ── Change 1: present-time recall uses live_facts ────────────────────────────

async def _fetch_live_candidates(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    barrier_group: Optional[str],
    filters: Optional[dict],
    query_embedding: list[float],
    k: int,
    reference_time: datetime,
) -> list[LiveFact]:
    """Fetch from live_facts — the compact present-time projection."""
    conditions = [
        LiveFact.namespace == namespace,
        LiveFact.agent_id == agent_id,
        LiveFact.event_time <= reference_time,
    ]
    # Change 4: barrier filter is structural — only the agent's partition is scanned
    if barrier_group is not None:
        conditions.append(
            or_(LiveFact.barrier_group == barrier_group, LiveFact.barrier_group.is_(None))
        )
    if filters:
        for key, val in filters.items():
            if key == "_allowed_scope_paths":
                conditions.append(LiveFact.metadata_["_scope_path"].as_string().in_(list(val)))
            else:
                conditions.append(LiveFact.metadata_[key].as_string() == str(val))

    base_stmt = select(LiveFact).where(and_(*conditions))

    if query_embedding:
        try:
            pre_k = min(
                max(k * _ANN_PREFETCH_MULTIPLIER, 100),
                MAX_RECALL_SCORING_CANDIDATES,
            )
            vec_lit = "[" + ",".join(f"{x:.8f}" for x in query_embedding) + "]"
            ann_stmt = (
                base_stmt
                .order_by(text(f"embedding <=> '{vec_lit}'::vector"))
                .limit(pre_k)
            )
            result = await db.execute(ann_stmt)
            return list(result.scalars().all())
        except Exception:
            logger.debug(
                "Live-fact ANN query failed; falling back to deterministic scan",
                exc_info=True,
            )

    result = await db.execute(
        base_stmt.order_by(
            LiveFact.importance.desc(),
            LiveFact.event_time.desc(),
            LiveFact.id.asc(),
        ).limit(min(
            max(k * _ANN_PREFETCH_MULTIPLIER, 100),
            MAX_RECALL_SCORING_CANDIDATES,
        ))
    )
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
            if key == "_allowed_scope_paths":
                conditions.append(Memory.metadata_["_scope_path"].as_string().in_(list(val)))
            else:
                conditions.append(Memory.metadata_[key].as_string() == str(val))

    base_stmt = select(Memory).where(and_(*conditions))

    if query_embedding:
        try:
            pre_k = min(
                max(k * _ANN_PREFETCH_MULTIPLIER, 100),
                MAX_RECALL_SCORING_CANDIDATES,
            )
            vec_lit = "[" + ",".join(f"{x:.8f}" for x in query_embedding) + "]"
            ann_stmt = (
                base_stmt
                .order_by(text(f"embedding <=> '{vec_lit}'::vector"))
                .limit(pre_k)
            )
            result = await db.execute(ann_stmt)
            return list(result.scalars().all())
        except Exception:
            logger.debug(
                "Historical ANN query failed; falling back to deterministic scan",
                exc_info=True,
            )

    result = await db.execute(
        base_stmt.order_by(
            Memory.importance.desc(),
            Memory.event_time.desc(),
            Memory.id.asc(),
        ).limit(min(
            max(k * _ANN_PREFETCH_MULTIPLIER, 100),
            MAX_RECALL_SCORING_CANDIDATES,
        ))
    )
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


def _score_components(
    row: Any,
    query: str,
    query_embedding: list[float],
    subject_keys: dict[str, bytes],
    decay_anchor: Optional[datetime] = None,
) -> tuple[float, float, float, Optional[str]]:
    """(sem, lex, rec_imp, content) for a LiveFact or Memory row; the caller
    applies context smoothing to sem before blending. ``decay_anchor`` pins
    the recency clock for as_of recall."""
    decrypted = _decrypt(row, subject_keys)
    content = _bounded_scoring_text(decrypted) if decrypted else decrypted
    emb = list(row.embedding) if row.embedding is not None else None
    sem = _cosine(query_embedding, emb) if emb else 0.0
    lex = _bm25_score(query, content or "") if content else 0.0
    rec = _recency_decay(row.event_time, _materiality_half_life(row.metadata_),
                         anchor=decay_anchor)
    return sem, lex, W_REC * rec + W_IMP * row.importance, content


class _ScoringPack:
    """Precomputed per-agent scoring artifacts (Change 7 extension).

    Everything about the pool that does not depend on the query — decrypted
    plaintext samples, an embedding matrix with row norms, BM25 term frequencies,
    event timestamps, materiality half-lives, stale-clause marks, temporal
    neighbor indices — computed once per working set and reused until the
    next write invalidates it. Recall scoring then reduces to one matrix
    product plus vectorized arithmetic: measured 380ms -> ~15ms per recall
    at ~700 memories.
    """

    __slots__ = ("rows", "contents", "emb", "emb_norm", "doc_tf", "doc_len",
                 "ts", "half_life", "importance", "stale_ts",
                 "prev_idx", "next_idx", "fingerprint")

    def __init__(self, facts: list, subject_keys: dict[str, bytes]):
        n = len(facts)
        self.rows = list(facts)
        self.contents = [
            _bounded_scoring_text(content) if content else content
            for content in (_decrypt(f, subject_keys) for f in facts)
        ]
        dim = None
        embs = []
        for f in facts:
            if f.embedding is not None:
                e = _np.asarray(list(f.embedding), dtype=_np.float32)
                dim = dim or len(e)
            else:
                e = None
            embs.append(e)
        dim = dim or 1
        self.emb = _np.zeros((n, dim), dtype=_np.float32)
        for i, e in enumerate(embs):
            if e is not None and len(e) == dim:
                self.emb[i] = e
        self.emb_norm = _np.linalg.norm(self.emb, axis=1)
        self.doc_tf, doc_len = [], []
        for c in self.contents:
            toks = _bm25_tokens(c) if c else []
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            self.doc_tf.append(tf)
            doc_len.append(len(toks))
        self.doc_len = _np.asarray(doc_len, dtype=_np.float64)
        self.ts = _np.asarray([_event_ts(f) for f in facts], dtype=_np.float64)
        self.half_life = _np.asarray(
            [_materiality_half_life(f.metadata_) for f in facts], dtype=_np.float64)
        self.importance = _np.asarray([f.importance for f in facts], dtype=_np.float64)
        stale = []
        for f in facts:
            marks = []
            for ts_raw in (dict(f.metadata_ or {}).get("_stale_clauses") or []):
                try:
                    t = datetime.fromisoformat(str(ts_raw))
                except (TypeError, ValueError):
                    continue
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                marks.append(t.timestamp())
            stale.append(_np.asarray(sorted(marks)))
        self.stale_ts = stale
        # temporal neighbors (previous/next by event_time within the gap)
        order = _np.lexsort((_np.arange(n), self.ts))
        self.prev_idx = _np.full(n, -1, dtype=_np.int64)
        self.next_idx = _np.full(n, -1, dtype=_np.int64)
        for pos, i in enumerate(order):
            if pos > 0:
                j = order[pos - 1]
                if abs(self.ts[i] - self.ts[j]) <= CONTEXT_SMOOTHING_MAX_GAP_S:
                    self.prev_idx[i] = j
            if pos + 1 < n:
                j = order[pos + 1]
                if abs(self.ts[i] - self.ts[j]) <= CONTEXT_SMOOTHING_MAX_GAP_S:
                    self.next_idx[i] = j
        self.fingerprint = (n, str(getattr(facts[-1], "id", "")) if n else "")


def _pack_fingerprint(facts: list) -> tuple:
    return (len(facts), str(getattr(facts[-1], "id", "")) if facts else "")


def _score_with_pack(
    pack: "_ScoringPack",
    query: str,
    query_embedding: list[float],
) -> list[float]:
    """Vectorized replica of the per-row live scoring (same blend, same
    smoothing, same bonuses/penalties, anchored to wall-clock now)."""
    n = len(pack.rows)
    q = _np.asarray(query_embedding, dtype=_np.float32) if query_embedding else None
    if q is not None and pack.emb.shape[1] == len(q):
        sem = (pack.emb @ q) / (pack.emb_norm * float(_np.linalg.norm(q)) + 1e-9)
    else:
        sem = _np.zeros(n, dtype=_np.float32)
    if CONTEXT_SMOOTHING > 0 and n >= 2:
        prev_s = _np.where(pack.prev_idx >= 0, sem[pack.prev_idx], 0.0)
        next_s = _np.where(pack.next_idx >= 0, sem[pack.next_idx], 0.0)
        nb = _np.maximum(_np.maximum(prev_s, next_s), 0.0)
        sem = sem + CONTEXT_SMOOTHING * nb

    q_tokens = set(_bm25_tokens(query))
    lex = _np.zeros(n, dtype=_np.float64)
    if q_tokens:
        for i, (tf, dl) in enumerate(zip(pack.doc_tf, pack.doc_len)):
            if not dl:
                continue
            denom_len = _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / _BM25_AVG_DOC_LEN)
            s = 0.0
            for t in q_tokens:
                f = tf.get(t, 0)
                if f:
                    s += (f * (_BM25_K1 + 1)) / (f + denom_len)
            lex[i] = s / len(q_tokens)

    now = datetime.now(timezone.utc)
    now_ts = now.timestamp()
    age_days = (now_ts - pack.ts) / 86400.0
    rec = _np.exp(-_np.log(2.0) * age_days / pack.half_life)

    scores = W_SEM * sem + W_LEX * lex + W_REC * rec + W_IMP * pack.importance

    windows = query_time_windows(query)
    if windows and TEMPORAL_GROUNDING_BONUS > 0:
        in_window = _np.zeros(n, dtype=bool)
        for lo, hi in windows:
            in_window |= (pack.ts >= lo) & (pack.ts <= hi)
        scores = scores + TEMPORAL_GROUNDING_BONUS * in_window

    ents = query_entities(query)
    if ents and ENTITY_MATCH_BONUS > 0:
        scores = scores + _np.asarray(_entity_bonus(pack.contents, ents))

    for i, marks in enumerate(pack.stale_ts):
        if len(marks):
            n_stale = int(_np.searchsorted(marks, now_ts, side="right"))
            if n_stale:
                scores[i] -= STALE_CLAUSE_PENALTY * min(n_stale, 2)
    return scores.tolist()


def _stale_clause_penalty(meta: Optional[dict], cutoff: datetime) -> float:
    """Demotion for a parent turn whose derived clause(s) closed by *cutoff*."""
    marks = (meta or {}).get("_stale_clauses") or []
    if not marks:
        return 0.0
    n = 0
    for ts in marks:
        try:
            t = datetime.fromisoformat(str(ts))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if t <= cutoff:
            n += 1
    return STALE_CLAUSE_PENALTY * min(n, 2)


def _collapse_derived(
    scored: list[tuple[Any, float, Optional[str]]],
) -> list[tuple[Any, float, Optional[str]]]:
    """Drop a derived clause when its parent turn is already selected — the
    clause is a substring of the parent, so it adds nothing to the result set.
    The parent is never dropped in favor of a clause: a clause is a lossy
    fragment of a multi-fact turn, and evicting the parent can evict the very
    fact the query wanted. ``scored`` must be sorted by score descending.
    No-op when interjection extraction never ran."""
    kept: list[tuple[Any, float, Optional[str]]] = []
    kept_ids: set[str] = set()
    for entry in scored:
        row = entry[0]
        parent = (dict(getattr(row, "metadata_", None) or {})).get("_parent")
        if parent and str(parent) in kept_ids:
            continue
        kept.append(entry)
        row_id = str(getattr(row, "id", "") or "")
        if row_id:
            kept_ids.add(row_id)
    return kept


def _event_ts(row: Any) -> float:
    t = row.event_time
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def _smoothed_sems(rows: list[Any], sems: list[float]) -> list[float]:
    """Temporal-context smoothing over the candidate pool.

    Each row inherits ``CONTEXT_SMOOTHING`` × the best semantic score among
    its immediate temporal neighbors (previous/next by event_time, within
    ``CONTEXT_SMOOTHING_MAX_GAP_S``). Deterministic, pool-local, no extra IO.
    """
    if CONTEXT_SMOOTHING <= 0 or len(rows) < 2:
        return sems
    order = sorted(range(len(rows)), key=lambda i: (_event_ts(rows[i]), i))
    out = list(sems)
    for pos, i in enumerate(order):
        best = 0.0
        for nb_pos in (pos - 1, pos + 1):
            if 0 <= nb_pos < len(order):
                j = order[nb_pos]
                if abs(_event_ts(rows[i]) - _event_ts(rows[j])) <= CONTEXT_SMOOTHING_MAX_GAP_S:
                    if sems[j] > best:
                        best = sems[j]
        out[i] = sems[i] + CONTEXT_SMOOTHING * best
    return out


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
    apply_reranker: bool = True,
    reference_time: Optional[datetime] = None,
    use_scoring_pack: bool = False,
) -> list[tuple[Any, float, Optional[str]]]:
    """Return list of (row, score, decrypted_content).

    present-time (no as_of): queries ``live_facts`` — compact, fast, no
    temporal predicates.  ``live_facts_override`` allows the session cache
    (Change 7) to supply pre-fetched rows without a DB round-trip.

    point-in-time (as_of set): queries ``memories`` with the full temporal
    filter — as_of recall always hits the bitemporal log.
    """
    subject_keys = subject_keys or {}
    reference = as_of or reference_time or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)

    if as_of is not None:
        # Point-in-time: must go to the bitemporal log
        candidates = await _fetch_historical_candidates(
            db, namespace, agent_id, barrier_group, filters, query_embedding, k, as_of
        )
        cutoff = reference
        parts = [
            _score_components(mem, query, query_embedding, subject_keys,
                              decay_anchor=cutoff)
            for mem in candidates
        ]
        sems = _smoothed_sems(candidates, [p[0] for p in parts])
        bonuses = _temporal_bonus(candidates, query_time_windows(query))
        ent_bonuses = _entity_bonus([p[3] for p in parts], query_entities(query))
        scored: list[tuple[Memory, float, Optional[str]]] = [
            (mem,
             W_SEM * sem + W_LEX * lex + rest + bonus + ent
             - _stale_clause_penalty(mem.metadata_, cutoff),
             content)
            for mem, sem, bonus, ent, (_, lex, rest, content)
            in zip(candidates, sems, bonuses, ent_bonuses, parts)
        ]
    else:
        # Present-time: use live_facts (Change 1)
        if live_facts_override is not None:
            facts = [
                fact for fact in live_facts_override
                if (
                    fact.event_time
                    if fact.event_time.tzinfo
                    else fact.event_time.replace(tzinfo=timezone.utc)
                ) <= reference
            ][:MAX_RECALL_SCORING_CANDIDATES]
            if barrier_group is not None:
                facts = [f for f in facts if f.barrier_group is None or f.barrier_group == barrier_group]
            if filters:
                facts = [
                    f for f in facts
                    if all(
                        (
                            (dict(f.metadata_ or {})).get("_scope_path") in val
                            if key == "_allowed_scope_paths"
                            else (dict(f.metadata_ or {})).get(key) == str(val)
                        )
                        for key, val in filters.items()
                    )
                ]
        else:
            facts = await _fetch_live_candidates(
                db, namespace, agent_id, barrier_group, filters, query_embedding, k,
                reference,
            )

        # Vectorized fast path (Change 7 extension): when the pool is the
        # agent's whole working set, score against the cached _ScoringPack —
        # one matrix product instead of per-row python.
        pack = None
        if (
            use_scoring_pack
            and _np is not None
            and facts
            and not filters
            and barrier_group is None
        ):
            from .session_cache import get_scoring_pack, set_scoring_pack
            pack = get_scoring_pack(namespace, agent_id)
            if pack is None or pack.fingerprint != _pack_fingerprint(facts):
                pack = _ScoringPack(facts, subject_keys)
                set_scoring_pack(namespace, agent_id, pack)

        if pack is not None:
            pack_scores = _score_with_pack(pack, query, query_embedding)
            contents = pack.contents
        else:
            parts = [
                _score_components(fact, query, query_embedding, subject_keys)
                for fact in facts
            ]
            sems = _smoothed_sems(facts, [p[0] for p in parts])
            bonuses = _temporal_bonus(facts, query_time_windows(query))
            ent_bonuses = _entity_bonus([p[3] for p in parts], query_entities(query))
            pack_scores = [
                W_SEM * sem + W_LEX * lex + rest + bonus + ent
                - _stale_clause_penalty(fact.metadata_, reference)
                for fact, sem, bonus, ent, (_, lex, rest, _c)
                in zip(facts, sems, bonuses, ent_bonuses, parts)
            ]
            contents = [p[3] for p in parts]

        # Always return Memory objects for API consistency — fetch the canonical
        # Memory rows so callers can use .id, .valid_to, .erased_at, etc.
        # Batched (one IN-query per 500), the embedding column deferred (the
        # recall response never carries it, and decoding 685 JSON vectors was
        # 330ms of every warm recall). Canonical rows are request-local: a
        # scoring pack must never retain full encrypted payloads indefinitely.
        from sqlalchemy.orm import defer
        mem_by_id = {}
        fact_ids = [fact.memory_id for fact in facts]
        for i in range(0, len(fact_ids), 500):
            chunk = fact_ids[i:i + 500]
            rows = await db.execute(
                select(Memory).options(defer(Memory.embedding))
                .where(Memory.id.in_(chunk)))
            for m in rows.scalars():
                mem_by_id[m.id] = m
        scored = []
        for fact, score, content in zip(facts, pack_scores, contents):
            mem = mem_by_id.get(fact.memory_id)
            if mem is not None:
                scored.append((mem, score, content))

    # Convert the existing retrieval blend into bounded, explainable quality
    # components. Candidate generation and bitemporal filtering remain
    # unchanged; safety is a hard gate before normal recall.
    explained = []
    scoring_query_tokens = tokenize_for_scoring(query)
    for mem, base_score, content in scored:
        metadata = dict(mem.metadata_ or {})
        admission = metadata.get("_admission") if isinstance(metadata.get("_admission"), dict) else {}
        risk_tags = list(admission.get("risk_tags") or [])
        action = str(admission.get("action") or "safe")
        safety_status = "review_needed" if action in {"review", "held_for_review", "pending"} else action
        breakdown = score_memory(
            content=content or "",
            reference_time=reference,
            metadata=metadata,
            importance=mem.importance,
            source=mem.source,
            event_time=mem.event_time,
            valid_from=mem.valid_from,
            valid_to=mem.valid_to,
            superseded=bool(mem.superseded_by),
            query=query,
            query_tokens=scoring_query_tokens,
            base_relevance=base_score,
            safety_status=safety_status,
            risk_tags=risk_tags,
            purpose="recall",
        )
        quality_score = breakdown["final_score"]
        ranking_score = 0.8 * max(0.0, min(1.0, float(base_score))) + 0.2 * quality_score
        breakdown["quality_score"] = quality_score
        breakdown["final_score"] = round(ranking_score, 6)
        breakdown["ranking_weights"] = {
            "existing_retrieval_score": 0.8,
            "memory_quality_score": 0.2,
        }
        breakdown["reasons"].append(
            "recall rank preserves proven hybrid retrieval with a bounded quality adjustment"
        )
        mem._score_breakdown = breakdown
        if breakdown["eligible"]:
            explained.append((mem, breakdown["final_score"], content))
    scored = sorted(
        explained,
        key=lambda item: stable_score_key(
            item[0].id,
            item[0].event_time,
            item[0].ingestion_time,
            item[0]._score_breakdown,
        ),
    )
    scored = _collapse_derived(scored)
    if RERANKER_MODEL and apply_reranker:
        selected = await rerank_cross_encoder_async(query, scored, k)
    else:
        selected = _mmr_select(scored, k)
    # Candidate scoring and optional reranking use deterministic bounded text
    # samples. Rehydrate only the final public rows so API content remains
    # lossless while candidate work and cached plaintext remain bounded.
    return [
        (memory, score, _decrypt(memory, subject_keys))
        for memory, score, _sample in selected
    ]


def _mmr_select(
    scored: list[tuple[Any, float, Optional[str]]],
    k: int,
    lam: float = MMR_LAMBDA,
) -> list[tuple[Any, float, Optional[str]]]:
    """Select k results by maximal marginal relevance.

    ``scored`` must be sorted by blended score descending. Blended scores are
    min-max normalized within the candidate pool so the relevance and novelty
    terms are commensurate; novelty is 1 - max cosine similarity to any
    already-selected row. Rows without embeddings incur no similarity penalty
    (they cannot crowd anything out semantically).
    """
    if lam >= 1.0 or len(scored) <= k:
        return scored[:k]

    lo = scored[-1][1]
    span = scored[0][1] - lo or 1.0
    embs = {
        id(entry): list(entry[0].embedding)
        for entry in scored
        if getattr(entry[0], "embedding", None) is not None
    }

    selected: list[tuple[Any, float, Optional[str]]] = [scored[0]]
    selection: list[dict[str, Any]] = [{
        "lambda": round(lam, 6),
        "selection_objective": round(lam, 6),
        "normalized_relevance": 1.0,
        "max_selected_similarity": 0.0,
    }]
    remaining = scored[1:]
    while remaining and len(selected) < k:
        best, best_val = None, -math.inf
        best_rel = 0.0
        best_sim = 0.0
        for entry in remaining:
            rel = (entry[1] - lo) / span
            emb = embs.get(id(entry))
            max_sim = 0.0
            if emb is not None:
                for chosen in selected:
                    c_emb = embs.get(id(chosen))
                    if c_emb is not None:
                        sim = _cosine(emb, c_emb)
                        if sim > max_sim:
                            max_sim = sim
            val = lam * rel - (1.0 - lam) * max_sim
            if val > best_val:
                best, best_val = entry, val
                best_rel, best_sim = rel, max_sim
        selected.append(best)
        remaining.remove(best)
        selection.append({
            "lambda": round(lam, 6),
            "selection_objective": round(best_val, 6),
            "normalized_relevance": round(best_rel, 6),
            "max_selected_similarity": round(best_sim, 6),
        })
    return apply_ordered_ranking_stage(
        selected,
        stage="mmr-selection",
        details=selection,
        reason="configured maximal marginal relevance selection applied",
    )
