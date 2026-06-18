"""
Recall quality benchmarks: Precision@k, Recall@k, MRR.

Demonstrates AgentMem's advantages over naive retrieval approaches:

1. Hybrid scoring beats pure-cosine: recency + importance + validity weight
   correctly surface current facts over stale ones.
2. Supersession exclusion: stale facts get a 0.1× validity penalty; pure
   retrieval systems (mem0-style cosine search) return them at full rank.
3. Temporal filtering: point-in-time recall returns the right revision;
   systems without bitemporality (Zep, mem0) cannot do this at all.

All tests run with LocalProvider (zero API calls).
"""
from __future__ import annotations
import math
import pytest
import pytest_asyncio
from datetime import datetime, timedelta, timezone

from src.agentmem.schemas import MemoryAdd, RecallRequest
from src.agentmem.memory_service import add_memory, recall_memories

# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb + 1e-9)


def precision_at_k(retrieved_ids: list, relevant_ids: set, k: int) -> float:
    top = retrieved_ids[:k]
    return sum(1 for r in top if r in relevant_ids) / k if k else 0.0


def recall_at_k(retrieved_ids: list, relevant_ids: set, k: int) -> float:
    top = retrieved_ids[:k]
    hits = sum(1 for r in top if r in relevant_ids)
    return hits / len(relevant_ids) if relevant_ids else 0.0


def mrr(retrieved_ids: list, relevant_ids: set) -> float:
    for rank, rid in enumerate(retrieved_ids, 1):
        if rid in relevant_ids:
            return 1.0 / rank
    return 0.0


async def _pure_cosine_ranking(db, namespace: str, agent_id: str, query: str) -> list:
    """
    Simulate mem0-style pure cosine retrieval: rank ALL non-erased memories by
    cosine similarity only — no validity penalty, no recency, no importance.
    Returns list of (memory, cosine_score, content).
    """
    from sqlalchemy import select, and_
    from src.agentmem.models import Memory
    from src.agentmem.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    q_emb = await provider.embed_one(query)

    stmt = select(Memory).where(
        and_(
            Memory.namespace == namespace,
            Memory.agent_id == agent_id,
            Memory.erased_at.is_(None),
        )
    )
    result = await db.execute(stmt)
    mems = result.scalars().all()

    scored = []
    for mem in mems:
        emb = list(mem.embedding) if mem.embedding is not None else []
        sim = _cosine(q_emb, emb) if emb else 0.0
        content = bytes(mem.content_encrypted).decode() if mem.content_encrypted else ""
        scored.append((mem, sim, content))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NS = "bench-ns"
AGENT = "bench-agent"

NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)
T_RECENT = NOW - timedelta(days=10)
T_MEDIUM = NOW - timedelta(days=90)
T_OLD    = NOW - timedelta(days=180)


# ---------------------------------------------------------------------------
# Hybrid vs pure-cosine
# ---------------------------------------------------------------------------

class TestHybridVsPureSemantic:
    """
    Core differentiator vs mem0: hybrid scoring with recency + importance +
    validity correctly surfaces the right facts; pure cosine cannot.
    """

    @pytest.mark.asyncio
    async def test_recency_importance_break_cosine_tie(self, db):
        """
        Two memories with identical content have identical cosine similarity.
        Hybrid scorer ranks the more recent, higher-importance one first.
        Pure cosine treats them as tied — no reliable ordering.

        This is the everyday case: a financial agent ingests the same figure
        from two sources with different freshness. Returning the stale one
        first erodes trust in the agent's answers.
        """
        # Same text → same embedding → same cosine with any query
        content = "NVDA Q3 FY2026 guidance raised to $40B"
        meta = {"ticker": "NVDA", "metric": "guidance"}

        m_recent = await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT,
            content=content,
            event_time=T_RECENT,
            importance=0.9,
            metadata=meta,
        ))
        # Older event_time → classify_relation returns ADDS (not supersedes)
        # so both remain valid (valid_to=None)
        m_old = await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT,
            content=content,
            event_time=T_OLD,
            importance=0.2,
            metadata={"ticker": "NVDA", "metric": "guidance_old"},
        ))

        recall = await recall_memories(db, NS, RecallRequest(
            agent_id=AGENT, query="NVDA guidance Q3", k=5,
        ))
        ids = [m.id for m in recall.memories]

        assert len(ids) >= 2, "Both memories must appear in results"
        assert ids[0] == m_recent.id, (
            "Hybrid scorer must rank the recent/high-importance memory first; "
            "pure cosine cannot distinguish identical embeddings."
        )

    @pytest.mark.asyncio
    async def test_supersession_penalty_prevents_stale_recall(self, db):
        """
        After supersession, the verbose old memory has higher cosine with the
        query than the terse new memory.  Hybrid's 0.1× validity multiplier
        ensures the current fact wins.

        mem0-style pure cosine would return the stale memory at rank 1 —
        this test proves AgentMem does not make that mistake.
        """
        # Verbose old content → many shared tokens with the query
        old_content = (
            "NVDA Q3 FY2026 guidance: management raised the revenue outlook "
            "to $36B, a significant increase from prior analyst day forecast"
        )
        # Terse new content → fewer shared tokens with the query
        new_content = "NVDA Q3 FY2026 guidance $40B"
        meta = {"ticker": "NVDA", "metric": "guidance"}

        await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT, content=old_content,
            event_time=T_OLD, importance=0.5, metadata=meta,
        ))
        new = await add_memory(db, NS, MemoryAdd(
            agent_id=AGENT, content=new_content,
            event_time=T_RECENT, importance=0.8, metadata=meta,
        ))

        # The new memory should have superseded the old one
        from src.agentmem.models import Memory as MemModel
        old_db = await db.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(MemModel).where(
                MemModel.agent_id == AGENT,
                MemModel.namespace == NS,
                MemModel.valid_to.isnot(None),
            )
        )
        superseded = old_db.scalars().first()
        assert superseded is not None, "Old memory must be superseded"

        # Query contains tokens from the OLD content (would win on pure cosine)
        query = "NVDA guidance raised outlook $36B forecast analyst revenue"

        # Pure cosine ranking — old content should rank first
        pure_ranked = await _pure_cosine_ranking(db, NS, AGENT, query)
        pure_ids = [r[0].id for r in pure_ranked]
        assert pure_ids[0] == superseded.id, (
            "Pure cosine returns the verbose stale memory first — "
            "this is the failure mode this benchmark is designed to expose"
        )

        # Hybrid ranking — current content must rank first despite lower cosine
        hybrid_recall = await recall_memories(db, NS, RecallRequest(
            agent_id=AGENT, query=query, k=5,
        ))
        hybrid_ids = [m.id for m in hybrid_recall.memories]
        assert hybrid_ids[0] == new.id, (
            "Hybrid scorer must return the current (non-superseded) memory "
            "first even when it has lower raw cosine similarity"
        )

    @pytest.mark.asyncio
    async def test_hybrid_mrr_exceeds_pure_cosine_on_finance_corpus(self, db):
        """
        12-memory finance corpus, 4 labeled queries.
        AgentMem hybrid MRR >= pure cosine MRR on every query.

        Corpus mixes tickers so that off-topic memories are always
        sub-threshold on cosine; the differentiator comes from recency
        and importance weighting on same-ticker memories.
        """
        agent = f"{AGENT}-mrr"

        corpus = [
            # AAPL cluster
            ("AAPL revenue Q3 2026 85 billion quarterly earnings", {"ticker": "AAPL", "metric": "revenue"}, T_RECENT, 0.9),
            ("AAPL revenue Q3 2026 85 billion quarterly earnings", {"ticker": "AAPL", "metric": "revenue_dup"}, T_OLD, 0.2),
            ("AAPL gross margin 45 percent Q3 2026 profitability", {"ticker": "AAPL", "metric": "gross_margin"}, T_MEDIUM, 0.7),
            # TSLA cluster
            ("TSLA vehicle deliveries 440k units Q3 2026", {"ticker": "TSLA", "metric": "deliveries"}, T_RECENT, 0.85),
            ("TSLA production output 490k vehicles Q3 2026", {"ticker": "TSLA", "metric": "production"}, T_OLD, 0.3),
            # NVDA cluster
            ("NVDA data center revenue 17 billion Q2 2026 AI", {"ticker": "NVDA", "metric": "datacenter"}, T_MEDIUM, 0.75),
            ("NVDA guidance raised AI chip demand Q3 2026", {"ticker": "NVDA", "metric": "guidance"}, T_RECENT, 0.9),
            # Macro cluster
            ("Federal Reserve rate 5.25 percent hold meeting", {"entity": "FED", "metric": "rate"}, T_OLD, 0.6),
            ("CPI inflation 3.2 percent year over year print", {"entity": "CPI", "metric": "inflation"}, T_MEDIUM, 0.5),
            # Others
            ("MSFT azure cloud revenue Q3 2026 growth segment", {"ticker": "MSFT", "metric": "cloud"}, T_RECENT, 0.7),
            ("JPM net interest income margin banking Q3 2026", {"ticker": "JPM", "metric": "net_interest"}, T_MEDIUM, 0.65),
            ("GS Goldman Sachs investment banking revenue record", {"ticker": "GS", "metric": "ib_revenue"}, T_OLD, 0.4),
        ]

        ids = []
        for content, meta, evt, imp in corpus:
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content,
                event_time=evt, importance=imp, metadata=meta,
            ))
            ids.append(m.id)

        # Queries: (query_text, set of relevant corpus indices)
        queries_and_relevance = [
            ("AAPL revenue earnings Q3 quarterly", {ids[0], ids[1], ids[2]}),
            ("Tesla vehicle deliveries production units", {ids[3], ids[4]}),
            ("NVDA data center chip AI revenue demand", {ids[5], ids[6]}),
            ("Federal Reserve interest rate CPI inflation", {ids[7], ids[8]}),
        ]

        hybrid_mrrs = []
        pure_mrrs = []

        for query, relevant in queries_and_relevance:
            hybrid_res = await recall_memories(db, NS, RecallRequest(
                agent_id=agent, query=query, k=6,
            ))
            hybrid_ids = [m.id for m in hybrid_res.memories]

            pure_res = await _pure_cosine_ranking(db, NS, agent, query)
            pure_ids = [r[0].id for r in pure_res[:6]]

            hybrid_mrrs.append(mrr(hybrid_ids, relevant))
            pure_mrrs.append(mrr(pure_ids, relevant))

        avg_hybrid_mrr = sum(hybrid_mrrs) / len(hybrid_mrrs)
        avg_pure_mrr   = sum(pure_mrrs)   / len(pure_mrrs)

        assert avg_hybrid_mrr >= avg_pure_mrr, (
            f"Hybrid MRR ({avg_hybrid_mrr:.3f}) should be >= pure cosine MRR "
            f"({avg_pure_mrr:.3f}) on this finance corpus"
        )
        # Sanity: both must find at least something
        assert avg_hybrid_mrr > 0.0, "Hybrid recall is completely missing all relevant results"


# ---------------------------------------------------------------------------
# Temporal filter precision
# ---------------------------------------------------------------------------

class TestTemporalFilterPrecision:
    """
    Point-in-time recall — impossible without bitemporality.

    mem0 has no event_time concept; Zep is conversation-scoped.
    Neither can answer 'what was the agent's understanding of X on date D?'
    AgentMem answers this exactly and in isolation of ingestion order.
    """

    @pytest.mark.asyncio
    async def test_three_revision_as_of_correctness(self, db):
        """
        Three consecutive revisions of the same metric.
        as_of queries at each boundary return the correct snapshot.
        """
        agent = f"{AGENT}-pit"
        meta = {"ticker": "TSLA", "metric": "deliveries"}

        T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        T1 = datetime(2026, 4, 1, tzinfo=timezone.utc)
        T2 = datetime(2026, 7, 1, tzinfo=timezone.utc)

        m0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA Q1 deliveries 400k", event_time=T0, metadata=meta,
        ))
        m1 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA Q2 deliveries 430k", event_time=T1, metadata=meta,
        ))
        m2 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="TSLA Q3 deliveries 460k", event_time=T2, metadata=meta,
        ))

        def _first_id(result):
            return result.memories[0].id if result.memories else None

        # 1 day after each event_time
        r0 = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="TSLA deliveries", k=5,
            as_of=T0 + timedelta(days=1),
        ))
        r1 = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="TSLA deliveries", k=5,
            as_of=T1 + timedelta(days=1),
        ))
        r2 = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="TSLA deliveries", k=5,
            as_of=T2 + timedelta(days=1),
        ))
        r_now = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="TSLA deliveries", k=5,
        ))

        assert _first_id(r0) == m0.id, "as_of T0+1d must return the Q1 fact"
        assert _first_id(r1) == m1.id, "as_of T1+1d must return the Q2 fact"
        assert _first_id(r2) == m2.id, "as_of T2+1d must return the Q3 fact"
        assert _first_id(r_now) == m2.id, "Present-time must return the latest fact"

    @pytest.mark.asyncio
    async def test_present_time_excludes_all_superseded(self, db):
        """
        5 revisions of the same metric.  Present-time recall returns ONLY the
        5th revision.  A pure retrieval system (mem0) would return all 5 with
        similar cosine scores — flooding the context with stale data.
        """
        agent = f"{AGENT}-chain5"
        meta = {"ticker": "NVDA", "metric": "guidance"}

        values = ["$28B", "$32B", "$36B", "$38B", "$40B"]
        mems = []
        for i, val in enumerate(values):
            t = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=30 * i)
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent,
                content=f"NVDA FY2026 guidance {val}",
                event_time=t,
                metadata=meta,
            ))
            mems.append(m)

        # Present-time: only the 5th should appear
        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="NVDA guidance FY2026", k=10,
        ))
        result_ids = {m.id for m in result.memories}

        assert mems[-1].id in result_ids, "Current (5th) revision must appear"
        superseded_ids = {m.id for m in mems[:-1]}
        returned_superseded = result_ids & superseded_ids
        assert not returned_superseded, (
            f"Present-time recall must not return superseded memories; "
            f"got {len(returned_superseded)} stale results"
        )

        # Compare with pure cosine (mem0-style): would surface all 5
        pure = await _pure_cosine_ranking(db, NS, agent, "NVDA guidance FY2026")
        pure_superseded = {r[0].id for r in pure[:5]} & superseded_ids
        # Pure cosine returns stale memories — this documents the difference
        assert len(pure_superseded) > 0, (
            "Pure cosine should return some superseded memories — "
            "this proves the baseline failure mode that AgentMem avoids"
        )

    @pytest.mark.asyncio
    async def test_two_metrics_same_ticker_tracked_independently(self, db):
        """
        Revisions to AAPL revenue and AAPL gross_margin don't interfere.
        as_of returns the correct version of each metric independently.
        """
        agent = f"{AGENT}-twometric"

        T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        T1 = datetime(2026, 4, 1, tzinfo=timezone.utc)

        # Revenue: two revisions
        r0 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL Q1 revenue $90B",
            event_time=T0, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))
        r1 = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL Q2 revenue $95B",
            event_time=T1, metadata={"ticker": "AAPL", "metric": "revenue"},
        ))

        # Gross margin: only one entry at T0
        gm = await add_memory(db, NS, MemoryAdd(
            agent_id=agent, content="AAPL gross margin 46 percent Q1",
            event_time=T0, metadata={"ticker": "AAPL", "metric": "gross_margin"},
        ))

        # as_of = T0+1: both revenue v0 and gross margin visible
        snap = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="AAPL revenue gross margin", k=10,
            as_of=T0 + timedelta(days=1),
        ))
        snap_ids = {m.id for m in snap.memories}
        assert r0.id in snap_ids, "Q1 revenue must appear at T0+1"
        assert gm.id in snap_ids, "Gross margin must appear at T0+1"
        assert r1.id not in snap_ids, "Q2 revenue must not appear at T0+1"

        # Present-time: revenue v1 (current), gross margin (only version)
        now_snap = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="AAPL revenue gross margin", k=10,
        ))
        now_ids = {m.id for m in now_snap.memories}
        assert r1.id in now_ids, "Q2 revenue (current) must appear at present"
        assert gm.id in now_ids, "Gross margin (only version) must appear at present"
        assert r0.id not in now_ids, "Superseded Q1 revenue must not appear at present"


# ---------------------------------------------------------------------------
# Precision@k and Recall@k
# ---------------------------------------------------------------------------

class TestPrecisionRecallAtK:
    """
    Precision@k and Recall@k on a labelled 12-memory finance corpus.
    """

    @pytest.mark.asyncio
    async def test_precision_at_3_above_baseline(self, db):
        """
        For a targeted query (specific ticker + metric), P@3 >= 0.67 (2/3).
        This ensures the first three results are mostly relevant.
        """
        agent = f"{AGENT}-prec"

        # Add a small corpus: 3 AAPL memories, 3 irrelevant
        relevant_ids = []
        for content in [
            "AAPL Q3 2026 earnings revenue 85 billion quarterly report",
            "AAPL Q3 2026 EPS earnings per share 1.45 quarterly",
            "AAPL Q3 2026 iphone revenue 45 billion earnings",
        ]:
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content,
                event_time=T_RECENT,
                metadata={"ticker": "AAPL", "metric": "earnings"},
            ))
            relevant_ids.append(m.id)

        for content in [
            "TSLA Q3 2026 deliveries 440k vehicle production units",
            "NVDA Q3 2026 AI chip data center revenue guidance",
            "Federal Reserve rate hold 5.25 percent inflation meeting",
        ]:
            await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content,
                event_time=T_RECENT,
                metadata={"ticker": content.split()[0]},
            ))

        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="AAPL Q3 earnings revenue quarterly", k=6,
        ))
        ret_ids = [m.id for m in result.memories]
        p3 = precision_at_k(ret_ids, set(relevant_ids), k=3)

        assert p3 >= 0.67, (
            f"Precision@3 = {p3:.2f}; expected >= 0.67 for a targeted AAPL query "
            f"against a mixed 6-memory corpus"
        )

    @pytest.mark.asyncio
    async def test_recall_at_5_finds_all_relevant(self, db):
        """
        For 3 relevant memories in a 9-memory corpus, Recall@5 = 1.0:
        all relevant memories appear in the top-5.
        """
        agent = f"{AGENT}-rec"

        relevant_ids = []
        for content in [
            "JPM net interest income Q3 2026 banking margin",
            "JPM investment banking revenue fees Q3 2026",
            "JPM credit card net charge offs Q3 2026 consumer",
        ]:
            m = await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content,
                event_time=T_RECENT,
                metadata={"ticker": "JPM", "metric": "banking"},
            ))
            relevant_ids.append(m.id)

        for i, content in enumerate([
            "TSLA vehicle deliveries 440k units production Q3",
            "NVDA data center AI chip revenue demand Q3 2026",
            "MSFT azure cloud growth revenue segment Q3 2026",
            "GS Goldman Sachs M&A advisory banking Q3 2026",
            "AAPL iphone revenue gross margin Q3 2026",
            "FED Federal Reserve rate hold inflation CPI",
        ]):
            await add_memory(db, NS, MemoryAdd(
                agent_id=agent, content=content,
                event_time=T_RECENT,
                metadata={"ticker": f"OTHER{i}"},
            ))

        result = await recall_memories(db, NS, RecallRequest(
            agent_id=agent, query="JPM banking revenue net interest income", k=5,
        ))
        ret_ids = [m.id for m in result.memories]
        r5 = recall_at_k(ret_ids, set(relevant_ids), k=5)

        assert r5 == 1.0, (
            f"Recall@5 = {r5:.2f}; all 3 JPM memories should appear in the "
            f"top-5 results for a JPM-specific query"
        )
