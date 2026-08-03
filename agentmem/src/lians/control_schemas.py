"""API contracts for trust administration, Lians Gate, and remediation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

ReceiptGrade = Literal["A", "B", "C", "D", "F"]
GateDisposition = Literal["allow", "deny", "review"]
ApprovalPrincipalType = Literal["human", "workload", "api_key"]
BarrierValue = str | None
SAFE_KEY_ID_PATTERN = r"^[A-Za-z0-9_~-](?:[A-Za-z0-9._~-]*[A-Za-z0-9_~-])?$"
SAFE_ACTION_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._:/~-]*[A-Za-z0-9])?$"
_UUID_REF = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
CANONICAL_PRINCIPAL_REF_PATTERN = (
    rf"^lians:principal:v1:(?:api-key:{_UUID_REF}|oidc:{_UUID_REF}:{_UUID_REF})$"
)
CANONICAL_TARGET_REF_PATTERN = re.compile(
    r"^[a-z][a-z0-9+.-]{0,31}:[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$"
)
NON_CANONICAL_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-F]{2})")


def _is_canonical_target_ref(value: str) -> bool:
    return bool(
        value.isascii()
        and CANONICAL_TARGET_REF_PATTERN.fullmatch(value) is not None
        and NON_CANONICAL_PERCENT_ESCAPE.search(value) is None
    )


def _unique_nonempty_strings(values: list[str], label: str) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError(f"{label} must not contain blank values")
    if len(cleaned) != len(set(cleaned)):
        raise ValueError(f"{label} must not contain duplicates")
    if any(
        any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in cleaned
    ):
        raise ValueError(f"{label} must not contain control characters")
    return cleaned


def _canonical_principal_refs(values: list[str], label: str) -> list[str]:
    cleaned = _unique_nonempty_strings(values, label)
    invalid = [
        value
        for value in cleaned
        if re.fullmatch(CANONICAL_PRINCIPAL_REF_PATTERN, value) is None
    ]
    if invalid:
        raise ValueError(
            f"{label} must contain canonical lowercase lians:principal:v1 "
            "references returned by /v1/identity/whoami"
        )
    return cleaned


def _canonical_target_refs(values: list[str], label: str) -> list[str]:
    cleaned = _unique_nonempty_strings(values, label)
    if any(
        len(value) > 2048
        or not _is_canonical_target_ref(value)
        for value in cleaned
    ):
        raise ValueError(
            f"{label} must contain canonical ASCII absolute resource URIs without "
            "whitespace or control characters; percent-encode non-ASCII data"
        )
    return cleaned


class _ORMOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class IssuerCreate(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    issuer_uri: str | None = Field(default=None, max_length=2048)
    description: str | None = Field(default=None, max_length=10_000)
    barrier_group: BarrierValue = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IssuerRevoke(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=10_000)


class IssuerOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    name: str
    issuer_uri: str | None
    description: str | None
    status: Literal["active", "revoked"]
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    created_by: str
    created_at: datetime
    revoked_by: str | None
    revoked_at: datetime | None
    revocation_reason: str | None


class TrustedKeyCreate(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    key_id: str = Field(
        min_length=1,
        max_length=255,
        pattern=SAFE_KEY_ID_PATTERN,
        description="Stable URL-segment identifier; slashes and reserved dot segments are forbidden.",
    )
    algorithm: Literal["ed25519"] = "ed25519"
    public_key: str = Field(
        min_length=40,
        max_length=128,
        description="Raw 32-byte Ed25519 public key encoded as base64 or hexadecimal.",
    )
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _valid_window(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class TrustedKeyRotate(TrustedKeyCreate):
    reason: str = Field(min_length=1, max_length=10_000)


class TrustedKeyRevoke(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=10_000)


class TrustedKeyOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    issuer_id: UUID
    key_id: str
    algorithm: Literal["ed25519"]
    public_key: str
    public_key_format: Literal["raw-base64"]
    fingerprint_sha256: str
    status: Literal["active", "revoked"]
    valid_from: datetime
    valid_until: datetime | None
    created_by: str
    created_at: datetime
    revoked_by: str | None
    revoked_at: datetime | None
    revocation_reason: str | None
    rotated_at: datetime | None
    rotated_from_key_id: str | None
    replaced_by_key_id: str | None
    rotation_reason: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_")


class GatePolicyRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    priority: int = Field(default=100, ge=0, le=1_000_000)
    enabled: bool = True
    action_on_failure: Literal["deny", "review"] = "deny"
    applies_to_decision_types: list[str] = Field(default_factory=list, max_length=100)
    applies_to_risk_levels: list[Literal["low", "medium", "high", "critical"]] = Field(
        default_factory=list, max_length=4
    )
    required_receipt_grade: ReceiptGrade | None = None
    require_trusted_issuer: bool = False
    require_sources_current: bool = False
    require_policy_attached: bool = False
    required_principal_scopes: list[str] = Field(default_factory=list, max_length=100)
    minimum_approval_count: int = Field(default=0, ge=0, le=1000)
    required_approval_roles: list[str] = Field(default_factory=list, max_length=100)
    allowed_approval_principal_types: list[ApprovalPrincipalType] = Field(
        default_factory=list, max_length=3
    )
    maximum_approval_age_seconds: int | None = Field(
        default=None, ge=60, le=31_536_000
    )
    require_information_barrier_match: bool = False
    block_untrusted_content: bool = False
    max_untrusted_content_score: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _approval_policy_is_unambiguous(self):
        for label, values in (
            ("required_principal_scopes", self.required_principal_scopes),
            ("required_approval_roles", self.required_approval_roles),
            (
                "allowed_approval_principal_types",
                self.allowed_approval_principal_types,
            ),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must not contain duplicates")
        if (
            self.allowed_approval_principal_types
            or self.maximum_approval_age_seconds is not None
        ) and not (self.minimum_approval_count or self.required_approval_roles):
            raise ValueError(
                "approval principal-type or freshness constraints require an "
                "approval count or role requirement"
            )
        return self


class GatePolicySetCreate(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=10_000)
    barrier_group: BarrierValue = Field(default=None, max_length=255)
    default_disposition: GateDisposition = "deny"
    protected_actions: list[str] = Field(min_length=1, max_length=1000)
    target_ref_prefixes: list[str] = Field(min_length=1, max_length=1000)
    enforcement_principal_ids: list[str] = Field(min_length=1, max_length=1000)
    maximum_permit_ttl_seconds: int = Field(default=60, ge=1, le=300)
    rules: list[GatePolicyRuleCreate] = Field(min_length=1, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_rule_names(self):
        names = [rule.name.casefold() for rule in self.rules]
        if len(names) != len(set(names)):
            raise ValueError("rule names must be unique within a policy set")
        self.protected_actions = _unique_nonempty_strings(
            self.protected_actions, "protected_actions"
        )
        self.target_ref_prefixes = _canonical_target_refs(
            self.target_ref_prefixes, "target_ref_prefixes"
        )
        self.enforcement_principal_ids = _canonical_principal_refs(
            self.enforcement_principal_ids, "enforcement_principal_ids"
        )
        return self

    @field_validator("protected_actions")
    @classmethod
    def _safe_actions(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(SAFE_ACTION_PATTERN, value.strip()) is None for value in values):
            raise ValueError(
                "protected_actions must use stable action identifiers containing only "
                "letters, digits, dot, underscore, colon, slash, tilde, or hyphen"
            )
        return values


class GatePolicyActivate(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)


class GatePolicyRuleOut(_ORMOut):
    id: UUID
    name: str
    description: str | None
    priority: int
    enabled: bool
    action_on_failure: Literal["deny", "review"]
    applies_to_decision_types: list[str]
    applies_to_risk_levels: list[str]
    required_receipt_grade: ReceiptGrade | None
    require_trusted_issuer: bool
    require_sources_current: bool
    require_policy_attached: bool
    required_principal_scopes: list[str]
    minimum_approval_count: int
    required_approval_roles: list[str]
    allowed_approval_principal_types: list[str]
    maximum_approval_age_seconds: int | None
    require_information_barrier_match: bool
    block_untrusted_content: bool
    max_untrusted_content_score: int | None


class GatePolicySetOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    name: str
    version: str
    description: str | None
    status: Literal["draft", "active", "retired"]
    default_disposition: GateDisposition
    protected_actions: list[str]
    target_ref_prefixes: list[str]
    enforcement_principal_ids: list[str]
    maximum_permit_ttl_seconds: int
    created_by: str
    created_at: datetime
    activated_by: str | None
    activated_at: datetime | None
    retired_at: datetime | None
    policy_hash: str
    metadata: dict[str, Any] = Field(validation_alias="metadata_")
    rules: list[GatePolicyRuleOut] = Field(default_factory=list)


class GateReceiptContext(BaseModel):
    grade: ReceiptGrade | None = None
    receipt_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    issuer_id: UUID | None = None
    key_id: str | None = Field(default=None, max_length=255)
    # Optional full envelope used only for in-process verification. The Gate
    # persists a hash reference, never this potentially sensitive document.
    document: dict[str, Any] | None = None


class GateApproval(BaseModel):
    """Server-resolved approval used only inside the Gate evaluator."""

    principal_id: str = Field(min_length=1, max_length=512)
    role: str = Field(min_length=1, max_length=100)
    status: Literal["approved", "rejected", "pending"] = "approved"
    attestation_ref: str | None = Field(default=None, max_length=2048)
    principal_type: str | None = None
    auth_method: str | None = None
    attested_at: datetime | None = None


class GateApprovalAttestationCreate(BaseModel):
    action: str = Field(min_length=1, max_length=255)
    decision_id: UUID
    change_event_id: UUID | None = None
    policy_set_id: UUID
    target_ref: str = Field(min_length=1, max_length=2048)
    target_barrier_group: BarrierValue = Field(default=None, max_length=255)
    receipt_hash: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    status: Literal["approved", "rejected"] = "approved"
    statement: str | None = Field(default=None, min_length=1, max_length=50_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=1000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _future_expiry_and_evidence(self):
        if self.expires_at is not None:
            expiry = self.expires_at
            if expiry.tzinfo is None:
                raise ValueError("expires_at must include a timezone")
            if expiry <= datetime.now(expiry.tzinfo):
                raise ValueError("expires_at must be in the future")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence_refs must not contain duplicates")
        if any(not ref or len(ref) > 2048 for ref in self.evidence_refs):
            raise ValueError("each evidence reference must contain 1-2048 characters")
        return self


class GateApprovalAttestationSupersede(BaseModel):
    status: Literal["approved", "rejected", "revoked"]
    statement: str | None = Field(default=None, min_length=1, max_length=50_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=1000)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _valid_successor(self):
        if self.status == "revoked" and self.expires_at is not None:
            raise ValueError("revoked attestations cannot have expires_at")
        if self.expires_at is not None:
            expiry = self.expires_at
            if expiry.tzinfo is None:
                raise ValueError("expires_at must include a timezone")
            if expiry <= datetime.now(expiry.tzinfo):
                raise ValueError("expires_at must be in the future")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("evidence_refs must not contain duplicates")
        if any(not ref or len(ref) > 2048 for ref in self.evidence_refs):
            raise ValueError("each evidence reference must contain 1-2048 characters")
        return self


class GateApprovalAttestationOut(BaseModel):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    series_key: str
    sequence: int
    approval_principal_id: str
    attested_by: str
    principal_type: str | None
    attester_role: str
    auth_method: str
    credential_id: str | None
    status: Literal["approved", "rejected", "revoked"]
    action: str
    decision_id: UUID | None
    change_event_id: UUID | None
    policy_set_id: UUID
    policy_hash: str
    target_ref: str | None
    target_barrier_group: BarrierValue
    receipt_hash: str | None
    context_hash: str
    statement: str | None
    statement_hash: str | None
    evidence_refs: list[str]
    expires_at: datetime | None
    supersedes_id: UUID | None
    prior_attestation_hash: str | None
    attestation_hash: str
    attested_at: datetime


class UntrustedContentSignal(BaseModel):
    signal_type: str = Field(min_length=1, max_length=255)
    source: str | None = Field(default=None, max_length=2048)
    score: int = Field(ge=0, le=100)
    trusted: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class GateEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_id: str | None = Field(default=None, min_length=1, max_length=512)
    principal_scopes: list[str] = Field(default_factory=list, max_length=1000)
    principal_barrier_group: BarrierValue = Field(default=None, max_length=255)
    target_barrier_group: BarrierValue = Field(default=None, max_length=255)
    target_ref: str = Field(min_length=1, max_length=2048)
    action: str = Field(min_length=1, max_length=255, pattern=SAFE_ACTION_PATTERN)
    enforcement_principal_id: str = Field(
        min_length=1,
        max_length=512,
        pattern=CANONICAL_PRINCIPAL_REF_PATTERN,
        description=(
            "Exact canonical identity of the separate mediator that will redeem "
            "an allow permit"
        ),
    )
    permit_ttl_seconds: int = Field(ge=1, le=300)
    execution_request_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "SHA-256 of the mediator's canonical provider/tool request, including "
            "all security-relevant arguments"
        ),
    )
    decision_type: str | None = Field(default=None, max_length=100)
    risk_level: Literal["low", "medium", "high", "critical"] = "medium"
    decision_id: UUID
    change_event_id: UUID | None = None
    policy_set_id: UUID | None = None
    policy_name: str | None = Field(default=None, max_length=255)
    policy_version: str | None = Field(default=None, max_length=100)
    receipt: GateReceiptContext = Field(default_factory=GateReceiptContext)
    # Compatibility hints only. The HTTP route clears them, then derives source
    # currency and policy version from the linked immutable decision; a verified
    # receipt may supply policy version for an otherwise unlinked action.
    sources_current: bool | None = None
    attached_policy_version: str | None = Field(default=None, max_length=255)
    approval_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    untrusted_content_signals: list[UntrustedContentSignal] = Field(
        default_factory=list, max_length=1000
    )
    context: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _reject_free_form_approvals(cls, value):
        if isinstance(value, dict) and "approvals" in value:
            raise ValueError(
                "free-form approvals are not accepted; use immutable approval_ids"
            )
        return value

    @model_validator(mode="after")
    def _request_is_unambiguous(self):
        if len(self.approval_ids) != len(set(self.approval_ids)):
            raise ValueError("approval_ids must not contain duplicates")
        return self

    @field_validator("target_ref")
    @classmethod
    def _canonical_target_ref(cls, value: str) -> str:
        if not _is_canonical_target_ref(value):
            raise ValueError(
                "target_ref must be a canonical ASCII absolute resource URI"
            )
        return value


class GateFailureReason(BaseModel):
    code: str
    message: str
    rule_id: UUID | None = None
    rule_name: str | None = None
    action: Literal["deny", "review"]
    expected: Any = None
    actual: Any = None


class GateDecisionOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    policy_set_id: UUID
    policy_name: str
    policy_version: str
    policy_hash: str
    principal_id: str
    action: str
    target_ref: str
    enforcement_principal_id: str | None
    execution_request_hash: str | None
    decision_id: UUID | None
    change_event_id: UUID | None
    receipt_hash: str | None
    disposition: GateDisposition
    reasons: list[dict[str, Any]]
    applied_rules: list[dict[str, Any]]
    input_snapshot: dict[str, Any]
    request_hash: str
    evaluation_hash: str
    evaluated_at: datetime


class GateExecutionPermitIssued(BaseModel):
    """Sensitive response-only capability returned by POST /gate/evaluate once."""

    permit_id: UUID
    evaluation_id: UUID
    enforcement_principal_id: str
    action: str
    target_ref: str
    decision_id: UUID
    execution_request_hash: str
    issued_at: datetime
    expires_at: datetime
    token: str = Field(
        min_length=59,
        max_length=59,
        pattern=r"^lians_permit_v1_[A-Za-z0-9_-]{43}$",
        repr=False,
        json_schema_extra={"readOnly": True, "x-sensitive": True},
    )


class GateEvaluationOut(GateDecisionOut):
    """Evaluation response; GET/list contracts deliberately use GateDecisionOut."""

    execution_permit: GateExecutionPermitIssued | None = None


class GateExecutionPermitConsume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permit_id: UUID
    # Deliberately unconstrained here: format/length errors must reach the same
    # non-oracular redemption path as unknown, expired, or replayed permits.
    token: SecretStr
    action: str = Field(min_length=1, max_length=255, pattern=SAFE_ACTION_PATTERN)
    target_ref: str = Field(min_length=1, max_length=2048)
    decision_id: UUID
    execution_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("target_ref")
    @classmethod
    def _canonical_target_ref(cls, value: str) -> str:
        if not _is_canonical_target_ref(value):
            raise ValueError(
                "target_ref must be a canonical ASCII absolute resource URI"
            )
        return value


class GateExecutionPermitConsumptionOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    permit_id: UUID
    evaluation_id: UUID
    policy_set_id: UUID
    decision_id: UUID
    consuming_principal_id: str
    action: str
    target_ref: str
    execution_request_hash: str
    grant_hash: str
    consumed_at: datetime
    consumption_hash: str


class InvestigationCaseCreate(BaseModel):
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=50_000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    owner_principal: str | None = Field(default=None, max_length=255)
    barrier_group: BarrierValue = Field(default=None, max_length=255)
    decision_id: UUID | None = None
    change_event_id: UUID | None = None
    gate_decision_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class InvestigationCaseUpdate(BaseModel):
    expected_updated_at: datetime
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    owner_principal: str | None = Field(default=None, max_length=255)
    status: Literal["open", "in_review", "remediating", "resolved"] | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    resolution_summary: str | None = Field(default=None, max_length=50_000)


class InvestigationCaseOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    title: str
    description: str | None
    severity: str
    status: str
    owner_principal: str | None
    decision_id: UUID | None
    change_event_id: UUID | None
    gate_decision_id: UUID | None
    opened_by: str
    opened_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    resolution_summary: str | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_")


class RemediationTaskCreate(BaseModel):
    expected_case_updated_at: datetime
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=50_000)
    owner_principal: str | None = Field(default=None, max_length=255)
    due_at: datetime | None = None
    decision_id: UUID | None = None
    change_event_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RemediationTaskUpdate(BaseModel):
    expected_updated_at: datetime
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    owner_principal: str | None = Field(default=None, max_length=255)
    status: Literal["pending", "in_progress", "blocked", "cancelled"] | None = None
    due_at: datetime | None = None


class RemediationTaskOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    case_id: UUID
    title: str
    description: str | None
    status: str
    owner_principal: str | None
    due_at: datetime | None
    decision_id: UUID | None
    change_event_id: UUID | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    metadata: dict[str, Any] = Field(validation_alias="metadata_")


class ClosureAttestationCreate(BaseModel):
    expected_updated_at: datetime
    actor_id: str | None = Field(default=None, min_length=1, max_length=255)
    statement: str = Field(min_length=1, max_length=50_000)
    evidence_refs: list[str] = Field(min_length=1, max_length=1000)
    resolution_summary: str | None = Field(default=None, max_length=50_000)


class ClosureAttestationOut(_ORMOut):
    id: UUID
    namespace: str
    barrier_group: BarrierValue
    resource_type: Literal["case", "task"]
    resource_id: UUID
    attested_by: str
    statement: str | None
    statement_hash: str
    hash_version: Literal[1, 2]
    evidence_refs: list[str]
    decision_id: UUID | None
    change_event_id: UUID | None
    attestation_hash: str
    attested_at: datetime


class AttestedClosureResult(BaseModel):
    resource_type: Literal["case", "task"]
    resource_id: UUID
    status: Literal["closed"]
    attestation: ClosureAttestationOut
