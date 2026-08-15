"""Opaque zero-knowledge sync storage for authenticated Lians workspaces."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import SyncDevice, SyncRevision, SyncWorkspace
from .deps import AuthContext, get_sync_auth

router = APIRouter(prefix="/v1/sync", tags=["zero-knowledge-sync"])

SYNC_VERSION = 1
GRANT_FORMAT = "lians-device-grant"
REVISION_FORMAT = "lians-encrypted-profile-revision"
MAX_ENVELOPE_BYTES = 1_500_000
MAX_DEVICES = 20
MAX_REVISIONS = 10_000
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    epoch: int = Field(ge=1, le=2_147_483_647)
    root_device: dict[str, Any]


class DeviceGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant: dict[str, Any]
    signature: dict[str, Any]


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: dict[str, Any]


class WorkspaceDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool
    confirmation: str


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _exact(document: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != fields:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    return document


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} is invalid") from exc
    if str(parsed) != value:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    return value


def _unb64(value: Any, *, label: str, length: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_ENVELOPE_BYTES * 2:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail=f"{label} is invalid") from exc
    if length is not None and len(decoded) != length:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    return decoded


def _device_id(exchange: bytes, signing: bytes) -> str:
    return hashlib.sha256(b"lians-device-v1\0" + exchange + signing).hexdigest()


def _device(document: Any) -> dict[str, str]:
    document = _exact(
        document,
        {"device_id", "display_name", "exchange_public_key", "signing_public_key"},
        label="Device descriptor",
    )
    name = document.get("display_name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or _CONTROL.search(name):
        raise HTTPException(status_code=422, detail="Device descriptor is invalid")
    exchange = _unb64(document.get("exchange_public_key"), label="Device exchange key", length=32)
    signing = _unb64(document.get("signing_public_key"), label="Device signing key", length=32)
    expected = _device_id(exchange, signing)
    if document.get("device_id") != expected:
        raise HTTPException(status_code=422, detail="Device descriptor ID does not match its keys")
    return {
        "device_id": expected,
        "display_name": name.strip(),
        "exchange_public_key": base64.b64encode(exchange).decode("ascii"),
        "signing_public_key": base64.b64encode(signing).decode("ascii"),
    }


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 128:
        raise HTTPException(status_code=422, detail=f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail=f"{label} must include a timezone")
    return parsed.astimezone(UTC)


def _verify_signature(signature: Any, public_key: str, value: bytes, *, label: str) -> None:
    signature = _exact(signature, {"algorithm", "value"}, label=f"{label} signature")
    if signature.get("algorithm") != "Ed25519":
        raise HTTPException(status_code=422, detail=f"{label} signature is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(
            _unb64(public_key, label=f"{label} public key", length=32)
        ).verify(
            _unb64(signature.get("value"), label=f"{label} signature", length=64),
            value,
        )
    except InvalidSignature as exc:
        raise HTTPException(status_code=422, detail=f"{label} signature is invalid") from exc


def _grant(document: Any, signature: Any, *, workspace: SyncWorkspace) -> tuple[dict, dict, dict]:
    document = _exact(
        document,
        {
            "format",
            "version",
            "workspace_id",
            "epoch",
            "request_id",
            "recipient_device",
            "approver_device",
            "issued_at",
        },
        label="Device grant",
    )
    if (
        document.get("format") != GRANT_FORMAT
        or document.get("version") != SYNC_VERSION
        or document.get("workspace_id") != workspace.workspace_id
        or document.get("epoch") != workspace.epoch
    ):
        raise HTTPException(status_code=422, detail="Device grant targets another workspace")
    _uuid(document.get("request_id"), label="Device grant request ID")
    _timestamp(document.get("issued_at"), label="Device grant issue time")
    recipient = _device(document.get("recipient_device"))
    approver = _device(document.get("approver_device"))
    _verify_signature(
        signature,
        approver["signing_public_key"],
        _canonical(document),
        label="Device grant",
    )
    return document, recipient, approver


def _revision(
    document: Any,
    *,
    workspace: SyncWorkspace,
    signing_public_key: str,
) -> tuple[dict[str, Any], datetime]:
    document = _exact(
        document,
        {
            "format",
            "version",
            "workspace_id",
            "epoch",
            "revision",
            "previous_hash",
            "device_id",
            "created_at",
            "cipher",
            "ciphertext",
            "signature",
            "object_hash",
        },
        label="Encrypted sync revision",
    )
    if len(_canonical(document)) > MAX_ENVELOPE_BYTES:
        raise HTTPException(status_code=413, detail="Encrypted sync revision is too large")
    if (
        document.get("format") != REVISION_FORMAT
        or document.get("version") != SYNC_VERSION
        or document.get("workspace_id") != workspace.workspace_id
        or document.get("epoch") != workspace.epoch
    ):
        raise HTTPException(status_code=422, detail="Encrypted sync revision targets another workspace")
    if type(document.get("revision")) is not int or document["revision"] < 1:
        raise HTTPException(status_code=422, detail="Encrypted sync revision number is invalid")
    previous_hash = document.get("previous_hash")
    if previous_hash is not None and (
        not isinstance(previous_hash, str) or not _HEX_64.fullmatch(previous_hash)
    ):
        raise HTTPException(status_code=422, detail="Encrypted sync previous hash is invalid")
    object_hash = document.get("object_hash")
    if not isinstance(object_hash, str) or not _HEX_64.fullmatch(object_hash):
        raise HTTPException(status_code=422, detail="Encrypted sync object hash is invalid")
    authored_at = _timestamp(document.get("created_at"), label="Encrypted sync creation time")
    cipher = _exact(document.get("cipher"), {"name", "nonce"}, label="Revision cipher")
    if cipher.get("name") != "AES-256-GCM":
        raise HTTPException(status_code=422, detail="Encrypted sync cipher is unsupported")
    _unb64(cipher.get("nonce"), label="Encrypted sync nonce", length=12)
    ciphertext = _unb64(document.get("ciphertext"), label="Encrypted sync ciphertext")
    if len(ciphertext) < 16:
        raise HTTPException(status_code=422, detail="Encrypted sync ciphertext is invalid")
    protected = {key: document[key] for key in document if key != "object_hash"}
    if hashlib.sha256(_canonical(protected)).hexdigest() != object_hash:
        raise HTTPException(status_code=422, detail="Encrypted sync object hash does not match")
    signed = {key: protected[key] for key in protected if key != "signature"}
    _verify_signature(
        document.get("signature"),
        signing_public_key,
        _canonical(signed),
        label="Encrypted sync revision",
    )
    return document, authored_at


async def _workspace(db: AsyncSession, namespace: str, workspace_id: str, *, lock: bool = False):
    _uuid(workspace_id, label="Sync workspace ID")
    query = select(SyncWorkspace).where(
        SyncWorkspace.workspace_id == workspace_id,
        SyncWorkspace.namespace == namespace,
    )
    if lock:
        query = query.with_for_update()
    row = (await db.execute(query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Sync workspace not found")
    return row


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    workspace_id = _uuid(body.workspace_id, label="Sync workspace ID")
    root = _device(body.root_device)
    existing = (
        await db.execute(
            select(SyncWorkspace).where(
                SyncWorkspace.workspace_id == workspace_id,
                SyncWorkspace.namespace == auth.namespace,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.epoch == body.epoch and existing.root_device == root:
            return {
                "status": "exists",
                "workspace_id": workspace_id,
                "epoch": existing.epoch,
                "head": {"revision": existing.head_revision, "object_hash": existing.head_hash},
            }
        raise HTTPException(status_code=409, detail="Sync workspace already exists")
    workspace = SyncWorkspace(
        workspace_id=workspace_id,
        namespace=auth.namespace,
        epoch=body.epoch,
        root_device=root,
    )
    db.add(workspace)
    db.add(
        SyncDevice(
            workspace_id=workspace_id,
            device_id=root["device_id"],
            namespace=auth.namespace,
            descriptor=root,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Sync workspace already exists") from exc
    return {
        "status": "created",
        "workspace_id": workspace_id,
        "epoch": body.epoch,
        "head": {"revision": 0, "object_hash": None},
    }


@router.get("/workspaces/{workspace_id}/head")
async def workspace_head(
    workspace_id: str,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    workspace = await _workspace(db, auth.namespace, workspace_id)
    device_count = await db.scalar(
        select(func.count()).select_from(SyncDevice).where(
            SyncDevice.workspace_id == workspace_id,
            SyncDevice.namespace == auth.namespace,
            SyncDevice.revoked_at.is_(None),
        )
    )
    return {
        "workspace_id": workspace.workspace_id,
        "epoch": workspace.epoch,
        "head": {"revision": workspace.head_revision, "object_hash": workspace.head_hash},
        "active_devices": int(device_count or 0),
        "encrypted": True,
    }


@router.post("/workspaces/{workspace_id}/devices", status_code=status.HTTP_201_CREATED)
async def register_device(
    workspace_id: str,
    body: DeviceGrantIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    workspace = await _workspace(db, auth.namespace, workspace_id, lock=True)
    grant, recipient, approver = _grant(body.grant, body.signature, workspace=workspace)
    approver_row = await db.get(SyncDevice, (workspace_id, approver["device_id"]))
    if (
        approver_row is None
        or approver_row.namespace != auth.namespace
        or approver_row.revoked_at is not None
        or approver_row.descriptor != approver
    ):
        raise HTTPException(status_code=403, detail="Device grant approver is not active")
    existing = await db.get(SyncDevice, (workspace_id, recipient["device_id"]))
    if existing is not None:
        if existing.descriptor == recipient and existing.grant == grant:
            return {"status": "exists", "device": recipient}
        raise HTTPException(status_code=409, detail="Sync device already exists")
    count = await db.scalar(
        select(func.count()).select_from(SyncDevice).where(
            SyncDevice.workspace_id == workspace_id,
            SyncDevice.namespace == auth.namespace,
            SyncDevice.revoked_at.is_(None),
        )
    )
    if int(count or 0) >= MAX_DEVICES:
        raise HTTPException(status_code=409, detail="Sync device limit reached")
    db.add(
        SyncDevice(
            workspace_id=workspace_id,
            device_id=recipient["device_id"],
            namespace=auth.namespace,
            descriptor=recipient,
            grant=grant,
            grant_signature=body.signature,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Sync device changed concurrently") from exc
    return {"status": "registered", "device": recipient}


@router.get("/workspaces/{workspace_id}/devices/grants")
async def device_grants(
    workspace_id: str,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    await _workspace(db, auth.namespace, workspace_id)
    rows = (
        await db.execute(
            select(SyncDevice)
            .where(
                SyncDevice.workspace_id == workspace_id,
                SyncDevice.namespace == auth.namespace,
                SyncDevice.grant.is_not(None),
            )
            .order_by(SyncDevice.enrolled_at, SyncDevice.device_id)
        )
    ).scalars()
    return {
        "workspace_id": workspace_id,
        "grants": [
            {"grant": row.grant, "signature": row.grant_signature} for row in rows
        ],
    }


@router.post("/workspaces/{workspace_id}/revisions", status_code=status.HTTP_201_CREATED)
async def push_revision(
    workspace_id: str,
    body: RevisionIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    workspace = await _workspace(db, auth.namespace, workspace_id, lock=True)
    candidate_device_id = body.envelope.get("device_id")
    if not isinstance(candidate_device_id, str):
        raise HTTPException(status_code=422, detail="Encrypted sync device ID is invalid")
    device = await db.get(SyncDevice, (workspace_id, candidate_device_id))
    if (
        device is None
        or device.namespace != auth.namespace
        or device.revoked_at is not None
    ):
        raise HTTPException(status_code=403, detail="Encrypted sync writer is not active")
    envelope, authored_at = _revision(
        body.envelope,
        workspace=workspace,
        signing_public_key=device.descriptor["signing_public_key"],
    )
    if workspace.head_revision >= MAX_REVISIONS:
        raise HTTPException(status_code=409, detail="Sync revision retention limit reached")
    if (
        envelope["revision"] != workspace.head_revision + 1
        or envelope["previous_hash"] != workspace.head_hash
    ):
        raise HTTPException(
            status_code=412,
            detail={
                "message": "Cloud sync head changed; pull before retrying",
                "head": {
                    "revision": workspace.head_revision,
                    "object_hash": workspace.head_hash,
                },
            },
        )
    db.add(
        SyncRevision(
            workspace_id=workspace_id,
            revision=envelope["revision"],
            namespace=auth.namespace,
            device_id=envelope["device_id"],
            previous_hash=envelope["previous_hash"],
            object_hash=envelope["object_hash"],
            envelope=envelope,
            authored_at=authored_at,
        )
    )
    workspace.head_revision = envelope["revision"]
    workspace.head_hash = envelope["object_hash"]
    workspace.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=412, detail="Cloud sync head changed; pull before retrying") from exc
    return {
        "status": "stored",
        "revision": envelope["revision"],
        "object_hash": envelope["object_hash"],
        "encrypted": True,
    }


@router.get("/workspaces/{workspace_id}/revisions")
async def pull_revisions(
    workspace_id: str,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    auth.require("sync")
    auth.require_unbarriered()
    workspace = await _workspace(db, auth.namespace, workspace_id)
    rows = (
        await db.execute(
            select(SyncRevision)
            .where(
                SyncRevision.workspace_id == workspace_id,
                SyncRevision.namespace == auth.namespace,
                SyncRevision.revision > after,
            )
            .order_by(SyncRevision.revision)
            .limit(limit)
        )
    ).scalars()
    revisions = [row.envelope for row in rows]
    return {
        "workspace_id": workspace_id,
        "revisions": revisions,
        "head": {"revision": workspace.head_revision, "object_hash": workspace.head_hash},
        "has_more": bool(revisions and revisions[-1]["revision"] < workspace.head_revision),
    }


@router.delete("/workspaces/{workspace_id}")
async def delete_workspace(
    workspace_id: str,
    body: WorkspaceDeleteIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    if not body.confirmed or body.confirmation != "DELETE ENCRYPTED LIANS CLOUD MEMORY":
        raise HTTPException(status_code=400, detail="Explicit cloud deletion confirmation is required")
    await _workspace(db, auth.namespace, workspace_id, lock=True)
    revision_count = int(
        await db.scalar(
            select(func.count()).select_from(SyncRevision).where(
                SyncRevision.workspace_id == workspace_id,
                SyncRevision.namespace == auth.namespace,
            )
        )
        or 0
    )
    device_count = int(
        await db.scalar(
            select(func.count()).select_from(SyncDevice).where(
                SyncDevice.workspace_id == workspace_id,
                SyncDevice.namespace == auth.namespace,
            )
        )
        or 0
    )
    await db.execute(
        delete(SyncRevision).where(
            SyncRevision.workspace_id == workspace_id,
            SyncRevision.namespace == auth.namespace,
        )
    )
    await db.execute(
        delete(SyncDevice).where(
            SyncDevice.workspace_id == workspace_id,
            SyncDevice.namespace == auth.namespace,
        )
    )
    await db.execute(
        delete(SyncWorkspace).where(
            SyncWorkspace.workspace_id == workspace_id,
            SyncWorkspace.namespace == auth.namespace,
        )
    )
    await db.commit()
    return {
        "status": "deleted",
        "workspace_id": workspace_id,
        "encrypted_revisions_deleted": revision_count,
        "device_records_deleted": device_count,
    }
