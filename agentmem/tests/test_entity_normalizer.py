"""
Tests for financial entity normalization.

Covers: direct ticker lookup, company name aliases, ISIN resolution,
CUSIP resolution, case insensitivity, unknown fallback, and integration
with the keyed supersession router.
"""
import pytest
from datetime import datetime, timezone

from src.lian.entity_normalizer import normalize_ticker, normalize_entity_value, cached_normalize


# â”€â”€ Unit tests: normalize_ticker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@pytest.mark.parametrize("raw,expected", [
    # Exact ticker passthrough
    ("AAPL",  "AAPL"),
    ("MSFT",  "MSFT"),
    ("NVDA",  "NVDA"),
    ("SPY",   "SPY"),
    # Case-insensitive ticker
    ("aapl",  "AAPL"),
    ("msft",  "MSFT"),
    ("nvda",  "NVDA"),
    # Company name â†’ ticker
    ("Apple",             "AAPL"),
    ("Apple Inc.",        "AAPL"),
    ("apple inc",         "AAPL"),
    ("apple computer",    "AAPL"),
    ("Microsoft",         "MSFT"),
    ("Microsoft Corporation", "MSFT"),
    ("NVIDIA",            "NVDA"),
    ("nvidia corporation","NVDA"),
    ("Google",            "GOOGL"),
    ("Alphabet Inc",      "GOOGL"),
    ("Meta Platforms",    "META"),
    ("Facebook",          "META"),
    ("Amazon",            "AMZN"),
    ("amazon.com",        "AMZN"),
    ("Tesla",             "TSLA"),
    ("Tesla Motors",      "TSLA"),
    ("JPMorgan",          "JPM"),
    ("JPMorgan Chase",    "JPM"),
    ("J.P. Morgan",       "JPM"),
    ("Goldman Sachs",     "GS"),
    ("Goldman",           "GS"),
    ("Pfizer",            "PFE"),
    ("Eli Lilly",         "LLY"),
    ("Johnson & Johnson", "JNJ"),
    ("McDonald's",        "MCD"),
    ("Home Depot",        "HD"),
    ("Walmart",           "WMT"),
    ("Coca-Cola",         "KO"),
    ("Coke",              "KO"),
    ("Bitcoin",           "BTCUSD"),
    ("BTC",               "BTCUSD"),
    ("Gold",              "XAUUSD"),
    ("crude oil",         "WTIUSD"),
    ("S&P 500",           "SPX"),
    ("Dow Jones",         "DJI"),
    ("DJIA",              "DJI"),
    ("fed funds rate",    "FEDFUNDS"),
    ("Federal Funds Rate","FEDFUNDS"),
])
def test_normalize_ticker_names(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("isin,expected", [
    ("US0378331005", "AAPL"),   # Apple
    ("US5949181045", "MSFT"),   # Microsoft
    ("US67066G1040", "NVDA"),   # Nvidia
    ("US30303M1027", "META"),   # Meta
    ("US88160R1014", "TSLA"),   # Tesla
    ("US46625H1005", "JPM"),    # JPMorgan
    ("US4781601046", "JNJ"),    # J&J
    ("US7170811035", "PFE"),    # Pfizer
    ("US02079K3059", "GOOGL"),  # Alphabet Class A
    ("US0231351067", "AMZN"),   # Amazon
    # Case insensitive
    ("us0378331005", "AAPL"),
])
def test_normalize_ticker_isin(isin, expected):
    assert normalize_ticker(isin) == expected


@pytest.mark.parametrize("cusip,expected", [
    ("037833100", "AAPL"),   # Apple
    ("594918104", "MSFT"),   # Microsoft
    ("67066G104", "NVDA"),   # Nvidia
    ("46625H100", "JPM"),    # JPMorgan
    ("717081103", "PFE"),    # Pfizer
    # 8-char CUSIP (no check digit)
    ("03783310",  "AAPL"),
    ("59491810",  "MSFT"),
])
def test_normalize_ticker_cusip(cusip, expected):
    assert normalize_ticker(cusip) == expected


def test_normalize_ticker_unknown_returns_uppercase():
    assert normalize_ticker("XYZ_UNKNOWN_CORP") == "XYZ_UNKNOWN_CORP"
    assert normalize_ticker("foobar") == "FOOBAR"


def test_normalize_ticker_strips_whitespace():
    assert normalize_ticker("  AAPL  ") == "AAPL"
    assert normalize_ticker(" Apple Inc. ") == "AAPL"


def test_normalize_entity_value_ticker_key():
    assert normalize_entity_value("ticker", "Apple") == "AAPL"
    assert normalize_entity_value("ticker", "US0378331005") == "AAPL"


def test_normalize_entity_value_entity_key():
    assert normalize_entity_value("entity", "Microsoft Corporation") == "MSFT"


def test_normalize_entity_value_isin_key():
    assert normalize_entity_value("isin", "US67066G1040") == "NVDA"


def test_normalize_entity_value_cusip_key():
    assert normalize_entity_value("cusip", "037833100") == "AAPL"


def test_normalize_entity_value_other_key_passthrough():
    """Non-entity keys are returned unchanged (just stripped)."""
    assert normalize_entity_value("metric", "eps") == "eps"
    assert normalize_entity_value("field", "revenue") == "revenue"
    assert normalize_entity_value("metric", "  EPS  ") == "EPS"


def test_cached_normalize_is_consistent():
    assert cached_normalize("ticker", "Apple") == "AAPL"
    assert cached_normalize("ticker", "US0378331005") == "AAPL"
    assert cached_normalize("ticker", "037833100") == "AAPL"


# â”€â”€ Integration: cross-identifier supersession â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_name_alias_triggers_supersession(db):
    """Memory stored with 'ticker=Apple' is superseded by 'ticker=AAPL' (newer)."""
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory

    old = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="Apple Q3 EPS $1.40",
        event_time=T0,
        metadata={"ticker": "Apple", "metric": "eps"},
    ))

    new = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="AAPL Q3 EPS revised to $1.45",
        event_time=T1,
        metadata={"ticker": "AAPL", "metric": "eps"},
    ))

    # Reload old â€” should be invalidated
    from src.lian.models import Memory
    from sqlalchemy import select
    row = (await db.execute(select(Memory).where(Memory.id == old.id))).scalar_one()
    assert row.valid_to is not None, "Old memory with 'Apple' ticker must be superseded by 'AAPL' memory"
    assert str(row.superseded_by) == str(new.id)


@pytest.mark.asyncio
async def test_isin_alias_triggers_supersession(db):
    """Memory stored with ISIN is superseded by matching ticker (newer)."""
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory
    from src.lian.models import Memory
    from sqlalchemy import select

    old = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="NVDA revenue $26B",
        event_time=T0,
        metadata={"ticker": "US67066G1040", "metric": "revenue"},  # ISIN
    ))

    new = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="Nvidia revenue guidance raised to $28B",
        event_time=T1,
        metadata={"ticker": "NVDA", "metric": "revenue"},  # canonical ticker
    ))

    row = (await db.execute(select(Memory).where(Memory.id == old.id))).scalar_one()
    assert row.valid_to is not None, "Memory with ISIN ticker must be superseded by canonical ticker memory"
    assert str(row.superseded_by) == str(new.id)


@pytest.mark.asyncio
async def test_cusip_alias_triggers_supersession(db):
    """Memory stored with CUSIP is superseded by canonical ticker."""
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory
    from src.lian.models import Memory
    from sqlalchemy import select

    old = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="MSFT price target $400",
        event_time=T0,
        metadata={"ticker": "594918104", "metric": "price_target"},  # CUSIP
    ))

    new = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="Microsoft price target raised to $450",
        event_time=T1,
        metadata={"ticker": "Microsoft", "metric": "price_target"},  # name alias
    ))

    row = (await db.execute(select(Memory).where(Memory.id == old.id))).scalar_one()
    assert row.valid_to is not None, "CUSIP memory must be superseded by name-alias memory for same entity"
    assert str(row.superseded_by) == str(new.id)


@pytest.mark.asyncio
async def test_different_entities_no_supersession(db):
    """Memories for different companies are never cross-superseded."""
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory
    from src.lian.models import Memory
    from sqlalchemy import select

    aapl = await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="Apple EPS $1.40",
        event_time=T0,
        metadata={"ticker": "AAPL", "metric": "eps"},
    ))

    # Storing MSFT memory â€” different entity, same metric, later time
    await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="Microsoft EPS $2.80",
        event_time=T1,
        metadata={"ticker": "MSFT", "metric": "eps"},
    ))

    row = (await db.execute(select(Memory).where(Memory.id == aapl.id))).scalar_one()
    assert row.valid_to is None, "AAPL memory must NOT be superseded by MSFT memory"


@pytest.mark.asyncio
async def test_same_time_isin_vs_ticker_flags_conflict(db):
    """Same entity (ISIN vs ticker), same event_time, different value â†’ conflict flag."""
    from src.lian.schemas import MemoryAdd
    from src.lian.memory_service import add_memory
    from src.lian.models import ConflictFlag
    from sqlalchemy import select

    await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="Apple Q1 EPS $1.52 (Bloomberg)",
        event_time=T0,
        metadata={"ticker": "US0378331005", "metric": "eps"},  # ISIN
    ))

    await add_memory(db, "test-ns", MemoryAdd(
        agent_id="agent-1",
        content="AAPL Q1 EPS $1.48 (Refinitiv)",
        event_time=T0,  # same time, different value
        metadata={"ticker": "AAPL", "metric": "eps"},
    ))

    flags = (await db.execute(select(ConflictFlag).where(ConflictFlag.namespace == "test-ns"))).scalars().all()
    assert len(flags) == 1, "Same entity at same time with different values must raise a conflict flag"
    assert flags[0].status == "open"
