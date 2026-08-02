import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .pii import SubjectKeyDestroyedError
from .db import get_db as _get_db
from .api.routes_memory import router as memory_router
from .api.routes_audit import router as audit_router
from .api.routes_privacy import router as privacy_router
from .api.routes_admin import router as admin_router
from .api.routes_supersessions import router as supersessions_router
from .api.routes_metrics import router as metrics_router
from .api.routes_conflicts import router as conflicts_router
from .api.routes_webhooks import router as webhooks_router
from .api.routes_compliance import router as compliance_router
from .api.routes_backtest import router as backtest_router
from .api.routes_snapshot import router as snapshot_router
from .api.routes_graph import router as graph_router
from .api.routes_admissions import router as admissions_router
from .api.routes_decisions import router as decisions_router, records_router
from .api.routes_evidence import (
    decision_router as decision_evidence_router,
    evidence_router,
    router as decision_envelopes_router,
)
from .api.routes_otlp import router as otlp_router
from .api.routes_validmind import legacy_router as validmind_legacy_router
from .api.routes_validmind import router as validmind_router
from .api.routes_learning import router as learning_router
from .telemetry import instrument_fastapi, instrument_sqlalchemy
from .middleware import (
    setup_logging,
    RequestIDMiddleware,
    AccessLogMiddleware,
    RequestBodyLimitMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)

logger = logging.getLogger("lians.startup")

_AIRGAP_SAFE_PROVIDERS = {"sentence-transformers", "local"}


_DEV_SECRETS = {
    "dev-seed-change-in-prod",
    "dev-seed-change-in-production",
    "dev-admin-secret-change-in-prod",
    "dev-admin-secret-change-in-production",
}


def _warn_insecure_secrets(settings) -> None:
    """
    Log prominent warnings when development placeholder secrets are detected.

    These defaults are intentionally weak so tests work without configuration.
    A production deployment using them is exploitable — any party that reads
    this source code can bypass admin authentication.
    """
    warnings = []
    if settings.admin_secret in _DEV_SECRETS:
        warnings.append(
            "ADMIN_SECRET is using the development default. "
            "The /v1/admin/* endpoints have no meaningful access control. "
            "Set a strong random value before deploying."
        )
    for msg in warnings:
        logger.warning("SECURITY: %s", msg)


def _validate_production_secrets(settings) -> None:
    """Fail closed instead of starting production with published secrets."""
    if settings.deployment_environment.strip().lower() not in {"prod", "production"}:
        return
    errors = []
    if settings.admin_secret in _DEV_SECRETS or len(settings.admin_secret) < 32:
        errors.append("ADMIN_SECRET must be a random value of at least 32 characters")
    origins = {o.strip() for o in settings.cors_origins.split(",") if o.strip()}
    if "*" in origins:
        errors.append("CORS_ORIGINS must list trusted origins instead of '*'")
    if errors:
        raise RuntimeError("Unsafe production secret configuration: " + "; ".join(errors))


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
            f"Set EMBEDDING_PROVIDER=sentence-transformers for self-hosted inference."
        )
    if settings.supersession_llm_stage:
        errors.append(
            "SUPERSESSION_LLM_STAGE=true sends memory content to Anthropic's API. "
            "Set SUPERSESSION_LLM_STAGE=false to disable external LLM calls."
        )
    if errors:
        raise RuntimeError(
            "AIRGAP_MODE=true but the following settings would leak data externally:\n"
            + "\n".join(f"  • {e}" for e in errors)
        )


async def _warm_embedding_provider(
    provider,
    *,
    expected_dim: int,
    provider_name: str,
) -> None:
    """Warm embedding inference without delaying API startup."""
    try:
        warmup_vec = await provider.embed_one("warmup")
        if warmup_vec and len(warmup_vec) != expected_dim:
            raise RuntimeError(
                f"Embedding provider {provider_name!r} returned "
                f"{len(warmup_vec)}-dim vectors but EMBEDDING_DIM={expected_dim}. "
                "The DB schema is built for EMBEDDING_DIM dimensions. "
                "Set EMBEDDING_DIM to match your model, or use a different model."
            )
        logger.info("Embedder warmed up", extra={"provider": provider_name})
    except Exception as exc:  # noqa: BLE001 - providers expose heterogeneous failures
        # The API can still serve writes, evidence exports, and health checks
        # while a local model is warming or temporarily unavailable. Retrieval
        # will retry provider initialization on its first real request.
        from .degradation import record_degradation

        record_degradation("embedding_warmup", type(exc).__name__)
        logger.warning("Embedder warmup failed (non-fatal): %s", exc)


def _start_embedding_warmup(
    provider,
    *,
    expected_dim: int,
    provider_name: str,
) -> asyncio.Task:
    """Schedule warmup and return immediately so readiness is not CPU-bound."""
    return asyncio.create_task(
        _warm_embedding_provider(
            provider,
            expected_dim=expected_dim,
            provider_name=provider_name,
        ),
        name="embedding-warmup",
    )


async def _validate_database_role(engine, *, production: bool) -> None:
    """Refuse production startup when PostgreSQL can bypass tenant RLS."""
    if not production or engine.dialect.name != "postgresql":
        return
    async with engine.connect() as connection:
        role = (
            await connection.execute(
                text(
                    """
                    SELECT r.rolsuper, r.rolbypassrls
                    FROM pg_roles AS r
                    WHERE r.rolname = current_user
                    """
                )
            )
        ).mappings().one()
    if role["rolsuper"] or role["rolbypassrls"]:
        raise RuntimeError(
            "Unsafe production database role: the application connection must "
            "be NOSUPERUSER and NOBYPASSRLS so tenant policies are enforceable"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .db import (
        engine,
        AsyncSessionLocal,
        set_current_barrier_group,
        set_current_namespace,
    )
    from .config import get_settings
    from .kms import load_master_key
    from .scheduler import run_learning_maintenance_scheduler, run_retention_scheduler
    settings = get_settings()

    setup_logging(level=settings.log_level, json_logs=settings.log_json)

    _warn_insecure_secrets(settings)
    _validate_production_secrets(settings)
    await _validate_database_role(engine, production=_is_production)

    if settings.airgap_mode:
        _validate_airgap(settings)

    await load_master_key()

    # Encrypt legacy review-queue content and webhook signing secrets before the
    # service accepts traffic. The admin sentinel is transaction-local and is
    # cleared immediately after this one-time, idempotent upgrade pass.
    from .secret_storage import protect_legacy_sensitive_rows
    set_current_namespace("__admin__")
    set_current_barrier_group(None)
    try:
        async with AsyncSessionLocal() as migration_db:
            protected_rows = await protect_legacy_sensitive_rows(migration_db)
    finally:
        set_current_namespace(None)
        set_current_barrier_group(None)
    if protected_rows:
        logger.info(
            "Protected legacy sensitive rows",
            extra={"rows_updated": protected_rows},
        )

    # Warm the embedder in the background. Local model inference can take
    # minutes on shared CPUs, so it must not hold liveness and readiness
    # endpoints hostage during a rolling deployment.
    from .embeddings import get_embedding_provider
    _provider = get_embedding_provider()
    embedding_warmup_task = _start_embedding_warmup(
        _provider,
        expected_dim=settings.embedding_dim,
        provider_name=settings.embedding_provider,
    )

    if settings.cors_origins == "*":
        logger.warning(
            "SECURITY: CORS_ORIGINS=* allows any website to make cross-origin requests. "
            "Set CORS_ORIGINS to a comma-separated list of trusted origins in production."
        )

    logger.info("Lians starting", extra={
        "embedding_provider": settings.embedding_provider,
        "airgap_mode": settings.airgap_mode,
        "llm_stage": settings.supersession_llm_stage,
        "kms_provider": settings.kms_provider,
        "merkle_batch_enabled": settings.merkle_batch_enabled,
        "llm_adjudication_async": settings.llm_adjudication_async,
    })

    instrument_sqlalchemy(engine)

    scheduler_task: asyncio.Task | None = None
    if settings.retention_prune_interval_hours > 0:
        scheduler_task = asyncio.create_task(
            run_retention_scheduler(AsyncSessionLocal, settings.retention_prune_interval_hours),
            name="retention-scheduler",
        )

    learning_task: asyncio.Task | None = None
    if settings.learning_maintenance_interval_hours > 0:
        learning_task = asyncio.create_task(
            run_learning_maintenance_scheduler(
                AsyncSessionLocal,
                settings.learning_maintenance_interval_hours,
                settings.learning_maintenance_min_signals,
            ),
            name="learning-maintenance-scheduler",
        )

    durable_job_task: asyncio.Task | None = None
    if settings.durable_job_worker_mode == "embedded":
        from .durable_jobs import run_durable_job_worker
        from .job_handlers import default_job_handlers
        durable_job_task = asyncio.create_task(
            run_durable_job_worker(
                AsyncSessionLocal,
                default_job_handlers(),
                poll_seconds=settings.durable_job_poll_seconds,
            ),
            name="durable-job-worker",
        )
    elif settings.durable_job_worker_mode not in {"external", "disabled"}:
        raise RuntimeError(
            "DURABLE_JOB_WORKER_MODE must be embedded, external, or disabled"
        )

    yield

    for task in (
        embedding_warmup_task,
        scheduler_task,
        learning_task,
        durable_job_task,
    ):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    logger.info("Lians shutdown")


_runtime_settings = get_settings()
_is_production = _runtime_settings.deployment_environment.strip().lower() in {
    "prod",
    "production",
}
_docs_enabled = (
    _runtime_settings.expose_api_docs
    if _runtime_settings.expose_api_docs is not None
    else not _is_production
)

app = FastAPI(
    title="Lians",
    description=(
        "Cross-platform decision evidence, reconstruction, and governed memory "
        "for regulated AI"
    ),
    version="0.5.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
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


instrument_fastapi(app)

# CORS — allows the demo/index.html page to call the API from a browser.
# In production, set CORS_ORIGINS to a comma-separated list of trusted origins.
_cors_origins = [o.strip() for o in (get_settings().cors_origins or "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # API keys are explicit headers, not browser cookies. Credentialed wildcard
    # CORS is both invalid in browsers and unnecessarily broad.
    allow_credentials="*" not in _cors_origins,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-API-Key",
        "X-Admin-Secret",
        "Idempotency-Key",
        "X-Request-ID",
    ],
    expose_headers=[
        "X-Request-ID",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "Retry-After",
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
    fingerprint_secret=get_settings().api_secret_seed,
)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=get_settings().max_request_body_bytes,
)
app.add_middleware(SecurityHeadersMiddleware, production=_is_production)

app.include_router(memory_router)
app.include_router(audit_router)
app.include_router(privacy_router)
app.include_router(admin_router)
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
app.include_router(decision_envelopes_router)
app.include_router(decision_evidence_router)
app.include_router(evidence_router)
app.include_router(metrics_router)
app.include_router(otlp_router)
app.include_router(validmind_router)
app.include_router(validmind_legacy_router)
app.include_router(learning_router)


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

    checks: dict[str, str] = {}

    # DB — SELECT 1 with a 2-second timeout
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=2.0)
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {type(exc).__name__}"

    # Redis — PING with a 1-second timeout (skipped when cache is disabled)
    if get_settings().recall_cache_enabled:
        try:
            await asyncio.wait_for(_get_redis().ping(), timeout=1.0)
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
    else:
        checks["redis"] = "disabled"

    from .degradation import recent_degradations
    degradations = recent_degradations()
    all_ok = all(v in ("ok", "disabled") for v in checks.values())
    status = "ok" if all_ok and not degradations else "degraded"
    details_enabled = (
        get_settings().expose_health_details
        if get_settings().expose_health_details is not None
        else not _is_production
    )
    content = {"status": status}
    if details_enabled:
        content.update({
            "checks": checks,
            "recent_degradations": degradations,
        })
    return JSONResponse(
        content=content,
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
