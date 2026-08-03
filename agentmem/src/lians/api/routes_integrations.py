"""Administrative APIs for durable SIEM, GRC, ticketing, and billing sinks."""

# FastAPI intentionally evaluates Depends/Query marker objects in signatures.
# ruff: noqa: B008

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from ..audit_chain import chain_log
from ..config import get_settings
from ..db import get_db
from ..idempotency import (
    IdempotencyConflict,
    IdempotencyReplayUnavailable,
    InvalidIdempotencyKey,
    InvalidIdempotencyRequest,
    OperationClaim,
    operation_claim,
)
from ..integration_models import (
    IntegrationDelivery,
    IntegrationDeliveryAttempt,
    IntegrationDestination,
    IntegrationOutboxEvent,
)
from ..integration_schemas import (
    DeliveryAttemptOut,
    DeliveryOut,
    DeliveryStatus,
    DestinationCreate,
    DestinationCreated,
    DestinationOut,
    DestinationPatch,
    DestinationSecretRotate,
    DestinationSecretRotated,
    DestinationTestResult,
    IntegrationEventCreate,
    IntegrationEventOut,
    IntegrationReadiness,
)
from ..integration_service import (
    IntegrationConfigurationError,
    IntegrationConflictError,
    IntegrationNotFoundError,
    create_destination,
    destination_out,
    enqueue_integration_event,
    event_out,
    get_destination,
    integration_inventory,
    integration_worker_status,
    replay_delivery,
    revoke_destination,
    rotate_destination_secrets,
    unseal_event_payload_value,
    update_destination,
)
from ..mutation_safety import reject_non_replayable_idempotency_key
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/integrations", tags=["integrations"])
_EVENT_ENQUEUE_OPERATION = "integration.event.enqueue"
_DESTINATION_TEST_OPERATION = "integration.destination.test"
_MAX_LIST_OFFSET = 100_000
_SEALED_PAYLOAD_RESPONSE_MULTIPLIER = 4
_PAYLOAD_EXPORT_RESPONSES = {
    409: {
        "description": (
            "The encrypted event snapshot changed during payload hydration "
            "(`integration_payload_export_snapshot_changed`)"
        )
    },
    413: {
        "description": (
            "The requested decrypted payload export exceeds the safe response "
            "budget (`integration_payload_export_capacity_exceeded`)"
        )
    },
}


def _set_page_headers(
    response: Response,
    *,
    total: int,
    limit: int,
    returned: int,
    has_more: bool,
    offset: int | None = None,
    next_cursor: dict[str, str] | None = None,
) -> None:
    response.headers["X-Lians-Total-Count"] = str(total)
    response.headers["X-Lians-Page-Limit"] = str(limit)
    response.headers["X-Lians-Page-Returned"] = str(returned)
    response.headers["X-Lians-Page-Complete"] = str(not has_more).lower()
    response.headers["X-Lians-Has-More"] = str(has_more).lower()
    if offset is not None:
        response.headers["X-Lians-Page-Offset"] = str(offset)
    if has_more and next_cursor:
        for name, value in next_cursor.items():
            header_name = "-".join(part.capitalize() for part in name.split("_"))
            response.headers[f"X-Lians-Next-{header_name}"] = value


def _require_paired_cursor(
    first_value: object | None,
    second_value: object | None,
    *,
    first_name: str,
    second_name: str,
) -> None:
    if (first_value is None) != (second_value is None):
        raise HTTPException(
            status_code=422,
            detail=f"{first_name} and {second_name} must be supplied together",
        )


def _descending_cursor(column, id_column, cursor_time: datetime, cursor_id: UUID):
    return or_(
        column < cursor_time,
        and_(column == cursor_time, id_column < cursor_id),
    )


def _actor(auth: AuthContext) -> str:
    principal = auth.principal_id or auth.credential_id or "unknown"
    return "principal:" + hashlib.sha256(principal.encode("utf-8")).hexdigest()


def _require_admin(auth: AuthContext) -> None:
    auth.require("admin")


def _effective_barrier(auth: AuthContext, requested: str | None) -> str | None:
    if auth.barrier_group is not None:
        if requested is not None and requested != auth.barrier_group:
            raise HTTPException(
                status_code=403,
                detail="A barrier-scoped principal cannot configure another barrier",
            )
        return auth.barrier_group
    return requested


def _translate(exc: Exception) -> None:
    if isinstance(exc, IntegrationNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, IntegrationConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, IntegrationConfigurationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _translate_idempotency(exc: Exception) -> None:
    if isinstance(exc, (InvalidIdempotencyKey, InvalidIdempotencyRequest)):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, (IdempotencyConflict, IdempotencyReplayUnavailable)):
        if isinstance(exc, IdempotencyReplayUnavailable):
            from ..metrics import record_idempotency_outcome

            record_idempotency_outcome("replay_unavailable")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


def _idempotency_request(
    body: object,
    auth: AuthContext,
    *,
    route: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "body": body,
        "route": route or {},
        "barrier_group": auth.barrier_group,
        "principal_id": auth.principal_id,
        "credential_id": auth.credential_id,
        "auth_method": auth.auth_method,
    }


def _replay_id(claim: OperationClaim, *, kind: str, cardinality: int) -> list[UUID]:
    if (
        claim.replay is None
        or claim.replay.resource_kind != kind
        or claim.replay.response_status != status.HTTP_202_ACCEPTED
    ):
        raise IdempotencyReplayUnavailable(
            "The committed integration idempotency result has an unexpected shape"
        )
    ids = claim.resource_ids
    if len(ids) != cardinality:
        raise IdempotencyReplayUnavailable(
            "The committed integration idempotency result has invalid cardinality"
        )
    return ids


async def _event_metadata_for_replay(
    db: AsyncSession,
    *,
    event_id: UUID,
    namespace: str,
) -> IntegrationOutboxEvent | None:
    """Load replay-visible metadata without hydrating encrypted payload bytes."""

    return (
        await db.execute(
            select(IntegrationOutboxEvent)
            .options(
                defer(
                    IntegrationOutboxEvent.payload_encrypted,
                    raiseload=True,
                )
            )
            .where(
                IntegrationOutboxEvent.id == event_id,
                IntegrationOutboxEvent.namespace == namespace,
            )
        )
    ).scalar_one_or_none()


async def _commit(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Integration state changed concurrently",
        ) from exc


@router.post(
    "/destinations",
    response_model=DestinationCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Register an encrypted durable integration destination",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def register_destination(
    body: DestinationCreate,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DestinationCreated:
    _require_admin(auth)
    barrier = _effective_barrier(auth, body.barrier_group)
    try:
        row, signing_secret = await create_destination(
            db,
            namespace=auth.namespace,
            body=body,
            effective_barrier_group=barrier,
        )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_actor(auth),
        op="integration.destination_create",
        payload={
            "destination_id": str(row.id),
            "destination_type": row.destination_type,
            "url_hash": row.url_fingerprint,
            "event_patterns": list(row.event_patterns),
            "barrier_group": row.barrier_group,
            "credential_kind": row.credential_kind,
        },
    )
    await _commit(db)
    await db.refresh(row)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return DestinationCreated(destination=destination_out(row), signing_secret=signing_secret)


@router.get(
    "/destinations",
    response_model=list[DestinationOut],
    summary="List destination metadata without disclosing credentials",
)
async def list_destinations(
    response: Response,
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[DestinationOut]:
    _require_admin(auth)
    _require_paired_cursor(
        before_created_at,
        before_id,
        first_name="before_created_at",
        second_name="before_id",
    )
    if before_created_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    filters = [IntegrationDestination.namespace == auth.namespace]
    if auth.barrier_group is not None:
        filters.append(IntegrationDestination.barrier_group == auth.barrier_group)
    if not include_revoked:
        filters.append(IntegrationDestination.revoked_at.is_(None))
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(IntegrationDestination).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_created_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor(
                IntegrationDestination.created_at,
                IntegrationDestination.id,
                before_created_at,
                before_id,
            )
        )
    rows = (
        (
            await db.execute(
                select(IntegrationDestination)
                .options(
                    defer(
                        IntegrationDestination.secret_config_encrypted,
                        raiseload=True,
                    )
                )
                .where(*page_filters)
                .order_by(
                    IntegrationDestination.created_at.desc(),
                    IntegrationDestination.id.desc(),
                )
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        next_cursor = {
            "before_created_at": page[-1].created_at.isoformat(),
            "before_id": str(page[-1].id),
        }
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        offset=offset,
        next_cursor=next_cursor,
    )
    return [destination_out(row) for row in page]


@router.get(
    "/destinations/{destination_id}",
    response_model=DestinationOut,
    summary="Read destination metadata without disclosing credentials",
)
async def read_destination(
    destination_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DestinationOut:
    _require_admin(auth)
    try:
        row = await get_destination(
            db,
            destination_id=destination_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            include_secret_config=False,
        )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    return destination_out(row)


@router.patch(
    "/destinations/{destination_id}",
    response_model=DestinationOut,
    summary="Update a destination with optimistic concurrency",
)
async def patch_destination(
    destination_id: UUID,
    body: DestinationPatch,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DestinationOut:
    _require_admin(auth)
    try:
        row, changed_fields = await update_destination(
            db,
            destination_id=destination_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            body=body,
        )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_actor(auth),
        op="integration.destination_update",
        payload={
            "destination_id": str(row.id),
            "version": row.version,
            "changed_fields": changed_fields,
            "url_hash": row.url_fingerprint,
            "barrier_group": row.barrier_group,
        },
    )
    await _commit(db)
    await db.refresh(row)
    return destination_out(row)


@router.post(
    "/destinations/{destination_id}/rotate-secrets",
    response_model=DestinationSecretRotated,
    summary="Rotate outbound credentials and the delivery signing secret",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def rotate_secrets(
    destination_id: UUID,
    body: DestinationSecretRotate,
    response: Response,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DestinationSecretRotated:
    _require_admin(auth)
    try:
        row, signing_secret = await rotate_destination_secrets(
            db,
            destination_id=destination_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            expected_version=body.expected_version,
            credentials=body.credentials,
            signing_secret=(
                body.signing_secret.get_secret_value() if body.signing_secret else None
            ),
        )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_actor(auth),
        op="integration.destination_secret_rotate",
        payload={
            "destination_id": str(row.id),
            "version": row.version,
            "credential_kind": row.credential_kind,
            "signing_secret_fingerprint": row.secret_fingerprint,
            "barrier_group": row.barrier_group,
        },
    )
    await _commit(db)
    await db.refresh(row)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return DestinationSecretRotated(destination=destination_out(row), signing_secret=signing_secret)


@router.delete(
    "/destinations/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-revoke a destination and cancel queued delivery runs",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def remove_destination(
    destination_id: UUID,
    expected_version: int = Query(..., ge=1),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _require_admin(auth)
    try:
        row = await revoke_destination(
            db,
            destination_id=destination_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
            expected_version=expected_version,
        )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_actor(auth),
        op="integration.destination_revoke",
        payload={
            "destination_id": str(row.id),
            "version": row.version,
            "barrier_group": row.barrier_group,
        },
    )
    await _commit(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/destinations/{destination_id}/test",
    response_model=DestinationTestResult,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a durable signed test delivery",
)
async def test_destination(
    destination_id: UUID,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ] = None,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DestinationTestResult:
    _require_admin(auth)
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_DESTINATION_TEST_OPERATION,
            key=idempotency_key,
            request=_idempotency_request(
                {},
                auth,
                route={"destination_id": destination_id},
            ),
        ) as claim:
            if claim.is_replay:
                event_id, delivery_id = _replay_id(
                    claim,
                    kind="integration_destination_test",
                    cardinality=2,
                )
                event = await _event_metadata_for_replay(
                    db,
                    event_id=event_id,
                    namespace=auth.namespace,
                )
                delivery = await db.get(IntegrationDelivery, delivery_id)
                if (
                    event is None
                    or delivery is None
                    or event.namespace != auth.namespace
                    or delivery.namespace != auth.namespace
                    or delivery.event_id != event.id
                    or delivery.destination_id != destination_id
                ):
                    raise IdempotencyReplayUnavailable(
                        "The committed destination-test result is unavailable"
                    )
                claim.replay_served()
                return DestinationTestResult(
                    event=event_out(event, delivery_count=1),
                    delivery=DeliveryOut.model_validate(delivery),
                )

            destination = await get_destination(
                db,
                destination_id=destination_id,
                namespace=auth.namespace,
                barrier_group=auth.barrier_group,
                for_update=True,
            )
            event, deliveries, _ = await enqueue_integration_event(
                db,
                namespace=auth.namespace,
                barrier_group=destination.barrier_group,
                event_type="integration.test",
                payload={
                    "kind": "destination_connectivity_test",
                    "destination_id": str(destination.id),
                    "requested_at": datetime.now(UTC).isoformat(),
                },
                aggregate_type="integration_destination",
                aggregate_id=str(destination.id),
                idempotency_key=f"destination-test:{uuid.uuid4()}",
                force_destination_id=destination.id,
            )
            if event is None or len(deliveries) != 1:
                raise IntegrationConflictError(
                    "Destination test could not create exactly one delivery"
                )
            delivery = deliveries[0]
            await chain_log(
                db,
                namespace=auth.namespace,
                agent_id=_actor(auth),
                op="integration.destination_test_queued",
                payload={
                    "destination_id": str(destination.id),
                    "event_id": str(event.id),
                    "delivery_id": str(delivery.id),
                    "barrier_group": destination.barrier_group,
                },
            )
            await claim.complete_and_commit(
                resource_kind="integration_destination_test",
                resource_ids=[event.id, delivery.id],
                response_status=status.HTTP_202_ACCEPTED,
            )
            await db.refresh(event)
            await db.refresh(delivery)
            return DestinationTestResult(
                event=event_out(event, delivery_count=1),
                delivery=DeliveryOut.model_validate(delivery),
            )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _translate_idempotency(exc)


@router.post(
    "/events",
    response_model=IntegrationEventOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Transactionally enqueue a custom integration event",
)
async def enqueue_event(
    body: IntegrationEventCreate,
    idempotency_header: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ] = None,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> IntegrationEventOut:
    _require_admin(auth)
    if idempotency_header and body.idempotency_key and idempotency_header != body.idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Header and body idempotency keys must match",
        )
    barrier = _effective_barrier(auth, body.barrier_group)
    idempotency_key = idempotency_header or body.idempotency_key
    try:
        async with operation_claim(
            db,
            namespace=auth.namespace,
            operation=_EVENT_ENQUEUE_OPERATION,
            key=idempotency_key,
            request=_idempotency_request(
                body,
                auth,
                route={"effective_barrier_group": barrier},
            ),
        ) as claim:
            if claim.is_replay:
                event_id = _replay_id(
                    claim,
                    kind="integration_event",
                    cardinality=1,
                )[0]
                event = await _event_metadata_for_replay(
                    db,
                    event_id=event_id,
                    namespace=auth.namespace,
                )
                if event is None or event.namespace != auth.namespace:
                    raise IdempotencyReplayUnavailable(
                        "The committed integration-event result is unavailable"
                    )
                delivery_count = int(
                    (
                        await db.execute(
                            select(func.count()).where(
                                IntegrationDelivery.event_id == event.id,
                                IntegrationDelivery.run_sequence == 1,
                            )
                        )
                    ).scalar_one()
                )
                claim.replay_served()
                return event_out(event, delivery_count=delivery_count)

            event, deliveries, _ = await enqueue_integration_event(
                db,
                namespace=auth.namespace,
                barrier_group=barrier,
                event_type=body.event_type,
                payload=body.payload,
                schema_version=body.schema_version,
                aggregate_type=body.aggregate_type,
                aggregate_id=body.aggregate_id,
                correlation_id=body.correlation_id,
                idempotency_key=idempotency_key,
                occurred_at=body.occurred_at,
            )
            if event is None:
                raise IntegrationConflictError("Integration event could not be persisted")
            await chain_log(
                db,
                namespace=auth.namespace,
                agent_id=_actor(auth),
                op="integration.event_enqueue",
                content_hash=event.payload_hash,
                payload={
                    "event_id": str(event.id),
                    "event_type": event.event_type,
                    "payload_hash": event.payload_hash,
                    "delivery_count": len(deliveries),
                    "barrier_group": event.barrier_group,
                },
            )
            await claim.complete_and_commit(
                resource_kind="integration_event",
                resource_ids=[event.id],
                response_status=status.HTTP_202_ACCEPTED,
            )
            await db.refresh(event)
            return event_out(event, delivery_count=len(deliveries))
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    except (
        InvalidIdempotencyKey,
        InvalidIdempotencyRequest,
        IdempotencyConflict,
        IdempotencyReplayUnavailable,
    ) as exc:
        _translate_idempotency(exc)


@router.get(
    "/events",
    response_model=list[IntegrationEventOut],
    responses=_PAYLOAD_EXPORT_RESPONSES,
    summary="List encrypted outbox event metadata",
)
async def list_events(
    response: Response,
    event_type: str | None = Query(default=None, max_length=255),
    include_payload: bool = Query(
        default=False,
        description="Decrypt event payloads for this explicit admin read",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    before_enqueued_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[IntegrationEventOut]:
    _require_admin(auth)
    _require_paired_cursor(
        before_enqueued_at,
        before_id,
        first_name="before_enqueued_at",
        second_name="before_id",
    )
    if before_enqueued_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    if include_payload:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    filters = [IntegrationOutboxEvent.namespace == auth.namespace]
    if auth.barrier_group is not None:
        filters.append(
            (IntegrationOutboxEvent.barrier_group.is_(None))
            | (IntegrationOutboxEvent.barrier_group == auth.barrier_group)
        )
    if event_type:
        filters.append(IntegrationOutboxEvent.event_type == event_type)
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(IntegrationOutboxEvent).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_enqueued_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor(
                IntegrationOutboxEvent.enqueued_at,
                IntegrationOutboxEvent.id,
                before_enqueued_at,
                before_id,
            )
        )
    rows = (
        (
            await db.execute(
                select(IntegrationOutboxEvent)
                .options(
                    defer(
                        IntegrationOutboxEvent.payload_encrypted,
                        raiseload=True,
                    )
                )
                .where(*page_filters)
                .order_by(
                    IntegrationOutboxEvent.enqueued_at.desc(),
                    IntegrationOutboxEvent.id.desc(),
                )
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        next_cursor = {
            "before_enqueued_at": page[-1].enqueued_at.isoformat(),
            "before_id": str(page[-1].id),
        }
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        offset=offset,
        next_cursor=next_cursor,
    )
    if not page:
        return []
    counts = dict(
        (
            await db.execute(
                select(IntegrationDelivery.event_id, func.count())
                .where(IntegrationDelivery.event_id.in_([row.id for row in page]))
                .group_by(IntegrationDelivery.event_id)
            )
        ).all()
    )
    metadata_outputs = [
        event_out(row, delivery_count=int(counts.get(row.id, 0)))
        for row in page
    ]
    payloads: dict[UUID, dict[str, Any]] = {}
    if include_payload:
        page_ids = [row.id for row in page]
        sealed_payload_chars = int(
            (
                await db.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                func.length(
                                    IntegrationOutboxEvent.payload_encrypted
                                )
                            ),
                            0,
                        )
                    ).where(IntegrationOutboxEvent.id.in_(page_ids))
                )
            ).scalar_one()
            or 0
        )
        metadata_response_bytes = (
            2
            + max(0, len(metadata_outputs) - 1)
            + sum(
                len(item.model_dump_json().encode("utf-8"))
                for item in metadata_outputs
            )
        )
        estimated_response_bytes = (
            metadata_response_bytes
            + sealed_payload_chars * _SEALED_PAYLOAD_RESPONSE_MULTIPLIER
        )
        byte_limit = get_settings().content_export_page_bytes_limit
        if estimated_response_bytes > byte_limit:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "integration_payload_export_capacity_exceeded",
                    "estimated_response_bytes": estimated_response_bytes,
                    "byte_limit": byte_limit,
                    "events_returned": len(page),
                    "payloads_decrypted": False,
                },
            )
        sealed_rows = (
            await db.execute(
                select(
                    IntegrationOutboxEvent.id,
                    IntegrationOutboxEvent.namespace,
                    IntegrationOutboxEvent.payload_encrypted,
                    IntegrationOutboxEvent.payload_hash,
                ).where(IntegrationOutboxEvent.id.in_(page_ids))
            )
        ).all()
        if len(sealed_rows) != len(page):
            raise HTTPException(
                status_code=409,
                detail={"code": "integration_payload_export_snapshot_changed"},
            )
        payloads = {
            event_id: unseal_event_payload_value(
                namespace=namespace,
                event_id=event_id,
                payload_encrypted=payload_encrypted,
                payload_hash=payload_hash,
            )
            for event_id, namespace, payload_encrypted, payload_hash in sealed_rows
        }
    outputs = [
        event_out(
            row,
            delivery_count=int(counts.get(row.id, 0)),
            include_payload=include_payload,
            payload=payloads.get(row.id),
        )
        for row in page
    ]
    if include_payload:
        serialized_response_bytes = (
            2
            + max(0, len(outputs) - 1)
            + sum(len(item.model_dump_json().encode("utf-8")) for item in outputs)
        )
        if serialized_response_bytes > get_settings().content_export_page_bytes_limit:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "integration_payload_export_capacity_exceeded",
                    "estimated_response_bytes": serialized_response_bytes,
                    "byte_limit": get_settings().content_export_page_bytes_limit,
                    "events_returned": len(page),
                    "payloads_decrypted": True,
                },
            )
        await chain_log(
            db,
            namespace=auth.namespace,
            agent_id=_actor(auth),
            op="integration.event_payload_export",
            payload={
                "event_ids": [str(row.id) for row in page],
                "event_payload_hashes": [row.payload_hash for row in page],
                "result_count": len(page),
                "barrier_group": auth.barrier_group,
            },
        )
        await _commit(db)
    return outputs


@router.get(
    "/events/{event_id}",
    response_model=IntegrationEventOut,
    responses=_PAYLOAD_EXPORT_RESPONSES,
    summary="Read encrypted outbox event metadata and optionally its payload",
)
async def read_event(
    event_id: UUID,
    response: Response,
    include_payload: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> IntegrationEventOut:
    _require_admin(auth)
    if include_payload:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    filters = [
        IntegrationOutboxEvent.id == event_id,
        IntegrationOutboxEvent.namespace == auth.namespace,
    ]
    if auth.barrier_group is not None:
        filters.append(
            (IntegrationOutboxEvent.barrier_group.is_(None))
            | (IntegrationOutboxEvent.barrier_group == auth.barrier_group)
        )
    row = (
        await db.execute(
            select(IntegrationOutboxEvent)
            .options(
                defer(
                    IntegrationOutboxEvent.payload_encrypted,
                    raiseload=True,
                )
            )
            .where(*filters)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Integration event not found")
    count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IntegrationDelivery)
                .where(IntegrationDelivery.event_id == row.id)
            )
        ).scalar_one()
    )
    metadata_output = event_out(row, delivery_count=count)
    payload: dict[str, Any] | None = None
    if include_payload:
        sealed_payload_chars = int(
            (
                await db.execute(
                    select(func.length(IntegrationOutboxEvent.payload_encrypted)).where(
                        *filters
                    )
                )
            ).scalar_one_or_none()
            or 0
        )
        metadata_response_bytes = len(
            metadata_output.model_dump_json().encode("utf-8")
        )
        estimated_response_bytes = (
            metadata_response_bytes
            + sealed_payload_chars * _SEALED_PAYLOAD_RESPONSE_MULTIPLIER
        )
        byte_limit = get_settings().content_export_page_bytes_limit
        if estimated_response_bytes > byte_limit:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "integration_payload_export_capacity_exceeded",
                    "estimated_response_bytes": estimated_response_bytes,
                    "byte_limit": byte_limit,
                    "events_returned": 1,
                    "payloads_decrypted": False,
                },
            )
        sealed_row = (
            await db.execute(
                select(
                    IntegrationOutboxEvent.namespace,
                    IntegrationOutboxEvent.payload_encrypted,
                    IntegrationOutboxEvent.payload_hash,
                ).where(*filters)
            )
        ).one_or_none()
        if sealed_row is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "integration_payload_export_snapshot_changed"},
            )
        payload = unseal_event_payload_value(
            namespace=sealed_row.namespace,
            event_id=row.id,
            payload_encrypted=sealed_row.payload_encrypted,
            payload_hash=sealed_row.payload_hash,
        )
    output = event_out(
        row,
        delivery_count=count,
        include_payload=include_payload,
        payload=payload,
    )
    if include_payload:
        serialized_response_bytes = len(output.model_dump_json().encode("utf-8"))
        if serialized_response_bytes > get_settings().content_export_page_bytes_limit:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "integration_payload_export_capacity_exceeded",
                    "estimated_response_bytes": serialized_response_bytes,
                    "byte_limit": get_settings().content_export_page_bytes_limit,
                    "events_returned": 1,
                    "payloads_decrypted": True,
                },
            )
        await chain_log(
            db,
            namespace=auth.namespace,
            agent_id=_actor(auth),
            op="integration.event_payload_read",
            content_hash=row.payload_hash,
            payload={
                "event_id": str(row.id),
                "payload_hash": row.payload_hash,
                "barrier_group": row.barrier_group,
            },
        )
        await _commit(db)
    return output


@router.get(
    "/deliveries",
    response_model=list[DeliveryOut],
    summary="List delivery runs, including retry and dead-letter state",
)
async def list_deliveries(
    response: Response,
    destination_id: UUID | None = Query(default=None),
    event_id: UUID | None = Query(default=None),
    delivery_status: DeliveryStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=_MAX_LIST_OFFSET),
    before_created_at: datetime | None = Query(default=None),
    before_id: UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[DeliveryOut]:
    _require_admin(auth)
    _require_paired_cursor(
        before_created_at,
        before_id,
        first_name="before_created_at",
        second_name="before_id",
    )
    if before_created_at is not None and offset:
        raise HTTPException(status_code=422, detail="offset cannot be combined with a cursor")
    filters = [IntegrationDelivery.namespace == auth.namespace]
    if auth.barrier_group is not None:
        filters.append(
            (IntegrationDelivery.barrier_group.is_(None))
            | (IntegrationDelivery.barrier_group == auth.barrier_group)
        )
    if destination_id:
        filters.append(IntegrationDelivery.destination_id == destination_id)
    if event_id:
        filters.append(IntegrationDelivery.event_id == event_id)
    if delivery_status:
        filters.append(IntegrationDelivery.status == delivery_status)
    total = int(
        (
            await db.execute(
                select(func.count()).select_from(IntegrationDelivery).where(*filters)
            )
        ).scalar_one()
    )
    page_filters = list(filters)
    if before_created_at is not None and before_id is not None:
        page_filters.append(
            _descending_cursor(
                IntegrationDelivery.created_at,
                IntegrationDelivery.id,
                before_created_at,
                before_id,
            )
        )
    rows = (
        (
            await db.execute(
                select(IntegrationDelivery)
                .where(*page_filters)
                .order_by(
                    IntegrationDelivery.created_at.desc(),
                    IntegrationDelivery.id.desc(),
                )
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        next_cursor = {
            "before_created_at": page[-1].created_at.isoformat(),
            "before_id": str(page[-1].id),
        }
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        offset=offset,
        next_cursor=next_cursor,
    )
    return [DeliveryOut.model_validate(row) for row in page]


async def _delivery_for_auth(
    db: AsyncSession,
    *,
    delivery_id: UUID,
    auth: AuthContext,
) -> IntegrationDelivery:
    filters = [
        IntegrationDelivery.id == delivery_id,
        IntegrationDelivery.namespace == auth.namespace,
    ]
    if auth.barrier_group is not None:
        filters.append(
            (IntegrationDelivery.barrier_group.is_(None))
            | (IntegrationDelivery.barrier_group == auth.barrier_group)
        )
    row = (await db.execute(select(IntegrationDelivery).where(*filters))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Integration delivery not found")
    return row


@router.get(
    "/deliveries/{delivery_id}",
    response_model=DeliveryOut,
    summary="Read one delivery run",
)
async def read_delivery(
    delivery_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOut:
    _require_admin(auth)
    return DeliveryOut.model_validate(
        await _delivery_for_auth(db, delivery_id=delivery_id, auth=auth)
    )


@router.get(
    "/deliveries/{delivery_id}/attempts",
    response_model=list[DeliveryAttemptOut],
    summary="Read immutable delivery attempt history",
)
async def list_delivery_attempts(
    delivery_id: UUID,
    response: Response,
    limit: int = Query(default=100, ge=1, le=500),
    after_attempt_number: int | None = Query(default=None, ge=1),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> list[DeliveryAttemptOut]:
    _require_admin(auth)
    await _delivery_for_auth(db, delivery_id=delivery_id, auth=auth)
    filters = [IntegrationDeliveryAttempt.delivery_id == delivery_id]
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(IntegrationDeliveryAttempt)
                .where(*filters)
            )
        ).scalar_one()
    )
    if after_attempt_number is not None:
        filters.append(
            IntegrationDeliveryAttempt.attempt_number > after_attempt_number
        )
    rows = (
        (
            await db.execute(
                select(IntegrationDeliveryAttempt)
                .where(*filters)
                .order_by(IntegrationDeliveryAttempt.attempt_number)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = rows[:limit]
    has_more = len(rows) > limit
    next_cursor = None
    if has_more and page:
        next_cursor = {"after_attempt_number": str(page[-1].attempt_number)}
    _set_page_headers(
        response,
        total=total,
        limit=limit,
        returned=len(page),
        has_more=has_more,
        next_cursor=next_cursor,
    )
    return [DeliveryAttemptOut.model_validate(row) for row in page]


@router.post(
    "/deliveries/{delivery_id}/replay",
    response_model=DeliveryOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replay a terminal delivery while preserving receiver idempotency",
    dependencies=[Depends(reject_non_replayable_idempotency_key)],
)
async def replay_terminal_delivery(
    delivery_id: UUID,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOut:
    _require_admin(auth)
    try:
        row = await replay_delivery(
            db,
            delivery_id=delivery_id,
            namespace=auth.namespace,
            barrier_group=auth.barrier_group,
        )
    except (
        IntegrationConfigurationError,
        IntegrationConflictError,
        IntegrationNotFoundError,
    ) as exc:
        _translate(exc)
    await chain_log(
        db,
        namespace=auth.namespace,
        agent_id=_actor(auth),
        op="integration.delivery_replay",
        payload={
            "delivery_id": str(row.id),
            "replayed_from_id": str(row.replayed_from_id),
            "event_id": str(row.event_id),
            "destination_id": str(row.destination_id),
            "run_sequence": row.run_sequence,
            "barrier_group": row.barrier_group,
        },
    )
    await _commit(db)
    await db.refresh(row)
    return DeliveryOut.model_validate(row)


@router.get(
    "/readiness",
    response_model=IntegrationReadiness,
    summary="Assess destination and queue readiness without exposing secrets",
)
async def integration_readiness(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
) -> IntegrationReadiness:
    _require_admin(auth)
    inventory = await integration_inventory(
        db,
        namespace=auth.namespace,
        barrier_group=auth.barrier_group,
    )
    settings = get_settings()
    worker_enabled = settings.integration_worker_enabled
    egress_allowed = worker_enabled and not settings.airgap_mode
    worker_healthy, worker_last_poll_at = integration_worker_status()
    if not egress_allowed:
        readiness_status = "egress_disabled"
    elif not worker_healthy:
        readiness_status = "degraded"
    elif inventory["active_destinations"] == 0:
        readiness_status = "configuration_required"
    elif inventory["dead_letter_deliveries"] > 0:
        readiness_status = "degraded"
    else:
        readiness_status = "ready"
    return IntegrationReadiness(
        generated_at=datetime.now(UTC),
        status=readiness_status,
        worker_enabled=worker_enabled,
        worker_healthy=worker_healthy,
        worker_last_poll_at=worker_last_poll_at,
        egress_allowed=egress_allowed,
        **inventory,
        disclosures=[
            "Readiness reports queue configuration and state, not receiver-side processing.",
            "HTTP response bodies are never retained; only bounded SHA-256 digests are stored.",
            (
                "Network egress policy remains a deployment control in addition "
                "to application SSRF checks."
            ),
        ],
    )
