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
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Memory, LiveFact
from .crypto import decrypt_content

_ANN_PREFETCH_MULTIPLIER = 20

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
    embs: list[Optional[list[float]]] = [
        list(r[0].embedding) if getattr(r[0], "embedding", None) is not None else None
        for r in results
    ]
    remaining = list(range(len(results)))
    order: list[int] = []
    while remaining:
        best_i: Optional[int] = None
        best_val: Optional[float] = None
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
        order.append(best_i)  # type: ignore[arg-type]
        remaining.remove(best_i)
    return [results[i] for i in order]


_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_AVG_DOC_LEN = 50.0

# Word runs (unicode-aware, so Cyrillic/Greek/Arabic/Devanagari words tokenize
# as words, and punctuation never glues onto a token the way naive
# str.split() left it: "revenue." must match a query for "revenue").
_BM25_WORD = re.compile(r"\w+", re.UNICODE)
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
    for word in _BM25_WORD.findall(text.lower()):
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
        if last < len(word):
            tokens.append(_light_stem(word[last:]))
    return tokens


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
    content = _decrypt(row, subject_keys)
    emb = list(row.embedding) if row.embedding is not None else None
    sem = _cosine(query_embedding, emb) if emb else 0.0
    lex = _bm25_score(query, content or "") if content else 0.0
    rec = _recency_decay(row.event_time, _materiality_half_life(row.metadata_),
                         anchor=decay_anchor)
    return sem, lex, W_REC * rec + W_IMP * row.importance, content


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
        cutoff = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
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

        parts = [
            _score_components(fact, query, query_embedding, subject_keys)
            for fact in facts
        ]
        sems = _smoothed_sems(facts, [p[0] for p in parts])
        bonuses = _temporal_bonus(facts, query_time_windows(query))
        ent_bonuses = _entity_bonus([p[3] for p in parts], query_entities(query))
        # Always return Memory objects for API consistency — fetch the canonical
        # Memory rows so callers can use .id, .valid_to, .erased_at, etc.
        # Batched: a per-fact db.get() here was one SQL round trip per live
        # fact per recall, dominating recall latency on local SQLite.
        mem_by_id: dict[Any, Memory] = {}
        fact_ids = [fact.memory_id for fact in facts]
        for i in range(0, len(fact_ids), 500):
            chunk = fact_ids[i:i + 500]
            rows = await db.execute(select(Memory).where(Memory.id.in_(chunk)))
            for m in rows.scalars():
                mem_by_id[m.id] = m
        scored = []
        now = datetime.now(timezone.utc)
        for fact, sem, bonus, ent, (_, lex, rest, content) in zip(
                facts, sems, bonuses, ent_bonuses, parts):
            mem = mem_by_id.get(fact.memory_id)
            if mem is not None:
                scored.append((
                    mem,
                    W_SEM * sem + W_LEX * lex + rest + bonus + ent
                    - _stale_clause_penalty(mem.metadata_, now),
                    content,
                ))

    scored.sort(key=lambda x: x[1], reverse=True)
    return _mmr_select(_collapse_derived(scored), k)


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
    remaining = scored[1:]
    while remaining and len(selected) < k:
        best, best_val = None, -math.inf
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
        selected.append(best)
        remaining.remove(best)
    return selected
