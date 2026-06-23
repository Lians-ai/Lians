"""
Tests for the domain adapter system (SCALE.md Â§3 â€” core/adapter boundary).

Verifies:
- Finance adapter returns correct structured_keys
- Finance adapter normalizes ISIN/CUSIP/alias to canonical ticker
- Passthrough adapter returns empty structured_keys and identity normalize
- Healthcare adapter normalizes ICD-10, NPI, medication names
- Legal adapter normalizes matter IDs, jurisdictions, claim types
- get_adapter() returns the configured adapter (finance by default)
- Adapter can be overridden at runtime (for custom verticals)
"""
import pytest

from src.lian.adapters import get_adapter, register_adapter
from src.lian.adapters.finance import FinanceAdapter
from src.lian.adapters.passthrough import PassthroughAdapter
from src.lian.adapters.healthcare import HealthcareAdapter
from src.lian.adapters.legal import LegalAdapter
from src.lian._types import (
    _FINANCE_STRUCTURED_KEYS,
    _PASSTHROUGH_STRUCTURED_KEYS,
    _HEALTHCARE_STRUCTURED_KEYS,
    _LEGAL_STRUCTURED_KEYS,
)


# â”€â”€ Finance adapter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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


# â”€â”€ Passthrough adapter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_passthrough_adapter_empty_structured_keys():
    adapter = PassthroughAdapter()
    assert len(adapter.structured_keys) == 0


def test_passthrough_adapter_normalize_strips_whitespace():
    adapter = PassthroughAdapter()
    assert adapter.normalize("anything", "  hello  ") == "hello"
    assert adapter.normalize("ticker", "AAPL") == "AAPL"


def test_passthrough_adapter_no_ticker_normalization():
    adapter = PassthroughAdapter()
    # Passthrough does NOT map Apple Inc â†’ AAPL (no finance logic)
    assert adapter.normalize("ticker", "Apple Inc.") == "Apple Inc."


# â”€â”€ Protocol compliance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_finance_adapter_implements_protocol():
    from src.lian.adapters import DomainAdapter
    assert isinstance(FinanceAdapter(), DomainAdapter)


def test_passthrough_adapter_implements_protocol():
    from src.lian.adapters import DomainAdapter
    assert isinstance(PassthroughAdapter(), DomainAdapter)


# â”€â”€ get_adapter() factory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_get_adapter_returns_finance_by_default(monkeypatch):
    from src.lian.adapters import _registry
    _registry.clear()
    # Default DOMAIN_ADAPTER is "finance"
    adapter = get_adapter()
    assert isinstance(adapter, FinanceAdapter)


def test_get_adapter_returns_passthrough_when_configured(monkeypatch):
    from src.lian.config import get_settings
    from src.lian.adapters import _registry
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
    from src.lian.adapters import _registry
    assert "healthcare" in _registry
    adapter = _registry["healthcare"]
    assert "patient_id" in adapter.structured_keys
    assert adapter.normalize("condition", "  Hypertension  ") == "hypertension"


# â”€â”€ Types module â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_finance_structured_keys_constant():
    assert _FINANCE_STRUCTURED_KEYS == frozenset({
        "ticker", "metric", "entity", "instrument", "cusip", "isin", "field",
    })


def test_passthrough_structured_keys_constant():
    assert _PASSTHROUGH_STRUCTURED_KEYS == frozenset()


# â”€â”€ Healthcare adapter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestHealthcareAdapter:
    def setup_method(self):
        self.adapter = HealthcareAdapter()

    def test_structured_keys(self):
        keys = self.adapter.structured_keys
        assert keys == _HEALTHCARE_STRUCTURED_KEYS
        assert "patient_id" in keys
        assert "condition" in keys
        assert "medication" in keys
        assert "encounter_id" in keys
        assert "provider_id" in keys
        assert "procedure_code" in keys

    def test_normalize_icd10_adds_dot(self):
        assert self.adapter.normalize("condition", "E119") == "E11.9"
        assert self.adapter.normalize("condition", "J0601") == "J06.01"
        assert self.adapter.normalize("condition", "Z0000") == "Z00.00"

    def test_normalize_icd10_already_dotted(self):
        assert self.adapter.normalize("condition", "E11.9") == "E11.9"

    def test_normalize_icd10_uppercase(self):
        assert self.adapter.normalize("icd_code", "e11.9") == "E11.9"

    def test_normalize_icd10_alias(self):
        assert self.adapter.normalize("diagnosis", "I10") == "I10"  # 3 chars, no dot needed
        assert self.adapter.normalize("icd10", "j0601") == "J06.01"

    def test_normalize_npi_strips_formatting(self):
        assert self.adapter.normalize("provider_id", "1234567890") == "1234567890"
        assert self.adapter.normalize("npi", "1-234-567-890") == "1234567890"
        assert self.adapter.normalize("clinician_id", "NPI: 1234567890") == "1234567890"

    def test_normalize_npi_passthrough_if_not_10_digits(self):
        # Non-10-digit values returned as-is (don't corrupt them)
        result = self.adapter.normalize("provider_id", "12345")
        assert result == "12345"

    def test_normalize_medication_strips_dosage(self):
        assert self.adapter.normalize("medication", "Metformin HCl 500mg tabs") == "metformin hcl"
        assert self.adapter.normalize("medication", "Lisinopril 10 mg tablet") == "lisinopril"
        assert self.adapter.normalize("drug", "Atorvastatin 40mg") == "atorvastatin"
        assert self.adapter.normalize("medication", "Insulin Glargine 100 units") == "insulin glargine"

    def test_normalize_medication_preserves_salt(self):
        # Salt forms must survive to distinguish metformin HCl from metformin ER
        assert self.adapter.normalize("medication", "Metformin HCl") == "metformin hcl"

    def test_normalize_patient_id_strips_whitespace(self):
        assert self.adapter.normalize("patient_id", "  MRN-0012345  ") == "MRN-0012345"
        assert self.adapter.normalize("mrn", "  MRN-9988  ") == "MRN-9988"

    def test_key_aliases_canonical(self):
        aliases = self.adapter.key_aliases("condition")
        assert "condition" in aliases
        assert "diagnosis" in aliases
        assert "icd10" in aliases
        assert "icd_code" in aliases

    def test_key_aliases_from_alias(self):
        # Resolving from an alias should return the same alias group
        aliases = self.adapter.key_aliases("diagnosis")
        assert "condition" in aliases
        assert "diagnosis" in aliases

    def test_key_aliases_unknown_key(self):
        assert self.adapter.key_aliases("unknown_key") == ["unknown_key"]

    def test_implements_protocol(self):
        from src.lian.adapters import DomainAdapter
        assert isinstance(self.adapter, DomainAdapter)


# â”€â”€ Legal adapter â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestLegalAdapter:
    def setup_method(self):
        self.adapter = LegalAdapter()

    def test_structured_keys(self):
        keys = self.adapter.structured_keys
        assert keys == _LEGAL_STRUCTURED_KEYS
        assert "matter_id" in keys
        assert "jurisdiction" in keys
        assert "claim_type" in keys
        assert "party_id" in keys
        assert "privilege_date" in keys
        assert "document_type" in keys

    def test_normalize_matter_id_uppercase(self):
        assert self.adapter.normalize("matter_id", "nyc-2024-antitrust-001") == "NYC-2024-ANTITRUST-001"
        assert self.adapter.normalize("matter_id", "  SEC-2025-001  ") == "SEC-2025-001"

    def test_normalize_matter_id_aliases(self):
        assert self.adapter.normalize("case_id", "sec 2025 001") == "SEC-2025-001"
        assert self.adapter.normalize("docket_no", "2:24-cv-01234") == "2:24-CV-01234"

    def test_normalize_jurisdiction_known_abbreviation(self):
        assert self.adapter.normalize("jurisdiction", "Southern District of New York") == "S.D.N.Y."
        assert self.adapter.normalize("jurisdiction", "sdny") == "S.D.N.Y."
        assert self.adapter.normalize("jurisdiction", "northern district of california") == "N.D. Cal."
        assert self.adapter.normalize("jurisdiction", "district of delaware") == "D. Del."

    def test_normalize_jurisdiction_passthrough_unknown(self):
        # Unrecognized jurisdictions returned as-is (don't corrupt them)
        assert self.adapter.normalize("jurisdiction", "Arbitration Panel, Geneva") == "Arbitration Panel, Geneva"

    def test_normalize_claim_type_lowercase(self):
        assert self.adapter.normalize("claim_type", "Breach of Contract") == "breach of contract"
        assert self.adapter.normalize("cause_of_action", "Securities Fraud") == "securities fraud"

    def test_normalize_party_id_strips_whitespace(self):
        assert self.adapter.normalize("party_id", "  PLT-001  ") == "plt-001"

    def test_key_aliases_canonical(self):
        aliases = self.adapter.key_aliases("matter_id")
        assert "matter_id" in aliases
        assert "case_id" in aliases
        assert "docket_no" in aliases

    def test_key_aliases_from_alias(self):
        aliases = self.adapter.key_aliases("case_id")
        assert "matter_id" in aliases
        assert "case_id" in aliases

    def test_key_aliases_unknown_key(self):
        assert self.adapter.key_aliases("unknown_key") == ["unknown_key"]

    def test_implements_protocol(self):
        from src.lian.adapters import DomainAdapter
        assert isinstance(self.adapter, DomainAdapter)


# â”€â”€ Types module â€” new constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_healthcare_structured_keys_constant():
    assert _HEALTHCARE_STRUCTURED_KEYS == frozenset({
        "patient_id", "condition", "medication", "encounter_id", "provider_id", "procedure_code",
    })


def test_legal_structured_keys_constant():
    assert _LEGAL_STRUCTURED_KEYS == frozenset({
        "matter_id", "jurisdiction", "claim_type", "party_id", "privilege_date", "document_type",
    })
