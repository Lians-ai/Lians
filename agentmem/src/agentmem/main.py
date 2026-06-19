from fastapi import FastAPI
from contextlib import asynccontextmanager

from .api.routes_memory import router as memory_router
from .api.routes_audit import router as audit_router
from .api.routes_privacy import router as privacy_router
from .api.routes_admin import router as admin_router
from .api.routes_supersessions import router as supersessions_router
from .telemetry import instrument_fastapi, instrument_sqlalchemy


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is managed by Alembic — run `alembic upgrade head` before starting.
    from .db import engine
    instrument_sqlalchemy(engine)
    yield


app = FastAPI(
    title="AgentMem",
    description="Financial-agent memory layer — bitemporal, auditable, erasable",
    version="0.2.0",
    lifespan=lifespan,
)

instrument_fastapi(app)

app.include_router(memory_router)
app.include_router(audit_router)
app.include_router(privacy_router)
app.include_router(admin_router)
app.include_router(supersessions_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
