"""Universal Recorder callbacks for LangChain and LangGraph.

LangGraph propagates LangChain callbacks through compiled graph invocations, so
the same public ``AsyncCallbackHandler`` covers both runtimes.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace
from functools import wraps
from typing import Any
from uuid import UUID

from ._recorder_adapter import adapter_event, public_name
from .recorder_sink import (
    AsyncRecorderSink,
    RecorderAttribution,
    RecorderBufferFull,
    RecorderSinkError,
    recorder_content_hash,
)


def build_langchain_recorder_handler(
    sink: AsyncRecorderSink,
    *,
    attribution: RecorderAttribution | None = None,
    session_metadata_key: str | None = "thread_id",
    propagate_sink_errors: bool = False,
    max_active_runs: int = 10_000,
) -> Any:
    """Return an async callback handler for LangChain and LangGraph runs.

    Supported public callbacks cover chain, chat-model/LLM, tool, retriever,
    agent, and custom-event boundaries.  Token/chunk callbacks are intentionally
    omitted to avoid unbounded event volume. Inputs and outputs use the sink's
    SHA-256 or HMAC-SHA-256 commitment policy under ``hash_only`` mode and are
    never queued or transmitted as raw content.
    """

    try:
        from langchain_core.callbacks import (  # type: ignore[import-not-found]
            AsyncCallbackHandler,
        )
    except ImportError as exc:
        raise ImportError(
            "langchain-core is required for native LangChain/LangGraph Recorder "
            "callbacks. Install with: pip install 'lians-sdk[langchain]'"
        ) from exc

    identity = attribution or RecorderAttribution()
    if identity.capture_mode == "full":
        raise ValueError(
            "native LangChain callbacks do not transport raw content; "
            "use hash_only or metadata_only"
        )
    if (
        not isinstance(max_active_runs, int)
        or isinstance(max_active_runs, bool)
        or max_active_runs < 1
    ):
        raise ValueError("max_active_runs must be a positive integer")

    def guarded(method: Any) -> Any:
        @wraps(method)
        async def callback(*args: Any, **kwargs: Any) -> None:
            try:
                await method(*args, **kwargs)
            except RecorderSinkError:
                # _submit already recorded an event-specific gap.
                if propagate_sink_errors:
                    raise
            except Exception as exc:
                sink.disclose_gap(
                    "langchain_callback_conversion_failed",
                    detail=type(exc).__name__,
                )
                if propagate_sink_errors:
                    raise

        return callback

    class LiansLangChainRecorderHandler(AsyncCallbackHandler):
        """Hash-only callback handler using LangChain's public async surface."""

        def __init__(self) -> None:
            super().__init__()
            self.raise_error = propagate_sink_errors
            self._state_lock = threading.RLock()
            self._roots: OrderedDict[str, str] = OrderedDict()
            self._attributions: OrderedDict[str, RecorderAttribution] = OrderedDict()
            self._sequences: OrderedDict[str, int] = OrderedDict()

        @property
        def active_run_count(self) -> int:
            """Number of retained callback run records (bounded)."""

            with self._state_lock:
                return len(self._roots)

        @guarded
        async def on_chain_start(
            self,
            serialized: dict[str, Any],
            inputs: dict[str, Any],
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            await self._start(
                "chain",
                serialized,
                inputs,
                run_id,
                parent_run_id,
                metadata,
            )

        @guarded
        async def on_chain_end(
            self,
            outputs: dict[str, Any],
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("chain", outputs, run_id, parent_run_id)

        @guarded
        async def on_chain_error(
            self,
            error: BaseException,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("chain", error, run_id, parent_run_id, failed=True)

        @guarded
        async def on_chat_model_start(
            self,
            serialized: dict[str, Any],
            messages: list[list[Any]],
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            await self._start(
                "llm",
                serialized,
                messages,
                run_id,
                parent_run_id,
                metadata,
            )

        @guarded
        async def on_llm_start(
            self,
            serialized: dict[str, Any],
            prompts: list[str],
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            await self._start(
                "llm",
                serialized,
                prompts,
                run_id,
                parent_run_id,
                metadata,
            )

        @guarded
        async def on_llm_end(
            self,
            response: Any,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("llm", response, run_id, parent_run_id)

        @guarded
        async def on_llm_error(
            self,
            error: BaseException,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("llm", error, run_id, parent_run_id, failed=True)

        @guarded
        async def on_tool_start(
            self,
            serialized: dict[str, Any],
            input_str: str,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            inputs: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            await self._start(
                "tool",
                serialized,
                inputs if inputs is not None else input_str,
                run_id,
                parent_run_id,
                metadata,
                tool=True,
            )

        @guarded
        async def on_tool_end(
            self,
            output: Any,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("tool", output, run_id, parent_run_id, tool=True)

        @guarded
        async def on_tool_error(
            self,
            error: BaseException,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("tool", error, run_id, parent_run_id, failed=True, tool=True)

        @guarded
        async def on_retriever_start(
            self,
            serialized: dict[str, Any],
            query: str,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            await self._start(
                "retriever",
                serialized,
                query,
                run_id,
                parent_run_id,
                metadata,
            )

        @guarded
        async def on_retriever_end(
            self,
            documents: Any,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("retriever", documents, run_id, parent_run_id)

        @guarded
        async def on_retriever_error(
            self,
            error: BaseException,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._end("retriever", error, run_id, parent_run_id, failed=True)

        @guarded
        async def on_agent_action(
            self,
            action: Any,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._point("agent_action", action, run_id, parent_run_id)

        @guarded
        async def on_agent_finish(
            self,
            finish: Any,
            *,
            run_id: Any,
            parent_run_id: Any | None = None,
            **kwargs: Any,
        ) -> None:
            await self._point("agent_finish", finish, run_id, parent_run_id)

        @guarded
        async def on_custom_event(
            self,
            name: str,
            data: Any,
            *,
            run_id: Any,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> None:
            await self._point(
                "custom_event", data, run_id, None, name=name, metadata=metadata
            )

        async def _start(
            self,
            kind: str,
            serialized: Any,
            observed: Any,
            run_id: Any,
            parent_run_id: Any | None,
            metadata: Mapping[str, Any] | None,
            *,
            tool: bool = False,
        ) -> None:
            name = public_name(serialized, kind)
            run, parent, root, scoped = self._begin(
                run_id, parent_run_id, metadata
            )
            await self._submit(
                adapter_event(
                    framework="langchain",
                    kind=kind,
                    phase="started",
                    source_identity=(run, kind, "started"),
                    run_id=root,
                    trace_id=_trace_component(root),
                    span_id=_trace_component(run),
                    parent_span_id=_trace_component(parent),
                    tool_call_id=run if tool else None,
                    attribution=scoped,
                    name=name,
                    model_id=_model_name(metadata),
                    status="running",
                    observed_input=observed,
                    metadata={"component_name": name},
                    content_hasher=sink.content_hash,
                    commitment_scheme=sink.commitment_scheme,
                )
            )

        async def _end(
            self,
            kind: str,
            observed: Any,
            run_id: Any,
            parent_run_id: Any | None,
            *,
            failed: bool = False,
            tool: bool = False,
        ) -> None:
            run, parent, root, scoped = self._resolve(run_id, parent_run_id)
            try:
                await self._submit(
                    adapter_event(
                        framework="langchain",
                        kind=kind,
                        phase="failed" if failed else "completed",
                        source_identity=(run, kind, "failed" if failed else "completed"),
                        run_id=root,
                        trace_id=_trace_component(root),
                        span_id=_trace_component(run),
                        parent_span_id=_trace_component(parent),
                        tool_call_id=run if tool else None,
                        attribution=scoped,
                        name=kind,
                        status="error" if failed else "completed",
                        observed_output=observed,
                        metadata={
                            "error_type": type(observed).__name__ if failed else None,
                        },
                        content_hasher=sink.content_hash,
                        commitment_scheme=sink.commitment_scheme,
                    )
                )
            finally:
                with self._state_lock:
                    self._roots.pop(run, None)
                    self._attributions.pop(run, None)
                    self._sequences.pop(run, None)

        async def _point(
            self,
            kind: str,
            observed: Any,
            run_id: Any,
            parent_run_id: Any | None,
            *,
            name: str | None = None,
            metadata: Mapping[str, Any] | None = None,
        ) -> None:
            run, parent, root, scoped, sequence = self._point_state(
                run_id, parent_run_id, metadata
            )
            await self._submit(
                adapter_event(
                    framework="langchain",
                    kind=kind,
                    phase="event",
                    source_identity=(run, kind, name or "event", sequence),
                    run_id=root,
                    trace_id=_trace_component(root),
                    span_id=_trace_component(run),
                    parent_span_id=_trace_component(parent),
                    attribution=scoped,
                    name=name or kind,
                    status="observed",
                    observed_output=observed,
                    content_hasher=sink.content_hash,
                    commitment_scheme=sink.commitment_scheme,
                )
            )

        async def _submit(self, event: Any) -> None:
            try:
                await sink.submit(event)
            except RecorderBufferFull:
                # The sink already emitted the event-specific backpressure gap.
                if propagate_sink_errors:
                    raise
            except RecorderSinkError as exc:
                sink.disclose_gap(
                    "langchain_callback_submission_failed",
                    event_id=event.get("event_id") or "unknown",
                    idempotency_key=event.get("idempotency_key") or "unknown",
                    detail=type(exc).__name__,
                )
                if propagate_sink_errors:
                    raise

        def _begin(
            self,
            run_id: Any,
            parent_run_id: Any | None,
            metadata: Mapping[str, Any] | None,
        ) -> tuple[str, str | None, str, RecorderAttribution]:
            run = _run_identifier(run_id)
            parent = (
                _run_identifier(parent_run_id) if parent_run_id is not None else None
            )
            scoped = self._identity(metadata)
            evicted = False
            with self._state_lock:
                evicted = self._make_room_locked(run)
                root = self._roots.get(parent, parent or run)
                parent_identity = self._attributions.get(parent) if parent else None
                if (
                    scoped.session_id is None
                    and parent_identity is not None
                    and parent_identity.session_id is not None
                ):
                    scoped = replace(scoped, session_id=parent_identity.session_id)
                self._roots[run] = root
                self._roots.move_to_end(run)
                self._attributions[run] = scoped
                self._attributions.move_to_end(run)
            if evicted:
                self._disclose_state_eviction()
            return run, parent, root, scoped

        def _resolve(
            self, run_id: Any, parent_run_id: Any | None
        ) -> tuple[str, str | None, str, RecorderAttribution]:
            run = _run_identifier(run_id)
            parent = (
                _run_identifier(parent_run_id) if parent_run_id is not None else None
            )
            with self._state_lock:
                root = self._roots.get(run) or self._roots.get(parent, parent or run)
                scoped = self._attributions.get(run, identity)
                if run in self._roots:
                    self._roots.move_to_end(run)
            return run, parent, root, scoped

        def _point_state(
            self,
            run_id: Any,
            parent_run_id: Any | None,
            metadata: Mapping[str, Any] | None,
        ) -> tuple[str, str | None, str, RecorderAttribution, int]:
            run = _run_identifier(run_id)
            parent = (
                _run_identifier(parent_run_id) if parent_run_id is not None else None
            )
            evicted = False
            with self._state_lock:
                evicted = self._make_room_locked(run)
                root = self._roots.get(run) or self._roots.get(parent, parent or run)
                self._roots[run] = root
                self._roots.move_to_end(run)
                scoped = self._attributions.get(run, self._identity(metadata))
                sequence = self._sequences.get(run, 0) + 1
                self._sequences[run] = sequence
                self._sequences.move_to_end(run)
            if evicted:
                self._disclose_state_eviction()
            return run, parent, root, scoped, sequence

        def _make_room_locked(self, run: str) -> bool:
            if run in self._roots or len(self._roots) < max_active_runs:
                return False
            evicted, _ = self._roots.popitem(last=False)
            self._attributions.pop(evicted, None)
            self._sequences.pop(evicted, None)
            return True

        @staticmethod
        def _disclose_state_eviction() -> None:
            sink.disclose_gap(
                "langchain_state_evicted",
                detail="active run tracking reached its configured bound",
            )

        @staticmethod
        def _identity(metadata: Mapping[str, Any] | None) -> RecorderAttribution:
            if identity.session_id is not None or not session_metadata_key or not metadata:
                return identity
            session = metadata.get(session_metadata_key)
            resolved = _optional_identifier(session)
            return replace(identity, session_id=resolved) if resolved is not None else identity

    return LiansLangChainRecorderHandler()


def _model_name(metadata: Mapping[str, Any] | None) -> str | None:
    if not metadata:
        return None
    value = metadata.get("ls_model_name")
    if not isinstance(value, str):
        return None
    text = value[:512].strip()
    return text or None


def _trace_component(value: str | None) -> str | None:
    if value is None or len(value) <= 64:
        return value
    return recorder_content_hash(["langchain-trace-component", value])


def _run_identifier(value: Any) -> str:
    resolved = _optional_identifier(value)
    if resolved is None:
        raise ValueError("LangChain callback run identifiers must be non-empty strings or UUIDs")
    return resolved


def _optional_identifier(value: Any) -> str | None:
    if isinstance(value, UUID):
        text = str(value)
    elif isinstance(value, str):
        if len(value) > 4_096:
            return None
        text = value.strip()
    elif isinstance(value, int) and not isinstance(value, bool):
        text = str(value)
    else:
        return None
    if not text:
        return None
    if len(text) <= 512:
        return text
    return "lians:langchain-id:v1:" + recorder_content_hash(
        ["langchain-id", text]
    )


__all__ = ["build_langchain_recorder_handler"]
