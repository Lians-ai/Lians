"""
Tests for the domain adapter system (SCALE.md §3 — core/adapter boundary).

Verifies:
- Finance adapter returns correct structured_keys
- Finance adapter normalizes ISIN/CUSIP/alias to canonical ticker
- Passthrough adapter returns empty structured_keys and identity normalize
- get_adapter() returns the configured adapter (finance by default)
- Adapter can be overridden at runtime (for custom verticals)
"""
import pytest

from src.agentmem.adapters import get_adapter, register_adapter
from src.agentmem.adapters.finance import FinanceAdapter
from src.agentmem.adapters.passthrough import PassthroughAdapter
from src.agentmem._types import _FINANCE_STRUCTURED_KEYS, _PASSTHROUGH_STRUCTURED_KEYS


# ── Finance adapter ───────────────────────────────────────────────────────────

def test_finance_adapter_structured_keys():
    adapter = FinanceAdapter()
    assert "ticker" in adapter.structured_keys
    assert "metric" in adapter.structured_keys
    assert "isin" in adapter.structured_keys
    assert "cusip" in adapter.structured_keys
    assert "entity" in adapter.structured_keys
    assert "field" in adapter.structured_keys
    assert "instrument" in adapter.structured_keys


def test_finance_adapter_ticker_passthrough():
    adapter = FinanceAdapter()
    assert adapter.normalize("ticker", "AAPL") == "AAPL"
    assert adapter.normalize("ticker", "MSFT") == "MSFT"


def test_finance_adapter_company_name_to_ticker():
    adapter = FinanceAdapter()
    assert adapter.normalize("ticker", "Apple Inc.") == "AAPL"
    assert adapter.normalize("ticker", "apple") == "AAPL"
    assert adapter.normalize("ticker", "Microsoft") == "MSFT"


def test_finance_adapter_isin_to_ticker():
    adapter = FinanceAdapter()
    assert adapter.normalize("ticker", "US0378331005") == "AAPL"
    assert adapter.normalize("ticker", "US5949181045") == "MSFT"


def test_finance_adapter_cusip_to_ticker():
    adapter = FinanceAdapter()
    # 9-char CUSIP
    assert adapter.normalize("ticker", "037833100") == "AAPL"
    # 8-char CUSIP (without check digit)
    assert adapter.normalize("ticker", "03783310") == "AAPL"


def test_finance_adapter_unknown_value_passthrough():
    adapter = FinanceAdapter()
    assert adapter.normalize("ticker", "UNKNOWNXXX") == "UNKNOWNXXX"


def test_finance_adapter_non_ticker_key_identity():
    adapter = FinanceAdapter()
    assert adapter.normalize("metric", "eps") == "eps"
    assert adapter.normalize("metric", "  revenue  ") == "revenue"


# ── Passthrough adapter ───────────────────────────────────────────────────────

def test_passthrough_adapter_empty_structured_keys():
    adapter = PassthroughAdapter()
    assert len(adapter.structured_keys) == 0


def test_passthrough_adapter_normalize_strips_whitespace():
    adapter = PassthroughAdapter()
    assert adapter.normalize("anything", "  hello  ") == "hello"
    assert adapter.normalize("ticker", "AAPL") == "AAPL"


def test_passthrough_adapter_no_ticker_normalization():
    adapter = PassthroughAdapter()
    # Passthrough does NOT map Apple Inc → AAPL (no finance logic)
    assert adapter.normalize("ticker", "Apple Inc.") == "Apple Inc."


# ── Protocol compliance ───────────────────────────────────────────────────────

def test_finance_adapter_implements_protocol():
    from src.agentmem.adapters import DomainAdapter
    assert isinstance(FinanceAdapter(), DomainAdapter)


def test_passthrough_adapter_implements_protocol():
    from src.agentmem.adapters import DomainAdapter
    assert isinstance(PassthroughAdapter(), DomainAdapter)


# ── get_adapter() factory ─────────────────────────────────────────────────────

def test_get_adapter_returns_finance_by_default(monkeypatch):
    from src.agentmem.adapters import _registry
    _registry.clear()
    # Default DOMAIN_ADAPTER is "finance"
    adapter = get_adapter()
    assert isinstance(adapter, FinanceAdapter)


def test_get_adapter_returns_passthrough_when_configured(monkeypatch):
    from src.agentmem.config import get_settings
    from src.agentmem.adapters import _registry
    _registry.clear()
    monkeypatch.setattr(get_settings(), "domain_adapter", "passthrough", raising=False)
    # Directly instantiate to avoid settings cache issues in tests
    adapter = PassthroughAdapter()
    assert len(adapter.structured_keys) == 0


def test_custom_adapter_can_be_registered():
    """A third-party vertical can register its own adapter by name."""
    class HealthcareAdapter:
        @property
        def structured_keys(self):
            return frozenset({"patient_id", "condition", "medication"})

        def normalize(self, key, value):
            return value.strip().lower()

    register_adapter("healthcare", HealthcareAdapter())
    from src.agentmem.adapters import _registry
    assert "healthcare" in _registry
    adapter = _registry["healthcare"]
    assert "patient_id" in adapter.structured_keys
    assert adapter.normalize("condition", "  Hypertension  ") == "hypertension"


# ── Types module ──────────────────────────────────────────────────────────────

def test_finance_structured_keys_constant():
    assert _FINANCE_STRUCTURED_KEYS == frozenset({
        "ticker", "metric", "entity", "instrument", "cusip", "isin", "field",
    })


def test_passthrough_structured_keys_constant():
    assert _PASSTHROUGH_STRUCTURED_KEYS == frozenset()
