"""
Database engine and session factory.

Change 9 (Postgres RLS enforcement)
-------------------------------------
When ``config.rls_barriers_enabled=True``, each session sets the Postgres
session variable ``agentmem.barrier_group`` before executing queries.  The
RLS policy on ``live_facts`` and ``memories`` then enforces the information
barrier at the database layer, eliminating the app-layer ``OR barrier_group
IS NULL`` post-filter.

RLS is applied automatically by Alembic migration 0011_rls_barriers.  The
effective policy on both tables is:

    USING (
        barrier_group IS NULL
        OR current_setting('agentmem.barrier_group', true) IS NULL
        OR barrier_group = current_setting('agentmem.barrier_group', true)
    )

    WITH FORCE ROW LEVEL SECURITY (applied to table owner as well)

Admin routes use get_db() and never SET the session variable, so
current_setting() returns NULL → the IS NULL branch fires → all rows visible.

Barrier-scoped routes use get_db_with_barrier(group) which issues
SET LOCAL agentmem.barrier_group = '<group>' → only unbarriered rows +
rows matching the group are visible.

``rls_barriers_enabled=True`` is the default after migration 0011 is applied.
"""
import contextvars
import ssl as ssl_module
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


def parse_db_url(database_url: str) -> tuple[str, dict]:
    """
    Strip ssl/sslmode query params from a postgresql+asyncpg URL and return
    a (clean_url, connect_args) pair.

    asyncpg does not accept ssl params in the URL the same way libpq does.
    Extracting them here and passing via connect_args is the correct approach.
    """
    # Managed platforms commonly expose a libpq-style URL even though Lians
    # always uses SQLAlchemy's asyncpg driver. Normalize the scheme once at the
    # trust boundary rather than requiring fragile shell rewrites in every
    # deployment manifest.
    if database_url.startswith("postgres://"):
        database_url = "postgresql+asyncpg://" + database_url[len("postgres://"):]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+asyncpg://" + database_url[len("postgresql://"):]

    # TLS query normalization below is an asyncpg-only boundary. Rebuilding a
    # SQLite URL with urllib's generic ``urlunparse`` drops the empty authority
    # delimiter (for example ``sqlite+aiosqlite:///:memory:`` becomes invalid),
    # breaking the documented local/benchmark mode before it can create an
    # engine. Other dialects own their connection arguments unchanged.
    if not database_url.startswith("postgresql+asyncpg://"):
        return database_url, {}

    parsed = urlparse(database_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    ssl_arg = None
    ssl_mode: str | None = None
    for key in ("sslmode", "ssl"):
        if key in params:
            val = params.pop(key)[0].lower()
            if val in ("disable", "false", "0", "no"):
                ssl_arg = False
            elif val in ("require", "true", "1", "yes"):
                ssl_mode = "require"
                ssl_arg = ssl_mode
            elif val in ("prefer", "allow", "verify-ca", "verify-full"):
                ssl_mode = val
                ssl_arg = val
            break

    ssl_root_cert = params.pop("sslrootcert", [""])[0]
    ssl_client_cert = params.pop("sslcert", [""])[0]
    ssl_client_key = params.pop("sslkey", [""])[0]
    ssl_client_password = params.pop("sslpassword", [""])[0]
    if bool(ssl_client_cert) != bool(ssl_client_key):
        raise ValueError("PostgreSQL TLS client certificate and key must be configured together")
    if ssl_client_password and not ssl_client_key:
        raise ValueError("PostgreSQL TLS key password requires a client key")
    if ssl_mode in {"verify-ca", "verify-full"} and (
        ssl_root_cert or ssl_client_cert
    ):
        ssl_context = ssl_module.create_default_context(
            ssl_module.Purpose.SERVER_AUTH,
            cafile=(
                None
                if not ssl_root_cert or ssl_root_cert.casefold() == "system"
                else ssl_root_cert
            ),
        )
        ssl_context.check_hostname = ssl_mode == "verify-full"
        ssl_context.verify_mode = ssl_module.CERT_REQUIRED
        if ssl_client_cert:
            ssl_context.load_cert_chain(
                ssl_client_cert,
                keyfile=ssl_client_key,
                password=ssl_client_password or None,
            )
        ssl_arg = ssl_context

    new_query = urlencode({k: v[0] for k, v in params.items()})
    clean_url = urlunparse(parsed._replace(query=new_query))
    connect_args = {"ssl": ssl_arg} if ssl_arg is not None else {}
    return clean_url, connect_args


def _make_engine():
    settings = get_settings()
    url, connect_args = parse_db_url(settings.database_url)
    if url.startswith("postgresql+asyncpg://"):
        connect_args = {
            **connect_args,
            "server_settings": {
                "statement_timeout": str(settings.database_statement_timeout_ms),
                "lock_timeout": str(settings.database_lock_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    settings.database_idle_transaction_timeout_ms
                ),
                "application_name": "lians-api",
            },
        }
        return create_async_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
        )
    # SQLite local mode uses StaticPool/NullPool depending on the URL. Those
    # pool implementations reject QueuePool-only sizing arguments.
    return create_async_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _report_pool_state() -> None:
    try:
        pool = engine.sync_engine.pool
        from .metrics import set_db_pool_state

        set_db_pool_state(
            size=int(pool.size()),
            checked_out=int(pool.checkedout()),
            overflow=max(0, int(pool.overflow())),
        )
    except (AttributeError, TypeError, ValueError):
        # Non-queue pools used by isolated tests do not expose this inventory.
        return


@event.listens_for(engine.sync_engine, "checkout")
def _pool_checkout(*_args) -> None:
    _report_pool_state()


@event.listens_for(engine.sync_engine, "checkin")
def _pool_checkin(*_args) -> None:
    _report_pool_state()


@event.listens_for(engine.sync_engine, "connect")
def _pool_connect(dbapi_connection, _connection_record) -> None:
    if engine.sync_engine.dialect.name == "sqlite":
        from .validmind_inventory import (
            validmind_external_id,
            validmind_legacy_model_id,
        )

        dbapi_connection.create_function(
            "lians_external_id",
            3,
            validmind_external_id,
            deterministic=True,
        )
        dbapi_connection.create_function(
            "lians_legacy_model_id",
            1,
            validmind_legacy_model_id,
            deterministic=True,
        )
    _report_pool_state()


_report_pool_state()


# ── Row-Level Security namespace, re-applied on every transaction ─────────────
# get_auth() records the caller's namespace here. The begin listener below then
# re-applies it as the transaction-local ``app.current_namespace`` GUC at the
# start of EVERY transaction — crucially including ones autobegun after a
# mid-request ``commit()``. Without this, commit() clears the is_local GUC and
# the next query (e.g. ``db.refresh`` / metering after a write) runs with no
# namespace set, so RLS hides the just-written row and the request 500s even
# though the data committed. The ContextVar is task-local, so pooled connections
# never leak one tenant's namespace into another request.
current_namespace: contextvars.ContextVar = contextvars.ContextVar(
    "agentmem_current_namespace", default=None
)
current_barrier_group: contextvars.ContextVar = contextvars.ContextVar(
    "agentmem_current_barrier_group", default=None
)


def set_current_namespace(namespace: Optional[str]) -> None:
    current_namespace.set(namespace)


def set_current_barrier_group(barrier_group: Optional[str]) -> None:
    """Record the authenticated caller's information barrier for every transaction."""
    current_barrier_group.set(barrier_group)


@event.listens_for(engine.sync_engine, "begin")
def _apply_rls_namespace(conn) -> None:
    ns = current_namespace.get()
    if not ns:
        return
    # Runs on the just-begun transaction inside the async greenlet. ns is an
    # internal value (ns_<id> / __admin__); set_config takes no bind params in
    # this raw context, so escape single quotes defensively.
    safe = ns.replace("'", "''")
    conn.exec_driver_sql(f"SELECT set_config('app.current_namespace', '{safe}', true)")
    # An empty value is the explicit unbarriered/admin sentinel used by the RLS
    # policies. Setting it on every transaction prevents a pooled connection or
    # a mid-request commit from silently dropping the caller's barrier context.
    barrier = current_barrier_group.get() or ""
    safe_barrier = barrier.replace("'", "''")
    conn.exec_driver_sql(
        f"SELECT set_config('agentmem.barrier_group', '{safe_barrier}', true)"
    )


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            # ContextVars are task-local, but clearing explicitly makes request
            # teardown safe even under unusual ASGI task reuse/background work.
            set_current_namespace(None)
            set_current_barrier_group(None)


async def get_db_with_barrier(barrier_group: Optional[str]) -> AsyncSession:
    """Session factory that sets the RLS barrier variable (Change 9).

    Use in place of ``get_db`` for agent-scoped routes when
    ``rls_barriers_enabled=True``.  Admin/compliance routes that need to see
    all memories should continue using the plain ``get_db``.
    """
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        if settings.rls_barriers_enabled and barrier_group is not None:
            if session.get_bind().dialect.name == "postgresql":
                from sqlalchemy import text as _text
                await session.execute(
                    _text("SELECT set_config('agentmem.barrier_group', :bg, true)"),
                    {"bg": barrier_group},
                )
            else:
                pass  # non-PG backend — RLS not available
        yield session
