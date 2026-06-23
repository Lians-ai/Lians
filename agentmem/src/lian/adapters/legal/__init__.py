"""
Legal domain adapter — matter ID, jurisdiction, claim type normalization.

This adapter is the ONLY place in AgentMem where legal-specific concepts
(matter identifiers, court jurisdictions, privilege dates) exist.

Compliance mapping
------------------
AgentMem features map directly to legal requirements:

  Information barriers (RLS, barrier_group per matter team)
    → ABA Model Rule 1.7 / 1.9 (conflicts of interest); Chinese wall enforcement

  Point-in-time recall (as_of=privilege_date)
    → FRCP Rule 34 eDiscovery: reproduce exactly what the agent knew before the
      privilege cutoff — without contamination from documents produced after cutoff

  Hash chain (/v1/admin/audit/verify)
    → Chain-of-custody documentation: proves no facts were altered post-deposition

  Crypto-shred (POST /v1/erase, subject_id=matter_id)
    → Client matter destruction at case close; content irrecoverable, audit survives
      (relevant to state bar rules on file retention and client confidentiality)

  Backtest contamination detection (/v1/backtest/check)
    → Expert witness simulation: confirm the expert's model used only data available
      at the relevant date, not data produced later in discovery

Deployment checklist
--------------------
  DOMAIN_ADAPTER=legal
  MASTER_ENCRYPTION_KEY=<32-byte base64 key>
  RLS_BARRIERS_ENABLED=true                  # enforces matter-team Chinese walls
  AIRGAP_MODE=true (recommended for matters with confidential client data)
"""
from __future__ import annotations

import re

from ..._types import _LEGAL_STRUCTURED_KEYS

_KEY_ALIASES: dict[str, list[str]] = {
    "matter_id":      ["matter_id", "case_id", "docket_no", "matter", "file_no", "matter_no"],
    "jurisdiction":   ["jurisdiction", "court", "venue", "district", "tribunal"],
    "claim_type":     ["claim_type", "cause_of_action", "charge", "allegation", "count"],
    "party_id":       ["party_id", "client_id", "counterparty_id", "adverse_party", "plaintiff", "defendant"],
    "privilege_date": ["privilege_date", "cutoff_date", "accrual_date", "trigger_date"],
    "document_type":  ["document_type", "doc_type", "filing_type", "instrument_type", "pleading_type"],
}

# Canonical U.S. federal district / common tribunal abbreviations
_JURISDICTION_MAP: dict[str, str] = {
    "southern district of new york": "S.D.N.Y.",
    "sdny": "S.D.N.Y.",
    "northern district of california": "N.D. Cal.",
    "n.d. cal.": "N.D. Cal.",
    "district of delaware": "D. Del.",
    "d. del.": "D. Del.",
    "eastern district of virginia": "E.D. Va.",
    "central district of california": "C.D. Cal.",
    "northern district of illinois": "N.D. Ill.",
    "district of columbia": "D.D.C.",
    "federal circuit": "Fed. Cir.",
    "court of international trade": "CIT",
    # Add as needed; unrecognized values are returned as-is
}


def _normalize_matter_id(value: str) -> str:
    """Uppercase, collapse whitespace to hyphens for canonical matter ID."""
    return re.sub(r"\s+", "-", value.strip().upper())


def _normalize_jurisdiction(value: str) -> str:
    v = value.strip()
    return _JURISDICTION_MAP.get(v.lower(), v)


class LegalAdapter:
    """
    Legal domain adapter: matter/jurisdiction/claim normalization.

    Enables keyed supersession on matter+claim pairs, supporting:

    - Privilege cutoff reconstruction
        recall(as_of=privilege_date) returns exactly what the agent knew before
        the cutoff — without contamination from documents produced later in
        discovery. This is a direct FRCP Rule 34 / eDiscovery requirement.

    - Chinese wall enforcement
        Set barrier_group=<matter_team_id> when provisioning an agent. The
        PostgreSQL RLS policy (migration 0011_rls_barriers) enforces that
        Matter Team A cannot read Matter Team B's memories at the DB layer.

    - Matter destruction
        POST /v1/erase with subject_id=<matter_id> crypto-shreds all matter
        content (per-subject DEK destroyed). Content becomes irrecoverable;
        the audit hash chain survives for chain-of-custody records.

    - Expert witness contamination check
        POST /v1/backtest/check with simulation_as_of=<relevant_date> flags
        any fact the agent possessed that wasn't available at that date —
        the same lookahead-bias detection used for quantitative finance.
    """

    @property
    def structured_keys(self) -> frozenset[str]:
        return _LEGAL_STRUCTURED_KEYS

    def normalize(self, key: str, value: str) -> str:
        canonical = next(
            (k for k, aliases in _KEY_ALIASES.items() if key in aliases),
            key,
        )
        if canonical == "matter_id":
            return _normalize_matter_id(value)
        if canonical == "jurisdiction":
            return _normalize_jurisdiction(value)
        return value.strip().lower()

    def key_aliases(self, key: str) -> list[str]:
        if key in _KEY_ALIASES:
            return _KEY_ALIASES[key]
        for canonical, aliases in _KEY_ALIASES.items():
            if key in aliases:
                return aliases
        return [key]

    async def matter_timeline(
        self,
        db,
        namespace: str,
        agent_id: str,
        matter_id: str,
        claim_type: str,
        limit: int = 100,
    ):
        """
        Return all versions of a matter+claim fact, ordered by event_time ascending.

        Legal equivalent of FinanceAdapter.fact_history(ticker, metric):
        "show every documented status change for this antitrust matter's damages claim."

        Parameters
        ----------
        matter_id:
            Any of: matter_id, case_id, docket_no — normalized to canonical form.
        claim_type:
            Cause of action or charge type — lowercased for canonical matching.
        """
        from ...memory_service import get_structured_fact_history

        key_values = {
            "matter_id":  _normalize_matter_id(matter_id),
            "claim_type": claim_type.strip().lower(),
        }
        return await get_structured_fact_history(db, namespace, agent_id, key_values, self, limit)
