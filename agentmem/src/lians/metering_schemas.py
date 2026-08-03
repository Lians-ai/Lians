"""Secret-free administration contracts for durable usage metering."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

MeteringStatus = Literal["pending", "leased", "retry", "delivered", "dead_letter"]


class MeteringReplayRequest(BaseModel):
    """Explicit, auditable assertion required before a potentially ambiguous retry."""

    reconciliation: Literal["provider_confirmed_not_accepted"]
    reconciliation_reference: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[^\x00-\x1f\x7f]+$",
        description="Opaque incident, ticket, or reconciliation record reference",
    )

    model_config = {"str_strip_whitespace": True}


class MeteringInventoryOut(BaseModel):
    delivery_enabled: bool
    worker_enabled: bool
    provider_configured: bool
    async_error_destination_configured: bool
    worker_healthy: bool
    worker_last_poll_at: datetime | None
    worker_last_heartbeat_at: datetime | None
    worker_last_delivery_at: datetime | None
    worker_last_error_at: datetime | None
    worker_last_error_digest: str | None
    worker_terminal_error: str | None
    pending_events: int
    leased_events: int
    retry_events: int
    delivered_events: int
    dead_letter_events: int
    oldest_due_at: datetime | None


class MeteringEventOut(BaseModel):
    id: UUID
    namespace: str
    event_name: str
    provider_identifier: str
    quantity: int
    status: MeteringStatus
    attempt_count: int
    attempt_limit: int
    replay_count: int
    next_attempt_at: datetime
    first_attempt_at: datetime | None
    last_attempt_at: datetime | None
    delivered_at: datetime | None
    dead_lettered_at: datetime | None
    last_status_code: int | None
    last_error_code: str | None
    last_error_digest: str | None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
