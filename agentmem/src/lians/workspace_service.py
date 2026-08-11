"""Hosted workspace metadata and governed, push-based source connectors."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .memory_service import add_memory_idempotent
from .models import Agent, Connector, IdempotencyKey, Memory, Workspace
from .schemas import (
    ConnectorCreate,
    ConnectorIngestRequest,
    ConnectorIngestResult,
    ConnectorOut,
    ConnectorUpdate,
    MemoryAdd,
    WorkspaceOut,
    WorkspaceUpdate,
)

CONNECTOR_CATALOG = [
    {
        "kind": "direct",
        "label": "Direct SDK",
        "description": "Push normalized application events from any Python or TypeScript service.",
        "delivery": "push",
        "config_fields": [],
    },
    {
        "kind": "github",
        "label": "GitHub",
        "description": "Push issues, pull requests, decisions, and repository conventions from a GitHub App gateway.",
        "delivery": "push",
        "config_fields": ["repository", "event_types"],
    },
    {
        "kind": "slack",
        "label": "Slack",
        "description": "Push selected, consented channel or saved-item events from a Slack app gateway.",
        "delivery": "push",
        "config_fields": ["team_id", "channel_ids"],
    },
    {
        "kind": "notion",
        "label": "Notion",
        "description": "Push approved page revisions from a workspace integration without storing OAuth tokens in Lians.",
        "delivery": "push",
        "config_fields": ["workspace_id", "database_ids"],
    },
    {
        "kind": "google_drive",
        "label": "Google Drive",
        "description": "Push approved document revisions from a Drive integration gateway.",
        "delivery": "push",
        "config_fields": ["drive_id", "folder_ids"],
    },
    {
        "kind": "webhook",
        "label": "Webhook source",
        "description": "Normalize events from an existing integration or automation platform.",
        "delivery": "push",
        "config_fields": ["event_schema"],
    },
]

_SECRET_MARKERS = ("secret", "token", "password", "api_key", "credential")


def _connector_out(row: Connector) -> ConnectorOut:
    return ConnectorOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        agent_id=row.agent_id,
        scope=row.scope,
        status=row.status,
        config=dict(row.config or {}),
        cursor=row.cursor,
        last_sync_at=row.last_sync_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_public_config(config: dict) -> None:
    unsafe: list[str] = []

    def walk(value, path: str, depth: int) -> None:
        if depth > 6:
            raise ValueError("Connector config nesting exceeds the supported depth")
        if isinstance(value, dict):
            for key, nested in value.items():
                key_path = f"{path}.{key}" if path else str(key)
                if any(marker in str(key).casefold() for marker in _SECRET_MARKERS):
                    unsafe.append(key_path)
                walk(nested, key_path, depth + 1)
        elif isinstance(value, list):
            for index, nested in enumerate(value[:100]):
                walk(nested, f"{path}[{index}]", depth + 1)

    walk(config, "", 0)
    if unsafe:
        raise ValueError(
            "Connector config cannot contain credentials; keep provider secrets in the integration gateway: "
            + ", ".join(sorted(unsafe))
        )


async def get_workspace(db: AsyncSession, namespace: str) -> WorkspaceOut:
    row = await db.get(Workspace, namespace)
    memory_count = int((await db.execute(
        select(func.count()).select_from(Memory).where(Memory.namespace == namespace)
    )).scalar_one())
    agent_count = int((await db.execute(
        select(func.count()).select_from(Agent).where(Agent.namespace == namespace)
    )).scalar_one())
    connector_count = int((await db.execute(
        select(func.count()).select_from(Connector).where(Connector.namespace == namespace)
    )).scalar_one())
    return WorkspaceOut(
        namespace=namespace,
        display_name=row.display_name if row else namespace,
        plan=row.plan if row else "developer",
        region=row.region if row else None,
        settings=dict(row.settings or {}) if row else {},
        created_at=row.created_at if row else None,
        updated_at=row.updated_at if row else None,
        stats={"memories": memory_count, "agents": agent_count, "connectors": connector_count},
    )


async def update_workspace(
    db: AsyncSession, namespace: str, req: WorkspaceUpdate,
) -> WorkspaceOut:
    row = await db.get(Workspace, namespace)
    if row is None:
        row = Workspace(namespace=namespace, display_name=req.display_name)
        db.add(row)
    row.display_name = req.display_name
    row.plan = req.plan
    row.region = req.region
    row.settings = dict(req.settings)
    await chain_log(
        db,
        namespace=namespace,
        agent_id="__workspace__",
        op="workspace_updated",
        payload={"plan": req.plan, "region": req.region, "setting_keys": sorted(req.settings)},
    )
    await db.commit()
    return await get_workspace(db, namespace)


async def list_connectors(db: AsyncSession, namespace: str) -> list[ConnectorOut]:
    rows = list((await db.execute(
        select(Connector)
        .where(Connector.namespace == namespace)
        .order_by(Connector.created_at.desc(), Connector.id.desc())
    )).scalars().all())
    return [_connector_out(row) for row in rows]


async def create_connector(
    db: AsyncSession, namespace: str, req: ConnectorCreate,
) -> ConnectorOut:
    _validate_public_config(req.config)
    row = Connector(
        namespace=namespace,
        kind=req.kind,
        name=req.name,
        agent_id=req.agent_id,
        scope=req.scope,
        config=dict(req.config),
    )
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        namespace=namespace,
        agent_id=req.agent_id,
        op="connector_created",
        payload={"connector_id": str(row.id), "kind": row.kind, "name": row.name},
    )
    await db.commit()
    await db.refresh(row)
    return _connector_out(row)


async def update_connector(
    db: AsyncSession, namespace: str, connector_id: UUID, req: ConnectorUpdate,
) -> ConnectorOut | None:
    row = await db.get(Connector, connector_id)
    if row is None or row.namespace != namespace:
        return None
    if req.config is not None:
        _validate_public_config(req.config)
        row.config = dict(req.config)
    if req.name is not None:
        row.name = req.name
    if req.status is not None:
        row.status = req.status
    if req.scope is not None:
        row.scope = req.scope
    await chain_log(
        db,
        namespace=namespace,
        agent_id=row.agent_id,
        op="connector_updated",
        payload={"connector_id": str(row.id), "status": row.status},
    )
    await db.commit()
    await db.refresh(row)
    return _connector_out(row)


async def ingest_connector_events(
    db: AsyncSession,
    namespace: str,
    connector_id: UUID,
    req: ConnectorIngestRequest,
    *,
    barrier_override: str | None = None,
) -> ConnectorIngestResult | None:
    connector = await db.get(Connector, connector_id)
    if connector is None or connector.namespace != namespace:
        return None
    if connector.status != "active":
        raise ValueError("Connector must be active before it can ingest events")

    memory_ids: list[UUID] = []
    duplicates = 0
    for event in req.events:
        digest = hashlib.sha256(
            f"{connector.id}\0{event.external_id}".encode()
        ).hexdigest()
        key = f"connector:{digest}"
        existing = await db.get(IdempotencyKey, (key, namespace))
        if existing is not None:
            duplicates += 1
            memory_ids.append(existing.memory_id)
            continue
        metadata = dict(event.metadata)
        metadata["_connector"] = {
            "id": str(connector.id),
            "kind": connector.kind,
            "external_id": event.external_id,
        }
        result = await add_memory_idempotent(
            db,
            namespace,
            MemoryAdd(
                agent_id=connector.agent_id,
                content=event.content,
                event_time=event.event_time,
                source=f"connector:{connector.kind}:{connector.name}",
                subject_id=event.subject_id,
                metadata=metadata,
                importance=event.importance,
                scope=connector.scope,
                write_mode=req.write_mode,
            ),
            key,
            barrier_override=barrier_override,
        )
        memory_ids.append(result.id)

    connector = await db.get(Connector, connector_id)
    connector.cursor = req.cursor
    connector.last_sync_at = datetime.now(timezone.utc)
    connector.last_error = None
    await chain_log(
        db,
        namespace=namespace,
        agent_id=connector.agent_id,
        op="connector_ingested",
        payload={
            "connector_id": str(connector.id),
            "accepted": len(req.events) - duplicates,
            "duplicates": duplicates,
            "cursor_set": req.cursor is not None,
        },
    )
    await db.commit()
    return ConnectorIngestResult(
        connector_id=connector.id,
        accepted=len(req.events) - duplicates,
        duplicates=duplicates,
        memory_ids=memory_ids,
        cursor=connector.cursor,
    )
