"""
Shared type constants — imported by both core modules and adapter modules.

Keeping these here breaks the circular import that would occur if adapters
imported from supersession.py and supersession.py imported from adapters.
"""

# Finance adapter: metadata keys that identify a structured financial fact.
# These keys trigger the keyed supersession fast path and live_facts indexing.
# A reporting period is part of fact identity: Q2 revenue and Q3 revenue are
# independent observations, while two revisions of Q3 revenue are versions of
# the same fact. ``quarter`` remains first-class because public payloads use it.
_FINANCE_STRUCTURED_KEYS: frozenset[str] = frozenset({
    "ticker", "metric", "entity", "instrument", "cusip", "isin", "field",
    "period", "quarter",
})

# Healthcare adapter: metadata keys that identify a structured clinical fact.
_HEALTHCARE_STRUCTURED_KEYS: frozenset[str] = frozenset({
    "patient_id", "condition", "medication", "encounter_id", "provider_id", "procedure_code",
})

# Legal adapter: metadata keys that identify a structured legal fact.
_LEGAL_STRUCTURED_KEYS: frozenset[str] = frozenset({
    "matter_id", "jurisdiction", "claim_type", "party_id", "privilege_date", "document_type",
})

# Passthrough adapter: no structured keys (pure semantic supersession only).
_PASSTHROUGH_STRUCTURED_KEYS: frozenset[str] = frozenset()
