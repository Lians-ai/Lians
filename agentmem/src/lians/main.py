import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .api.routes_admin import router as admin_router
from .api.routes_admissions import router as admissions_router
from .api.routes_audit import router as audit_router
from .api.routes_backtest import router as backtest_router
from .api.routes_compliance import router as compliance_router
from .api.routes_conflicts import router as conflicts_router
from .api.routes_control import router as control_router
from .api.routes_decisions import (
    receipts_router,
    records_router,
)
from .api.routes_decisions import (
    router as decisions_router,
)
from .api.routes_governance import (
    admin_router as governance_admin_router,
)
from .api.routes_governance import (
    router as governance_router,
)
from .api.routes_graph import router as graph_router
from .api.routes_identity import (
    admin_router as identity_admin_router,
)
from .api.routes_improvement import (
    agents_router,
    eval_router,
    optimization_router,
)
from .api.routes_identity import (
    router as identity_router,
)
from .api.routes_integrations import router as integrations_router
from .api.routes_investigator import router as investigator_router
from .api.routes_learning import drift_router, feedback_router, learning_router, outcomes_router
from .api.routes_memory import router as memory_router
from .api.routes_metrics import router as metrics_router
from .api.routes_otlp import router as otlp_router
from .api.routes_optimization import context_router, tools_router
from .api.routes_platform import router as platform_router
from .api.routes_privacy import router as privacy_router
from .api.routes_recorder import router as recorder_router
from .api.routes_release import deployments_router, releases_router, rollback_router
from .api.routes_runtime import cache_router, routing_router, runtime_router
from .api.routes_scim import admin_router as scim_admin_router
from .api.routes_scim import router as scim_router
from .api.routes_snapshot import router as snapshot_router
from .api.routes_supersessions import router as supersessions_router
from .api.routes_validmind import router as validmind_router
from .api.routes_webhooks import router as webhooks_router
from .api.routes_workload_credentials import router as workload_credentials_router
from .config import get_settings
from .db import get_db as _get_db
from .middleware import (
    AccessLogMiddleware,
    NoStoreResponseMiddleware,
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
    RequestIDMiddleware,
    RequestMetricsMiddleware,
    parse_trusted_proxy_cidrs,
    setup_logging,
)
from .migration_contract import SchemaContractError, assert_database_schema
from .pii import SubjectKeyDestroyedError
from .subject_privacy import SubjectReferenceError
from .telemetry import instrument_fastapi, instrument_sqlalchemy
from .version import __version__

logger = logging.getLogger("lians.startup")

_AIRGAP_SAFE_PROVIDERS = {"sentence-transformers", "bge-onnx", "local"}


_DEV_SECRETS = {
    "dev-admin-secret-change-in-prod",
    "dev-admin-secret-change-in-production",
}
_BACKGROUND_TASK_NAMES = frozenset(
    {
        "retention-scheduler",
        "metering-worker",
        "integration-outbox-worker",
        "impact-assessment-worker",
        "recorder-evidence-index-worker",
        "durable-observability-inventory",
        "llm-adjudication-worker",
    }
)
_BACKGROUND_WORKER_STARTUP_GRACE_SECONDS = 10.0


def _background_task_error_digest(exc: BaseException | None) -> str:
    """Create a non-sensitive operator correlation value for worker death."""

    error_type = (
        "unexpected_completion"
        if exc is None
        else f"{type(exc).__module__}.{type(exc).__qualname__}"
    )
    return hashlib.sha256(error_type.encode()).hexdigest()[:16]


def _track_background_task(app: FastAPI, task: asyncio.Task) -> asyncio.Task:
    """Make unexpected worker termination immediately visible to readiness.

    Every registered task is a process-lifetime loop. Returning normally is
    therefore as unhealthy as raising. Cancellation during orderly shutdown is
    the only non-failure terminal state.
    """

    if task.get_name() not in _BACKGROUND_TASK_NAMES:
        raise RuntimeError("Unregistered background task name")

    def _record_terminal_state(completed: asyncio.Task) -> None:
        if getattr(app.state, "background_tasks_shutting_down", False):
            return
        if completed.cancelled():
            digest = hashlib.sha256(b"unexpected_cancellation").hexdigest()[:16]
        else:
            error = completed.exception()
            digest = _background_task_error_digest(error)
        failures = getattr(app.state, "background_task_failures", None)
        if isinstance(failures, dict):
            failures[completed.get_name()] = digest
        logger.error(
            "Background task terminated unexpectedly",
            extra={
                "task_name": completed.get_name(),
                "error_digest": digest,
            },
        )

    task.add_done_callback(_record_terminal_state)
    return task


def _warn_insecure_secrets(settings) -> None:
    """
    Log prominent warnings when development placeholder secrets are detected.

    These defaults are intentionally weak so tests work without configuration.
    A production deployment using them is exploitable — any party that reads
    this source code can bypass admin authentication.
    """
    warnings = []
    if settings.api_surface in {"admin", "all"} and settings.admin_secret in _DEV_SECRETS:
        warnings.append(
            "ADMIN_SECRET is using the development default. "
            "The /v1/admin/* endpoints have no meaningful access control. "
            "Set a strong random value before deploying."
        )
    from .receipt_signer import (
        ReceiptSignerConfigurationError,
        validate_receipt_signer_configuration,
    )

    try:
        signer_config = validate_receipt_signer_configuration(settings)
    except ReceiptSignerConfigurationError:
        signer_config = None
        warnings.append(
            "Decision Receipt signer configuration is invalid; startup will refuse "
            "to publish or sign receipts."
        )
    if signer_config is not None and not signer_config.enabled:
        warnings.append(
            "A Decision Receipt signer is not configured. Decision Receipts remain "
            "hash-verifiable but will be unsigned and their completeness grade will name "
            "the missing deployment signature."
        )
    from .subject_privacy import (
        SubjectReferenceConfigurationError,
        validate_subject_reference_configuration,
    )

    try:
        subject_reference_configured = validate_subject_reference_configuration(settings)
    except SubjectReferenceConfigurationError:
        subject_reference_configured = False
        warnings.append(
            "Subject-reference configuration is invalid; production startup will refuse traffic."
        )
    if not subject_reference_configured:
        warnings.append(
            "SUBJECT_REFERENCE_KEY is unset; the stable development-only subject "
            "reference key is active."
        )
    for msg in warnings:
        logger.warning("SECURITY: %s", msg)


def _validate_production_secrets(settings) -> None:
    """Fail closed instead of starting production with published secrets."""
    if settings.deployment_environment.strip().lower() not in {"prod", "production"}:
        return
    errors = []
    if settings.api_surface in {"admin", "all"} and (
        settings.admin_secret in _DEV_SECRETS or len(settings.admin_secret) < 32
    ):
        errors.append("ADMIN_SECRET must be a random value of at least 32 characters")
    from .receipt_signer import (
        ReceiptSignerConfigurationError,
        validate_receipt_signer_configuration,
    )

    try:
        validate_receipt_signer_configuration(settings)
    except ReceiptSignerConfigurationError as exc:
        errors.append(str(exc))
    from .subject_privacy import (
        SubjectReferenceConfigurationError,
        validate_subject_reference_configuration,
    )

    try:
        validate_subject_reference_configuration(settings)
    except SubjectReferenceConfigurationError as exc:
        errors.append(str(exc))
    if settings.api_surface == "all":
        errors.append(
            "API_SURFACE=all is forbidden in production; run separate public and "
            "network-isolated admin processes"
        )
    if settings.merkle_batch_enabled:
        errors.append(
            "MERKLE_BATCH_ENABLED is unsupported in production until window membership "
            "and anchor publication are transactionally durable"
        )
    embedding_provider = settings.embedding_provider.strip().lower()
    if embedding_provider not in {
        "voyage",
        "openai",
        "sentence-transformers",
        "bge-onnx",
    }:
        errors.append(
            "EMBEDDING_PROVIDER must be voyage, openai, sentence-transformers, "
            "or bge-onnx "
            "in production; local is deterministic test-only"
        )
    elif embedding_provider == "voyage" and not settings.voyage_api_key.strip():
        errors.append("VOYAGE_API_KEY is required when EMBEDDING_PROVIDER=voyage")
    elif embedding_provider == "openai" and not settings.openai_api_key.strip():
        errors.append("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
    elif embedding_provider == "bge-onnx" and not settings.bge_onnx_artifact_dir.strip():
        errors.append(
            "BGE_ONNX_ARTIFACT_DIR is required when EMBEDDING_PROVIDER=bge-onnx"
        )
    if (
        settings.supersession_llm_stage or settings.graph_extract_llm or settings.auto_metadata_llm
    ) and not settings.anthropic_api_key.strip():
        errors.append("ANTHROPIC_API_KEY is required when an Anthropic-backed LLM stage is enabled")
    origins = {o.strip() for o in settings.cors_origins.split(",") if o.strip()}
    if "*" in origins:
        errors.append("CORS_ORIGINS must list trusted origins instead of '*'")
    if settings.rate_limit_backend_failure_mode.strip().lower() == "open":
        errors.append("RATE_LIMIT_BACKEND_FAILURE_MODE=open is forbidden in production")
    if settings.rate_limit_backend_failure_mode.strip().lower() not in {"local", "deny", "open"}:
        errors.append("RATE_LIMIT_BACKEND_FAILURE_MODE must be local, deny, or open")
    if not 1 <= settings.rate_limit_per_minute <= 1_000_000:
        errors.append("RATE_LIMIT_PER_MINUTE must be between 1 and 1000000")
    if not 1 <= settings.rate_limit_admin_per_minute <= 1_000_000:
        errors.append("RATE_LIMIT_ADMIN_PER_MINUTE must be between 1 and 1000000")
    if not 1 <= settings.rate_limit_network_multiplier <= 1_000:
        errors.append("RATE_LIMIT_NETWORK_MULTIPLIER must be between 1 and 1000")
    if not 1_024 <= settings.max_request_body_bytes <= 16_777_216:
        errors.append("MAX_REQUEST_BODY_BYTES must be between 1024 and 16777216")
    if not 10 <= settings.supersession_candidate_limit <= 5_000:
        errors.append("SUPERSESSION_CANDIDATE_LIMIT must be between 10 and 5000")
    if not 1_048_576 <= settings.supersession_candidate_bytes_limit <= 268_435_456:
        errors.append("SUPERSESSION_CANDIDATE_BYTES_LIMIT must be between 1048576 and 268435456")
    if not 1 <= settings.graph_exclusive_invalidation_limit <= 5_000:
        errors.append("GRAPH_EXCLUSIVE_INVALIDATION_LIMIT must be between 1 and 5000")
    if not 1 <= settings.graph_extract_candidate_limit <= 5_000:
        errors.append("GRAPH_EXTRACT_CANDIDATE_LIMIT must be between 1 and 5000")
    if not 65_536 <= settings.graph_extract_candidate_bytes_limit <= 67_108_864:
        errors.append("GRAPH_EXTRACT_CANDIDATE_BYTES_LIMIT must be between 65536 and 67108864")
    if not 1_000 <= settings.database_statement_timeout_ms <= 300_000:
        errors.append("DATABASE_STATEMENT_TIMEOUT_MS must be between 1000 and 300000")
    if not 100 <= settings.database_lock_timeout_ms <= 60_000:
        errors.append("DATABASE_LOCK_TIMEOUT_MS must be between 100 and 60000")
    if not 1_000 <= settings.database_idle_transaction_timeout_ms <= 300_000:
        errors.append("DATABASE_IDLE_TRANSACTION_TIMEOUT_MS must be between 1000 and 300000")
    if not 1_000 <= settings.migration_statement_timeout_ms <= 3_600_000:
        errors.append("MIGRATION_STATEMENT_TIMEOUT_MS must be between 1000 and 3600000")
    if not 100 <= settings.migration_lock_timeout_ms <= 60_000:
        errors.append("MIGRATION_LOCK_TIMEOUT_MS must be between 100 and 60000")
    if not 1_000 <= settings.migration_idle_transaction_timeout_ms <= 600_000:
        errors.append("MIGRATION_IDLE_TRANSACTION_TIMEOUT_MS must be between 1000 and 600000")
    if not 1 <= settings.embedding_provider_timeout_seconds <= 120:
        errors.append("EMBEDDING_PROVIDER_TIMEOUT_SECONDS must be between 1 and 120")
    if not 1 <= settings.llm_provider_timeout_seconds <= 120:
        errors.append("LLM_PROVIDER_TIMEOUT_SECONDS must be between 1 and 120")
    if settings.supersession_llm_stage and settings.llm_adjudication_async:
        errors.append(
            "LLM_ADJUDICATION_ASYNC is forbidden in production until adjudication "
            "uses a durable leased queue; set it to false for correctness"
        )
    try:
        parse_trusted_proxy_cidrs(settings.trusted_proxy_cidrs)
    except (TypeError, ValueError) as exc:
        errors.append(str(exc))
    if not 60 <= settings.workload_credential_min_ttl_seconds <= 31_536_000:
        errors.append("WORKLOAD_CREDENTIAL_MIN_TTL_SECONDS must be between 60 and 31536000")
    if not (
        settings.workload_credential_min_ttl_seconds
        <= settings.workload_credential_max_ttl_seconds
        <= 31_536_000
    ):
        errors.append(
            "WORKLOAD_CREDENTIAL_MAX_TTL_SECONDS must be at least the minimum TTL and no more than 31536000"
        )
    if not 60 <= settings.workload_credential_last_used_write_interval_seconds <= 86_400:
        errors.append(
            "WORKLOAD_CREDENTIAL_LAST_USED_WRITE_INTERVAL_SECONDS must be between 60 and 86400"
        )
    if settings.otlp_capture_mode.strip().lower() not in {
        "metadata_only",
        "hash_only",
        "full",
    }:
        errors.append("OTLP_CAPTURE_MODE must be metadata_only, hash_only, or full")
    if (
        settings.otlp_capture_mode.strip().lower() == "full"
        and not settings.recorder_allow_full_capture
    ):
        errors.append("OTLP_CAPTURE_MODE=full requires RECORDER_ALLOW_FULL_CAPTURE=true")
    if settings.metrics_enabled and len(settings.metrics_bearer_token) < 32:
        errors.append(
            "METRICS_BEARER_TOKEN must be at least 32 characters when metrics are enabled"
        )
    if not 5 <= settings.observability_refresh_seconds <= 300:
        errors.append("OBSERVABILITY_REFRESH_SECONDS must be between 5 and 300")
    if not 100 <= settings.decision_evidence_candidate_limit <= 10_000:
        errors.append("DECISION_EVIDENCE_CANDIDATE_LIMIT must be between 100 and 10000")
    if not (1_048_576 <= settings.decision_evidence_candidate_bytes_limit <= 134_217_728):
        errors.append("DECISION_EVIDENCE_CANDIDATE_BYTES_LIMIT must be between 1MiB and 128MiB")
    if not 1 / 60 <= settings.retention_prune_interval_hours <= 168:
        errors.append(
            "RETENTION_PRUNE_INTERVAL_HOURS must be between one minute and 168 hours in production"
        )
    region = settings.deployment_region.strip().lower()
    if region in {"", "local", "unknown", "unset", "configure-me"}:
        errors.append("DEPLOYMENT_REGION must explicitly identify the server processing region")
    elif not all(character.isalnum() or character in {"-", "_", "."} for character in region):
        errors.append("DEPLOYMENT_REGION may contain only letters, numbers, '-', '_', and '.'")
    if settings.integration_allow_insecure_http:
        errors.append("INTEGRATION_ALLOW_INSECURE_HTTP is forbidden in production")
    if settings.legacy_webhooks_enabled:
        errors.append(
            "LEGACY_WEBHOOKS_ENABLED is forbidden in production; use the durable integration outbox"
        )
    if settings.siem_url.strip():
        errors.append(
            "SIEM_URL is a lossy compatibility path and is forbidden in production; "
            "configure a durable namespace-scoped SIEM integration destination"
        )
    from .metering import validate_metering_configuration

    errors.extend(validate_metering_configuration(settings, production=True))
    if not 0.05 <= settings.integration_worker_poll_seconds <= 60:
        errors.append("INTEGRATION_WORKER_POLL_SECONDS must be between 0.05 and 60")
    if not 1 <= settings.integration_worker_batch_size <= 1_000:
        errors.append("INTEGRATION_WORKER_BATCH_SIZE must be between 1 and 1000")
    if not 1 <= settings.integration_delivery_concurrency <= 100:
        errors.append("INTEGRATION_DELIVERY_CONCURRENCY must be between 1 and 100")
    if not 130 <= settings.integration_lease_seconds <= 3_600:
        errors.append(
            "INTEGRATION_LEASE_SECONDS must be between 130 and 3600 to exceed "
            "the maximum destination timeout and DNS guard"
        )
    if not 0.1 <= settings.integration_retry_base_seconds <= 3_600:
        errors.append("INTEGRATION_RETRY_BASE_SECONDS must be between 0.1 and 3600")
    if not (
        settings.integration_retry_base_seconds <= settings.integration_retry_max_seconds <= 3_600
    ):
        errors.append(
            "INTEGRATION_RETRY_MAX_SECONDS must be at least the base and no more than 3600"
        )
    if not 1_024 <= settings.integration_max_payload_bytes <= 10_000_000:
        errors.append("INTEGRATION_MAX_PAYLOAD_BYTES must be between 1024 and 10000000")
    if not 0 <= settings.integration_max_response_digest_bytes <= 1_000_000:
        errors.append("INTEGRATION_MAX_RESPONSE_DIGEST_BYTES must be between 0 and 1000000")
    kms_provider = settings.kms_provider.strip().lower()
    if kms_provider not in {"env", "aws", "azure", "vault"}:
        errors.append("KMS_PROVIDER must be env, aws, azure, or vault")
    elif kms_provider == "env":
        errors.append("KMS_PROVIDER=env is forbidden in production; use aws, azure, or vault")
    elif kms_provider == "aws" and not settings.kms_aws_encrypted_key:
        errors.append("KMS_AWS_ENCRYPTED_KEY is required when KMS_PROVIDER=aws")
    elif kms_provider == "azure" and not settings.kms_azure_vault_url:
        errors.append("KMS_AZURE_VAULT_URL is required when KMS_PROVIDER=azure")
    elif kms_provider == "vault" and not settings.kms_vault_token:
        errors.append("KMS_VAULT_TOKEN is required when KMS_PROVIDER=vault")
    try:
        from .kms import validate_keyring_configuration

        validate_keyring_configuration(settings)
    except ValueError as exc:
        errors.append(str(exc))
    from .connection_security import validate_production_data_transports

    errors.extend(validate_production_data_transports(settings))
    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))


def _validate_airgap(settings) -> None:
    """
    Hard-fail at startup if AIRGAP_MODE=true but the configuration would
    send data to an external API.  Catches misconfiguration before any
    customer data is processed — not at request time.
    """
    errors = []
    if settings.embedding_provider not in _AIRGAP_SAFE_PROVIDERS:
        errors.append(
            f"EMBEDDING_PROVIDER={settings.embedding_provider!r} makes external API calls. "
            "Set EMBEDDING_PROVIDER=sentence-transformers or bge-onnx for "
            "self-hosted inference."
        )
    if settings.supersession_llm_stage:
        errors.append(
            "SUPERSESSION_LLM_STAGE=true sends memory content to Anthropic's API. "
            "Set SUPERSESSION_LLM_STAGE=false to disable external LLM calls."
        )
    if settings.graph_extract_llm:
        errors.append(
            "GRAPH_EXTRACT_LLM=true can send graph content to an external model. "
            "Set GRAPH_EXTRACT_LLM=false."
        )
    if settings.auto_metadata_llm:
        errors.append(
            "AUTO_METADATA_LLM=true can send memory content to an external model. "
            "Set AUTO_METADATA_LLM=false."
        )
    if settings.siem_url.strip():
        errors.append("SIEM_URL is configured. Clear it to disable legacy SIEM egress.")
    if settings.stripe_api_key.strip():
        errors.append("STRIPE_API_KEY is configured. Clear it to disable billing egress.")
    if settings.otel_exporter_otlp_endpoint.strip():
        errors.append(
            "OTEL_EXPORTER_OTLP_ENDPOINT is configured. Clear it to disable trace egress."
        )
    if errors:
        raise RuntimeError(
            "AIRGAP_MODE=true but the following settings would leak data externally:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .audit_chain import audit_append_boundary_status
    from .config import get_settings
    from .db import (
        AsyncSessionLocal,
        engine,
        set_current_barrier_group,
        set_current_namespace,
    )
    from .impact_assessment_service import (
        run_impact_assessment_worker,
        validate_impact_worker_configuration,
    )
    from .kms import get_master_keyring, load_master_key
    from .metering import run_metering_worker, validate_metering_configuration
    from .receipt_signer import (
        close_receipt_signer,
        load_receipt_signer,
        receipt_signer_identity,
    )
    from .recorder_index_service import (
        run_recorder_index_worker,
        validate_recorder_index_worker_configuration,
    )
    from .scim_reconciliation_service import (
        run_scim_reconciliation_worker,
        validate_scim_reconciliation_worker_configuration,
    )
    from .scheduler import run_retention_scheduler
    from .subject_erasure_service import (
        run_subject_erasure_worker,
        validate_subject_erasure_worker_configuration,
    )

    settings = get_settings()
    # A worker that terminates is observed by its done callback immediately,
    # rather than being discovered only when the process eventually shuts down.
    app.state.background_task_failures = {}
    app.state.background_tasks_shutting_down = False
    production = settings.deployment_environment.strip().lower() in {
        "prod",
        "production",
    }

    setup_logging(level=settings.log_level, json_logs=settings.log_json)

    _warn_insecure_secrets(settings)
    _validate_production_secrets(settings)
    metering_configuration_errors = validate_metering_configuration(
        settings,
        production=settings.deployment_environment.strip().lower() in {"prod", "production"},
    )
    if metering_configuration_errors:
        raise RuntimeError(
            "Invalid durable metering configuration: " + "; ".join(metering_configuration_errors)
        )
    impact_worker_configuration_errors = validate_impact_worker_configuration(
        settings,
        production=settings.deployment_environment.strip().lower() in {"prod", "production"},
    )
    if impact_worker_configuration_errors:
        raise RuntimeError(
            "Invalid autonomous impact-worker configuration: "
            + "; ".join(impact_worker_configuration_errors)
        )
    recorder_index_configuration_errors = validate_recorder_index_worker_configuration(
        settings,
        production=production,
    )
    if recorder_index_configuration_errors:
        raise RuntimeError(
            "Invalid Recorder evidence indexing configuration: "
            + "; ".join(recorder_index_configuration_errors)
        )
    subject_erasure_configuration_errors = validate_subject_erasure_worker_configuration(
        settings,
        production=production,
    )
    if subject_erasure_configuration_errors:
        raise RuntimeError(
            "Invalid subject-erasure worker configuration: "
            + "; ".join(subject_erasure_configuration_errors)
        )
    scim_reconciliation_configuration_errors = validate_scim_reconciliation_worker_configuration(
        settings,
        production=production,
    )
    if scim_reconciliation_configuration_errors:
        raise RuntimeError(
            "Invalid SCIM reconciliation worker configuration: "
            + "; ".join(scim_reconciliation_configuration_errors)
        )

    if settings.airgap_mode:
        _validate_airgap(settings)
        # The model must already be baked into the image or provisioned by the
        # operator. A missing local model now fails before traffic instead of
        # reaching a model hub from an air-gapped process.
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"

    if production:
        # Standalone processes may receive traffic even when an orchestrator is
        # not honoring /readyz. Enforce the release schema, dynamic tenant RLS
        # inventory, and runtime-role posture before opening the application.
        from .database_role_posture import (
            database_role_posture_status,
            failed_database_role_checks,
        )

        async with AsyncSessionLocal() as posture_db:
            try:
                await assert_database_schema(posture_db)
            except SchemaContractError as exc:
                raise RuntimeError(
                    "Database schema and tenant-isolation contract is not enforced"
                ) from exc
            role_posture = await database_role_posture_status(posture_db)
        if not role_posture["enforced"]:
            failed_checks = failed_database_role_checks(role_posture)
            raise RuntimeError(
                "Database runtime role posture is not enforced: " + ", ".join(failed_checks)
            )

    await load_master_key()
    master_keyring = get_master_keyring()

    async with AsyncSessionLocal() as boundary_db:
        audit_boundary = await audit_append_boundary_status(boundary_db)
    if not audit_boundary["enforced"]:
        failed_boundary_checks = sorted(
            name for name, passed in audit_boundary["checks"].items() if not passed
        )
        message = "Database audit append boundary is not enforced: " + ", ".join(
            failed_boundary_checks
        )
        if settings.deployment_environment.strip().lower() in {"prod", "production"}:
            raise RuntimeError(message)
        logger.warning("SECURITY: %s", message)

    # Encrypt legacy review-queue content and webhook signing secrets before the
    # service accepts traffic. The admin sentinel is transaction-local and is
    # cleared immediately after this one-time, idempotent upgrade pass.
    from .secret_storage import protect_legacy_sensitive_rows

    set_current_namespace("__admin__")
    set_current_barrier_group(None)
    try:
        async with AsyncSessionLocal() as migration_db:
            write_fence = (
                (
                    await migration_db.execute(
                        text(
                            "SELECT phase, current_key_id, previous_key_id, generation "
                            "FROM master_key_write_fence_state WHERE singleton_id = 1"
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if write_fence is not None:
                fence_phase = str(write_fence["phase"])
                fence_current_id = str(write_fence["current_key_id"])
                fence_previous_id = write_fence["previous_key_id"]
                loaded_ids = {master_keyring.current.key_id}
                if master_keyring.previous is not None:
                    loaded_ids.add(master_keyring.previous.key_id)
                if fence_phase == "prepared":
                    fence_ids = {fence_current_id, str(fence_previous_id)}
                    fence_safe = bool(
                        fence_previous_id is not None
                        and master_keyring.current.key_id in fence_ids
                        and loaded_ids.issubset(fence_ids)
                    )
                elif fence_phase == "narrowed":
                    fence_safe = bool(
                        fence_previous_id is None
                        and master_keyring.current.key_id == fence_current_id
                    )
                else:
                    fence_safe = False
                if not fence_safe:
                    raise RuntimeError(
                        "Master-key configuration is rejected by the persistent database write fence"
                    )
            rotation_checkpoint = (
                (
                    await migration_db.execute(
                        text(
                            "SELECT current_key_id, status, legacy_values_remaining, "
                            "previous_values_remaining, unknown_values_remaining, "
                            "plaintext_closures_remaining "
                            "FROM master_key_rotation_state WHERE singleton_id = 1"
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if rotation_checkpoint is not None:
                loaded_ids = {master_keyring.current.key_id}
                if master_keyring.previous is not None:
                    loaded_ids.add(master_keyring.previous.key_id)
                checkpoint_safe = bool(
                    rotation_checkpoint["current_key_id"] in loaded_ids
                    and rotation_checkpoint["status"] == "verified"
                    and int(rotation_checkpoint["legacy_values_remaining"]) == 0
                    and int(rotation_checkpoint["previous_values_remaining"]) == 0
                    and int(rotation_checkpoint["unknown_values_remaining"]) == 0
                    and int(rotation_checkpoint["plaintext_closures_remaining"]) == 0
                )
                if not checkpoint_safe:
                    raise RuntimeError(
                        "Master-key configuration does not safely overlap the latest "
                        "verified rotation checkpoint"
                    )
            protected_rows = await protect_legacy_sensitive_rows(migration_db)
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)
    if protected_rows:
        logger.info(
            "Protected legacy sensitive rows",
            extra={"rows_updated": protected_rows},
        )

    # Change 5: pre-warm the embedder at startup so the first recall doesn't pay
    # the model-load penalty.  For sentence-transformers this blocks briefly in a
    # thread-pool executor; for API providers it is a no-op.
    from .embeddings import get_embedding_provider

    _provider = get_embedding_provider()
    try:
        _warmup_vec = await _provider.embed_one("warmup")
        if _warmup_vec and len(_warmup_vec) != settings.embedding_dim:
            raise RuntimeError(
                f"Embedding provider {settings.embedding_provider!r} returned "
                f"{len(_warmup_vec)}-dim vectors but EMBEDDING_DIM={settings.embedding_dim}. "
                "The DB schema is built for EMBEDDING_DIM dimensions. "
                "Set EMBEDDING_DIM to match your model, or use a different model."
            )
        logger.info("Embedder warmed up", extra={"provider": settings.embedding_provider})
    except RuntimeError:
        raise
    except Exception:
        # Provider exception strings may echo endpoint or response details.
        logger.warning("Embedder warmup failed (non-fatal)")

    if settings.cors_origins == "*":
        logger.warning(
            "SECURITY: CORS_ORIGINS=* allows any website to make cross-origin requests. "
            "Set CORS_ORIGINS to a comma-separated list of trusted origins in production."
        )

    logger.info(
        "Lians starting",
        extra={
            "embedding_provider": settings.embedding_provider,
            "airgap_mode": settings.airgap_mode,
            "llm_stage": settings.supersession_llm_stage,
            "kms_provider": settings.kms_provider,
            "master_key_id": master_keyring.current.key_id,
            "previous_master_key_configured": master_keyring.previous is not None,
            "deployment_region": settings.deployment_region.strip().lower(),
            "merkle_batch_enabled": settings.merkle_batch_enabled,
            "llm_adjudication_async": settings.llm_adjudication_async,
        },
    )

    instrument_sqlalchemy(engine)

    receipt_signer = await load_receipt_signer(settings)
    signer_identity = receipt_signer_identity(receipt_signer)
    logger.info(
        "Decision Receipt signer loaded",
        extra={
            "provider": signer_identity["provider"],
            "key_id": signer_identity["key_id"],
            "key_version": signer_identity["key_version"],
            "public_key_sha256": signer_identity["public_key_sha256"],
        },
    )

    scheduler_task: asyncio.Task | None = None
    metering_task: asyncio.Task | None = None
    integration_worker_task: asyncio.Task | None = None
    impact_worker_task: asyncio.Task | None = None
    recorder_index_worker_task: asyncio.Task | None = None
    subject_erasure_worker_task: asyncio.Task | None = None
    scim_reconciliation_worker_task: asyncio.Task | None = None
    observability_task: asyncio.Task | None = None
    llm_worker_task: asyncio.Task | None = None
    # Start the grace clock only after all synchronous startup validation and
    # key/signer initialization have completed and immediately before workers
    # are scheduled.
    app.state.background_workers_started_at = asyncio.get_running_loop().time()
    try:
        if settings.retention_prune_interval_hours > 0:
            scheduler_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_retention_scheduler(
                        AsyncSessionLocal,
                        settings.retention_prune_interval_hours,
                        engine,
                    ),
                    name="retention-scheduler",
                ),
            )

        if (
            settings.stripe_api_key
            and settings.stripe_meter_worker_enabled
            and not settings.airgap_mode
        ):
            metering_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_metering_worker(
                        AsyncSessionLocal,
                        api_key=settings.stripe_api_key,
                    ),
                    name="metering-worker",
                ),
            )

        if settings.integration_worker_enabled and not settings.airgap_mode:
            from .integration_service import run_integration_worker

            integration_worker_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_integration_worker(AsyncSessionLocal),
                    name="integration-outbox-worker",
                ),
            )

        if settings.impact_assessment_worker_enabled:
            impact_worker_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_impact_assessment_worker(AsyncSessionLocal),
                    name="impact-assessment-worker",
                ),
            )

        if settings.recorder_evidence_index_worker_enabled:
            recorder_index_worker_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_recorder_index_worker(AsyncSessionLocal),
                    name="recorder-evidence-index-worker",
                ),
            )

        if settings.subject_erasure_worker_enabled:
            subject_erasure_worker_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_subject_erasure_worker(AsyncSessionLocal),
                    name="subject-erasure-worker",
                ),
            )

        if settings.scim_reconciliation_worker_enabled:
            scim_reconciliation_worker_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_scim_reconciliation_worker(AsyncSessionLocal),
                    name="scim-binding-reconciliation-worker",
                ),
            )

        if settings.metrics_enabled:
            from .observability_service import run_durable_inventory_refresher

            observability_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_durable_inventory_refresher(
                        AsyncSessionLocal,
                        interval_seconds=settings.observability_refresh_seconds,
                    ),
                    name="durable-observability-inventory",
                ),
            )

        # Change 3: start async LLM adjudication worker (off the write path)
        if settings.supersession_llm_stage and settings.llm_adjudication_async:
            from .supersession import run_llm_adjudication_worker

            llm_worker_task = _track_background_task(
                app,
                asyncio.create_task(
                    run_llm_adjudication_worker(AsyncSessionLocal),
                    name="llm-adjudication-worker",
                ),
            )

        yield
    finally:
        background_tasks = tuple(
            task
            for task in (
                scheduler_task,
                metering_task,
                integration_worker_task,
                impact_worker_task,
                recorder_index_worker_task,
                subject_erasure_worker_task,
                scim_reconciliation_worker_task,
                observability_task,
                llm_worker_task,
            )
            if task is not None
        )
        # Cancel every worker before awaiting any one of them. A task that
        # already failed must not prevent the remaining workers or signer from
        # being closed during process shutdown.
        app.state.background_tasks_shutting_down = True
        for task in background_tasks:
            task.cancel()
        try:
            # Done callbacks have already recorded any pre-shutdown failure.
            # Gathering still retrieves every result and lets cancellation
            # cleanup finish without one failed task short-circuiting another.
            await asyncio.gather(*background_tasks, return_exceptions=True)
        finally:
            await close_receipt_signer()
            logger.info("Lians shutdown")


app = FastAPI(
    title="Lians Decision Evidence API",
    description=(
        "Provider-neutral decision receipts, bitemporal reconstruction, "
        "change-impact analysis, and auditable agent memory"
    ),
    version=__version__,
    lifespan=lifespan,
)


@app.exception_handler(SubjectKeyDestroyedError)
async def _shredded_subject_handler(request: Request, exc: SubjectKeyDestroyedError):
    # A destroyed subject key is never re-created (GDPR Art. 17), so a write
    # for that subject is permanently impossible — 410 Gone, not a 500.
    return JSONResponse(
        status_code=410,
        content={
            "detail": str(exc),
            "code": "subject_crypto_shredded",
            "hint": "The subject's encryption key was destroyed by /v1/erase and is never re-created; use a new subject_id for new data.",
        },
    )


@app.exception_handler(SubjectReferenceError)
async def _invalid_subject_reference_handler(request: Request, exc: SubjectReferenceError):
    return JSONResponse(
        status_code=400,
        content={
            "detail": str(exc),
            "code": "invalid_subject_reference",
        },
    )


instrument_fastapi(app)

# CORS — allows the demo/index.html page to call the API from a browser.
# In production, set CORS_ORIGINS to a comma-separated list of trusted origins.
_cors_origins = [o.strip() for o in (get_settings().cors_origins or "*").split(",")]
_cors_allow_headers = [
    "Content-Type",
    "Authorization",
    "X-API-Key",
    "Idempotency-Key",
    "X-Request-ID",
]
if get_settings().api_surface in {"admin", "all"}:
    _cors_allow_headers.append("X-Admin-Secret")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # API keys are explicit headers, not browser cookies. Credentialed wildcard
    # CORS is both invalid in browsers and unnecessarily broad.
    allow_credentials="*" not in _cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=_cors_allow_headers,
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "Retry-After",
        "X-Lians-Total-Count",
        "X-Lians-Page-Limit",
        "X-Lians-Page-Returned",
        "X-Lians-Page-Complete",
        "X-Lians-Collection-Complete",
        "X-Lians-Has-More",
        "X-Lians-Page-Offset",
        "X-Lians-Next-Before-Created-At",
        "X-Lians-Next-Before-Id",
        "X-Lians-Next-Before-Attested-At",
        "X-Lians-Next-Before-Evaluated-At",
        "X-Lians-Next-Before-Enqueued-At",
        "X-Lians-Next-Before-Occurred-At",
        "X-Lians-Next-Before-Decided-At",
        "X-Lians-Next-Before-Recorded-At",
        "X-Lians-Next-Before-Opened-At",
        "X-Lians-Next-After-Created-At",
        "X-Lians-Next-After-Id",
        "X-Lians-Next-After-Attempt-Number",
        "X-Lians-Next-After-Namespace",
        "X-Lians-Next-Before-Policy-Version",
    ],
)

# Middleware is applied in reverse registration order (last added = outermost).
# Order: CORS → RequestID → AccessLog → RateLimit → routes
# Wire the configured limit through: without this argument the middleware silently
# pins every deployment to its hardcoded 300/min default and RATE_LIMIT_PER_MINUTE
# (documented as tunable in .env.example) has no effect.
app.add_middleware(
    RateLimitMiddleware,
    requests_per_minute=get_settings().rate_limit_per_minute,
    network_multiplier=get_settings().rate_limit_network_multiplier,
    admin_requests_per_minute=get_settings().rate_limit_admin_per_minute,
    backend_failure_mode=get_settings().rate_limit_backend_failure_mode,
    trusted_proxy_cidrs=get_settings().trusted_proxy_cidrs,
)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=get_settings().max_request_body_bytes,
)
# Registered outside the body-limit/rate-limit/routing layers so SLO metrics
# include their outcomes. Labels use route templates.
app.add_middleware(RequestMetricsMiddleware)
# Capability responses must remain non-cacheable even when an outer middleware
# rejects the request before routing (for example, the body-size guard).
app.add_middleware(
    NoStoreResponseMiddleware,
    paths=(
        "/v1/control/gate/evaluate",
        "/v1/control/gate/evaluate/",
        "/v1/control/gate/permits/consume",
        "/v1/control/gate/permits/consume/",
    ),
)

_api_surface = get_settings().api_surface
if _api_surface in {"public", "all"}:
    app.include_router(memory_router)
    app.include_router(audit_router)
    app.include_router(privacy_router)
    app.include_router(supersessions_router)
    app.include_router(conflicts_router)
    app.include_router(webhooks_router)
    app.include_router(compliance_router)
    app.include_router(backtest_router)
    app.include_router(snapshot_router)
    app.include_router(graph_router)
    app.include_router(admissions_router)
    app.include_router(decisions_router)
    app.include_router(records_router)
    app.include_router(receipts_router)
    app.include_router(metrics_router)
    app.include_router(otlp_router)
    app.include_router(validmind_router)
    app.include_router(identity_router)
    app.include_router(scim_router)
    app.include_router(recorder_router)
    app.include_router(control_router)
    app.include_router(governance_router)
    app.include_router(investigator_router)
    app.include_router(platform_router)
    app.include_router(integrations_router)
    app.include_router(workload_credentials_router)
    app.include_router(agents_router)
    app.include_router(eval_router)
    app.include_router(optimization_router)
    app.include_router(context_router)
    app.include_router(tools_router)
    app.include_router(runtime_router)
    app.include_router(routing_router)
    app.include_router(cache_router)
    app.include_router(outcomes_router)
    app.include_router(feedback_router)
    app.include_router(drift_router)
    app.include_router(learning_router)
    app.include_router(releases_router)
    app.include_router(deployments_router)
    app.include_router(rollback_router)
if _api_surface in {"admin", "all"}:
    app.include_router(admin_router)
    app.include_router(identity_admin_router)
    app.include_router(scim_admin_router)
    app.include_router(governance_admin_router)


def _worker_readiness_status(healthy: bool) -> str:
    """Map process worker state to a closed, unauthenticated probe vocabulary."""

    if healthy:
        return "ok"
    started_at = getattr(app.state, "background_workers_started_at", None)
    if isinstance(started_at, (int, float)):
        elapsed = asyncio.get_running_loop().time() - float(started_at)
        if elapsed < _BACKGROUND_WORKER_STARTUP_GRACE_SECONDS:
            return "starting"
    return "error: stale"


@app.get("/health", include_in_schema=False)
async def health(db: AsyncSession = Depends(_get_db)):
    """
    Deep health check — verifies DB and Redis connectivity, not just process liveness.

    Returns 200 {"status": "ok"} when all dependencies are reachable.
    Returns 503 {"status": "degraded"} with per-dependency details when any fail.

    Load balancer probes should use this endpoint.  A 503 means the instance
    should be taken out of rotation immediately.
    """
    from .cache import _get_redis

    settings = get_settings()
    production = settings.deployment_environment.strip().lower() in {
        "prod",
        "production",
    }
    checks: dict[str, str] = {}

    # DB — SELECT 1 with a 2-second timeout
    db_ok = False
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        checks["db"] = "ok"
        db_ok = True
    except Exception:
        checks["db"] = "error: unavailable"

    if db_ok:
        try:
            await asyncio.wait_for(assert_database_schema(db), timeout=2.0)
            checks["schema"] = "ok"
        except SchemaContractError:
            checks["schema"] = "error: mismatch"
        except Exception:
            checks["schema"] = "error: unavailable"
    else:
        checks["schema"] = "error: database_unavailable"

    if db_ok and production:
        try:
            from .database_role_posture import database_role_posture_status

            role_posture = await asyncio.wait_for(database_role_posture_status(db), timeout=2.0)
            checks["database_role"] = "ok" if role_posture["enforced"] else "error: unsafe_posture"
        except Exception:
            checks["database_role"] = "error: unavailable"
    elif production:
        checks["database_role"] = "error: database_unavailable"
    else:
        checks["database_role"] = "not_required"

    # Redis — PING with a 1-second timeout (skipped when cache is disabled)
    redis_required = (
        settings.recall_cache_enabled
        or settings.rate_limit_backend_failure_mode.strip().lower() == "deny"
    )
    if redis_required:
        try:
            await asyncio.wait_for(_get_redis().ping(), timeout=1.0)
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "error: unavailable"
    else:
        checks["redis"] = "optional"

    try:
        from .kms import get_master_keyring

        keyring = get_master_keyring()
        if len(keyring.current.material) != 32:
            raise RuntimeError("invalid loaded key length")
        checks["kms_keyring"] = "ok"
    except Exception:
        checks["kms_keyring"] = "error: invalid"

    if settings.integration_worker_enabled and not settings.airgap_mode:
        from .integration_service import integration_worker_status

        worker_healthy, _ = integration_worker_status()
        checks["integration_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["integration_worker"] = "not_required"

    if (
        settings.stripe_api_key
        and settings.stripe_meter_worker_enabled
        and not settings.airgap_mode
    ):
        from .metering import metering_worker_status

        worker_healthy, _ = metering_worker_status()
        checks["metering_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["metering_worker"] = "not_required"

    if settings.impact_assessment_worker_enabled:
        from .impact_assessment_service import impact_worker_status

        worker_healthy, _ = impact_worker_status()
        checks["impact_assessment_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["impact_assessment_worker"] = "not_required"

    if settings.recorder_evidence_index_worker_enabled:
        from .recorder_index_service import recorder_index_worker_status

        worker_healthy, _ = recorder_index_worker_status()
        checks["recorder_evidence_index_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["recorder_evidence_index_worker"] = "not_required"

    if settings.subject_erasure_worker_enabled:
        from .subject_erasure_service import subject_erasure_worker_status

        worker_healthy, _ = subject_erasure_worker_status()
        checks["subject_erasure_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["subject_erasure_worker"] = "not_required"

    if settings.scim_reconciliation_worker_enabled:
        from .scim_reconciliation_service import scim_reconciliation_worker_status

        worker_healthy, _ = scim_reconciliation_worker_status()
        checks["scim_reconciliation_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["scim_reconciliation_worker"] = "not_required"

    if settings.retention_prune_interval_hours > 0:
        from .scheduler import retention_scheduler_status

        worker_healthy, _ = retention_scheduler_status()
        checks["retention_scheduler"] = _worker_readiness_status(worker_healthy)
    else:
        checks["retention_scheduler"] = "not_required"

    if settings.metrics_enabled:
        from .observability_service import durable_inventory_refresher_status

        worker_healthy, _ = durable_inventory_refresher_status()
        checks["observability_inventory"] = _worker_readiness_status(worker_healthy)
    else:
        checks["observability_inventory"] = "not_required"

    if settings.supersession_llm_stage and settings.llm_adjudication_async:
        from .supersession import llm_adjudication_worker_status

        worker_healthy, _ = llm_adjudication_worker_status()
        checks["llm_adjudication_worker"] = _worker_readiness_status(worker_healthy)
    else:
        checks["llm_adjudication_worker"] = "not_required"

    failed_background_tasks = getattr(app.state, "background_task_failures", {})
    if failed_background_tasks:
        checks["background_tasks"] = "error: terminated"
    else:
        checks["background_tasks"] = "ok"

    all_ok = all(value in {"ok", "not_required", "optional"} for value in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )


@app.get("/livez", include_in_schema=False)
async def livez():
    """
    Liveness probe — confirms the process is up. Intentionally cheap: it never
    touches the database or Redis, so a transient dependency blip does not cause
    Kubernetes to restart an otherwise-healthy pod. Use for livenessProbe.
    """
    return {"status": "alive"}


@app.get("/readyz", include_in_schema=False)
async def readyz(db: AsyncSession = Depends(_get_db)):
    """
    Readiness probe — deep dependency check (same as /health). A 503 takes the
    instance out of rotation without killing the process. Use for readinessProbe.
    """
    return await health(db)
