"""
Stage 3 LLM adjudication tests.

All LLM calls are mocked â€” these tests verify caching behaviour, error
handling, correct integration with run_supersession, and the contract
that Stage 3 can override a Stage 2 SUPERSEDES verdict.
"""
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.lian.llm_adjudication import llm_adjudicate, _CACHE, _pair_key


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(relation: str, confidence: float, rationale: str):
    """Build a minimal mock Anthropic message object."""
    content_block = MagicMock()
    content_block.text = json.dumps(
        {"relation": relation, "confidence": confidence, "rationale": rationale}
    )
    msg = MagicMock()
    msg.content = [content_block]
    return msg


@pytest.fixture(autouse=True)
def clear_cache():
    """Each test runs against an empty adjudication cache."""
    _CACHE.clear()
    yield
    _CACHE.clear()


# ---------------------------------------------------------------------------
# llm_adjudicate unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paraphrase_returns_confirms():
    """LLM that returns CONFIRMS overrides the assumed SUPERSEDES verdict."""
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(
            return_value=_mock_response("CONFIRMS", 0.93, "Same $36B value, different wording")
        )

        relation, confidence, rationale = await llm_adjudicate(
            old_content="NVDA Q3 guidance $36B",
            new_content="Nvidia raised its Q3 outlook to thirty-six billion dollars",
            meta={"ticker": "NVDA", "metric": "guidance"},
        )

    assert relation == "CONFIRMS"
    assert confidence >= 0.9
    assert rationale != ""


@pytest.mark.asyncio
async def test_genuine_supersedes_confirmed_by_llm():
    """LLM confirms that a real value change is SUPERSEDES."""
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(
            return_value=_mock_response("SUPERSEDES", 0.95, "Value changed from $32B to $36B")
        )

        relation, confidence, rationale = await llm_adjudicate(
            old_content="NVDA Q3 guidance $32B",
            new_content="NVDA Q3 guidance raised to $36B",
            meta={"ticker": "NVDA", "metric": "guidance"},
        )

    assert relation == "SUPERSEDES"
    assert confidence >= 0.9


@pytest.mark.asyncio
async def test_cache_hit_calls_llm_only_once():
    """Identical pair â†’ second call returns cached result, LLM not called again."""
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(
            return_value=_mock_response("SUPERSEDES", 0.9, "Different value")
        )

        r1 = await llm_adjudicate("old guidance $32B", "new guidance $36B", {})
        r2 = await llm_adjudicate("old guidance $32B", "new guidance $36B", {})

    assert r1 == r2
    assert inst.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_different_pairs_each_call_llm():
    """Different content pairs are each adjudicated independently."""
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(
            return_value=_mock_response("SUPERSEDES", 0.9, "ok")
        )

        await llm_adjudicate("old A", "new A", {})
        await llm_adjudicate("old B", "new B", {})

    assert inst.messages.create.call_count == 2


@pytest.mark.asyncio
async def test_llm_api_error_falls_back_gracefully():
    """Network/API error returns a safe fallback â€” the write path must not break."""
    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(side_effect=RuntimeError("connection refused"))

        relation, confidence, rationale = await llm_adjudicate("old", "new", {})

    assert relation == "SUPERSEDES"
    assert confidence < 0.85          # lower than normal Stage 2 confidence
    assert "llm_error" in rationale


@pytest.mark.asyncio
async def test_llm_invalid_json_falls_back():
    """Malformed JSON from the LLM falls back without raising."""
    bad = MagicMock()
    bad.text = "sorry I cannot help with that"
    msg = MagicMock()
    msg.content = [bad]

    with patch("anthropic.AsyncAnthropic") as MockCls:
        inst = AsyncMock()
        MockCls.return_value = inst
        inst.messages.create = AsyncMock(return_value=msg)

        relation, confidence, rationale = await llm_adjudicate("old", "new", {})

    assert relation == "SUPERSEDES"
    assert "llm_error" in rationale


# ---------------------------------------------------------------------------
# Integration: Stage 3 inside run_supersession
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage3_disabled_by_default_llm_never_called(db):
    """supersession_llm_stage defaults to False â€” llm_adjudicate is never invoked."""
    from src.lian.supersession import run_supersession
    from src.lian.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    emb = await provider.embed_one("NVDA Q3 guidance raised to $36B")

    with patch("src.lian.supersession.llm_adjudicate") as mock_llm:
        await run_supersession(
            db=db,
            namespace="test-ns",
            agent_id="agent-1",
            new_content="NVDA Q3 guidance raised to $36B",
            new_meta={"ticker": "NVDA", "metric": "guidance"},
            new_embedding=emb,
            new_event_time=T1,
        )

    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_keyed_facts_bypass_llm_stage3(db, monkeypatch):
    """Change 3: keyed facts (full structured-key match) supersede deterministically
    by event_time. Stage 3 LLM is never called, even when supersession_llm_stage=True.
    """
    from src.lian.supersession import run_supersession
    from src.lian.embeddings import get_embedding_provider
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory
    from src.lian.config import get_settings

    await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance $36B",
        event_time=T0,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    ))

    monkeypatch.setenv("SUPERSESSION_LLM_STAGE", "true")
    get_settings.cache_clear()

    provider = get_embedding_provider()
    emb = await provider.embed_one("Nvidia raised Q3 outlook to thirty-six billion")

    with patch("src.lian.supersession.llm_adjudicate") as mock_llm:
        result = await run_supersession(
            db=db,
            namespace="test-ns",
            agent_id="agent-1",
            new_content="Nvidia raised Q3 outlook to thirty-six billion",
            new_meta={"ticker": "NVDA", "metric": "guidance"},
            new_embedding=emb,
            new_event_time=T1,  # newer â†’ deterministic SUPERSEDES
        )

    get_settings.cache_clear()

    mock_llm.assert_not_called()  # keyed fast path never invokes LLM
    assert result.relation == "SUPERSEDES"
    assert len(result.superseded_ids) == 1
    assert result.confidence == 1.0  # deterministic, not probabilistic
    assert result.rationale is None  # no LLM rationale for keyed path


@pytest.mark.asyncio
async def test_keyed_facts_same_time_flags_conflict(db, monkeypatch):
    """Keyed facts with equal event_time and different content â†’ CONTRADICTS_SAME_TIME.

    Neither memory is superseded (superseded_ids stays empty), but a conflict
    flag is raised so a human can decide which source to trust.  This is the
    correct behavior for same-time disagreement between data sources.
    """
    from src.lian.supersession import run_supersession
    from src.lian.embeddings import get_embedding_provider
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory

    mem = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance $36B revenue",
        event_time=T0,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    ))

    provider = get_embedding_provider()
    emb = await provider.embed_one("Nvidia Q3 outlook thirty-six billion")

    result = await run_supersession(
        db=db,
        namespace="test-ns",
        agent_id="agent-1",
        new_content="Nvidia Q3 outlook thirty-six billion",
        new_meta={"ticker": "NVDA", "metric": "guidance"},
        new_embedding=emb,
        new_event_time=T0,  # same timestamp, different value â†’ conflict
    )

    assert result.relation == "CONTRADICTS_SAME_TIME"
    assert len(result.superseded_ids) == 0      # neither memory overwritten
    assert len(result.conflict_ids) == 1        # conflict flag will be raised
    assert result.conflict_ids[0] == mem.id


@pytest.mark.asyncio
async def test_stage3_event_log_records_stage_number(db, monkeypatch):
    """Keyed supersession records adjudication_stage=2 (deterministic) in the event log."""
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory
    from src.lian.models import EventLog
    from src.lian.config import get_settings
    from sqlalchemy import select

    monkeypatch.setenv("SUPERSESSION_LLM_STAGE", "true")
    get_settings.cache_clear()

    await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="NVDA Q3 guidance $32B",
        event_time=T0,
        metadata={"ticker": "NVDA", "metric": "guidance"},
    ))

    # Keyed fast path: llm_adjudicate is NOT called regardless of the patch
    with patch("src.lian.supersession.llm_adjudicate", new=AsyncMock(
        return_value=("SUPERSEDES", 0.97, "Value changed from $32B to $36B")
    )):
        await add_memory(db, "test-ns", MemoryAdd(
            agent_id="agent-1",
            content="NVDA Q3 guidance raised to $36B",
            event_time=T1,
            metadata={"ticker": "NVDA", "metric": "guidance"},
        ))

    get_settings.cache_clear()

    result = await db.execute(
        select(EventLog).where(EventLog.op == "supersede")
    )
    log_row = result.scalar_one()
    payload = dict(log_row.payload)

    # Change 3: keyed path is deterministic â€” adjudication_stage=2, no LLM rationale
    assert payload["adjudication_stage"] == 2
    assert payload.get("rationale") is None
    assert payload["confidence"] == 1.0
