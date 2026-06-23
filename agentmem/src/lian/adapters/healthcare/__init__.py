"""
Healthcare domain adapter — patient ID, condition, medication normalization.

This adapter is the ONLY place in AgentMem where healthcare-specific concepts
(patient identifiers, ICD-10 codes, NPI numbers, medication names) exist.

PHI scope
---------
The following structured keys frequently contain PHI under HIPAA:
  patient_id   — maps to MRN, member ID, beneficiary ID
  encounter_id — hospital visit / admission reference
  provider_id  — NPI-formatted clinician identifier

AgentMem's per-subject AES-256-GCM encryption and crypto-shred erasure
(POST /v1/erase with subject_id=<patient_id>) satisfy:
  HIPAA §164.312(a)(2)(iv) — Encryption
  HIPAA §164.312(c)(1)     — Integrity (hash chain; content cannot be altered)
  HIPAA §164.312(e)(2)(ii) — Encryption in transit (enforced at the TLS layer)

Information barriers (RLS) enforce access controls per HIPAA §164.312(a)(1)
(Access Control) when barrier_group is set to a care-team or department identifier.

Deployment checklist
--------------------
  DOMAIN_ADAPTER=healthcare
  MASTER_ENCRYPTION_KEY=<32-byte base64 key>          # required; no zero-key bypass
  RLS_BARRIERS_ENABLED=true                            # enforces care-team barriers
  AIRGAP_MODE=true (recommended)                       # prevents PHI leaving perimeter

A HIPAA Business Associate Agreement (BAA) must be in place before processing
real patient data.  See HIPAA_SAFEGUARDS.md for the full technical safeguard mapping.
"""
from __future__ import annotations

import re

from ..._types import _HEALTHCARE_STRUCTURED_KEYS

# Maps each top-level structured key to all metadata field names that can carry it.
_KEY_ALIASES: dict[str, list[str]] = {
    "patient_id":     ["patient_id", "mrn", "member_id", "patient_mrn", "beneficiary_id"],
    "condition":      ["condition", "diagnosis", "icd10", "icd_code", "problem"],
    "medication":     ["medication", "drug", "rx", "ndc", "prescription", "med_name"],
    "encounter_id":   ["encounter_id", "visit_id", "admission_id", "episode_id"],
    "provider_id":    ["provider_id", "npi", "clinician_id", "physician_id"],
    "procedure_code": ["procedure_code", "cpt", "hcpcs", "procedure"],
}


def _normalize_icd10(value: str) -> str:
    """Canonical ICD-10 format: uppercase, dot after 3rd character if absent."""
    v = value.strip().upper().replace(" ", "")
    if "." not in v and len(v) > 3:
        v = v[:3] + "." + v[3:]
    return v


def _normalize_npi(value: str) -> str:
    """Strip non-digits; NPI is exactly 10 digits per CMS standard."""
    digits = re.sub(r"\D", "", value)
    return digits if len(digits) == 10 else value.strip()


def _normalize_medication(value: str) -> str:
    """
    Canonical drug name: lowercase, strip trailing dosage/form.

    "Metformin HCl 500mg tabs" → "metformin hcl"
    Preserves salt forms (HCl, Na, etc.) for accurate supersession matching.
    """
    v = re.sub(
        r"\s+\d+\s*(mg|mcg|ml|g|iu|units?|tab(let)?s?|cap(sule)?s?)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return v.strip().lower()


class HealthcareAdapter:
    """
    Healthcare domain adapter: patient/encounter/provider normalization.

    Enables keyed supersession on patient-condition pairs, encounter chains,
    and medication changes — the same temporal correctness engine used for
    financial guidance revisions, applied to clinical fact updates.

    Example supersession chains this enables:
      patient_id=MRN-001, condition=E11.9 (Type 2 DM) — tracks glycemic control
        updates across encounters with correct event_time ordering
      patient_id=MRN-001, medication=metformin — tracks dose titration chain
        so the agent always recalls the current dosage, not the starting one

    Point-in-time recall (as_of) answers: "What did the care agent know about
    this patient at 3am on the night of admission?" — a clinical and legal question
    that no other memory layer can answer correctly under out-of-order ingestion.
    """

    @property
    def structured_keys(self) -> frozenset[str]:
        return _HEALTHCARE_STRUCTURED_KEYS

    def normalize(self, key: str, value: str) -> str:
        # Resolve alias to canonical key
        canonical = next(
            (k for k, aliases in _KEY_ALIASES.items() if key in aliases),
            key,
        )
        if canonical == "condition":
            return _normalize_icd10(value)
        if canonical == "provider_id":
            return _normalize_npi(value)
        if canonical == "medication":
            return _normalize_medication(value)
        return value.strip()

    def key_aliases(self, key: str) -> list[str]:
        # If key is already a canonical key, return its aliases
        if key in _KEY_ALIASES:
            return _KEY_ALIASES[key]
        # If key is an alias, find its canonical group
        for canonical, aliases in _KEY_ALIASES.items():
            if key in aliases:
                return aliases
        return [key]

    async def patient_timeline(
        self,
        db,
        namespace: str,
        agent_id: str,
        patient_id: str,
        condition: str,
        limit: int = 100,
    ):
        """
        Return all versions of a patient+condition fact, ordered by event_time ascending.

        Clinical equivalent of FinanceAdapter.fact_history(ticker, metric):
        "show every documented status change for this patient's diabetes diagnosis."

        Parameters
        ----------
        patient_id:
            MRN, member ID, or any identifier that maps to the patient_id key.
        condition:
            ICD-10 code or description — normalized to canonical ICD-10 format.
        """
        from ...memory_service import get_structured_fact_history

        key_values = {
            "patient_id": patient_id.strip(),
            "condition":  _normalize_icd10(condition),
        }
        return await get_structured_fact_history(db, namespace, agent_id, key_values, self, limit)
