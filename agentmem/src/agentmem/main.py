import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db as _get_db
from .api.routes_memory import router as memory_router
from .api.routes_audit import router as audit_router
from .api.routes_privacy import router as privacy_router
from .api.routes_admin import router as admin_router
from .api.routes_supersessions import router as supersessions_router
from .telemetry import instrument_fastapi, instrument_sqlalchemy
from .middleware import (
    setup_logging,
    RequestIDMiddleware,
    AccessLogMiddleware,
    RateLimitMiddleware,
)

logger = logging.getLogger("agentmem.startup")

_AIRGAP_SAFE_PROVIDERS = {"sentence-transformers", "local"}


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .db import engine
    from .config import get_settings
    settings = get_settings()

    setup_logging(level=settings.log_level, json_logs=settings.log_json)

    if settings.airgap_mode:
        _validate_airgap(settings)

    logger.info("AgentMem starting", extra={
        "embedding_provider": settings.embedding_provider,
        "airgap_mode": settings.airgap_mode,
        "llm_stage": settings.supersession_llm_stage,
    })

    instrument_sqlalchemy(engine)
    yield

    logger.info("AgentMem shutdown")


app = FastAPI(
    title="AgentMem",
    description="Financial-agent memory layer — bitemporal, auditable, erasable",
    version="0.2.0",
    lifespan=lifespan,
)

instrument_fastapi(app)

# Middleware is applied in reverse registration order (last added = outermost).
# Order: RequestID → AccessLog → RateLimit → routes
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)

app.include_router(memory_router)
app.include_router(audit_router)
app.include_router(privacy_router)
app.include_router(admin_router)
app.include_router(supersessions_router)


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

    # Redis — PING with a 1-second timeout
    try:
        await asyncio.wait_for(_get_redis().ping(), timeout=1.0)
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        content={"status": "ok" if all_ok else "degraded", "checks": checks},
        status_code=200 if all_ok else 503,
    )
