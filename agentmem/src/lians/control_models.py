"""Persistence models for the Lians trust, Gate, and remediation control plane.

These models intentionally live outside ``models.py`` so the control plane can
evolve as an independently deployable slice. Importing this module registers
the tables with the shared ``Base.metadata``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ReceiptIssuer(Base):
    """A tenant-approved issuer of Lians Decision Receipts."""

    __tablename__ = "receipt_issuers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    name = Column(String(255), nullable=False)
    issuer_uri = Column(String(2048), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_by = Column(String(255), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revocation_reason = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("namespace", "name", name="uq_receipt_issuer_ns_name"),
        Index("ix_receipt_issuer_ns_status", "namespace", "status"),
        Index(
            "ix_receipt_issuer_list",
            "namespace",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_receipt_issuer_all_list",
            "namespace",
            "created_at",
            "id",
        ),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_receipt_issuer_status"),
    )


class TrustedReceiptKey(Base):
    """Public verification material only; private signing keys never enter Lians."""

    __tablename__ = "trusted_receipt_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    issuer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("receipt_issuers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    key_id = Column(String(255), nullable=False)
    algorithm = Column(String(32), nullable=False, default="ed25519", server_default="ed25519")
    public_key = Column(Text, nullable=False)
    public_key_format = Column(
        String(32), nullable=False, default="raw-base64", server_default="raw-base64"
    )
    fingerprint_sha256 = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="active", server_default="active")
    valid_from = Column(DateTime(timezone=True), nullable=False, default=_now)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_by = Column(String(255), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revocation_reason = Column(Text, nullable=True)
    rotated_at = Column(DateTime(timezone=True), nullable=True)
    rotated_from_key_id = Column(String(255), nullable=True)
    replaced_by_key_id = Column(String(255), nullable=True)
    rotation_reason = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("namespace", "key_id", name="uq_trusted_receipt_key_ns_key_id"),
        Index("ix_trusted_key_ns_status", "namespace", "status"),
        Index("ix_trusted_key_issuer_status", "issuer_id", "status"),
        Index(
            "ix_trusted_key_issuer_list",
            "issuer_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_trusted_key_issuer_all_list",
            "issuer_id",
            "created_at",
            "id",
        ),
        CheckConstraint("algorithm = 'ed25519'", name="ck_trusted_receipt_key_algorithm"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_trusted_receipt_key_status"),
        CheckConstraint(
            "length(key_id) BETWEEN 1 AND 255 "
            "AND key_id NOT LIKE '%/%' "
            "AND key_id NOT LIKE '% %' "
            "AND key_id NOT IN ('.', '..')",
            name="ck_trusted_receipt_key_safe_id",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > valid_from",
            name="ck_trusted_receipt_key_window",
        ),
    )


class GatePolicySet(Base):
    """An immutable-version policy bundle; changes create a new version."""

    __tablename__ = "gate_policy_sets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    name = Column(String(255), nullable=False)
    version = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="draft", server_default="draft")
    default_disposition = Column(
        String(16), nullable=False, default="deny", server_default="deny"
    )
    # Gate policy selection is server-authoritative.  Callers describe the
    # action they intend to execute; active policies declare the exact actions
    # and target URI prefixes they protect.  Empty selector arrays are retained
    # only for safe migration of legacy rows and are never eligible at runtime.
    protected_actions = Column(JSON, nullable=False, default=list, server_default="[]")
    target_ref_prefixes = Column(JSON, nullable=False, default=list, server_default="[]")
    # Exact, versioned identities for the mediators that may redeem an allow
    # verdict.  Legacy rows can contain an empty list only so the migration can
    # preserve their historical policy hash; they are retired and cannot be
    # reactivated.  Every policy created through the current contract requires
    # at least one canonical principal reference.
    enforcement_principal_ids = Column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    maximum_permit_ttl_seconds = Column(
        Integer, nullable=False, default=60, server_default="60"
    )
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    activated_by = Column(String(255), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    policy_hash = Column(String(64), nullable=False, index=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        UniqueConstraint("namespace", "name", "version", name="uq_gate_policy_ns_name_version"),
        Index("ix_gate_policy_ns_name_status", "namespace", "name", "status"),
        Index("ix_gate_policy_ns_status_barrier", "namespace", "status", "barrier_group"),
        CheckConstraint("status IN ('draft', 'active', 'retired')", name="ck_gate_policy_status"),
        CheckConstraint(
            "default_disposition IN ('allow', 'deny', 'review')",
            name="ck_gate_policy_default_disposition",
        ),
        CheckConstraint(
            "maximum_permit_ttl_seconds BETWEEN 1 AND 300",
            name="ck_gate_policy_permit_ttl",
        ),
    )


class GatePolicyRule(Base):
    """A rule within an immutable-version Gate policy set."""

    __tablename__ = "gate_policy_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    policy_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    action_on_failure = Column(String(16), nullable=False, default="deny", server_default="deny")
    applies_to_decision_types = Column(JSON, nullable=False, default=list, server_default="[]")
    applies_to_risk_levels = Column(JSON, nullable=False, default=list, server_default="[]")
    required_receipt_grade = Column(String(1), nullable=True)
    require_trusted_issuer = Column(Boolean, nullable=False, default=False, server_default="false")
    require_sources_current = Column(Boolean, nullable=False, default=False, server_default="false")
    require_policy_attached = Column(Boolean, nullable=False, default=False, server_default="false")
    required_principal_scopes = Column(JSON, nullable=False, default=list, server_default="[]")
    minimum_approval_count = Column(Integer, nullable=False, default=0, server_default="0")
    required_approval_roles = Column(JSON, nullable=False, default=list, server_default="[]")
    allowed_approval_principal_types = Column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    maximum_approval_age_seconds = Column(Integer, nullable=True)
    require_information_barrier_match = Column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    block_untrusted_content = Column(Boolean, nullable=False, default=False, server_default="false")
    max_untrusted_content_score = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("policy_set_id", "name", name="uq_gate_rule_policy_name"),
        Index("ix_gate_rule_policy_priority", "policy_set_id", "priority"),
        CheckConstraint(
            "action_on_failure IN ('deny', 'review')", name="ck_gate_rule_failure_action"
        ),
        CheckConstraint("minimum_approval_count >= 0", name="ck_gate_rule_approval_count"),
        CheckConstraint(
            "maximum_approval_age_seconds IS NULL OR "
            "maximum_approval_age_seconds BETWEEN 60 AND 31536000",
            name="ck_gate_rule_approval_age",
        ),
        CheckConstraint(
            "max_untrusted_content_score IS NULL OR "
            "(max_untrusted_content_score >= 0 AND max_untrusted_content_score <= 100)",
            name="ck_gate_rule_untrusted_score",
        ),
    )


class GateApprovalAttestation(Base):
    """One append-only event in a principal's approval-attestation series.

    The semantic boundary is addressed by ``context_hash``.  Supersession and
    revocation append a successor row; no lifecycle field is ever updated in
    place.  ``series_key`` prevents a principal from manufacturing multiple
    independently-counted approval chains for the same boundary.
    """

    __tablename__ = "gate_approval_attestations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    series_key = Column(String(64), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    approval_principal_id = Column(String(512), nullable=False, index=True)
    attested_by = Column(String(512), nullable=False)
    principal_type = Column(String(32), nullable=True)
    attester_role = Column(String(100), nullable=False, index=True)
    auth_method = Column(String(32), nullable=False)
    credential_id = Column(String(255), nullable=True)
    status = Column(String(16), nullable=False, index=True)
    action = Column(String(255), nullable=False, index=True)
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    change_event_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ledger_events.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    policy_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    policy_hash = Column(String(64), nullable=False)
    target_ref = Column(String(2048), nullable=True)
    target_barrier_group = Column(String(255), nullable=True)
    receipt_hash = Column(String(64), nullable=True, index=True)
    context_hash = Column(String(64), nullable=False, index=True)
    statement_encrypted = Column(Text, nullable=True)
    statement_hash = Column(String(64), nullable=True)
    evidence_refs = Column(JSON, nullable=False, default=list, server_default="[]")
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    supersedes_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_approval_attestations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    prior_attestation_hash = Column(String(64), nullable=True)
    attestation_hash = Column(String(64), nullable=False, index=True)
    attested_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "series_key",
            "sequence",
            name="uq_gate_approval_series_sequence",
        ),
        UniqueConstraint("supersedes_id", name="uq_gate_approval_supersedes"),
        UniqueConstraint(
            "attestation_hash", name="uq_gate_approval_attestation_hash"
        ),
        Index(
            "ix_gate_approval_ns_context_time",
            "namespace",
            "context_hash",
            "attested_at",
        ),
        CheckConstraint("sequence > 0", name="ck_gate_approval_sequence"),
        CheckConstraint(
            "status IN ('approved', 'rejected', 'revoked')",
            name="ck_gate_approval_status",
        ),
        CheckConstraint(
            "attester_role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_gate_approval_role",
        ),
        CheckConstraint(
            "auth_method IN ('api_key', 'oidc_bearer')",
            name="ck_gate_approval_auth_method",
        ),
        CheckConstraint(
            "target_barrier_group IS NULL OR target_barrier_group = barrier_group",
            name="ck_gate_approval_target_barrier",
        ),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > attested_at",
            name="ck_gate_approval_expiry",
        ),
        CheckConstraint(
            "status != 'revoked' OR expires_at IS NULL",
            name="ck_gate_approval_revoked_expiry",
        ),
        CheckConstraint(
            "statement_encrypted IS NULL OR "
            "statement_encrypted LIKE 'lians-sealed:v1:%' OR "
            "statement_encrypted LIKE 'lians-sealed:v2:%'",
            name="ck_gate_approval_statement_sealed",
        ),
        CheckConstraint(
            "(sequence = 1 AND supersedes_id IS NULL AND prior_attestation_hash IS NULL) OR "
            "(sequence > 1 AND supersedes_id IS NOT NULL AND prior_attestation_hash IS NOT NULL)",
            name="ck_gate_approval_chain_shape",
        ),
    )


class GateDecisionRecord(Base):
    """Append-only result of evaluating one runtime action against Gate."""

    __tablename__ = "gate_decision_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    policy_set_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    policy_name = Column(String(255), nullable=False)
    policy_version = Column(String(100), nullable=False)
    policy_hash = Column(String(64), nullable=False)
    principal_id = Column(String(512), nullable=False, index=True)
    action = Column(String(255), nullable=False, index=True)
    # These execution-boundary claims are first-class rather than recoverable
    # only from input_snapshot.  They remain nullable solely for pre-0040
    # historical evaluations; current evaluation requests always populate them.
    target_ref = Column(String(2048), nullable=False, index=True)
    enforcement_principal_id = Column(String(512), nullable=True, index=True)
    execution_request_hash = Column(String(64), nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    change_event_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    receipt_hash = Column(String(64), nullable=True, index=True)
    disposition = Column(String(16), nullable=False, index=True)
    reasons = Column(JSON, nullable=False, default=list, server_default="[]")
    applied_rules = Column(JSON, nullable=False, default=list, server_default="[]")
    input_snapshot = Column(JSON, nullable=False, default=dict, server_default="{}")
    request_hash = Column(String(64), nullable=False, index=True)
    evaluation_hash = Column(String(64), nullable=False, unique=True)
    evaluated_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)

    __table_args__ = (
        Index("ix_gate_decision_records_evaluation_hash", "evaluation_hash"),
        Index("ix_gate_decision_ns_time", "namespace", "evaluated_at"),
        Index("ix_gate_decision_ns_disposition", "namespace", "disposition"),
        CheckConstraint(
            "disposition IN ('allow', 'deny', 'review')", name="ck_gate_decision_disposition"
        ),
    )


class GateExecutionPermit(Base):
    """One opaque, short-lived execution capability issued for an allow verdict.

    The plaintext bearer token is never persisted.  A unique evaluation link
    makes issuance exactly-once, while immutable consumption events provide the
    replay boundary without mutating this grant.
    """

    __tablename__ = "gate_execution_permits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    evaluation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_decision_records.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    policy_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    enforcement_principal_id = Column(String(512), nullable=False, index=True)
    action = Column(String(255), nullable=False, index=True)
    target_ref = Column(String(2048), nullable=False)
    execution_request_hash = Column(String(64), nullable=False)
    token_digest = Column(String(64), nullable=False, unique=True)
    issued_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    grant_hash = Column(String(64), nullable=False, unique=True)

    __table_args__ = (
        Index("ix_gate_execution_permits_evaluation_id", "evaluation_id"),
        Index("ix_gate_execution_permits_grant_hash", "grant_hash"),
        Index("ix_gate_permit_ns_expiry", "namespace", "expires_at"),
        CheckConstraint("expires_at > issued_at", name="ck_gate_permit_expiry"),
        CheckConstraint(
            "length(token_digest) = 64", name="ck_gate_permit_token_digest"
        ),
        CheckConstraint(
            "length(execution_request_hash) = 64",
            name="ck_gate_permit_request_hash",
        ),
        CheckConstraint("length(grant_hash) = 64", name="ck_gate_permit_grant_hash"),
        CheckConstraint(
            "enforcement_principal_id LIKE 'lians:principal:v1:%'",
            name="ck_gate_permit_principal_ref",
        ),
    )


class GateExecutionPermitConsumption(Base):
    """Append-only proof that a mediator redeemed a permit exactly once."""

    __tablename__ = "gate_execution_permit_consumptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    permit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_execution_permits.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    evaluation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_decision_records.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    policy_set_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gate_policy_sets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    consuming_principal_id = Column(String(512), nullable=False, index=True)
    action = Column(String(255), nullable=False)
    target_ref = Column(String(2048), nullable=False)
    execution_request_hash = Column(String(64), nullable=False)
    grant_hash = Column(String(64), nullable=False)
    token_digest = Column(String(64), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    consumption_hash = Column(String(64), nullable=False, unique=True)

    __table_args__ = (
        Index("ix_gate_execution_permit_consumptions_permit_id", "permit_id"),
        Index(
            "ix_gate_execution_permit_consumptions_evaluation_id",
            "evaluation_id",
        ),
        Index(
            "ix_gate_execution_permit_consumptions_consumption_hash",
            "consumption_hash",
        ),
        Index("ix_gate_permit_consumption_ns_time", "namespace", "consumed_at"),
        CheckConstraint(
            "length(grant_hash) = 64", name="ck_gate_permit_consumption_grant_hash"
        ),
        CheckConstraint(
            "length(token_digest) = 64",
            name="ck_gate_permit_consumption_token_digest",
        ),
        CheckConstraint(
            "length(execution_request_hash) = 64",
            name="ck_gate_permit_consumption_request_hash",
        ),
        CheckConstraint(
            "length(consumption_hash) = 64",
            name="ck_gate_permit_consumption_hash",
        ),
        CheckConstraint(
            "consuming_principal_id LIKE 'lians:principal:v1:%'",
            name="ck_gate_permit_consumption_principal_ref",
        ),
    )


class DecisionReviewEvent(Base):
    """Tamper-evident, append-only human review history for a decision."""

    __tablename__ = "decision_review_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    decision_id = Column(
        UUID(as_uuid=True),
        ForeignKey("decision_records.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sequence = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, index=True)
    reviewer_principal_id = Column(String(512), nullable=False)
    reviewer_principal_type = Column(String(32), nullable=True)
    reviewer_role = Column(String(100), nullable=True)
    auth_method = Column(String(32), nullable=False)
    credential_id = Column(String(255), nullable=True)
    note_encrypted = Column(Text, nullable=True)
    note_hash = Column(String(64), nullable=True)
    prior_event_hash = Column(String(64), nullable=True)
    event_hash = Column(String(64), nullable=False, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)

    __table_args__ = (
        UniqueConstraint(
            "namespace",
            "decision_id",
            "sequence",
            name="uq_decision_review_sequence",
        ),
        UniqueConstraint(
            "namespace",
            "decision_id",
            "prior_event_hash",
            name="uq_decision_review_prior_hash",
        ),
        UniqueConstraint("event_hash", name="uq_decision_review_event_hash"),
        Index(
            "ix_decision_review_ns_decision_time",
            "namespace",
            "decision_id",
            "reviewed_at",
        ),
        CheckConstraint("sequence > 0", name="ck_decision_review_sequence"),
        CheckConstraint(
            "status IN ('requested', 'affirmed', 'overturned', 'withdrawn')",
            name="ck_decision_review_status",
        ),
        CheckConstraint(
            "reviewer_role IS NULL OR "
            "reviewer_role IN ('owner', 'analyst', 'compliance', 'readonly')",
            name="ck_decision_review_role",
        ),
        CheckConstraint(
            "auth_method IN ('api_key', 'oidc_bearer')",
            name="ck_decision_review_auth_method",
        ),
        CheckConstraint(
            "note_encrypted IS NULL OR note_encrypted LIKE 'lians-sealed:v1:%' OR "
            "note_encrypted LIKE 'lians-sealed:v2:%'",
            name="ck_decision_review_note_sealed",
        ),
        CheckConstraint(
            "(sequence = 1 AND prior_event_hash IS NULL) OR "
            "(sequence > 1 AND prior_event_hash IS NOT NULL)",
            name="ck_decision_review_chain_shape",
        ),
    )


class InvestigationCase(Base):
    """Operational case that preserves links to affected decisions and changes."""

    __tablename__ = "investigation_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    severity = Column(String(16), nullable=False, default="medium", server_default="medium")
    status = Column(String(32), nullable=False, default="open", server_default="open")
    owner_principal = Column(String(255), nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    change_event_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    gate_decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    opened_by = Column(String(255), nullable=False)
    opened_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    resolution_summary = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        Index("ix_investigation_case_ns_status", "namespace", "status"),
        Index("ix_investigation_case_ns_owner", "namespace", "owner_principal"),
        Index("ix_investigation_case_global_status", "status"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_investigation_case_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'in_review', 'remediating', 'resolved', 'closed')",
            name="ck_investigation_case_status",
        ),
    )


class RemediationTask(Base):
    """Owned work item whose closure requires an immutable attestation."""

    __tablename__ = "remediation_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    case_id = Column(
        UUID(as_uuid=True),
        ForeignKey("investigation_cases.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    owner_principal = Column(String(255), nullable=True, index=True)
    due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    change_event_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    created_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    metadata_ = Column("metadata", JSON, nullable=False, default=dict, server_default="{}")

    __table_args__ = (
        Index("ix_remediation_task_ns_status", "namespace", "status"),
        Index("ix_remediation_task_case_status", "case_id", "status"),
        Index(
            "ix_remediation_task_case_status_list",
            "case_id",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_remediation_task_case_list",
            "case_id",
            "created_at",
            "id",
        ),
        Index("ix_remediation_task_global_status_due", "status", "due_at"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'blocked', 'cancelled', 'closed')",
            name="ck_remediation_task_status",
        ),
    )


class ControlClosureAttestation(Base):
    """Append-only proof that a task or case was deliberately closed."""

    __tablename__ = "control_closure_attestations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace = Column(String, nullable=False, index=True)
    barrier_group = Column(String, nullable=True, index=True)
    resource_type = Column(String(16), nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    attested_by = Column(String(255), nullable=False)
    # v1 rows may contain the original plaintext ``statement``. New writes use
    # the purpose-separated encrypted field and retain only a content hash in
    # the immutable evidence envelope. The offline key-protection workflow
    # upgrades legacy rows under the table's append-only trigger guard.
    statement = Column(Text, nullable=True)
    statement_encrypted = Column(Text, nullable=True)
    statement_hash = Column(String(64), nullable=True)
    hash_version = Column(Integer, nullable=False, default=1, server_default="1")
    evidence_refs = Column(JSON, nullable=False, default=list, server_default="[]")
    decision_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    change_event_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    attestation_hash = Column(String(64), nullable=False, unique=True)
    attested_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)

    __table_args__ = (
        Index(
            "ix_control_closure_attestations_attestation_hash",
            "attestation_hash",
        ),
        UniqueConstraint(
            "namespace",
            "resource_type",
            "resource_id",
            name="uq_control_closure_resource",
        ),
        Index("ix_control_attestation_ns_time", "namespace", "attested_at"),
        CheckConstraint(
            "resource_type IN ('case', 'task')", name="ck_control_attestation_resource_type"
        ),
        CheckConstraint(
            "(statement IS NOT NULL AND statement_encrypted IS NULL) OR "
            "(statement IS NULL AND statement_encrypted IS NOT NULL)",
            name="ck_control_attestation_statement_storage",
        ),
        CheckConstraint(
            "statement_encrypted IS NULL OR "
            "statement_encrypted LIKE 'lians-sealed:v1:%' OR "
            "statement_encrypted LIKE 'lians-sealed:v2:%'",
            name="ck_control_attestation_statement_sealed",
        ),
        CheckConstraint(
            "statement_hash IS NULL OR "
            "(length(statement_hash) = 64 AND statement_hash = lower(statement_hash))",
            name="ck_control_attestation_statement_hash",
        ),
        CheckConstraint(
            "hash_version IN (1, 2)", name="ck_control_attestation_hash_version"
        ),
        CheckConstraint(
            "hash_version = 1 OR statement_hash IS NOT NULL",
            name="ck_control_attestation_v2_statement_hash",
        ),
    )
