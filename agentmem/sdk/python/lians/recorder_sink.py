"""Bounded, retry-safe asynchronous delivery for Universal Recorder events.

The sink is deliberately an in-process buffer, not a durable queue.  It makes
transport retries safe by freezing an event identity before enqueueing, but a
process crash can still lose buffered events.  Applications that require a
zero-loss boundary should put a durable queue/outbox in front of the Recorder
API and use the same idempotency keys when replaying it.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import math
import random
import threading
from collections import deque
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from types import TracebackType
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from .platform_types import CaptureMode, RecorderBatchResult, RecorderEnvelope

BackpressurePolicy = Literal["block", "raise", "drop_newest"]
DeliveryFailurePolicy = Literal["halt", "drop"]

_RECORDER_PROTOCOLS = {"lians", "otlp.genai", "mcp", "a2a"}
_CAPTURE_MODES = {"metadata_only", "hash_only", "full"}
_TOP_LEVEL_FIELDS = {
    "schema_version",
    "protocol",
    "event_type",
    "event_id",
    "idempotency_key",
    "occurred_at",
    "subject_id",
    "actor",
    "correlation",
    "capture",
    "payload",
    "extensions",
}
_ACTOR_FIELDS = {
    "agent_id",
    "principal_id",
    "roles",
    "authentication_context",
    "extensions",
}
_CORRELATION_FIELDS = {
    "run_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "session_id",
    "task_id",
    "context_id",
    "message_id",
    "tool_call_id",
    "decision_id",
    "extensions",
}
_CAPTURE_FIELDS = {"mode", "sensitive_fields"}


class AsyncRecorderClient(Protocol):
    """Minimal client surface consumed by :class:`AsyncRecorderSink`."""

    async def ingest_recorder_batch(
        self,
        events: list[RecorderEnvelope],
        *,
        atomic: bool = True,
    ) -> RecorderBatchResult: ...


class RecorderSinkError(RuntimeError):
    """Base error for sink lifecycle or delivery failures."""


class RecorderSinkClosed(RecorderSinkError):
    """Raised when an event is submitted before start or after close."""


class RecorderBufferFull(RecorderSinkError):
    """Raised when ``backpressure='raise'`` encounters a full buffer."""


class RecorderIdentityError(RecorderSinkError):
    """Raised when an envelope cannot receive a valid stable identity."""


class RecorderEnvelopeValidationError(RecorderSinkError):
    """Raised when an envelope cannot satisfy the Recorder v0.1 JSON contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RecorderDeliveryError(RecorderSinkError):
    """Raised after a batch exhausts its configured delivery attempts."""


@dataclass(frozen=True)
class RecorderSinkConfig:
    """Delivery and loss behavior for :class:`AsyncRecorderSink`.

    ``block`` applies only to awaited :meth:`AsyncRecorderSink.submit` calls.
    Framework callbacks are synchronous in some runtimes and are never blocked;
    when their callback submission encounters full total admission, the event is
    rejected and a ``callback_backpressure`` capture gap is recorded.
    """

    max_buffered_events: int = 2_048
    batch_size: int = 100
    flush_interval_seconds: float = 0.5
    max_delivery_attempts: int = 5
    retry_initial_seconds: float = 0.25
    retry_max_seconds: float = 5.0
    retry_jitter_ratio: float = 0.2
    backpressure: BackpressurePolicy = "block"
    delivery_failure: DeliveryFailurePolicy = "halt"
    atomic_batches: bool = False
    max_capture_gaps: int = 256
    max_envelope_bytes: int = 1_048_576
    max_content_hash_bytes: int = 4_194_304
    max_value_depth: int = 32
    max_container_items: int = 20_000

    def __post_init__(self) -> None:
        if not _is_positive_int(self.max_buffered_events):
            raise ValueError("max_buffered_events must be at least 1")
        if not _is_positive_int(self.batch_size) or not 1 <= self.batch_size <= min(
            500, self.max_buffered_events
        ):
            raise ValueError("batch_size must be between 1 and min(500, buffer size)")
        if not math.isfinite(self.flush_interval_seconds) or self.flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be positive")
        if not _is_positive_int(self.max_delivery_attempts):
            raise ValueError("max_delivery_attempts must be at least 1")
        if not math.isfinite(self.retry_initial_seconds) or not math.isfinite(
            self.retry_max_seconds
        ):
            raise ValueError("retry delays must be finite")
        if self.retry_initial_seconds < 0 or self.retry_max_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("retry_max_seconds cannot be less than retry_initial_seconds")
        if not math.isfinite(self.retry_jitter_ratio) or not 0 <= self.retry_jitter_ratio <= 1:
            raise ValueError("retry_jitter_ratio must be between 0 and 1")
        if self.backpressure not in {"block", "raise", "drop_newest"}:
            raise ValueError("unsupported backpressure policy")
        if self.delivery_failure not in {"halt", "drop"}:
            raise ValueError("unsupported delivery failure policy")
        if not _is_positive_int(self.max_capture_gaps):
            raise ValueError("max_capture_gaps must be at least 1")
        if not _is_positive_int(self.max_envelope_bytes):
            raise ValueError("max_envelope_bytes must be at least 1")
        if not _is_positive_int(self.max_content_hash_bytes):
            raise ValueError("max_content_hash_bytes must be at least 1")
        if not _is_positive_int(self.max_value_depth):
            raise ValueError("max_value_depth must be at least 1")
        if not _is_positive_int(self.max_container_items):
            raise ValueError("max_container_items must be at least 1")


@dataclass(frozen=True)
class RecorderAttribution:
    """Caller-reported labels attached to adapter events.

    These values are evidence correlation claims, not authentication.  Lians
    derives the authoritative ingestion principal from the credential used by
    the client passed to :class:`AsyncRecorderSink`.
    """

    claimed_agent_id: str | None = None
    claimed_principal_id: str | None = None
    claimed_roles: tuple[str, ...] = ()
    subject_id: str | None = None
    session_id: str | None = None
    decision_id: str | None = None
    capture_mode: CaptureMode = "hash_only"

    def __post_init__(self) -> None:
        if self.capture_mode not in {"metadata_only", "hash_only", "full"}:
            raise ValueError("unsupported Recorder capture mode")
        _validate_optional_text("claimed_agent_id", self.claimed_agent_id, 255)
        _validate_optional_text("claimed_principal_id", self.claimed_principal_id, 512)
        _validate_optional_text("subject_id", self.subject_id, 512)
        _validate_optional_text("session_id", self.session_id, 512)
        _validate_optional_text("decision_id", self.decision_id, 64)
        if self.decision_id is not None:
            try:
                UUID(self.decision_id)
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError("decision_id must be a UUID string") from exc
        if len(self.claimed_roles) > 100:
            raise ValueError("claimed_roles cannot contain more than 100 roles")
        for role in self.claimed_roles:
            if not isinstance(role, str):
                raise TypeError("claimed roles must be strings")
            _validate_optional_text("claimed role", role, 255)


@dataclass(frozen=True)
class RecorderSubmission:
    accepted: bool
    event_id: str
    idempotency_key: str
    reason: str | None = None


@dataclass(frozen=True)
class RecorderCaptureGap:
    """Payload-free disclosure that persistence was absent or unconfirmed."""

    occurred_at: str
    reason: str
    event_id: str
    idempotency_key: str
    detail: str | None = None


@dataclass(frozen=True)
class RecorderSinkStats:
    """Point-in-time counters.

    ``delivered`` includes confirmed duplicates. ``rejected`` includes local
    validation/admission/lifecycle rejection and server rejection; ``dropped`` also
    includes accepted events whose delivery later became unconfirmed.
    """

    submitted: int
    enqueued: int
    delivered: int
    duplicates: int
    rejected: int
    dropped: int
    delivery_attempts: int
    delivery_failures: int
    buffered: int
    in_flight: int
    capture_gaps_total: int
    running: bool
    halted: bool


def recorder_content_hash(
    value: Any,
    *,
    key: bytes | str | None = None,
    max_depth: int = 32,
    max_items: int = 20_000,
    max_bytes: int = 4_194_304,
) -> str:
    """Return a bounded deterministic SHA-256 or HMAC-SHA-256 commitment.

    Unkeyed SHA-256 is interoperable evidence, but low-entropy values can be
    guessed offline. Pass a deployment-held key for a hiding commitment. The
    key never enters an envelope; native adapters disclose only the algorithm.
    """

    key_bytes = _validated_commitment_key(key)
    try:
        normalized = _json_value(
            value,
            max_depth=max_depth,
            max_items=max_items,
            max_bytes=max_bytes,
        )
        return _canonical_json_digest(normalized, key=key_bytes, max_bytes=max_bytes)
    except RecorderIdentityError:
        raise
    except Exception as exc:
        raise RecorderIdentityError(
            "Recorder commitment input cannot be canonically serialized"
        ) from exc


def stabilize_recorder_envelope(
    envelope: RecorderEnvelope,
    *,
    max_depth: int = 32,
    max_items: int = 20_000,
    max_bytes: int = 4_194_304,
) -> RecorderEnvelope:
    """Freeze a copy and ensure it has stable event and idempotency identities.

    Supplying a business-stable ``idempotency_key`` is still recommended for
    replay across process restarts.  If neither identity is supplied, the key
    is derived from the complete canonical envelope; callers should include a
    unique run/tool/source identifier to avoid collapsing identical events.
    """

    try:
        frozen_value = _freeze_json_value(
            envelope,
            max_depth=max_depth,
            max_items=max_items,
            max_bytes=max_bytes,
        )
        if not isinstance(frozen_value, dict):
            raise TypeError("Recorder envelope must be a mapping")
        frozen = cast(RecorderEnvelope, frozen_value)
    except Exception as exc:
        raise RecorderIdentityError(
            "Recorder envelopes must be copyable and canonically serializable"
        ) from exc
    raw_event_id = frozen.get("event_id")
    raw_idempotency_key = frozen.get("idempotency_key")
    if raw_event_id is not None and not isinstance(raw_event_id, str):
        raise RecorderIdentityError("Recorder event_id must be a string")
    if raw_idempotency_key is not None and not isinstance(raw_idempotency_key, str):
        raise RecorderIdentityError("Recorder idempotency_key must be a string")
    event_id = (raw_event_id or "").strip()
    idempotency_key = (raw_idempotency_key or "").strip()
    if not event_id and not idempotency_key:
        digest = recorder_content_hash(
            frozen,
            max_depth=max_depth,
            max_items=max_items,
            max_bytes=max_bytes,
        )
        event_id = idempotency_key = f"lians-auto-v1:{digest}"
    elif not event_id:
        event_id = idempotency_key
    elif not idempotency_key:
        idempotency_key = event_id
    if len(event_id) > 512 or len(idempotency_key) > 512:
        raise RecorderIdentityError("Recorder event identities cannot exceed 512 characters")
    frozen["event_id"] = event_id
    frozen["idempotency_key"] = idempotency_key
    return frozen


def validate_recorder_envelope(
    envelope: RecorderEnvelope,
    *,
    max_depth: int = 32,
    max_items: int = 20_000,
    max_bytes: int = 1_048_576,
) -> None:
    """Validate the dependency-free wire subset of Recorder envelope v0.1."""

    if not isinstance(envelope, dict):
        _invalid("invalid_object", "Recorder envelope must be a JSON object")
    data = cast(dict[str, Any], envelope)
    _forbid_extra("envelope", data, _TOP_LEVEL_FIELDS)
    if data.get("schema_version", "0.1") != "0.1":
        _invalid("schema_version", "schema_version must be '0.1'")
    if data.get("protocol") not in _RECORDER_PROTOCOLS:
        _invalid("protocol", "protocol is not a supported Recorder protocol")
    _required_mapping("payload", data.get("payload"), maximum=1_000)
    if "extensions" in data:
        _required_mapping("extensions", data["extensions"], maximum=256)
    _optional_text("event_type", data.get("event_type"), 128)
    _optional_text("event_id", data.get("event_id"), 512)
    _optional_text("idempotency_key", data.get("idempotency_key"), 512)
    _optional_text("subject_id", data.get("subject_id"), 512)
    _optional_datetime(data.get("occurred_at"))

    actor = (
        _required_mapping("actor", data["actor"], maximum=5)
        if "actor" in data
        else None
    )
    if actor is not None:
        _forbid_extra("actor", actor, _ACTOR_FIELDS)
        _optional_text("actor.agent_id", actor.get("agent_id"), 255)
        _optional_text("actor.principal_id", actor.get("principal_id"), 512)
        _text_list("actor.roles", actor.get("roles", []), maximum=100, text_maximum=255)
        if "authentication_context" in actor:
            _required_mapping(
                "actor.authentication_context",
                actor["authentication_context"],
                maximum=100,
            )
        if "extensions" in actor:
            _required_mapping("actor.extensions", actor["extensions"], maximum=256)

    correlation = (
        _required_mapping(
            "correlation", data["correlation"], maximum=len(_CORRELATION_FIELDS)
        )
        if "correlation" in data
        else None
    )
    if correlation is not None:
        _forbid_extra("correlation", correlation, _CORRELATION_FIELDS)
        for field in (
            "run_id",
            "session_id",
            "task_id",
            "context_id",
            "message_id",
            "tool_call_id",
        ):
            _optional_text(f"correlation.{field}", correlation.get(field), 512)
        for field in ("trace_id", "span_id", "parent_span_id"):
            _optional_text(f"correlation.{field}", correlation.get(field), 64)
        decision_id = correlation.get("decision_id")
        if decision_id is not None:
            _optional_text("correlation.decision_id", decision_id, 64)
            try:
                UUID(cast(str, decision_id))
            except (TypeError, ValueError, AttributeError):
                _invalid("decision_id", "correlation.decision_id must be a UUID string")
        if "extensions" in correlation:
            _required_mapping(
                "correlation.extensions", correlation["extensions"], maximum=256
            )

    capture = (
        _required_mapping("capture", data["capture"], maximum=2)
        if "capture" in data
        else None
    )
    if capture is not None:
        _forbid_extra("capture", capture, _CAPTURE_FIELDS)
        if capture.get("mode", "hash_only") not in _CAPTURE_MODES:
            _invalid("capture_mode", "capture.mode is not supported")
        _text_list(
            "capture.sensitive_fields",
            capture.get("sensitive_fields", []),
            maximum=100,
            text_maximum=512,
        )

    _validate_json_value(
        data,
        max_depth=max_depth,
        max_items=max_items,
        max_bytes=max_bytes,
    )
    try:
        _validate_json_encoding(data, max_bytes=max_bytes)
    except RecorderEnvelopeValidationError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise RecorderEnvelopeValidationError(
            "non_json_value", "Recorder envelopes must contain JSON values only"
        ) from exc


class AsyncRecorderSink:
    """Bounded asynchronous Recorder batch sink with explicit loss semantics.

    The sink must be started on the event loop that owns the async Lians client.
    Use it as an async context manager or call :meth:`start` and :meth:`close`.
    """

    def __init__(
        self,
        client: AsyncRecorderClient,
        *,
        config: RecorderSinkConfig | None = None,
        commitment_key: bytes | str | None = None,
    ) -> None:
        self._client = client
        self.config = config or RecorderSinkConfig()
        self._commitment_key = _validated_commitment_key(commitment_key)
        self._queue: asyncio.Queue[RecorderEnvelope] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: asyncio.Task[None] | None = None
        self._state: Literal["new", "running", "stopping", "halted", "closed"] = "new"
        self._drain_aborted = False
        self._terminal: asyncio.Event | None = None
        self._drained: asyncio.Event | None = None
        self._capacity_available: asyncio.Event | None = None
        self._flush_requested: asyncio.Event | None = None
        self._close_complete: asyncio.Event | None = None
        self._close_error: BaseException | None = None
        self._terminal_error: RecorderDeliveryError | None = None
        self._admitted = 0
        self._in_flight = 0
        self._state_lock = threading.RLock()
        self._gaps: deque[RecorderCaptureGap] = deque(maxlen=self.config.max_capture_gaps)
        self._counters = {
            "submitted": 0,
            "enqueued": 0,
            "delivered": 0,
            "duplicates": 0,
            "rejected": 0,
            "dropped": 0,
            "delivery_attempts": 0,
            "delivery_failures": 0,
            "capture_gaps_total": 0,
        }

    async def __aenter__(self) -> AsyncRecorderSink:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            # Failure boundaries are often the most important evidence. Drain
            # even when the protected agent operation raised.
            await self.close(drain=True)
        except BaseException as close_error:
            if exc is None:
                raise
            self.disclose_gap(
                "close_failed_during_application_error",
                detail=type(close_error).__name__,
            )

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._state == "running"

    @property
    def commitment_scheme(self) -> Literal["sha256", "hmac-sha256"]:
        """Algorithm used by :meth:`content_hash` for captured content."""

        return "hmac-sha256" if self._commitment_key is not None else "sha256"

    def content_hash(self, value: Any) -> str:
        """Create a bounded content commitment using this sink's policy."""

        return recorder_content_hash(
            value,
            key=self._commitment_key,
            max_depth=self.config.max_value_depth,
            max_items=self.config.max_container_items,
            max_bytes=self.config.max_content_hash_bytes,
        )

    def owns_current_loop(self) -> bool:
        """Return whether the caller is executing on the sink's event loop."""

        with self._state_lock:
            loop = self._loop
        return loop is not None and loop is _running_loop_or_none()

    async def start(self) -> None:
        """Bind to the current loop and start the delivery worker."""

        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._state == "running":
                if self._loop is not loop:
                    raise RecorderSinkError("Recorder sink is already bound to another loop")
                return
            if self._state != "new":
                raise RecorderSinkClosed("a stopped Recorder sink cannot be restarted")
            self._loop = loop
            self._queue = asyncio.Queue(maxsize=self.config.max_buffered_events)
            self._terminal = asyncio.Event()
            self._drained = asyncio.Event()
            self._drained.set()
            self._capacity_available = asyncio.Event()
            self._capacity_available.set()
            self._flush_requested = asyncio.Event()
            self._close_complete = asyncio.Event()
            self._state = "running"
            self._worker = loop.create_task(self._run(), name="lians-recorder-sink")
            self._worker.add_done_callback(self._worker_done)

    async def submit(self, envelope: RecorderEnvelope) -> RecorderSubmission:
        """Submit one event, awaiting capacity only under ``block`` policy."""

        self._require_owner_loop()
        frozen, rejected = self._prepare_submission(envelope)
        if rejected is not None:
            return rejected
        assert frozen is not None

        if self.config.backpressure != "block":
            if not self._reserve():
                rejected = self._reject_unreserved(frozen, "buffer_full")
                if self.config.backpressure == "raise":
                    raise RecorderBufferFull(
                        "Recorder buffer is full "
                        f"({self.config.max_buffered_events} total admitted events)"
                    )
                return rejected
            return self._enqueue_reserved(frozen)

        try:
            while not self._reserve():
                self._raise_if_not_accepting()
                await self._wait_for_capacity()
        except asyncio.CancelledError:
            self._reject_unreserved(frozen, "submit_cancelled_before_admission")
            raise
        except RecorderSinkError:
            self._reject_unreserved(frozen, "sink_stopped_before_admission")
            raise
        return self._enqueue_reserved(frozen)

    def submit_nowait(self, envelope: RecorderEnvelope) -> RecorderSubmission:
        """Submit on the sink event loop without waiting for capacity."""

        self._require_owner_loop()
        frozen, rejected = self._prepare_submission(envelope)
        if rejected is not None:
            return rejected
        assert frozen is not None
        if not self._reserve():
            rejected = self._reject_unreserved(frozen, "buffer_full")
            if self.config.backpressure == "raise":
                raise RecorderBufferFull(
                    "Recorder buffer is full "
                    f"({self.config.max_buffered_events} total admitted events)"
                )
            return rejected
        return self._enqueue_reserved(frozen)

    def submit_threadsafe(self, envelope: RecorderEnvelope) -> Future[RecorderSubmission]:
        """Schedule a non-blocking submission from a synchronous callback/thread.

        The returned future reports whether bounded total admission accepted
        the event. Native framework adapters intentionally do not wait on it,
        because their callback contracts require a quick synchronous return.
        """

        result: Future[RecorderSubmission] = Future()
        frozen, rejected = self._prepare_submission(envelope)
        if rejected is not None:
            result.set_result(rejected)
            return result
        assert frozen is not None

        def enqueue() -> None:
            if not result.set_running_or_notify_cancel():
                self._drop_reserved(frozen, "callback_submission_cancelled")
                return
            try:
                with self._state_lock:
                    still_owned = self._state in {"running", "stopping"}
                if not still_owned:
                    result.set_result(
                        self._drop_reserved(frozen, "sink_stopped_before_enqueue")
                    )
                    return
                result.set_result(self._enqueue_reserved(frozen))
            except BaseException as exc:  # noqa: BLE001 -- preserve Future cancellation/error
                self._drop_reserved(frozen, "callback_enqueue_failed")
                result.set_exception(exc)

        rejection_reason: str | None = None
        schedule_failed = False
        run_inline = False
        with self._state_lock:
            loop = self._loop
            if self._state != "running" or loop is None:
                rejection_reason = "sink_not_running"
            elif self._admitted >= self.config.max_buffered_events:
                rejection_reason = "callback_backpressure"
            else:
                self._reserve_locked()
                if loop is _running_loop_or_none():
                    run_inline = True
                else:
                    try:
                        # Scheduling and admission are one lifecycle-critical
                        # section: close cannot strand a reservation between them.
                        loop.call_soon_threadsafe(enqueue)
                    except RuntimeError:
                        schedule_failed = True
        if rejection_reason is not None:
            result.set_result(self._reject_unreserved(frozen, rejection_reason))
            return result
        if schedule_failed:
            submission = self._drop_reserved(frozen, "sink_not_running")
            if not result.done():
                result.set_result(submission)
            return result
        if run_inline:
            enqueue()
        return result

    def request_flush_threadsafe(self) -> Future[None]:
        """Schedule :meth:`flush` on the sink loop for a synchronous runtime."""

        result: Future[None] = Future()
        with self._state_lock:
            loop = self._loop
            active = self._state in {"running", "stopping"}
        if loop is None or not active:
            result.set_exception(RecorderSinkClosed("Recorder sink is not running"))
            return result

        async def run_flush() -> None:
            try:
                await self.flush()
            except BaseException as exc:  # noqa: BLE001 -- mirror async failure into Future
                result.set_exception(exc)
            else:
                result.set_result(None)

        def schedule() -> None:
            if not result.set_running_or_notify_cancel():
                return
            loop.create_task(run_flush(), name="lians-recorder-flush")

        try:
            if loop is _running_loop_or_none():
                schedule()
            else:
                loop.call_soon_threadsafe(schedule)
        except RuntimeError as exc:
            error = RecorderSinkClosed("Recorder sink is not running")
            error.__cause__ = exc
            result.set_exception(error)
        return result

    async def flush(self, *, timeout: float | None = None) -> None:
        """Wait until all accepted events are delivered or explicitly dropped."""

        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and nonnegative, or None")
        self._require_owner_loop(allow_stopping=True)
        drained = self._require_event(self._drained)
        terminal = self._require_event(self._terminal)
        flush_requested = self._require_event(self._flush_requested)
        flush_requested.set()

        async def wait() -> None:
            while True:
                with self._state_lock:
                    error = self._terminal_error
                    aborted = self._drain_aborted
                    admitted = self._admitted
                    state = self._state
                if error is not None:
                    raise error
                if admitted == 0:
                    flush_requested.clear()
                    return
                if aborted:
                    raise RecorderSinkClosed(
                        "Recorder sink stopped before accepted events were drained"
                    )
                if state in {"halted", "closed"}:
                    raise RecorderSinkClosed("Recorder sink stopped before flush completed")
                drained.clear()
                with self._state_lock:
                    if self._admitted == 0:
                        drained.set()
                        flush_requested.clear()
                        return
                    if self._terminal_error is not None:
                        raise self._terminal_error
                drained_wait = asyncio.create_task(drained.wait())
                terminal_wait = asyncio.create_task(terminal.wait())
                watchers = {drained_wait, terminal_wait}
                try:
                    await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in watchers:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*watchers, return_exceptions=True)

        if timeout is None:
            await wait()
        else:
            await asyncio.wait_for(wait(), timeout=timeout)

    async def close(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Stop the worker, optionally draining accepted events first."""

        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._state == "closed":
                return
            if self._state == "new":
                self._state = "closed"
                return
            if self._loop is not loop:
                raise RecorderSinkError("close must run on the sink event loop")
            if self._state == "stopping":
                close_complete = self._close_complete
                lead_close = False
            else:
                self._state = "stopping"
                close_complete = self._close_complete
                lead_close = True
            capacity_available = self._capacity_available
        if not lead_close:
            if close_complete is None:
                raise RecorderSinkClosed("Recorder sink close state is unavailable")
            await close_complete.wait()
            with self._state_lock:
                close_error = self._close_error
            if close_error is not None:
                raise close_error
            return
        if capacity_available is not None:
            capacity_available.set()
        error: BaseException | None = None
        if drain:
            try:
                await self.flush(timeout=timeout)
            except BaseException as exc:  # noqa: BLE001 -- close before re-raising exact failure
                error = exc
        with self._state_lock:
            self._drain_aborted = not drain or error is not None
            worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        if not drain or error is not None:
            reason = "closed_without_drain" if not drain else "close_drain_failed"
            self._discard_queued(reason)
        with self._state_lock:
            self._state = "closed"
            self._close_error = error
            terminal = self._terminal
            close_complete = self._close_complete
        if terminal is not None:
            terminal.set()
        if close_complete is not None:
            close_complete.set()
        if error is not None:
            raise error

    def capture_gaps(self) -> tuple[RecorderCaptureGap, ...]:
        """Return the retained, bounded capture-gap window (never payloads)."""

        with self._state_lock:
            return tuple(self._gaps)

    def stats(self) -> RecorderSinkStats:
        """Return a thread-safe point-in-time sink snapshot."""

        with self._state_lock:
            values = dict(self._counters)
            in_flight = self._in_flight
            buffered = self._admitted - in_flight
            state = self._state
        return RecorderSinkStats(
            **values,
            buffered=buffered,
            in_flight=in_flight,
            running=state == "running",
            halted=state == "halted",
        )

    def disclose_gap(
        self,
        reason: str,
        *,
        event_id: str = "framework-unobservable",
        idempotency_key: str = "framework-unobservable",
        detail: str | None = None,
    ) -> None:
        """Let adapters disclose an observation/callback gap without payloads."""

        gap = RecorderCaptureGap(
            occurred_at=datetime.now(timezone.utc).isoformat(),
            reason=reason[:128],
            event_id=event_id[:512],
            idempotency_key=idempotency_key[:512],
            detail=detail[:512] if detail else None,
        )
        with self._state_lock:
            self._gaps.append(gap)
            self._counters["capture_gaps_total"] += 1

    def _prepare_submission(
        self, envelope: RecorderEnvelope
    ) -> tuple[RecorderEnvelope | None, RecorderSubmission | None]:
        self._increment("submitted")
        try:
            frozen = stabilize_recorder_envelope(
                envelope,
                max_depth=self.config.max_value_depth,
                max_items=self.config.max_container_items,
                max_bytes=self.config.max_content_hash_bytes,
            )
            validate_recorder_envelope(
                frozen,
                max_depth=self.config.max_value_depth,
                max_items=self.config.max_container_items,
                max_bytes=self.config.max_envelope_bytes,
            )
        except (RecorderIdentityError, RecorderEnvelopeValidationError) as exc:
            code = getattr(exc, "code", "invalid_identity")
            return None, self._reject_invalid(envelope, code)
        return frozen, None

    def _reject_invalid(self, envelope: Any, code: str) -> RecorderSubmission:
        event_id = _safe_envelope_identity(envelope, "event_id")
        idempotency_key = _safe_envelope_identity(envelope, "idempotency_key")
        if event_id == "invalid-envelope" and idempotency_key != "invalid-envelope":
            event_id = idempotency_key
        elif idempotency_key == "invalid-envelope" and event_id != "invalid-envelope":
            idempotency_key = event_id
        submission = RecorderSubmission(
            accepted=False,
            event_id=event_id,
            idempotency_key=idempotency_key,
            reason="invalid_envelope",
        )
        self._increment("rejected")
        self._increment("dropped")
        self.disclose_gap(
            "invalid_envelope",
            event_id=event_id,
            idempotency_key=idempotency_key,
            detail=code,
        )
        return submission

    def _reject_unreserved(
        self, envelope: RecorderEnvelope, reason: str
    ) -> RecorderSubmission:
        submission = self._submission(envelope, accepted=False, reason=reason)
        self._increment("rejected")
        self._increment("dropped")
        self.disclose_gap(
            reason,
            event_id=submission.event_id,
            idempotency_key=submission.idempotency_key,
        )
        return submission

    def _drop_reserved(
        self, envelope: RecorderEnvelope, reason: str
    ) -> RecorderSubmission:
        submission = self._submission(envelope, accepted=False, reason=reason)
        self._increment("rejected")
        self._increment("dropped")
        self._delivery_gap(envelope, reason, None)
        self._release_admission()
        return submission

    @staticmethod
    def _submission(
        envelope: RecorderEnvelope,
        *,
        accepted: bool,
        reason: str | None = None,
    ) -> RecorderSubmission:
        return RecorderSubmission(
            accepted=accepted,
            event_id=str(envelope["event_id"]),
            idempotency_key=str(envelope["idempotency_key"]),
            reason=reason,
        )

    async def _run(self) -> None:
        queue = self._require_queue_initialized()
        while True:
            batch: list[RecorderEnvelope] = []
            try:
                batch.append(await queue.get())
                await self._accumulate_batch(queue, batch)
                with self._state_lock:
                    self._in_flight += len(batch)
                await self._deliver(batch)
            except asyncio.CancelledError:
                if batch:
                    self._increment("dropped", len(batch))
                for event in batch:
                    self._delivery_gap(event, "delivery_cancelled_ambiguous", None)
                raise
            except RecorderDeliveryError as exc:
                self._increment("delivery_failures")
                if self.config.delivery_failure == "halt":
                    self._increment("dropped", len(batch))
                    for event in batch:
                        self._delivery_gap(
                            event,
                            "delivery_halted_unconfirmed",
                            type(exc).__name__,
                        )
                    with self._state_lock:
                        self._terminal_error = exc
                        self._state = "halted"
                    self._discard_queued("delivery_halted_pending", type(exc).__name__)
                else:
                    self._increment("dropped", len(batch))
                    for event in batch:
                        self._delivery_gap(
                            event,
                            "delivery_attempts_exhausted_unconfirmed",
                            type(exc).__name__,
                        )
            except Exception as exc:  # noqa: BLE001 -- protect lifecycle accounting
                wrapped = RecorderDeliveryError(
                    f"Recorder worker failed: {type(exc).__name__}"
                )
                wrapped.__cause__ = exc
                self._increment("delivery_failures")
                self._increment("dropped", len(batch))
                for event in batch:
                    self._delivery_gap(
                        event, "delivery_halted_unconfirmed", type(exc).__name__
                    )
                with self._state_lock:
                    self._terminal_error = wrapped
                    self._state = "halted"
                self._discard_queued("delivery_halted_pending", type(exc).__name__)
            finally:
                for _ in batch:
                    queue.task_done()
                if batch:
                    with self._state_lock:
                        self._in_flight = max(0, self._in_flight - len(batch))
                    self._release_admission(len(batch))
            with self._state_lock:
                if self._state == "halted":
                    terminal = self._terminal
                else:
                    terminal = None
            if terminal is not None:
                terminal.set()
                return

    async def _accumulate_batch(
        self,
        queue: asyncio.Queue[RecorderEnvelope],
        batch: list[RecorderEnvelope],
    ) -> None:
        flush_requested = self._require_event(self._flush_requested)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.config.flush_interval_seconds
        while len(batch) < self.config.batch_size:
            if flush_requested.is_set():
                flush_requested.clear()
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            get_event = asyncio.create_task(queue.get())
            flush_wait = asyncio.create_task(flush_requested.wait())
            watchers = {get_event, flush_wait}
            try:
                done, _ = await asyncio.wait(
                    watchers,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if get_event in done and not get_event.cancelled():
                    batch.append(get_event.result())
                if flush_wait in done:
                    flush_requested.clear()
                    return
                if not done:
                    return
            except asyncio.CancelledError:
                if get_event.done() and not get_event.cancelled():
                    try:
                        batch.append(get_event.result())
                    except BaseException:
                        pass
                raise
            finally:
                for task in watchers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*watchers, return_exceptions=True)

    async def _deliver(self, batch: list[RecorderEnvelope]) -> None:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_delivery_attempts + 1):
            self._increment("delivery_attempts")
            try:
                result = self._client.ingest_recorder_batch(
                    batch, atomic=self.config.atomic_batches
                )
                if not inspect.isawaitable(result):
                    raise TypeError(
                        "AsyncRecorderSink requires an async client; "
                        "pass AsyncLiansClient, not LiansClient"
                    )
                response = await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- transport/client failures are retryable
                last_error = exc
                if (
                    attempt == self.config.max_delivery_attempts
                    or not _is_retryable_delivery_error(exc)
                ):
                    break
                await asyncio.sleep(self._retry_delay(attempt, exc))
                continue
            try:
                if not isinstance(response, Mapping):
                    raise TypeError("Recorder batch response must be a mapping")
                received = _response_count(response, "received")
                accepted = _response_count(response, "accepted")
                duplicates = _response_count(response, "duplicates")
                rejected_count = _response_count(response, "rejected")
                raw_rejections = response.get("rejections", [])
                if not isinstance(raw_rejections, list):
                    raise TypeError("Recorder batch rejections must be a list")
                rejections = raw_rejections
                if (
                    received != len(batch)
                    or min(accepted, duplicates, rejected_count) < 0
                    or accepted + duplicates + rejected_count != len(batch)
                    or rejected_count != len(rejections)
                ):
                    raise ValueError("Recorder batch response counters are inconsistent")
                mapped_rejections: list[tuple[RecorderEnvelope, str]] = []
                seen_rejection_indices: set[int] = set()
                for rejection in rejections:
                    if not isinstance(rejection, Mapping):
                        raise TypeError("Recorder batch rejection must be a mapping")
                    index = rejection.get("index")
                    if (
                        not isinstance(index, int)
                        or isinstance(index, bool)
                        or not 0 <= index < len(batch)
                        or index in seen_rejection_indices
                    ):
                        raise ValueError("Recorder batch rejection indices are inconsistent")
                    seen_rejection_indices.add(index)
                    raw_code = rejection.get("code")
                    code = _safe_gap_code(raw_code)
                    mapped_rejections.append((batch[index], code))
                self._increment("delivered", accepted + duplicates)
                self._increment("duplicates", duplicates)
                self._increment("rejected", rejected_count)
                self._increment("dropped", rejected_count)
                for event, code in mapped_rejections:
                    self._delivery_gap(
                        event,
                        "server_rejected",
                        code,
                    )
                return
            except Exception as exc:  # noqa: BLE001 -- malformed responses follow retry policy
                last_error = exc
                if (
                    attempt == self.config.max_delivery_attempts
                    or not _is_retryable_delivery_error(exc)
                ):
                    break
                await asyncio.sleep(self._retry_delay(attempt, exc))
        raise RecorderDeliveryError(
            "Recorder delivery attempts exhausted; "
            f"last error type: {type(last_error).__name__ if last_error else 'unknown'}"
        ) from last_error

    def _retry_delay(self, attempt: int, error: Exception) -> float:
        base = min(
            self.config.retry_max_seconds,
            self.config.retry_initial_seconds * (2 ** max(0, attempt - 1)),
        )
        retry_after = _retry_after_seconds(error)
        if retry_after is not None:
            if retry_after > self.config.retry_max_seconds:
                raise RecorderDeliveryError(
                    "server Retry-After exceeds retry_max_seconds; refusing to "
                    "retry earlier than the server requested"
                ) from error
            base = max(base, retry_after)
        jitter = base * self.config.retry_jitter_ratio
        return min(self.config.retry_max_seconds, base + random.uniform(0.0, jitter))

    def _delivery_gap(
        self,
        envelope: RecorderEnvelope,
        reason: str,
        detail: str | None,
    ) -> None:
        self.disclose_gap(
            reason,
            event_id=str(envelope["event_id"]),
            idempotency_key=str(envelope["idempotency_key"]),
            detail=detail,
        )

    def _discard_queued(self, reason: str, detail: str | None = None) -> None:
        queue = self._queue
        if queue is None:
            return
        discarded = 0
        while True:
            try:
                event = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            discarded += 1
            self._increment("dropped")
            self._delivery_gap(event, reason, detail)
            queue.task_done()
        if discarded:
            self._release_admission(discarded)

    def _increment(self, name: str, amount: int = 1) -> None:
        with self._state_lock:
            self._counters[name] += amount

    def _reserve(self) -> bool:
        with self._state_lock:
            if (
                self._state != "running"
                or self._admitted >= self.config.max_buffered_events
            ):
                return False
            self._reserve_locked()
            return True

    def _reserve_locked(self) -> None:
        self._admitted += 1
        self._counters["enqueued"] += 1

    def _release_admission(self, amount: int = 1) -> None:
        with self._state_lock:
            if amount < 0 or amount > self._admitted:
                raise RuntimeError("Recorder sink admission accounting underflow")
            self._admitted -= amount
            admitted = self._admitted
            capacity_available = self._capacity_available
            drained = self._drained
            flush_requested = self._flush_requested
            loop = self._loop

        def signal() -> None:
            if capacity_available is not None:
                capacity_available.set()
            if admitted == 0:
                if drained is not None:
                    drained.set()
                if flush_requested is not None:
                    flush_requested.clear()

        if loop is None or loop is _running_loop_or_none():
            signal()
            return
        try:
            loop.call_soon_threadsafe(signal)
        except RuntimeError:
            # The loop has already stopped. Counters remain authoritative and
            # no asyncio waiter can still make progress on the closed loop.
            return

    def _enqueue_reserved(self, envelope: RecorderEnvelope) -> RecorderSubmission:
        queue = self._require_queue_initialized()
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            return self._drop_reserved(envelope, "internal_queue_capacity_mismatch")
        drained = self._require_event(self._drained)
        drained.clear()
        return self._submission(envelope, accepted=True)

    async def _wait_for_capacity(self) -> None:
        capacity_available = self._require_event(self._capacity_available)
        terminal = self._require_event(self._terminal)
        capacity_available.clear()
        with self._state_lock:
            if self._state != "running" or (
                self._admitted < self.config.max_buffered_events
            ):
                capacity_available.set()
                self._raise_if_not_accepting_locked()
                return
        capacity_wait = asyncio.create_task(capacity_available.wait())
        terminal_wait = asyncio.create_task(terminal.wait())
        watchers = {capacity_wait, terminal_wait}
        try:
            await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in watchers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*watchers, return_exceptions=True)
        self._raise_if_not_accepting()

    def _raise_if_not_accepting(self) -> None:
        with self._state_lock:
            self._raise_if_not_accepting_locked()

    def _raise_if_not_accepting_locked(self) -> None:
        if self._terminal_error is not None:
            raise self._terminal_error
        if self._state != "running":
            raise RecorderSinkClosed(
                "Recorder sink is not accepting events; start it or use async with"
            )

    def _require_owner_loop(self, *, allow_stopping: bool = False) -> None:
        current = _running_loop_or_none()
        with self._state_lock:
            loop = self._loop
            state = self._state
            error = self._terminal_error
        if loop is None or current is not loop:
            raise RecorderSinkError(
                "operation must run on the sink event loop; use the thread-safe API "
                "from synchronous callbacks"
            )
        if error is not None:
            raise error
        allowed = {"running", "stopping"} if allow_stopping else {"running"}
        if state not in allowed:
            raise RecorderSinkClosed("Recorder sink is not running")

    def _require_queue_initialized(self) -> asyncio.Queue[RecorderEnvelope]:
        queue = self._queue
        if queue is None:
            raise RecorderSinkClosed("Recorder sink is not initialized")
        return queue

    @staticmethod
    def _require_event(event: asyncio.Event | None) -> asyncio.Event:
        if event is None:
            raise RecorderSinkClosed("Recorder sink is not initialized")
        return event

    def _worker_done(self, worker: asyncio.Task[None]) -> None:
        if worker.cancelled():
            return
        try:
            error = worker.exception()
        except asyncio.CancelledError:
            return
        if error is None:
            return
        wrapped = RecorderDeliveryError(
            f"Recorder worker stopped unexpectedly: {type(error).__name__}"
        )
        wrapped.__cause__ = error
        with self._state_lock:
            if self._state in {"closed", "halted"}:
                return
            self._terminal_error = wrapped
            self._state = "halted"
            terminal = self._terminal
        self._discard_queued("delivery_halted_pending", type(error).__name__)
        if terminal is not None:
            terminal.set()


def _running_loop_or_none() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _validate_optional_text(name: str, value: str | None, maximum: int) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} cannot be blank")
    if len(value) > maximum:
        raise ValueError(f"{name} cannot exceed {maximum} characters")


def _is_retryable_delivery_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status >= 500 or status in {408, 409, 425, 429}
    # Invalid local client implementations and malformed 2xx JSON are not
    # transient. Network, timeout, and unknown transport errors remain
    # retryable because an idempotency key protects ambiguous commits.
    return not isinstance(error, (TypeError, ValueError))


def _response_count(response: Mapping[str, Any], field: str) -> int:
    value = response.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Recorder batch response field {field!r} must be an integer")
    return value


def _safe_gap_code(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return "rejected"
    if any(
        not (
            character.isascii()
            and (character.isalnum() or character in "_.-")
        )
        for character in value
    ):
        return "rejected"
    return value


def _retry_after_seconds(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
        return None
    raw_text = str(raw).strip()
    try:
        value = float(raw_text)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(raw_text)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        value = (target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    if not math.isfinite(value):
        return None
    return max(0.0, value)


def _canonical_json_digest(
    value: Any,
    *,
    key: bytes | None,
    max_bytes: int,
) -> str:
    if not _is_positive_int(max_bytes):
        raise ValueError("max_bytes must be a positive integer")
    if key is None:
        digest: Any = hashlib.sha256()
    else:
        digest = hmac.new(key, digestmod=hashlib.sha256)
    encoder = json.JSONEncoder(
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    total = 0
    for chunk in encoder.iterencode(value):
        encoded = chunk.encode("utf-8")
        total += len(encoded)
        if total > max_bytes:
            raise RecorderIdentityError(
                f"Recorder commitment input exceeds the {max_bytes}-byte local limit"
            )
        digest.update(encoded)
    return cast(str, digest.hexdigest())


def _validated_commitment_key(key: bytes | str | None) -> bytes | None:
    if key is None:
        return None
    if isinstance(key, str):
        if len(key) > 4_096:
            raise ValueError("Recorder commitment keys cannot exceed 4,096 characters")
        key_bytes = key.encode("utf-8")
    elif isinstance(key, bytes):
        key_bytes = bytes(key)
    else:
        raise TypeError("Recorder commitment keys must be bytes or strings")
    if len(key_bytes) < 32:
        raise ValueError("Recorder commitment keys must contain at least 32 bytes")
    if len(key_bytes) > 16_384:
        raise ValueError("Recorder commitment keys cannot exceed 16,384 bytes")
    return key_bytes


def _validate_json_encoding(value: Any, *, max_bytes: int) -> None:
    if not _is_positive_int(max_bytes):
        raise ValueError("max_bytes must be a positive integer")
    encoder = json.JSONEncoder(
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    total = 0
    for chunk in encoder.iterencode(value):
        total += len(chunk.encode("utf-8"))
        if total > max_bytes:
            _invalid(
                "envelope_too_large",
                f"Recorder envelope exceeds the {max_bytes}-byte local limit",
            )


@dataclass
class _Traversal:
    max_depth: int
    max_items: int
    max_bytes: int
    items: int = 0

    def visit(self, depth: int, *, identity: bool) -> None:
        if depth > self.max_depth:
            if identity:
                raise RecorderIdentityError(
                    f"Recorder commitment input exceeds depth {self.max_depth}"
                )
            _invalid(
                "value_too_deep",
                f"Recorder envelope exceeds value depth {self.max_depth}",
            )
        self.items += 1
        if self.items > self.max_items:
            if identity:
                raise RecorderIdentityError(
                    f"Recorder commitment input exceeds {self.max_items} values"
                )
            _invalid(
                "too_many_values",
                f"Recorder envelope exceeds {self.max_items} values",
            )

    def text(self, value: str, *, identity: bool) -> None:
        if len(value) <= self.max_bytes:
            return
        if identity:
            raise RecorderIdentityError(
                f"Recorder commitment text exceeds {self.max_bytes} characters"
            )
        _invalid(
            "text_too_large",
            f"Recorder envelope text exceeds {self.max_bytes} characters",
        )

    def integer(self, value: int, *, identity: bool) -> None:
        # A base-10 rendering is smaller than four bits per digit. Reject before
        # JSON encoding could allocate an integer token above the byte budget.
        if value.bit_length() <= self.max_bytes * 4:
            return
        if identity:
            raise RecorderIdentityError(
                f"Recorder commitment integer exceeds the {self.max_bytes}-byte limit"
            )
        _invalid(
            "integer_too_large",
            f"Recorder envelope integer exceeds the {self.max_bytes}-byte limit",
        )


def _freeze_json_value(
    value: Any,
    *,
    max_depth: int,
    max_items: int,
    max_bytes: int,
) -> Any:
    traversal = _Traversal(
        max_depth=max_depth,
        max_items=max_items,
        max_bytes=max_bytes,
    )
    return _freeze_json_node(value, traversal, 0, set())


def _freeze_json_node(
    value: Any,
    traversal: _Traversal,
    depth: int,
    active: set[int],
) -> Any:
    traversal.visit(depth, identity=False)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        traversal.text(value, identity=False)
        return value
    if isinstance(value, int):
        traversal.integer(value, identity=False)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _invalid("non_finite_number", "Recorder envelopes cannot contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            _invalid("cyclic_value", "Recorder envelopes cannot contain cyclic values")
        active.add(marker)
        try:
            frozen: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    _invalid(
                        "non_string_key", "Recorder envelope object keys must be strings"
                    )
                traversal.text(key, identity=False)
                frozen[key] = _freeze_json_node(item, traversal, depth + 1, active)
            return frozen
        finally:
            active.remove(marker)
    if isinstance(value, list):
        marker = id(value)
        if marker in active:
            _invalid("cyclic_value", "Recorder envelopes cannot contain cyclic values")
        active.add(marker)
        try:
            return [
                _freeze_json_node(item, traversal, depth + 1, active) for item in value
            ]
        finally:
            active.remove(marker)
    _invalid("non_json_value", "Recorder envelopes must contain JSON values only")


def _validate_json_value(
    value: Any,
    *,
    max_depth: int,
    max_items: int,
    max_bytes: int,
) -> None:
    _freeze_json_value(
        value,
        max_depth=max_depth,
        max_items=max_items,
        max_bytes=max_bytes,
    )


def _json_value(
    value: Any,
    *,
    max_depth: int,
    max_items: int,
    max_bytes: int,
) -> Any:
    traversal = _Traversal(
        max_depth=max_depth,
        max_items=max_items,
        max_bytes=max_bytes,
    )
    return _json_node(value, traversal, 0, set())


def _json_node(
    value: Any,
    traversal: _Traversal,
    depth: int,
    active: set[int],
) -> Any:
    traversal.visit(depth, identity=True)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        traversal.text(value, identity=True)
        return value
    if isinstance(value, int):
        traversal.integer(value, identity=True)
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RecorderIdentityError(
                "Recorder commitment input cannot contain NaN or infinity"
            )
        return value
    if isinstance(value, Enum):
        return _json_node(value.value, traversal, depth + 1, active)
    if isinstance(value, BaseException):
        return _json_node(
            {
                "type": f"{type(value).__module__}.{type(value).__qualname__}",
                "args": value.args,
            },
            traversal,
            depth + 1,
            active,
        )
    if isinstance(value, UUID):
        result = str(value)
        traversal.text(result, identity=True)
        return result
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result = value.isoformat()
        traversal.text(result, identity=True)
        return result
    if isinstance(value, date):
        result = value.isoformat()
        traversal.text(result, identity=True)
        return result
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in active:
            raise RecorderIdentityError("Recorder commitment input cannot be cyclic")
        active.add(marker)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                normalized_key = _commitment_key(key, traversal)
                if normalized_key in normalized:
                    raise RecorderIdentityError(
                        "Recorder commitment mapping keys are not unique after normalization"
                    )
                normalized[normalized_key] = _json_node(
                    item, traversal, depth + 1, active
                )
            return normalized
        finally:
            active.remove(marker)
    if isinstance(value, (list, tuple, set, frozenset)):
        marker = id(value)
        if marker in active:
            raise RecorderIdentityError("Recorder commitment input cannot be cyclic")
        active.add(marker)
        try:
            items = [
                _json_node(item, traversal, depth + 1, active) for item in value
            ]
        finally:
            active.remove(marker)
        if isinstance(value, (set, frozenset)):
            return sorted(
                items,
                key=lambda item: _canonical_json_digest(
                    item,
                    key=None,
                    max_bytes=traversal.max_bytes,
                ),
            )
        return items
    if is_dataclass(value) and not isinstance(value, type):
        marker = id(value)
        if marker in active:
            raise RecorderIdentityError("Recorder commitment input cannot be cyclic")
        active.add(marker)
        try:
            normalized_fields: dict[str, Any] = {}
            for field in fields(value):
                traversal.text(field.name, identity=True)
                normalized_fields[field.name] = _json_node(
                    getattr(value, field.name), traversal, depth + 1, active
                )
            return normalized_fields
        finally:
            active.remove(marker)
    descriptor = f"{type(value).__module__}.{type(value).__qualname__}"
    traversal.text(descriptor, identity=True)
    return {"type": descriptor}


def _commitment_key(value: Any, traversal: _Traversal) -> str:
    if isinstance(value, str):
        traversal.text(value, identity=True)
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        traversal.integer(value, identity=True)
        return str(value)
    if isinstance(value, UUID):
        result = str(value)
        traversal.text(result, identity=True)
        return result
    if isinstance(value, Enum):
        result = f"<{type(value).__module__}.{type(value).__qualname__}.{value.name}>"
        traversal.text(result, identity=True)
        return result
    result = f"<{type(value).__module__}.{type(value).__qualname__}>"
    traversal.text(result, identity=True)
    return result


def _invalid(code: str, message: str) -> None:
    raise RecorderEnvelopeValidationError(code, message)


def _forbid_extra(scope: str, value: Any, allowed: set[str]) -> None:
    if not isinstance(value, dict):
        _invalid("invalid_object", f"{scope} must be a JSON object")
    if any(key not in allowed for key in value):
        _invalid("extra_field", f"{scope} contains unsupported fields")


def _required_mapping(name: str, value: Any, *, maximum: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        _invalid("missing_object", f"{name} must be a JSON object")
    if len(value) > maximum:
        _invalid("object_too_large", f"{name} cannot exceed {maximum} fields")
    return value


def _optional_text(name: str, value: Any, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _invalid("invalid_text", f"{name} must be a string")
    if not value.strip():
        _invalid("blank_text", f"{name} cannot be blank")
    if len(value) > maximum:
        _invalid("text_too_long", f"{name} cannot exceed {maximum} characters")
    return value


def _optional_datetime(value: Any) -> None:
    if value is None:
        return
    text = _optional_text("occurred_at", value, 128)
    assert text is not None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _invalid("invalid_datetime", "occurred_at must be an ISO 8601 datetime")


def _text_list(
    name: str,
    value: Any,
    *,
    maximum: int,
    text_maximum: int,
) -> None:
    if not isinstance(value, list):
        _invalid("invalid_list", f"{name} must be a JSON array")
    if len(value) > maximum:
        _invalid("list_too_large", f"{name} cannot exceed {maximum} items")
    for item in value:
        _optional_text(f"{name} item", item, text_maximum)


def _safe_envelope_identity(envelope: Any, field: str) -> str:
    if not isinstance(envelope, Mapping):
        return "invalid-envelope"
    try:
        value = envelope.get(field)
    except Exception:
        return "invalid-envelope"
    if isinstance(value, str) and value.strip() and len(value) <= 512:
        return value.strip()
    return "invalid-envelope"
