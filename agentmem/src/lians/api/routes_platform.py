"""Standards discovery, capability negotiation, and operator readiness."""

# FastAPI intentionally evaluates Depends marker objects in signatures.
# ruff: noqa: B008

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit_chain import audit_append_boundary_status
from ..config import get_settings
from ..connection_security import validate_production_data_transports
from ..control_models import (
    ControlClosureAttestation,
    GatePolicySet,
    ReceiptIssuer,
    TrustedReceiptKey,
)
from ..db import get_db
from ..enterprise_models import ScimTenantConfig
from ..identity_models import IdentityBinding, TrustedIdentityProvider
from ..models import NamespacePolicy
from ..platform_schemas import (
    LiansDiscoveryDocument,
    PlatformCapabilities,
    PlatformReadiness,
    ReadinessCheck,
)
from ..receipt_signer import (
    ReceiptSignerConfigurationError,
    ReceiptSigningUnavailable,
    get_receipt_signer,
    receipt_signer_identity,
    validate_receipt_signer_configuration,
)
from ..subject_privacy import (
    SubjectReferenceConfigurationError,
    validate_subject_reference_configuration,
)
from .deps import AuthContext, get_auth

router = APIRouter(tags=["platform"])

_DECISION_RECEIPT_CONFORMANCE_ARTIFACTS = frozenset(
    {
        "manifest.json",
        "manifest.schema.json",
        "manifest.schema.json",
        "mutation.schema.json",
        "fixtures/valid-signed.json",
        "fixtures/valid-unsigned.json",
        "mutations/protected-payload-tamper.json",
        "trust/test-only-ed25519-public-key.base64",
        "trust/wrong-test-only-ed25519-public-key.base64",
    }
)
_DECISION_RECEIPT_MAPPING_ARTIFACTS = frozenset(
    {
        "manifest.json",
        "manifest.schema.json",
        "opentelemetry-genai.md",
        "mcp.md",
        "a2a.md",
    }
)


def _discovery() -> LiansDiscoveryDocument:
    return LiansDiscoveryDocument(
        api_version="1",
        decision_receipt_version="0.1",
        universal_recorder_version="0.2",
        protocols=["lians.native", "otlp.genai", "mcp", "a2a"],
        authentication=["api_key", "oidc_bearer"],
        links={
            "openapi": "/openapi.json",
            "docs": "/docs",
            "decision_receipt_schema": "/specs/decision-receipt/v0.1/schema.json",
            "decision_receipt_conformance": (
                "/specs/decision-receipt/v0.1/conformance/manifest.json"
            ),
            "decision_receipt_mappings": ("/specs/decision-receipt/v0.1/mappings/manifest.json"),
            "recorder_schema": "/specs/universal-recorder/v0.2/envelope.schema.json",
            "recorder_event_schema": "/specs/universal-recorder/v0.2/event.schema.json",
            "recorder_index_job_schema": (
                "/specs/universal-recorder/v0.1/evidence-index-job.schema.json"
            ),
            "evaluation_attestation_schema": ("/specs/evaluation-attestation/v0.1/schema.json"),
            "release_attestation_schema": ("/specs/release-attestation/v0.1/schema.json"),
            "capabilities": "/v1/platform/capabilities",
            "readiness": "/v1/platform/readiness",
        },
    )


@router.get(
    "/.well-known/lians",
    response_model=LiansDiscoveryDocument,
    include_in_schema=False,
)
async def lians_discovery() -> LiansDiscoveryDocument:
    """Return a non-secret, cacheable protocol discovery document."""
    return _discovery()


def _spec_bytes(relative_path: str) -> bytes:
    package_candidate = resources.files("lians").joinpath("specs", *relative_path.split("/"))
    if package_candidate.is_file():
        return package_candidate.read_bytes()
    source_candidate = Path(__file__).resolve().parents[4] / "specs" / relative_path
    if source_candidate.is_file():
        return source_candidate.read_bytes()
    raise HTTPException(status_code=404, detail="Protocol specification is not packaged")


def _spec_response(
    request: Request,
    relative_path: str,
    *,
    media_type: str = "application/schema+json",
) -> Response:
    content = _spec_bytes(relative_path)
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/specs/decision-receipt/v0.1/schema.json", include_in_schema=False)
async def decision_receipt_schema(request: Request) -> Response:
    return _spec_response(request, "decision-receipt/v0.1/schema.json")


@router.get("/specs/universal-recorder/v0.1/envelope.schema.json", include_in_schema=False)
async def universal_recorder_schema(request: Request) -> Response:
    return _spec_response(request, "universal-recorder/v0.1/envelope.schema.json")


@router.get("/specs/universal-recorder/v0.1/event.schema.json", include_in_schema=False)
async def universal_recorder_event_schema(request: Request) -> Response:
    return _spec_response(request, "universal-recorder/v0.1/event.schema.json")


@router.get("/specs/universal-recorder/v0.2/envelope.schema.json", include_in_schema=False)
async def universal_recorder_v02_schema(request: Request) -> Response:
    return _spec_response(request, "universal-recorder/v0.2/envelope.schema.json")


@router.get("/specs/universal-recorder/v0.2/event.schema.json", include_in_schema=False)
async def universal_recorder_v02_event_schema(request: Request) -> Response:
    return _spec_response(request, "universal-recorder/v0.2/event.schema.json")


@router.get("/specs/evaluation-attestation/v0.1/schema.json", include_in_schema=False)
async def evaluation_attestation_schema(request: Request) -> Response:
    return _spec_response(request, "evaluation-attestation/v0.1/schema.json")


@router.get("/specs/release-attestation/v0.1/schema.json", include_in_schema=False)
async def release_attestation_schema(request: Request) -> Response:
    return _spec_response(request, "release-attestation/v0.1/schema.json")


@router.get(
    "/specs/universal-recorder/v0.1/evidence-index-job.schema.json",
    include_in_schema=False,
)
async def universal_recorder_index_job_schema(request: Request) -> Response:
    return _spec_response(
        request,
        "universal-recorder/v0.1/evidence-index-job.schema.json",
    )


@router.get(
    "/specs/decision-receipt/v0.1/conformance/{artifact_path:path}",
    include_in_schema=False,
)
async def decision_receipt_conformance_artifact(
    request: Request,
    artifact_path: str,
) -> Response:
    """Serve only the immutable, language-neutral public conformance vectors."""

    if artifact_path not in _DECISION_RECEIPT_CONFORMANCE_ARTIFACTS:
        raise HTTPException(status_code=404, detail="Conformance artifact not published")
    media_type = (
        "application/json" if artifact_path.endswith(".json") else "text/plain; charset=utf-8"
    )
    return _spec_response(
        request,
        f"decision-receipt/v0.1/conformance/{artifact_path}",
        media_type=media_type,
    )


@router.get(
    "/specs/decision-receipt/v0.1/mappings/{mapping_name}",
    include_in_schema=False,
)
async def decision_receipt_mapping(
    request: Request,
    mapping_name: str,
) -> Response:
    """Serve the immutable, allowlisted standards-to-receipt mappings."""

    if mapping_name not in _DECISION_RECEIPT_MAPPING_ARTIFACTS:
        raise HTTPException(status_code=404, detail="Receipt mapping not published")
    return _spec_response(
        request,
        f"decision-receipt/v0.1/mappings/{mapping_name}",
        media_type=(
            "application/json" if mapping_name.endswith(".json") else "text/markdown; charset=utf-8"
        ),
    )


@router.get("/v1/platform/capabilities", response_model=PlatformCapabilities)
async def platform_capabilities(
    auth: AuthContext = Depends(get_auth),
) -> PlatformCapabilities:
    auth.require("read")
    settings = get_settings()
    try:
        signer_config = validate_receipt_signer_configuration(settings)
        signer_enabled = signer_config.enabled
        signer_provider = signer_config.provider if signer_enabled else None
    except ReceiptSignerConfigurationError:
        signer_enabled = False
        signer_provider = None
    try:
        subject_reference_key_configured = validate_subject_reference_configuration(settings)
    except SubjectReferenceConfigurationError:
        subject_reference_key_configured = False
    return PlatformCapabilities(
        generated_at=datetime.now(UTC),
        namespace=auth.namespace,
        principal_type=auth.principal_type,
        authentication_method=auth.auth_method,
        information_barrier_scoped=auth.barrier_group is not None,
        components={
            "recorder": {
                "version": "0.2",
                "protocols": ["lians.native", "otlp.genai", "mcp", "a2a"],
                "native_sdk_hooks": [
                    "anthropic",
                    "anthropic_managed_agents_webhook",
                    "google_adk",
                    "openai_agents",
                    "langchain",
                    "langgraph",
                    "crewai",
                ],
                "batch": True,
                "idempotent": True,
                "first_receipt_readiness": True,
                "capture_gap_disclosure": True,
                "durable_fixed_snapshot_backlinking": True,
                "deferred_coverage_fail_closed": True,
                "run_event_exact_keyset_pagination": True,
                "run_event_bulk_audit_verification": True,
                "operational_measurement_provenance": True,
            },
            "agent_improvement": {
                "immutable_agent_versions": True,
                "repeated_trial_evaluations": True,
                "signed_evaluation_attestations": True,
                "exact_context_accounting": True,
                "permission_aware_tool_selection": True,
                "constrained_routing": True,
                "exact_cache_only": True,
                "advisory_optimization_only": True,
                "signed_release_attestations": True,
                "shadow_canary_rollback_evidence": True,
                "outcome_drift_learning_proposals": True,
                "automatic_production_change": False,
            },
            "decision_receipts": {
                "version": "0.1",
                "signed": signer_enabled,
                "signing_provider": signer_provider,
                "remote_private_key_isolation": signer_provider == "vault-transit",
                "hash_only_sources_by_default": True,
                "independent_conformance_vectors": True,
                "standards_mappings": ["opentelemetry_genai", "mcp", "a2a"],
            },
            "evidence_graph": {
                "normalized": True,
                "indexed_blast_radius": True,
                "legacy_gap_disclosure": True,
                "frozen_exhaustive_snapshots": True,
                "autonomous_multi_replica_processing": True,
                "caller_advance_compatibility": True,
                "durable_leases_and_poison_job_bounds": True,
                "atomic_candidate_limit": settings.decision_evidence_candidate_limit,
                "atomic_candidate_bytes_limit": (settings.decision_evidence_candidate_bytes_limit),
            },
            "gate": {
                "versioned_policies": True,
                "immutable_evaluations": True,
                "context_bound_approval_attestations": True,
                "trusted_receipt_registry": True,
                "single_use_execution_permits": True,
                "identity_bound_policy_routing": True,
                "enforcement_mediator": True,
            },
            "investigator": {
                "priority_queue": True,
                "decision_reconstruction": True,
                "cases_and_remediation": True,
                "attested_closure": True,
            },
            "enterprise": {
                "oidc": True,
                "scim_2_0": True,
                "rbac": True,
                "information_barriers": True,
                "workload_api_keys": True,
            },
            "namespace_governance": {
                "server_owned_processing_region": True,
                "capture_mode_policy": True,
                "atomic_daily_quotas": True,
                "immutable_policy_revisions": True,
            },
            "mutation_safety": {
                "transactional_idempotency": True,
                "body_bound_replay_claims": True,
                "optimistic_concurrency": True,
                "one_time_secret_replay_rejected": True,
            },
            "integration_outbox": {
                "transactional_enqueue": True,
                "multi_replica_leasing": True,
                "bounded_retry": True,
                "signed_webhook_delivery": True,
                "reconciliation": True,
            },
            "operations": {
                "bounded_cardinality_metrics": True,
                "durable_inventory_metrics": True,
                "slo_alert_rules": True,
                "retention_leader_election": True,
                "durable_metering": True,
                "protected_unit_metering": {
                    "authoritative_decision_creation": True,
                    "successful_gate_permit_consumption": True,
                    "transactional_with_source_and_audit": True,
                },
                "compatibility_memory_metering": True,
            },
            "master_key_encryption": {
                "sealed_text_format": "v2",
                "subject_key_envelope_format": "v2",
                "bounded_dual_key_reads": True,
                "offline_transactional_rewrap": True,
            },
        },
        standards={
            "opentelemetry": {
                "transport": ["otlp/http+json", "otlp/http+protobuf"],
                "genai_semantic_conventions": True,
            },
            "mcp": {"mapping": "v0.1"},
            "a2a": {"mapping": "v0.1"},
            "scim": {"version": "2.0"},
            "oidc": {"jwt_verification": True, "jwks_rotation": True},
        },
        privacy={
            "recorder_default": "hash_only",
            "otlp_capture_mode": settings.otlp_capture_mode,
            "full_capture_enabled": settings.recorder_allow_full_capture,
            "secret_fields_always_redacted": True,
            "source_content_export_default": "hash_only",
            "tenant_scoped_subject_references": True,
            "subject_reference_key_configured": subject_reference_key_configured,
            "deployment_region": settings.deployment_region.strip().lower(),
        },
        links={
            "investigator_queue": "/v1/investigator/queue",
            "recorder": "/v1/recorder/events",
            "gate": "/v1/control/gate/evaluate",
            "decisions": "/v1/decisions",
            "openapi": "/openapi.json",
            "effective_governance": "/v1/governance/effective",
            "integration_readiness": "/v1/integrations/readiness",
            "decision_receipt_conformance": (
                "/specs/decision-receipt/v0.1/conformance/manifest.json"
            ),
            "decision_receipt_mappings": ("/specs/decision-receipt/v0.1/mappings/manifest.json"),
            "evaluation_attestation_schema": ("/specs/evaluation-attestation/v0.1/schema.json"),
            "release_attestation_schema": ("/specs/release-attestation/v0.1/schema.json"),
        },
    )


async def _count(db: AsyncSession, model, *conditions) -> int:
    return int(
        (await db.execute(select(func.count()).select_from(model).where(*conditions))).scalar_one()
    )


@router.get("/v1/platform/readiness", response_model=PlatformReadiness)
async def platform_readiness(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> PlatformReadiness:
    """Assess configuration without exposing credentials or claiming certification."""
    auth.require("admin")
    settings = get_settings()
    is_production = settings.deployment_environment.strip().lower() in {"prod", "production"}
    try:
        subject_reference_key_configured = validate_subject_reference_configuration(settings)
        subject_reference_error = None
    except SubjectReferenceConfigurationError as exc:
        subject_reference_key_configured = False
        subject_reference_error = str(exc)
    transport_failures = validate_production_data_transports(settings)
    audit_boundary = await audit_append_boundary_status(db)
    signer_error: str | None = None
    try:
        signer_identity = receipt_signer_identity(await get_receipt_signer())
    except (ReceiptSignerConfigurationError, ReceiptSigningUnavailable) as exc:
        signer_identity = receipt_signer_identity(None)
        signer_error = str(exc)
    active_gate_policies = await _count(
        db,
        GatePolicySet,
        GatePolicySet.namespace == auth.namespace,
        GatePolicySet.status == "active",
    )
    trusted_issuers = await _count(
        db,
        ReceiptIssuer,
        ReceiptIssuer.namespace == auth.namespace,
        ReceiptIssuer.status == "active",
        ReceiptIssuer.revoked_at.is_(None),
    )
    trusted_keys = await _count(
        db,
        TrustedReceiptKey,
        TrustedReceiptKey.namespace == auth.namespace,
        TrustedReceiptKey.status == "active",
        TrustedReceiptKey.revoked_at.is_(None),
    )
    identity_providers = int(
        (
            await db.execute(
                select(func.count(func.distinct(TrustedIdentityProvider.id)))
                .select_from(TrustedIdentityProvider)
                .join(
                    IdentityBinding,
                    IdentityBinding.provider_id == TrustedIdentityProvider.id,
                )
                .where(
                    IdentityBinding.namespace == auth.namespace,
                    IdentityBinding.enabled.is_(True),
                    IdentityBinding.revoked_at.is_(None),
                    TrustedIdentityProvider.enabled.is_(True),
                    TrustedIdentityProvider.revoked_at.is_(None),
                )
            )
        ).scalar_one()
    )
    scim_tenants = await _count(
        db,
        ScimTenantConfig,
        ScimTenantConfig.namespace == auth.namespace,
        ScimTenantConfig.enabled.is_(True),
        ScimTenantConfig.revoked_at.is_(None),
    )
    namespace_policy = await db.get(NamespacePolicy, auth.namespace)
    from ..impact_assessment_service import impact_assessment_inventory
    from ..integration_service import integration_inventory, integration_worker_status
    from ..metering import metering_inventory
    from ..recorder_index_service import recorder_index_inventory
    from ..scim_reconciliation_service import scim_reconciliation_inventory

    integration_state = await integration_inventory(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
    )
    integration_worker_healthy, _ = integration_worker_status()
    metering_state = await metering_inventory(db, namespace=auth.namespace)
    impact_state = await impact_assessment_inventory(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
    )
    recorder_index_state = await recorder_index_inventory(db)
    scim_reconciliation_state = await scim_reconciliation_inventory(
        db,
        namespace=auth.namespace,
    )
    governance_active = bool(
        namespace_policy is not None and namespace_policy.governance_status == "active"
    )
    deployment_region = settings.deployment_region.strip().lower()
    explicit_region = deployment_region not in {
        "",
        "local",
        "unknown",
        "unset",
        "configure-me",
    }
    region_allowed = bool(
        not governance_active
        or namespace_policy.allowed_processing_regions is None
        or deployment_region in set(namespace_policy.allowed_processing_regions)
    )
    configured_quota_count = (
        sum(
            value is not None
            for value in (
                namespace_policy.recorder_events_daily_limit,
                namespace_policy.decision_records_daily_limit,
                namespace_policy.protected_actions_daily_limit,
                namespace_policy.memory_writes_daily_limit,
                namespace_policy.recalls_daily_limit,
                namespace_policy.estimated_ingest_bytes_daily_limit,
            )
        )
        if governance_active
        else 0
    )
    global_capture_modes = {"metadata_only", "hash_only"}
    if settings.recorder_allow_full_capture:
        global_capture_modes.add("full")
    effective_capture_modes = (
        global_capture_modes.intersection(namespace_policy.allowed_recorder_capture_modes)
        if governance_active and namespace_policy.allowed_recorder_capture_modes is not None
        else global_capture_modes
    )
    from ..kms import get_master_keyring, validate_keyring_configuration

    keyring_configuration_error: str | None = None
    try:
        configured_current_id, configured_previous_id = validate_keyring_configuration(settings)
        keyring = get_master_keyring()
        if keyring.current.key_id != configured_current_id:
            raise ValueError("Loaded current key id differs from validated configuration")
    except (RuntimeError, ValueError) as exc:
        configured_current_id = "invalid-or-unconfigured"
        configured_previous_id = None
        keyring_configuration_error = str(exc)

    plaintext_closures = await _count(
        db,
        ControlClosureAttestation,
        ControlClosureAttestation.namespace == auth.namespace,
        ControlClosureAttestation.statement.is_not(None),
    )
    rotation_state = (
        (
            await db.execute(
                text(
                    "SELECT current_key_id, previous_key_id, status, "
                    "legacy_values_remaining, previous_values_remaining, "
                    "unknown_values_remaining, plaintext_closures_remaining "
                    "FROM master_key_rotation_state WHERE singleton_id = 1"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    write_fence = (
        (
            await db.execute(
                text(
                    "SELECT phase, current_key_id, previous_key_id, generation "
                    "FROM master_key_write_fence_state WHERE singleton_id = 1"
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    fence_prepared_matches = bool(
        write_fence is not None
        and write_fence["phase"] == "prepared"
        and write_fence["current_key_id"] == configured_current_id
        and write_fence["previous_key_id"] == configured_previous_id
        and configured_previous_id is not None
    )
    fence_narrowed_matches = bool(
        write_fence is not None
        and write_fence["phase"] == "narrowed"
        and write_fence["current_key_id"] == configured_current_id
        and write_fence["previous_key_id"] is None
    )
    rotation_checkpoint_matches = bool(
        rotation_state is not None
        and rotation_state["current_key_id"] == configured_current_id
        and (
            rotation_state["previous_key_id"] == configured_previous_id
            or configured_previous_id is None
        )
        and rotation_state["status"] == "verified"
        and int(rotation_state["legacy_values_remaining"]) == 0
        and int(rotation_state["previous_values_remaining"]) == 0
        and int(rotation_state["unknown_values_remaining"]) == 0
        and int(rotation_state["plaintext_closures_remaining"]) == 0
        and plaintext_closures == 0
        and fence_narrowed_matches
    )

    checks: list[ReadinessCheck] = []

    def check(
        check_id: str, passed: bool, message: str, required_for: list[str], *, warning: bool = False
    ):
        checks.append(
            ReadinessCheck(
                id=check_id,
                status="pass" if passed else "warning" if warning else "fail",
                message=message,
                required_for=required_for,
            )
        )

    check(
        "receipt.signing_key",
        bool(signer_identity["configured"]) and signer_error is None,
        (
            "Decision Receipts use a validated "
            f"{signer_identity['provider']} Ed25519 signer with key identifier "
            f"{signer_identity['key_id']!r}, pinned version "
            f"{signer_identity['key_version']!r}, and public-key fingerprint "
            f"{str(signer_identity['public_key_sha256'])[:16]}."
            if signer_identity["configured"] and signer_error is None
            else "The Decision Receipt signer is unavailable or not safely configured."
        ),
        ["production_baseline", "trusted_receipts"],
    )
    check(
        "security.cors",
        "*" not in {item.strip() for item in settings.cors_origins.split(",")},
        "CORS is restricted to explicit origins.",
        ["production_baseline"],
    )
    check(
        "security.rate_limit",
        settings.rate_limit_backend_failure_mode.strip().lower() != "open",
        "Rate limiting has a fail-safe Redis outage posture.",
        ["production_baseline"],
    )
    check(
        "security.data_transport",
        not transport_failures,
        (
            "PostgreSQL and Redis use peer-verifying encrypted transport and the "
            "database pool has bounded capacity."
            if not transport_failures
            else "Stateful dependency transport/pool checks failed: "
            + "; ".join(transport_failures)
        ),
        ["production_baseline", "transport_security", "capacity_control"],
        warning=not is_production,
    )
    check(
        "security.rls",
        settings.rls_barriers_enabled,
        "PostgreSQL information-barrier RLS is enabled.",
        ["production_baseline", "enterprise_identity"],
    )
    failed_audit_checks = sorted(
        name for name, passed in audit_boundary["checks"].items() if not passed
    )
    check(
        "audit.append_boundary",
        bool(audit_boundary["enforced"]),
        (
            "The runtime login and fixed capability role are non-owner, least-privilege "
            "identities, and audit events append only through the database-owned v3 "
            "function; direct mutation, owner-role assumption, public execution, and "
            "RLS bypass are absent."
            if audit_boundary["enforced"]
            else "Audit append boundary checks failed: " + ", ".join(failed_audit_checks)
        ),
        ["production_baseline", "audit_integrity"],
        warning=not is_production,
    )
    check(
        "security.api_surface",
        settings.api_surface == "public",
        (
            "Break-glass administration is absent from this public API surface."
            if settings.api_surface == "public"
            else "This process exposes the combined development API surface."
        ),
        ["production_baseline", "privileged_administration"],
        warning=not is_production,
    )
    check(
        "audit.merkle_durability",
        not settings.merkle_batch_enabled,
        (
            "Process-local Merkle batching is disabled until membership and anchor "
            "publication are transactionally durable."
        ),
        ["production_baseline", "audit_integrity"],
        warning=not is_production,
    )
    check(
        "privacy.capture",
        settings.otlp_capture_mode in {"metadata_only", "hash_only"}
        and not settings.recorder_allow_full_capture,
        "Evidence ingestion defaults to minimized content capture.",
        ["production_baseline"],
        warning=settings.recorder_allow_full_capture,
    )
    check(
        "privacy.subject_references",
        subject_reference_key_configured,
        (
            "A dedicated key protects tenant-scoped subject references."
            if subject_reference_error is None
            else subject_reference_error
        ),
        ["production_baseline", "privacy"],
        warning=not is_production,
    )
    check(
        "kms.external",
        settings.kms_provider.strip().lower() in {"aws", "azure", "vault"},
        "An external KMS/secret authority protects the master key.",
        ["production_baseline"],
        warning=not is_production,
    )
    check(
        "kms.key_version",
        keyring_configuration_error is None,
        (
            f"The loaded current master-key version is {configured_current_id!r}; "
            f"previous key configured: {configured_previous_id is not None}."
            if keyring_configuration_error is None
            else "The master-key identifier/keyring configuration is invalid."
        ),
        ["production_baseline", "key_rotation"],
    )
    if rotation_state is None:
        checks.append(
            ReadinessCheck(
                id="kms.rotation_checkpoint",
                status="not_configured",
                message=(
                    "No advisory-locked master-key inventory/rewrap checkpoint has been recorded."
                ),
                required_for=["key_rotation"],
            )
        )
    else:
        check(
            "kms.rotation_checkpoint",
            rotation_checkpoint_matches,
            (
                "The latest rotation checkpoint matches this bounded keyring and reports "
                "zero legacy, previous-key, unknown-key, and plaintext closure values."
                if rotation_checkpoint_matches
                else "The latest rotation checkpoint is stale, incomplete, or reports remaining values."
            ),
            ["key_rotation"],
            warning=True,
        )
    if write_fence is None:
        checks.append(
            ReadinessCheck(
                id="kms.write_fence",
                status="not_configured",
                message="No persistent master-key database write fence is active.",
                required_for=["key_rotation"],
            )
        )
    elif fence_prepared_matches:
        checks.append(
            ReadinessCheck(
                id="kms.write_fence",
                status="warning",
                message=(
                    "The persistent write fence is prepared for exactly the configured "
                    "current and previous key identifiers; final narrowing is pending."
                ),
                required_for=["key_rotation"],
            )
        )
    else:
        check(
            "kms.write_fence",
            fence_narrowed_matches,
            (
                "The persistent write fence permits only the configured current key identifier."
                if fence_narrowed_matches
                else "The persistent write fence does not match the configured keyring."
            ),
            ["key_rotation"],
        )
    check(
        "deployment.region",
        explicit_region,
        (
            f"Server processing region is explicitly configured as {deployment_region!r}."
            if explicit_region
            else "Server processing region is still a development placeholder."
        ),
        ["production_baseline", "data_residency"],
        warning=not is_production,
    )
    if governance_active:
        check(
            "governance.region_policy",
            region_allowed,
            (
                "The active namespace policy allows this server processing region."
                if region_allowed
                else "The active namespace policy denies this server processing region."
            ),
            ["namespace_governance", "data_residency"],
        )
    else:
        checks.append(
            ReadinessCheck(
                id="governance.region_policy",
                status="not_configured",
                message=(
                    "No active namespace governance policy is enforcing residency or daily quotas."
                ),
                required_for=["namespace_governance", "data_residency"],
            )
        )
    if governance_active:
        check(
            "governance.capture_policy",
            bool(effective_capture_modes),
            (
                "The namespace capture policy permits at least one globally enabled mode."
                if effective_capture_modes
                else "The namespace capture policy and global deployment policy have no common mode."
            ),
            ["namespace_governance", "universal_recorder"],
        )
    else:
        checks.append(
            ReadinessCheck(
                id="governance.capture_policy",
                status="not_configured",
                message="Recorder capture uses the global deployment policy.",
                required_for=["namespace_governance", "universal_recorder"],
            )
        )
    if governance_active:
        check(
            "governance.daily_quotas",
            configured_quota_count > 0,
            (
                f"The active policy configures {configured_quota_count} of 6 daily quota dimensions."
                if configured_quota_count
                else "The active policy has no finite daily quotas; all usage dimensions are unlimited."
            ),
            ["namespace_governance", "capacity_control"],
            warning=True,
        )
    else:
        checks.append(
            ReadinessCheck(
                id="governance.daily_quotas",
                status="not_configured",
                message="No active namespace policy is enforcing daily usage quotas.",
                required_for=["namespace_governance", "capacity_control"],
            )
        )
    check(
        "retention.scheduler",
        settings.retention_prune_interval_hours > 0,
        (
            "The advisory-locked retention scheduler is enabled."
            if settings.retention_prune_interval_hours > 0
            else "Automated retention enforcement is disabled."
        ),
        ["production_baseline", "data_lifecycle"],
    )
    check(
        "observability.metrics",
        settings.metrics_enabled and settings.observability_refresh_seconds > 0,
        (
            "Bounded-cardinality request and durable-inventory metrics are enabled."
            if settings.metrics_enabled and settings.observability_refresh_seconds > 0
            else "Operational metrics or the durable inventory refresher are disabled."
        ),
        ["production_baseline", "operations"],
    )
    check(
        "impact.autonomous_worker",
        bool(impact_state["worker_enabled"] and impact_state["worker_healthy"]),
        (
            "The leased exhaustive impact worker is healthy and advances frozen "
            "snapshots autonomously."
            if impact_state["worker_enabled"] and impact_state["worker_healthy"]
            else "Autonomous exhaustive impact processing is disabled or unhealthy."
        ),
        ["production_baseline", "change_impact", "operations"],
    )
    check(
        "recorder.durable_index_worker",
        bool(recorder_index_state["worker_enabled"] and recorder_index_state["worker_healthy"]),
        (
            "The leased Recorder evidence worker is healthy and advances exact "
            "fixed snapshots without partial completeness claims."
            if recorder_index_state["worker_enabled"] and recorder_index_state["worker_healthy"]
            else "Durable Recorder evidence indexing is disabled or unhealthy."
        ),
        ["production_baseline", "universal_recorder", "operations"],
    )
    check(
        "identity.scim_reconciliation_worker",
        bool(
            scim_reconciliation_state["worker_enabled"]
            and scim_reconciliation_state["worker_healthy"]
        ),
        (
            "The leased SCIM binding worker is healthy and advances exact "
            "tenant-version snapshots behind the activation fence."
            if scim_reconciliation_state["worker_enabled"]
            and scim_reconciliation_state["worker_healthy"]
            else "Durable SCIM binding reconciliation is disabled or unhealthy."
        ),
        ["production_baseline", "enterprise_provisioning", "operations"],
    )
    if integration_state["active_destinations"] == 0:
        checks.append(
            ReadinessCheck(
                id="integrations.delivery",
                status="not_configured",
                message="No active tenant integration destination is configured.",
                required_for=["enterprise_integrations"],
            )
        )
    else:
        check(
            "integrations.delivery",
            bool(
                settings.integration_worker_enabled
                and not settings.airgap_mode
                and integration_worker_healthy
                and integration_state["dead_letter_deliveries"] == 0
            ),
            (
                "The durable integration worker is healthy and has no dead-letter deliveries."
                if integration_worker_healthy and integration_state["dead_letter_deliveries"] == 0
                else "Configured integration delivery is disabled, unhealthy, or has dead letters."
            ),
            ["enterprise_integrations"],
            warning=True,
        )
    metering_configured_for_namespace = bool(
        namespace_policy is not None and namespace_policy.stripe_customer_id
    )
    if not metering_configured_for_namespace:
        checks.append(
            ReadinessCheck(
                id="billing.metering",
                status="not_configured",
                message=(
                    "Protected-unit and compatibility usage metering is not configured "
                    "for this namespace."
                ),
                required_for=["usage_billing"],
            )
        )
    else:
        check(
            "billing.metering",
            bool(
                metering_state["delivery_enabled"]
                and metering_state["worker_healthy"]
                and metering_state["async_error_destination_configured"]
                and metering_state["dead_letter_events"] == 0
            ),
            (
                "Durable protected-unit metering, asynchronous error handling, and "
                "delivery are healthy."
                if metering_state["delivery_enabled"]
                and metering_state["worker_healthy"]
                and metering_state["async_error_destination_configured"]
                and metering_state["dead_letter_events"] == 0
                else "Namespace billing is configured but durable metering is incomplete or degraded."
            ),
            ["usage_billing"],
            warning=True,
        )
    check(
        "gate.active_policy",
        active_gate_policies > 0,
        "At least one runtime Gate policy is active in this namespace.",
        ["control_plane"],
    )
    check(
        "trust.registry",
        trusted_issuers > 0 and trusted_keys > 0,
        "The namespace has an active receipt issuer and trusted public key.",
        ["trusted_receipts"],
        warning=True,
    )
    check(
        "identity.oidc",
        identity_providers > 0,
        "At least one trusted OIDC provider is active.",
        ["enterprise_identity"],
        warning=True,
    )
    check(
        "identity.scim",
        scim_tenants > 0,
        "At least one SCIM tenant is active.",
        ["enterprise_provisioning"],
        warning=True,
    )
    baseline_ids = {
        "receipt.signing_key",
        "audit.append_boundary",
        "audit.merkle_durability",
        "security.api_surface",
        "security.cors",
        "security.rate_limit",
        "security.data_transport",
        "security.rls",
        "privacy.capture",
        "privacy.subject_references",
        "retention.scheduler",
        "observability.metrics",
        "impact.autonomous_worker",
        "recorder.durable_index_worker",
        "identity.scim_reconciliation_worker",
        "kms.external",
        "kms.key_version",
        "deployment.region",
    }
    production_ready = all(item.status == "pass" for item in checks if item.id in baseline_ids)
    control_ready = active_gate_policies > 0
    identity_ready = identity_providers > 0
    hard_failures = [item for item in checks if item.status == "fail"]
    status = (
        "ready"
        if production_ready and control_ready
        else "configuration_required"
        if hard_failures
        else "degraded"
    )
    return PlatformReadiness(
        generated_at=datetime.now(UTC),
        namespace=auth.namespace,
        status=status,
        production_baseline_ready=production_ready,
        control_plane_ready=control_ready,
        enterprise_identity_ready=identity_ready,
        checks=checks,
        inventory={
            "active_gate_policies": active_gate_policies,
            "trusted_receipt_issuers": trusted_issuers,
            "trusted_receipt_keys": trusted_keys,
            "active_identity_providers": identity_providers,
            "active_scim_tenants": scim_tenants,
            "active_integration_destinations": integration_state["active_destinations"],
            "integration_pending_deliveries": integration_state["pending_deliveries"],
            "integration_retry_deliveries": integration_state["retry_deliveries"],
            "integration_leased_deliveries": integration_state["leased_deliveries"],
            "integration_dead_letter_deliveries": integration_state["dead_letter_deliveries"],
            "metering_pending_events": metering_state["pending_events"],
            "metering_retry_events": metering_state["retry_events"],
            "metering_leased_events": metering_state["leased_events"],
            "metering_dead_letter_events": metering_state["dead_letter_events"],
            "impact_pending_jobs": impact_state["pending_jobs"],
            "impact_running_jobs": impact_state["running_jobs"],
            "impact_completed_jobs": impact_state["completed_jobs"],
            "impact_failed_jobs": impact_state["failed_jobs"],
            "impact_active_leases": impact_state["active_leases"],
            "impact_retry_wait_jobs": impact_state["retry_wait_jobs"],
            "recorder_index_pending_jobs": int(recorder_index_state["counts"].get("pending", 0)),
            "recorder_index_running_jobs": int(recorder_index_state["counts"].get("running", 0)),
            "recorder_index_completed_jobs": int(
                recorder_index_state["counts"].get("completed", 0)
            ),
            "recorder_index_failed_jobs": int(recorder_index_state["counts"].get("failed", 0)),
            "recorder_index_events_indexed": int(recorder_index_state["events_indexed"]),
            "recorder_index_snapshot_events": int(recorder_index_state["snapshot_events"]),
            "scim_reconciliation_pending_jobs": int(
                scim_reconciliation_state["counts"].get("pending", 0)
            ),
            "scim_reconciliation_running_jobs": int(
                scim_reconciliation_state["counts"].get("running", 0)
            ),
            "scim_reconciliation_completed_jobs": int(
                scim_reconciliation_state["counts"].get("completed", 0)
            ),
            "scim_reconciliation_failed_jobs": int(
                scim_reconciliation_state["counts"].get("failed", 0)
            ),
            "scim_reconciliation_superseded_jobs": int(
                scim_reconciliation_state["counts"].get("superseded", 0)
            ),
            "scim_reconciliation_users_reconciled": int(
                scim_reconciliation_state["users_reconciled"]
            ),
            "scim_reconciliation_snapshot_users": int(scim_reconciliation_state["snapshot_users"]),
            "active_namespace_governance_policies": int(governance_active),
            "configured_namespace_daily_quota_dimensions": configured_quota_count,
            "effective_namespace_recorder_capture_modes": len(effective_capture_modes),
            "master_key_previous_configured": int(configured_previous_id is not None),
            "master_key_rotation_checkpoint_present": int(rotation_state is not None),
            "master_key_rotation_checkpoint_matches": int(rotation_checkpoint_matches),
            "master_key_write_fence_active": int(write_fence is not None),
            "master_key_write_fence_prepared": int(fence_prepared_matches),
            "master_key_write_fence_narrowed": int(fence_narrowed_matches),
            "plaintext_closure_attestations_remaining": plaintext_closures,
            "audit_append_boundary_enforced": int(audit_boundary["enforced"]),
            "receipt_signer_configured": int(bool(signer_identity["configured"])),
            "receipt_signer_remote": int(signer_identity["provider"] == "vault-transit"),
            "database_pool_size_per_process": settings.database_pool_size,
            "database_max_overflow_per_process": settings.database_max_overflow,
            # Deployment-global counts are deliberately reduced to booleans so
            # one tenant cannot infer another tenant's encrypted-row volume.
            "master_key_legacy_values_remaining_detected": int(
                bool(rotation_state and rotation_state["legacy_values_remaining"])
            ),
            "master_key_previous_values_remaining_detected": int(
                bool(rotation_state and rotation_state["previous_values_remaining"])
            ),
            "master_key_unknown_values_remaining_detected": int(
                bool(rotation_state and rotation_state["unknown_values_remaining"])
            ),
        },
        disclosures=[
            "Readiness reports configuration state, not legal or regulatory certification.",
            "Backup restore drills, incident exercises, and external control evidence remain operator-run controls.",
            f"Authoritative server processing region: {deployment_region}.",
            (
                "Namespace governance is active and enforced transactionally."
                if governance_active
                else "No active namespace governance policy: global/unlimited legacy behavior applies."
            ),
            f"Configured finite daily quota dimensions: {configured_quota_count} of 6.",
            f"Current master-key version identifier: {configured_current_id}.",
            "Key identifiers and zero/nonzero deployment rotation/fence signals are disclosed; key material and cross-tenant row counts are never returned.",
        ],
    )
