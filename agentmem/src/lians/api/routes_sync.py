"""Opaque zero-knowledge sync storage for authenticated Lians workspaces."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    SyncDevice,
    SyncEnrollment,
    SyncKeyRotation,
    SyncRevision,
    SyncWorkspace,
)
from .deps import AuthContext, get_sync_auth

router = APIRouter(prefix="/v1/sync", tags=["zero-knowledge-sync"])

SYNC_VERSION = 1
GRANT_FORMAT = "lians-device-grant"
GRANT_VERSION = 2
REQUEST_FORMAT = "lians-device-enrollment-request"
APPROVAL_FORMAT = "lians-device-enrollment-approval"
REVISION_FORMAT = "lians-encrypted-profile-revision"
ROTATION_FORMAT = "lians-workspace-key-rotation"
MAX_ENVELOPE_BYTES = 1_500_000
MAX_DEVICES = 20
MAX_REVISIONS = 10_000
MAX_PENDING_ENROLLMENTS = 5
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


class EnrollmentRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: dict[str, Any]


class EnrollmentApprovalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval: dict[str, Any]


class EnrollmentDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool


class KeyRotationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rotation: dict[str, Any]
    signature: dict[str, Any]


class RevisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: dict[str, Any]


class WorkspaceDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool
    confirmation: str


class AccountDataDeleteIn(BaseModel):
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
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 80
        or _CONTROL.search(name)
    ):
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


def _verification_code(request_body: dict[str, Any]) -> str:
    digest = hashlib.sha256(b"lians-enrollment-code-v1\0" + _canonical(request_body)).hexdigest()
    return f"{digest[:4]}-{digest[4:8]}".upper()


def _enrollment_request(
    document: Any,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any], datetime, datetime]:
    request = _exact(
        document,
        {
            "format",
            "version",
            "request_id",
            "device",
            "created_at",
            "expires_at",
            "verification_code",
        },
        label="Enrollment request",
    )
    if request.get("format") != REQUEST_FORMAT or request.get("version") != SYNC_VERSION:
        raise HTTPException(status_code=422, detail="Enrollment request is unsupported")
    _uuid(request.get("request_id"), label="Enrollment request ID")
    device = _device(request.get("device"))
    created = _timestamp(request.get("created_at"), label="Enrollment creation time")
    expires = _timestamp(request.get("expires_at"), label="Enrollment expiration time")
    current = now or datetime.now(UTC)
    if (
        expires <= created
        or expires - created > timedelta(minutes=30)
        or current > expires
        or created - current > timedelta(minutes=5)
    ):
        raise HTTPException(status_code=422, detail="Enrollment request expired")
    body = {key: request[key] for key in request if key != "verification_code"}
    if not hmac.compare_digest(str(request.get("verification_code", "")), _verification_code(body)):
        raise HTTPException(status_code=422, detail="Enrollment verification code is invalid")
    return {**request, "device": device}, created, expires


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


def _grant(
    document: Any,
    signature: Any,
    *,
    workspace: SyncWorkspace,
) -> tuple[dict, dict, dict, list[dict[str, str]] | None]:
    if not isinstance(document, dict):
        raise HTTPException(status_code=422, detail="Device grant is invalid")
    version = document.get("version")
    common_fields = {
        "format",
        "version",
        "workspace_id",
        "epoch",
        "request_id",
        "recipient_device",
        "approver_device",
        "issued_at",
    }
    fields = common_fields | ({"trusted_devices"} if version == GRANT_VERSION else set())
    document = _exact(document, fields, label="Device grant")
    if (
        document.get("format") != GRANT_FORMAT
        or version not in {SYNC_VERSION, GRANT_VERSION}
        or document.get("workspace_id") != workspace.workspace_id
        or document.get("epoch") != workspace.epoch
    ):
        raise HTTPException(status_code=422, detail="Device grant targets another workspace")
    _uuid(document.get("request_id"), label="Device grant request ID")
    _timestamp(document.get("issued_at"), label="Device grant issue time")
    recipient = _device(document.get("recipient_device"))
    approver = _device(document.get("approver_device"))
    registry = None
    if version == GRANT_VERSION:
        values = document.get("trusted_devices")
        if not isinstance(values, list) or not values:
            raise HTTPException(status_code=422, detail="Device grant registry is invalid")
        registry_map: dict[str, dict[str, str]] = {}
        for value in values:
            device = _device(value)
            if device["device_id"] in registry_map:
                raise HTTPException(status_code=422, detail="Device grant registry has duplicates")
            registry_map[device["device_id"]] = device
        if (
            list(registry_map) != sorted(registry_map)
            or registry_map.get(recipient["device_id"]) != recipient
            or registry_map.get(approver["device_id"]) != approver
        ):
            raise HTTPException(status_code=422, detail="Device grant registry does not match")
        registry = list(registry_map.values())
    _verify_signature(
        signature,
        approver["signing_public_key"],
        _canonical(document),
        label="Device grant",
    )
    return document, recipient, approver, registry


def _enrollment_approval(
    document: Any,
    *,
    request: dict[str, Any],
    workspace: SyncWorkspace,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
    dict[str, str],
    list[dict[str, str]] | None,
]:
    approval = _exact(
        document,
        {
            "format",
            "version",
            "request_id",
            "verification_code",
            "grant",
            "grant_signature",
            "key_wrap",
        },
        label="Enrollment approval",
    )
    if (
        approval.get("format") != APPROVAL_FORMAT
        or approval.get("version") != SYNC_VERSION
        or approval.get("request_id") != request["request_id"]
        or not hmac.compare_digest(
            str(approval.get("verification_code", "")),
            request["verification_code"],
        )
    ):
        raise HTTPException(status_code=422, detail="Enrollment approval does not match")
    grant, recipient, approver, registry = _grant(
        approval.get("grant"),
        approval.get("grant_signature"),
        workspace=workspace,
    )
    if grant["request_id"] != request["request_id"] or recipient != request["device"]:
        raise HTTPException(status_code=422, detail="Enrollment grant does not match")
    issued = _timestamp(grant["issued_at"], label="Enrollment grant issue time")
    created = _timestamp(request["created_at"], label="Enrollment creation time")
    expires = _timestamp(request["expires_at"], label="Enrollment expiration time")
    if not created <= issued <= expires:
        raise HTTPException(status_code=422, detail="Enrollment approval was issued too late")

    wrap = _exact(
        approval.get("key_wrap"),
        {
            "cipher",
            "workspace_id",
            "epoch",
            "request_id",
            "recipient_device_id",
            "approver_device_id",
            "ephemeral_public_key",
            "nonce",
            "ciphertext",
        },
        label="Enrollment key wrap",
    )
    expected = {
        "cipher": "X25519-HKDF-SHA256+A256GCM",
        "workspace_id": workspace.workspace_id,
        "epoch": workspace.epoch,
        "request_id": request["request_id"],
        "recipient_device_id": recipient["device_id"],
        "approver_device_id": approver["device_id"],
    }
    if any(wrap.get(key) != value for key, value in expected.items()):
        raise HTTPException(status_code=422, detail="Enrollment key wrap does not match")
    _unb64(wrap.get("ephemeral_public_key"), label="Enrollment exchange key", length=32)
    _unb64(wrap.get("nonce"), label="Enrollment key nonce", length=12)
    _unb64(wrap.get("ciphertext"), label="Encrypted workspace key", length=48)
    return approval, grant, recipient, approver, registry


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
        raise HTTPException(
            status_code=422, detail="Encrypted sync revision targets another workspace"
        )
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


async def _add_device(
    db: AsyncSession,
    *,
    namespace: str,
    workspace: SyncWorkspace,
    grant: dict[str, Any],
    signature: dict[str, Any],
    recipient: dict[str, str],
    approver: dict[str, str],
    registry: list[dict[str, str]] | None,
) -> str:
    approver_row = await db.get(
        SyncDevice,
        (workspace.workspace_id, approver["device_id"]),
    )
    if (
        approver_row is None
        or approver_row.namespace != namespace
        or approver_row.revoked_at is not None
        or approver_row.descriptor != approver
    ):
        raise HTTPException(status_code=403, detail="Device grant approver is not active")
    if registry is not None:
        active_rows = (
            await db.execute(
                select(SyncDevice).where(
                    SyncDevice.workspace_id == workspace.workspace_id,
                    SyncDevice.namespace == namespace,
                    SyncDevice.revoked_at.is_(None),
                )
            )
        ).scalars()
        expected_registry = {row.device_id: row.descriptor for row in active_rows}
        expected_registry[recipient["device_id"]] = recipient
        supplied_registry = {device["device_id"]: device for device in registry}
        if supplied_registry != expected_registry:
            raise HTTPException(
                status_code=409,
                detail="Device grant registry changed; refresh devices and approve again",
            )
    existing = await db.get(
        SyncDevice,
        (workspace.workspace_id, recipient["device_id"]),
    )
    if existing is not None:
        if existing.descriptor == recipient and existing.grant == grant:
            return "exists"
        raise HTTPException(status_code=409, detail="Sync device already exists")
    count = await db.scalar(
        select(func.count())
        .select_from(SyncDevice)
        .where(
            SyncDevice.workspace_id == workspace.workspace_id,
            SyncDevice.namespace == namespace,
            SyncDevice.revoked_at.is_(None),
        )
    )
    if int(count or 0) >= MAX_DEVICES:
        raise HTTPException(status_code=409, detail="Sync device limit reached")
    db.add(
        SyncDevice(
            workspace_id=workspace.workspace_id,
            device_id=recipient["device_id"],
            namespace=namespace,
            descriptor=recipient,
            grant=grant,
            grant_signature=signature,
        )
    )
    return "registered"


def _enrollment_document(row: SyncEnrollment) -> dict[str, Any]:
    return {
        "request_id": row.request_id,
        "state": "approved" if row.approval is not None else "waiting_for_approval",
        "device": {
            "device_id": row.device_id,
            "display_name": row.device_name,
        },
        "verification_code": row.verification_code,
        "expires_at": row.expires_at.isoformat(),
        "request": row.request,
        "approval": row.approval,
    }


def _key_rotation(
    document: Any,
    signature: Any,
    *,
    workspace: SyncWorkspace,
    revoked_device_id: str,
    active_devices: dict[str, dict[str, str]],
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    rotation = _exact(
        document,
        {
            "format",
            "version",
            "rotation_id",
            "workspace_id",
            "previous_epoch",
            "epoch",
            "previous_head",
            "revoked_device",
            "initiator_device",
            "active_devices",
            "key_wraps",
            "created_at",
        },
        label="Workspace key rotation",
    )
    if len(_canonical(rotation)) > MAX_ENVELOPE_BYTES:
        raise HTTPException(status_code=413, detail="Workspace key rotation is too large")
    if (
        rotation.get("format") != ROTATION_FORMAT
        or rotation.get("version") != SYNC_VERSION
        or rotation.get("workspace_id") != workspace.workspace_id
        or rotation.get("previous_epoch") != workspace.epoch
        or rotation.get("epoch") != workspace.epoch + 1
    ):
        raise HTTPException(status_code=412, detail="Workspace key rotation is stale")
    _uuid(rotation.get("rotation_id"), label="Workspace key rotation ID")
    created = _timestamp(rotation.get("created_at"), label="Workspace key rotation time")
    current = now or datetime.now(UTC)
    if created - current > timedelta(minutes=5) or current - created > timedelta(minutes=30):
        raise HTTPException(status_code=422, detail="Workspace key rotation expired")
    previous_head = _exact(
        rotation.get("previous_head"),
        {"revision", "object_hash"},
        label="Rotation previous head",
    )
    if previous_head != {
        "revision": workspace.head_revision,
        "object_hash": workspace.head_hash,
    }:
        raise HTTPException(status_code=412, detail="Cloud sync head changed; sync before removal")
    revoked = _device(rotation.get("revoked_device"))
    initiator = _device(rotation.get("initiator_device"))
    if revoked["device_id"] != revoked_device_id:
        raise HTTPException(status_code=422, detail="Workspace key rotation removes another device")
    if (
        active_devices.get(revoked_device_id) != revoked
        or active_devices.get(initiator["device_id"]) != initiator
        or initiator["device_id"] == revoked_device_id
    ):
        raise HTTPException(status_code=403, detail="Workspace key rotation signer is not active")
    values = rotation.get("active_devices")
    if not isinstance(values, list) or not values:
        raise HTTPException(status_code=422, detail="Workspace key rotation has no active devices")
    supplied_active: dict[str, dict[str, str]] = {}
    for value in values:
        device = _device(value)
        if device["device_id"] in supplied_active:
            raise HTTPException(status_code=422, detail="Workspace key rotation repeats a device")
        supplied_active[device["device_id"]] = device
    expected_active = {
        device_id: device
        for device_id, device in active_devices.items()
        if device_id != revoked_device_id
    }
    if list(supplied_active) != sorted(supplied_active) or supplied_active != expected_active:
        raise HTTPException(
            status_code=409, detail="Connected devices changed; refresh and try again"
        )
    wraps = rotation.get("key_wraps")
    if not isinstance(wraps, list) or len(wraps) != len(supplied_active):
        raise HTTPException(status_code=422, detail="Workspace key rotation wraps are incomplete")
    wrapped_recipients: list[str] = []
    for value in wraps:
        wrap = _exact(
            value,
            {
                "cipher",
                "workspace_id",
                "epoch",
                "rotation_id",
                "recipient_device_id",
                "initiator_device_id",
                "ephemeral_public_key",
                "nonce",
                "ciphertext",
            },
            label="Workspace key rotation wrap",
        )
        recipient_id = wrap.get("recipient_device_id")
        expected_wrap = {
            "cipher": "X25519-HKDF-SHA256+A256GCM",
            "workspace_id": workspace.workspace_id,
            "epoch": workspace.epoch + 1,
            "rotation_id": rotation["rotation_id"],
            "initiator_device_id": initiator["device_id"],
        }
        if (
            not isinstance(recipient_id, str)
            or recipient_id not in supplied_active
            or recipient_id in wrapped_recipients
            or any(wrap.get(key) != value for key, value in expected_wrap.items())
        ):
            raise HTTPException(
                status_code=422, detail="Workspace key rotation wrap does not match"
            )
        _unb64(wrap.get("ephemeral_public_key"), label="Rotation exchange key", length=32)
        _unb64(wrap.get("nonce"), label="Rotation key nonce", length=12)
        _unb64(wrap.get("ciphertext"), label="Encrypted future-memory key", length=48)
        wrapped_recipients.append(recipient_id)
    if wrapped_recipients != sorted(supplied_active):
        raise HTTPException(status_code=422, detail="Workspace key rotation wraps are incomplete")
    _verify_signature(
        signature,
        initiator["signing_public_key"],
        _canonical(rotation),
        label="Workspace key rotation",
    )
    return rotation, revoked, initiator


def _key_rotation_document(row: SyncKeyRotation) -> dict[str, Any]:
    return {"rotation": row.document, "signature": row.signature}


@router.post("/enrollments", status_code=status.HTTP_201_CREATED)
async def create_enrollment_request(
    body: EnrollmentRequestIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Publish one short-lived public device request inside an account boundary."""

    auth.require("sync")
    auth.require_unbarriered()
    now = datetime.now(UTC)
    request, _, expires = _enrollment_request(body.request, now=now)
    await db.execute(
        delete(SyncEnrollment).where(
            SyncEnrollment.namespace == auth.namespace,
            SyncEnrollment.expires_at <= now,
        )
    )
    existing = await db.get(SyncEnrollment, request["request_id"])
    if existing is not None:
        if existing.namespace == auth.namespace and existing.request == request:
            return {"status": "exists", **_enrollment_document(existing)}
        raise HTTPException(status_code=409, detail="Enrollment request already exists")
    same_device = (
        await db.execute(
            select(SyncEnrollment).where(
                SyncEnrollment.namespace == auth.namespace,
                SyncEnrollment.device_id == request["device"]["device_id"],
                SyncEnrollment.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if same_device is not None:
        raise HTTPException(status_code=409, detail="This device already has a pending request")
    pending_count = await db.scalar(
        select(func.count())
        .select_from(SyncEnrollment)
        .where(
            SyncEnrollment.namespace == auth.namespace,
            SyncEnrollment.expires_at > now,
        )
    )
    if int(pending_count or 0) >= MAX_PENDING_ENROLLMENTS:
        raise HTTPException(status_code=409, detail="Too many pending device requests")
    row = SyncEnrollment(
        request_id=request["request_id"],
        namespace=auth.namespace,
        device_id=request["device"]["device_id"],
        device_name=request["device"]["display_name"],
        verification_code=request["verification_code"],
        request=request,
        expires_at=expires,
        created_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Enrollment request changed concurrently"
        ) from exc
    return {"status": "created", **_enrollment_document(row)}


@router.get("/enrollments")
async def list_enrollment_requests(
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List unexpired requests that an existing trusted device may approve."""

    auth.require("sync")
    auth.require_unbarriered()
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(SyncEnrollment)
            .where(
                SyncEnrollment.namespace == auth.namespace,
                SyncEnrollment.expires_at > now,
                SyncEnrollment.approval.is_(None),
            )
            .order_by(SyncEnrollment.created_at, SyncEnrollment.request_id)
            .limit(MAX_PENDING_ENROLLMENTS)
        )
    ).scalars()
    return {"enrollments": [_enrollment_document(row) for row in rows]}


@router.get("/enrollments/{request_id}")
async def enrollment_request_status(
    request_id: str,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    _uuid(request_id, label="Enrollment request ID")
    row = (
        await db.execute(
            select(SyncEnrollment).where(
                SyncEnrollment.request_id == request_id,
                SyncEnrollment.namespace == auth.namespace,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Enrollment request not found")
    if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Enrollment request expired")
    return _enrollment_document(row)


@router.post("/enrollments/{request_id}/approval")
async def approve_enrollment_request(
    request_id: str,
    body: EnrollmentApprovalIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Register a signed device and store only its recipient-encrypted key wrap."""

    auth.require("sync")
    auth.require_unbarriered()
    _uuid(request_id, label="Enrollment request ID")
    row = (
        await db.execute(
            select(SyncEnrollment)
            .where(
                SyncEnrollment.request_id == request_id,
                SyncEnrollment.namespace == auth.namespace,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Enrollment request not found")
    if row.expires_at.replace(tzinfo=row.expires_at.tzinfo or UTC) <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Enrollment request expired")
    if row.approval is not None:
        if row.approval == body.approval:
            return {"status": "exists", **_enrollment_document(row)}
        raise HTTPException(status_code=409, detail="Enrollment request was already approved")
    request, _, _ = _enrollment_request(row.request)
    grant_document = body.approval.get("grant")
    if not isinstance(grant_document, dict):
        raise HTTPException(status_code=422, detail="Enrollment approval is invalid")
    workspace_id = _uuid(
        grant_document.get("workspace_id"),
        label="Enrollment workspace ID",
    )
    workspace = await _workspace(db, auth.namespace, workspace_id, lock=True)
    approval, grant, recipient, approver, registry = _enrollment_approval(
        body.approval,
        request=request,
        workspace=workspace,
    )
    device_status = await _add_device(
        db,
        namespace=auth.namespace,
        workspace=workspace,
        grant=grant,
        signature=approval["grant_signature"],
        recipient=recipient,
        approver=approver,
        registry=registry,
    )
    row.approval = approval
    row.workspace_id = workspace.workspace_id
    row.approved_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Enrollment approval changed concurrently"
        ) from exc
    return {"status": device_status, **_enrollment_document(row)}


@router.delete("/enrollments/{request_id}")
async def delete_enrollment_request(
    request_id: str,
    body: EnrollmentDeleteIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    auth.require("sync")
    auth.require_unbarriered()
    if body.confirmed is not True:
        raise HTTPException(status_code=400, detail="Enrollment removal requires confirmation")
    _uuid(request_id, label="Enrollment request ID")
    result = await db.execute(
        delete(SyncEnrollment).where(
            SyncEnrollment.request_id == request_id,
            SyncEnrollment.namespace == auth.namespace,
        )
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=404, detail="Enrollment request not found")
    await db.commit()
    return {"status": "deleted", "request_id": request_id}


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
        select(func.count())
        .select_from(SyncDevice)
        .where(
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
    grant, recipient, approver, registry = _grant(body.grant, body.signature, workspace=workspace)
    result = await _add_device(
        db,
        namespace=auth.namespace,
        workspace=workspace,
        grant=grant,
        signature=body.signature,
        recipient=recipient,
        approver=approver,
        registry=registry,
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Sync device changed concurrently") from exc
    return {"status": result, "device": recipient}


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
                SyncDevice.revoked_at.is_(None),
            )
            .order_by(SyncDevice.enrolled_at, SyncDevice.device_id)
        )
    ).scalars()
    return {
        "workspace_id": workspace_id,
        "grants": [{"grant": row.grant, "signature": row.grant_signature} for row in rows],
    }


@router.get("/workspaces/{workspace_id}/devices")
async def list_devices(
    workspace_id: str,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List public device identity and signed removal evidence for the account."""

    auth.require("sync")
    auth.require_unbarriered()
    workspace = await _workspace(db, auth.namespace, workspace_id)
    rows = (
        (
            await db.execute(
                select(SyncDevice)
                .where(
                    SyncDevice.workspace_id == workspace_id,
                    SyncDevice.namespace == auth.namespace,
                )
                .order_by(SyncDevice.enrolled_at, SyncDevice.device_id)
            )
        )
        .scalars()
        .all()
    )
    rotation_rows = (
        (
            await db.execute(
                select(SyncKeyRotation)
                .where(
                    SyncKeyRotation.workspace_id == workspace_id,
                    SyncKeyRotation.namespace == auth.namespace,
                )
                .order_by(SyncKeyRotation.epoch)
            )
        )
        .scalars()
        .all()
    )
    rotations = {row.revoked_device_id: row for row in rotation_rows}
    return {
        "workspace_id": workspace_id,
        "epoch": workspace.epoch,
        "devices": [
            {
                "device": row.descriptor,
                "state": "revoked" if row.revoked_at is not None else "active",
                "enrolled_at": row.enrolled_at.isoformat(),
                "revoked_at": row.revoked_at.isoformat() if row.revoked_at is not None else None,
                "revocation": (
                    {
                        "rotation_id": rotations[row.device_id].rotation_id,
                        "epoch": rotations[row.device_id].epoch,
                        "initiator_device_id": rotations[row.device_id].initiator_device_id,
                        "created_at": rotations[row.device_id].created_at.isoformat(),
                        "signature": rotations[row.device_id].signature,
                    }
                    if row.device_id in rotations
                    else None
                ),
            }
            for row in rows
        ],
    }


@router.get("/workspaces/{workspace_id}/key-rotations")
async def list_key_rotations(
    workspace_id: str,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
    after: Annotated[int, Query(ge=0)] = 0,
):
    """Return signed next-epoch key wraps; only surviving devices can decrypt one."""

    auth.require("sync")
    auth.require_unbarriered()
    workspace = await _workspace(db, auth.namespace, workspace_id)
    rows = (
        (
            await db.execute(
                select(SyncKeyRotation)
                .where(
                    SyncKeyRotation.workspace_id == workspace_id,
                    SyncKeyRotation.namespace == auth.namespace,
                    SyncKeyRotation.epoch > after,
                )
                .order_by(SyncKeyRotation.epoch)
                .limit(MAX_DEVICES)
            )
        )
        .scalars()
        .all()
    )
    return {
        "workspace_id": workspace_id,
        "epoch": workspace.epoch,
        "rotations": [_key_rotation_document(row) for row in rows],
    }


@router.post("/workspaces/{workspace_id}/devices/{device_id}/remove")
async def remove_device_and_rotate_key(
    workspace_id: str,
    device_id: str,
    body: KeyRotationIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Block one device and replace the future-memory key for every survivor."""

    auth.require("sync")
    auth.require_unbarriered()
    if not isinstance(device_id, str) or not _HEX_64.fullmatch(device_id):
        raise HTTPException(status_code=422, detail="Sync device ID is invalid")
    workspace = await _workspace(db, auth.namespace, workspace_id, lock=True)
    rotation_id = _uuid(
        body.rotation.get("rotation_id"),
        label="Workspace key rotation ID",
    )
    existing_rotation = (
        await db.execute(
            select(SyncKeyRotation).where(
                SyncKeyRotation.rotation_id == rotation_id,
                SyncKeyRotation.namespace == auth.namespace,
                SyncKeyRotation.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if existing_rotation is not None:
        if (
            existing_rotation.namespace == auth.namespace
            and existing_rotation.workspace_id == workspace_id
            and existing_rotation.revoked_device_id == device_id
            and existing_rotation.document == body.rotation
            and existing_rotation.signature == body.signature
        ):
            return {
                "status": "exists",
                "workspace_id": workspace_id,
                "epoch": existing_rotation.epoch,
                "revoked_device_id": device_id,
                "encrypted_revisions_deleted": 0,
                "future_memory_protected": True,
            }
        raise HTTPException(status_code=409, detail="Workspace key rotation already exists")
    rows = (
        (
            await db.execute(
                select(SyncDevice)
                .where(
                    SyncDevice.workspace_id == workspace_id,
                    SyncDevice.namespace == auth.namespace,
                    SyncDevice.revoked_at.is_(None),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    active_devices = {row.device_id: row.descriptor for row in rows}
    target = next((row for row in rows if row.device_id == device_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Active sync device not found")
    now = datetime.now(UTC)
    rotation, _, initiator = _key_rotation(
        body.rotation,
        body.signature,
        workspace=workspace,
        revoked_device_id=device_id,
        active_devices=active_devices,
        now=now,
    )
    revision_count = int(
        await db.scalar(
            select(func.count())
            .select_from(SyncRevision)
            .where(
                SyncRevision.workspace_id == workspace_id,
                SyncRevision.namespace == auth.namespace,
            )
        )
        or 0
    )
    target.revoked_at = now
    workspace.epoch = rotation["epoch"]
    workspace.head_revision = 0
    workspace.head_hash = None
    workspace.updated_at = now
    db.add(
        SyncKeyRotation(
            workspace_id=workspace_id,
            epoch=rotation["epoch"],
            rotation_id=rotation_id,
            namespace=auth.namespace,
            revoked_device_id=device_id,
            initiator_device_id=initiator["device_id"],
            document=rotation,
            signature=body.signature,
            created_at=now,
        )
    )
    await db.execute(
        delete(SyncRevision).where(
            SyncRevision.workspace_id == workspace_id,
            SyncRevision.namespace == auth.namespace,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Connected devices changed concurrently"
        ) from exc
    return {
        "status": "removed",
        "workspace_id": workspace_id,
        "epoch": rotation["epoch"],
        "revoked_device_id": device_id,
        "encrypted_revisions_deleted": revision_count,
        "future_memory_protected": True,
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
    if device is None or device.namespace != auth.namespace or device.revoked_at is not None:
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
        raise HTTPException(
            status_code=412, detail="Cloud sync head changed; pull before retrying"
        ) from exc
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
        raise HTTPException(
            status_code=400, detail="Explicit cloud deletion confirmation is required"
        )
    await _workspace(db, auth.namespace, workspace_id, lock=True)
    revision_count = int(
        await db.scalar(
            select(func.count())
            .select_from(SyncRevision)
            .where(
                SyncRevision.workspace_id == workspace_id,
                SyncRevision.namespace == auth.namespace,
            )
        )
        or 0
    )
    device_count = int(
        await db.scalar(
            select(func.count())
            .select_from(SyncDevice)
            .where(
                SyncDevice.workspace_id == workspace_id,
                SyncDevice.namespace == auth.namespace,
            )
        )
        or 0
    )
    rotation_count = int(
        await db.scalar(
            select(func.count())
            .select_from(SyncKeyRotation)
            .where(
                SyncKeyRotation.workspace_id == workspace_id,
                SyncKeyRotation.namespace == auth.namespace,
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
        delete(SyncKeyRotation).where(
            SyncKeyRotation.workspace_id == workspace_id,
            SyncKeyRotation.namespace == auth.namespace,
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
        "key_rotation_records_deleted": rotation_count,
    }


@router.delete("/account-data")
async def delete_account_sync_data(
    body: AccountDataDeleteIn,
    auth: Annotated[AuthContext, Depends(get_sync_auth)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete every opaque sync object for the authenticated account namespace."""

    auth.require("sync")
    auth.require_unbarriered()
    if not body.confirmed or body.confirmation != "DELETE ALL LIANS CLOUD DATA":
        raise HTTPException(
            status_code=400,
            detail="Explicit account cloud-data deletion confirmation is required",
        )

    models = (
        ("enrollment_records_deleted", SyncEnrollment),
        ("encrypted_revisions_deleted", SyncRevision),
        ("device_records_deleted", SyncDevice),
        ("key_rotation_records_deleted", SyncKeyRotation),
        ("workspaces_deleted", SyncWorkspace),
    )
    counts = {
        label: int(
            await db.scalar(
                select(func.count()).select_from(model).where(model.namespace == auth.namespace)
            )
            or 0
        )
        for label, model in models
    }
    # Delete children first so this remains correct even when a test or
    # operator database does not enforce ON DELETE CASCADE.
    for _, model in models:
        await db.execute(delete(model).where(model.namespace == auth.namespace))
    await db.commit()
    return {
        "status": "deleted",
        **counts,
        "encrypted": True,
    }
