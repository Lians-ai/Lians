"""ValidMind custom-integration reference API."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, literal, select, text, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    Agent,
    ConflictFlag,
    ValidMindLegacyModelAlias,
    ValidMindModelInventory,
    ValidMindModelLink,
)
from ..mutation_safety import MutationVersionConflict, assert_expected_updated_at
from ..validmind_inventory import validmind_external_id
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/api/v1", tags=["validmind"])

_VERSION_LIMIT_PER_MODEL = 100
_MODEL_PAGE_LIMIT = 250
_OFFSET_LIMIT = 50_000
_RESERVED_MODEL_METADATA_KEYS = (
    "lians_model_id",
    "lians_agent_id",
    "lians_scope_id",
    "versions",
    "versions_total",
    "versions_complete",
    "versions_limit",
    "decision_count",
    "genai_span_count",
    "vm_cuid",
    "vm_link_updated_at",
)


class ValidMindUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_updated_at: datetime | None
    vm_cuid: str = Field(min_length=1, max_length=512)


class ValidMindModelMetadata(BaseModel):
    """Typed integration-owned fields plus provider-specific agent metadata."""

    model_config = ConfigDict(extra="allow")

    lians_model_id: str | None = None
    lians_agent_id: str | None = None
    lians_scope_id: str | None = None
    versions: list[str] | None = None
    versions_total: int | None = Field(default=None, ge=0)
    versions_complete: bool | None = None
    versions_limit: int | None = Field(default=None, ge=1)
    decision_count: int | None = Field(default=None, ge=0)
    genai_span_count: int | None = Field(default=None, ge=0)
    vm_cuid: str | None = None
    vm_link_updated_at: datetime | None = None


class ValidMindModelOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    status: Literal["active"]
    resource_type: Literal["agent", "llm", "ml_model"]
    metadata: ValidMindModelMetadata
    created_at: datetime
    updated_at: datetime


class ValidMindTicketMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    memory_a_id: str
    memory_b_id: str
    confidence: float
    resolver_note: str | None


class ValidMindTicketOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    title: str
    status: str
    resource_type: Literal["finding"]
    metadata: ValidMindTicketMetadata
    created_at: datetime
    updated_at: datetime


def _external_id(kind: str, source_id: str, scope_id: str | None = None) -> str:
    return validmind_external_id(kind, source_id, scope_id)


def _external_kind(external_id: str) -> str | None:
    for kind in ("model", "agent"):
        prefix = f"lians-{kind}-"
        suffix = external_id.removeprefix(prefix)
        if (
            external_id.startswith(prefix)
            and len(suffix) == 20
            and all(character in "0123456789abcdef" for character in suffix)
        ):
            return kind
    return None


async def _ensure_sqlite_id_functions(db: AsyncSession) -> None:
    if db.get_bind().dialect.name != "sqlite":
        return
    from ..validmind_inventory import validmind_legacy_model_id

    connection = await db.connection()

    def register(sync_connection) -> None:
        sync_connection.connection.create_function(
            "lians_external_id",
            3,
            validmind_external_id,
            deterministic=True,
        )
        sync_connection.connection.create_function(
            "lians_legacy_model_id",
            1,
            validmind_legacy_model_id,
            deterministic=True,
        )

    await connection.run_sync(register)


async def _records_from_keys(
    db: AsyncSession,
    namespace: str,
    key_rows: list[Any],
) -> list[dict[str, Any]]:
    if not key_rows:
        return []
    model_keys = [
        (str(row.scope_id), str(row.source_id))
        for row in key_rows
        if row.kind == "model"
    ]
    agent_ids = [str(row.source_id) for row in key_rows if row.kind == "agent"]
    inventories: dict[tuple[str, str], ValidMindModelInventory] = {}
    if model_keys:
        inventory_rows = (
            await db.execute(
                select(ValidMindModelInventory).where(
                    ValidMindModelInventory.namespace == namespace,
                    tuple_(
                        ValidMindModelInventory.scope_id,
                        ValidMindModelInventory.model_id,
                    ).in_(model_keys),
                )
            )
        ).scalars().all()
        inventories = {
            (str(row.scope_id), str(row.model_id)): row for row in inventory_rows
        }
    agents: dict[str, Agent] = {}
    if agent_ids:
        agent_rows = (
            await db.execute(
                select(Agent).where(
                    Agent.namespace == namespace,
                    Agent.agent_id.in_(agent_ids),
                )
            )
        ).scalars().all()
        agents = {str(row.agent_id): row for row in agent_rows}

    legacy_ids = {
        row.legacy_external_id for row in inventories.values()
    }
    aliases: dict[str, ValidMindLegacyModelAlias] = {}
    if legacy_ids:
        alias_rows = (
            await db.execute(
                select(ValidMindLegacyModelAlias).where(
                    ValidMindLegacyModelAlias.namespace == namespace,
                    ValidMindLegacyModelAlias.legacy_external_id.in_(legacy_ids),
                )
            )
        ).scalars().all()
        aliases = {str(row.legacy_external_id): row for row in alias_rows}

    canonical_ids = {row.external_id for row in inventories.values()}
    if len(canonical_ids) != len(inventories):
        raise HTTPException(
            status_code=409,
            detail="Opaque ValidMind identifier collision; contact the operator",
        )
    if any(
        row.legacy_external_id in canonical_ids
        and row.legacy_external_id != row.external_id
        for row in inventories.values()
    ):
        raise HTTPException(
            status_code=409,
            detail="Legacy and scoped ValidMind identifiers collide",
        )
    canonical_ids.update(
        _external_id("agent", source_id) for source_id in agent_ids
    )
    link_ids = canonical_ids | legacy_ids
    links: dict[str, ValidMindModelLink] = {}
    if link_ids:
        link_rows = (
            await db.execute(
                select(ValidMindModelLink).where(
                    ValidMindModelLink.namespace == namespace,
                    ValidMindModelLink.external_id.in_(link_ids),
                )
            )
        ).scalars().all()
        links = {str(row.external_id): row for row in link_rows}

    records: list[dict[str, Any]] = []
    for key in key_rows:
        kind = str(key.kind)
        source_id = str(key.source_id)
        if kind == "model":
            inventory = inventories.get((str(key.scope_id), source_id))
            if inventory is None:
                raise HTTPException(
                    status_code=409,
                    detail="ValidMind inventory changed while the page was assembled",
                )
            external_id = str(inventory.external_id)
            version_total = int(inventory.version_count)
            metadata: dict[str, Any] = {
                "lians_model_id": source_id,
                "lians_scope_id": str(inventory.scope_id),
                "versions": list(inventory.versions or []),
                "versions_total": version_total,
                "versions_complete": version_total <= _VERSION_LIMIT_PER_MODEL,
                "versions_limit": _VERSION_LIMIT_PER_MODEL,
                "decision_count": int(inventory.decision_count),
                "genai_span_count": int(inventory.span_count),
            }
            created_at = inventory.created_at
            updated_at = inventory.updated_at
            resource_type = "llm" if inventory.span_count else "ml_model"
            link = links.get(external_id)
            if link is None:
                alias = aliases.get(str(inventory.legacy_external_id))
                if (
                    alias is not None
                    and alias.target_count == 1
                    and alias.canonical_external_id == external_id
                ):
                    link = links.get(str(inventory.legacy_external_id))
        else:
            agent = agents.get(source_id)
            if agent is None:
                raise HTTPException(
                    status_code=409,
                    detail="ValidMind agent inventory changed while the page was assembled",
                )
            external_id = _external_id("agent", source_id)
            metadata = dict(agent.config or {})
            for reserved_key in _RESERVED_MODEL_METADATA_KEYS:
                metadata.pop(reserved_key, None)
            metadata["lians_agent_id"] = source_id
            created_at = updated_at = agent.created_at
            resource_type = "agent"
            link = links.get(external_id)
        if link is not None:
            metadata["vm_cuid"] = str(link.vm_cuid)
            metadata["vm_link_updated_at"] = link.updated_at.isoformat()
        records.append(
            {
                "id": external_id,
                "name": source_id,
                "status": "active",
                "resource_type": resource_type,
                "metadata": metadata,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return records


async def _model_records(
    db: AsyncSession,
    namespace: str,
    *,
    resource_type: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read an exact bounded page from maintained inventory rows only."""
    window = offset + limit
    branches: list[Any] = []

    if resource_type in {None, "llm"}:
        llm_keys = (
            select(
                literal("model").label("kind"),
                ValidMindModelInventory.model_id.label("source_id"),
                ValidMindModelInventory.scope_id.label("scope_id"),
                literal("llm").label("resource_type"),
            )
            .where(
                ValidMindModelInventory.namespace == namespace,
                ValidMindModelInventory.span_count > 0,
            )
            .order_by(
                ValidMindModelInventory.model_id,
                ValidMindModelInventory.scope_id,
            )
            .limit(window)
            .subquery("validmind_llm_keys")
        )
        branches.append(select(llm_keys))

    if resource_type in {None, "ml_model"}:
        model_keys = (
            select(
                literal("model").label("kind"),
                ValidMindModelInventory.model_id.label("source_id"),
                ValidMindModelInventory.scope_id.label("scope_id"),
                literal("ml_model").label("resource_type"),
            )
            .where(
                ValidMindModelInventory.namespace == namespace,
                ValidMindModelInventory.span_count == 0,
                ValidMindModelInventory.decision_count > 0,
            )
            .order_by(
                ValidMindModelInventory.model_id,
                ValidMindModelInventory.scope_id,
            )
            .limit(window)
            .subquery("validmind_ml_keys")
        )
        branches.append(select(model_keys))

    if resource_type in {None, "agent"}:
        agent_keys = (
            select(
                literal("agent").label("kind"),
                Agent.agent_id.label("source_id"),
                literal("").label("scope_id"),
                literal("agent").label("resource_type"),
            )
            .where(Agent.namespace == namespace)
            .order_by(Agent.agent_id)
            .limit(window)
            .subquery("validmind_agent_keys")
        )
        branches.append(select(agent_keys))

    if not branches:
        return []
    key_catalog = union_all(*branches).subquery("validmind_catalog_keys")
    key_rows = list(
        (
            await db.execute(
                select(key_catalog)
                .order_by(
                    key_catalog.c.resource_type,
                    key_catalog.c.source_id,
                    key_catalog.c.scope_id,
                )
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    if not key_rows:
        return []

    return await _records_from_keys(db, namespace, key_rows)


async def _model_record(
    db: AsyncSession,
    namespace: str,
    external_id: str,
) -> dict[str, Any] | None:
    """Resolve a canonical or uniquely aliased legacy ID with bounded indexes."""
    kind = _external_kind(external_id)
    if kind is None:
        return None
    if kind == "model":
        rows = list(
            (
                await db.execute(
                    select(ValidMindModelInventory)
                    .where(
                        ValidMindModelInventory.namespace == namespace,
                        ValidMindModelInventory.external_id == external_id,
                    )
                    .limit(2)
                )
            ).scalars().all()
        )
        alias = (
            await db.execute(
                select(ValidMindLegacyModelAlias).where(
                    ValidMindLegacyModelAlias.namespace == namespace,
                    ValidMindLegacyModelAlias.legacy_external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if rows and alias is not None:
            if (
                alias.target_count != 1
                or alias.canonical_external_id != rows[0].external_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Legacy and scoped ValidMind identifiers collide",
                )
        elif not rows:
            if alias is None:
                return None
            if alias.target_count != 1 or alias.canonical_external_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Legacy ValidMind model identifier is ambiguous across "
                        "information-barrier scopes; use the scoped model ID"
                    ),
                )
            rows = list(
                (
                    await db.execute(
                        select(ValidMindModelInventory)
                        .where(
                            ValidMindModelInventory.namespace == namespace,
                            ValidMindModelInventory.external_id
                            == alias.canonical_external_id,
                        )
                        .limit(2)
                    )
                ).scalars().all()
            )
        if len(rows) > 1:
            raise HTTPException(
                status_code=409,
                detail="Opaque ValidMind identifier collision; contact the operator",
            )
        if not rows:
            raise HTTPException(
                status_code=409,
                detail="ValidMind legacy alias points to a missing scoped model",
            )
        inventory = rows[0]
        keys = [
            SimpleNamespace(
                kind="model",
                source_id=inventory.model_id,
                scope_id=inventory.scope_id,
            )
        ]
    else:
        # Agent IDs remain namespace-wide. Their small one-row-per-resource
        # catalog is already bounded, but opaque lookup still needs a hash
        # predicate on SQLite/PostgreSQL. A bounded page cannot reverse SHA-256,
        # so scan only indexed Agent identities through the registered UDF.
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            await _ensure_sqlite_id_functions(db)
            predicate = func.lians_external_id("agent", Agent.agent_id, None) == external_id
            agent_ids = list(
                (
                    await db.execute(
                        select(Agent.agent_id)
                        .where(Agent.namespace == namespace, predicate)
                        .order_by(Agent.agent_id)
                        .limit(2)
                    )
                ).scalars().all()
            )
        elif dialect == "postgresql":
            agent_ids = list(
                (
                    await db.execute(
                        text(
                            "SELECT agent_id FROM "
                            "public.lians_validmind_lookup_agent("
                            ":namespace, :agent_external_id)"
                        ),
                        {
                            "namespace": namespace,
                            "agent_external_id": external_id,
                        },
                    )
                ).scalars().all()
            )
        else:
            raise RuntimeError(
                f"ValidMind agent identifiers are unsupported on {dialect}"
            )
        if len(agent_ids) > 1:
            raise HTTPException(
                status_code=409,
                detail="Opaque ValidMind identifier collision; contact the operator",
            )
        if not agent_ids:
            return None
        keys = [
            SimpleNamespace(
                kind="agent",
                source_id=agent_ids[0],
                scope_id="",
            )
        ]
    records = await _records_from_keys(db, namespace, keys)
    return records[0] if records else None


@router.get("/health")
async def validmind_health(
    auth: AuthContext = Depends(get_auth),
):
    auth.require("read")
    auth.require_unbarriered()
    return {"status": "healthy"}


@router.get(
    "/models",
    response_model=list[ValidMindModelOut],
    response_model_exclude_none=True,
)
async def list_validmind_models(
    response: Response,
    resource_type: Literal["agent", "llm", "ml_model"] | None = None,
    offset: int = Query(0, ge=0, le=_OFFSET_LIMIT),
    limit: int = Query(100, ge=1, le=_MODEL_PAGE_LIMIT),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    records = await _model_records(
        db,
        auth.namespace,
        resource_type=resource_type,
        offset=offset,
        limit=limit + 1,
    )
    response.headers["X-Lians-Page-Offset"] = str(offset)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Complete"] = str(len(records) <= limit).lower()
    return records[:limit]


@router.get(
    "/models/{external_id}",
    response_model=ValidMindModelOut,
    response_model_exclude_none=True,
)
async def get_validmind_model(
    external_id: str,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    item = await _model_record(db, auth.namespace, external_id)
    if item is not None:
        return item
    raise HTTPException(status_code=404, detail="Model not found")


@router.put(
    "/models/{external_id}",
    response_model=ValidMindModelOut,
    response_model_exclude_none=True,
)
async def update_validmind_model(
    external_id: str,
    req: ValidMindUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("write")
    auth.require_unbarriered()
    if not req.vm_cuid.strip():
        raise HTTPException(status_code=400, detail="vm_cuid must not be empty")
    resolved = await _model_record(db, auth.namespace, external_id)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Model not found")
    canonical_external_id = str(resolved["id"])
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {
                "key": (
                    f"lians:validmind-link:{auth.namespace}:"
                    f"{canonical_external_id}"
                )
            },
        )
    row = (
        await db.execute(
            select(ValidMindModelLink)
            .where(
                ValidMindModelLink.namespace == auth.namespace,
                ValidMindModelLink.external_id == canonical_external_id,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        if req.expected_updated_at is not None:
            raise HTTPException(status_code=409, detail="Resource version conflict")
        row = ValidMindModelLink(
            namespace=auth.namespace,
            external_id=canonical_external_id,
            vm_cuid=req.vm_cuid,
        )
        db.add(row)
    else:
        if req.expected_updated_at is None:
            raise HTTPException(status_code=409, detail="Resource version conflict")
        try:
            assert_expected_updated_at(row.updated_at, req.expected_updated_at)
        except MutationVersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        row.vm_cuid = req.vm_cuid
        row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return await get_validmind_model(canonical_external_id, auth, db)


def _ticket(row: ConflictFlag) -> dict:
    return {
        "id": str(row.id),
        "name": f"Memory conflict for agent {row.agent_id}",
        "title": f"Conflicting evidence for {row.agent_id}",
        "status": row.status,
        "resource_type": "finding",
        "metadata": {
            "agent_id": row.agent_id,
            "memory_a_id": str(row.memory_a_id),
            "memory_b_id": str(row.memory_b_id),
            "confidence": row.confidence,
            "resolver_note": row.resolver_note,
        },
        "created_at": row.detected_at,
        "updated_at": row.resolved_at or row.detected_at,
    }


@router.get("/tickets", response_model=list[ValidMindTicketOut])
async def list_validmind_tickets(
    response: Response,
    offset: int = Query(0, ge=0, le=_OFFSET_LIMIT),
    limit: int = Query(100, ge=1, le=1000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    rows = (
        await db.execute(
            select(ConflictFlag)
            .where(ConflictFlag.namespace == auth.namespace)
            .order_by(ConflictFlag.detected_at.desc(), ConflictFlag.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    ).scalars().all()
    response.headers["X-Lians-Page-Offset"] = str(offset)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Complete"] = str(len(rows) <= limit).lower()
    return [_ticket(row) for row in rows[:limit]]


@router.get("/tickets/{ticket_id}", response_model=ValidMindTicketOut)
async def get_validmind_ticket(
    ticket_id: str,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    auth.require_unbarriered()
    try:
        import uuid
        parsed_id = uuid.UUID(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Ticket not found") from exc
    row = await db.get(ConflictFlag, parsed_id)
    if row is None or row.namespace != auth.namespace:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return _ticket(row)


@router.get("/schema")
async def validmind_schema(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    auth.require_unbarriered()
    return {
        "models": {
            "id": {"type": "string", "required": True},
            "name": {"type": "string", "required": True},
            "status": {"type": "string"},
            "resource_type": {"type": "string"},
            "metadata": {
                "type": "object",
                "properties": {
                    "lians_model_id": {"type": "string"},
                    "lians_agent_id": {"type": "string"},
                    "lians_scope_id": {
                        "type": "string",
                        "description": (
                            "Opaque information-barrier scope; raw barrier names "
                            "are never exposed"
                        ),
                    },
                    "versions": {"type": "array", "items": {"type": "string"}},
                    "versions_total": {"type": "integer"},
                    "versions_complete": {"type": "boolean"},
                    "versions_limit": {"type": "integer"},
                    "decision_count": {"type": "integer"},
                    "genai_span_count": {"type": "integer"},
                    "vm_cuid": {"type": "string"},
                    "vm_link_updated_at": {"type": "datetime"},
                },
            },
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
        },
        "tickets": {
            "id": {"type": "string", "required": True},
            "name": {"type": "string", "required": True},
            "status": {"type": "string"},
            "resource_type": {"type": "string"},
            "metadata": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "memory_a_id": {"type": "string"},
                    "memory_b_id": {"type": "string"},
                    "confidence": {"type": "number"},
                    "resolver_note": {"type": "string"},
                },
            },
            "created_at": {"type": "datetime"},
            "updated_at": {"type": "datetime"},
        },
    }


@router.get("/resource-types")
async def validmind_resource_types(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    auth.require_unbarriered()
    return [
        {"id": "ml_model", "name": "Machine-learning model"},
        {"id": "llm", "name": "Large language model"},
        {"id": "agent", "name": "AI agent"},
    ]
