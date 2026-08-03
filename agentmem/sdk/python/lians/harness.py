"""
Lians agent memory harness — a drop-in memory loop for any agent framework.

The harness wraps the two operations every memory-augmented agent needs:

    1. **Recall-before** — fetch the current (non-stale) facts relevant to the
       turn and format them for injection into the model's context.
    2. **Remember-after** — persist what the agent learned or decided, with the
       compliance scoping (subject, source, event-time, importance) that
       regulated deployments require.

Unlike a raw vector store, the harness inherits Lians' bitemporal model:
superseded facts are excluded at the database layer, so the context you inject
is never contaminated by stale revisions. Every write lands in the tamper-evident
audit chain, and per-subject scoping keeps GDPR/HIPAA crypto-shred intact.

It is deliberately framework-agnostic. It works with any Lians client that
exposes the shared synchronous surface (``add``, ``recall``, ``recall_at``,
``add_from_messages``, ``snapshot``, ``backtest_check``, ``erase``) — that means
``LiansClient`` (hosted/self-hosted), ``LocalLiansClient`` (SQLite), and any
duck-typed stand-in used in tests.

Quick start::

    from datetime import datetime, timezone
    from lians import LocalLiansClient
    from lians.harness import LiansMemoryHarness

    mem = LocalLiansClient()
    harness = LiansMemoryHarness(mem, agent_id="research-desk")

    def my_llm(prompt: str) -> str:
        ...  # call any model

    # One call: recall context, run the model, persist the response.
    answer = harness.run_turn(
        "What is NVDA's current revenue guidance?",
        generate=lambda ctx, q: my_llm(f"{ctx}\n\nUser: {q}"),
    )

Manual control::

    context = harness.recall_context("NVDA revenue guidance")
    response = my_llm(context + "\n\nUser: ...")
    harness.remember(response, metadata={"ticker": "NVDA"})

Regulated scoping::

    harness = LiansMemoryHarness(
        mem,
        agent_id="care-team-3",
        subject_id="MRN-00042",        # ties every write to one data subject
        barrier_group="oncology",      # tags writes for the information barrier
        source="ehr-agent",
        domain="healthcare",
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol, Sequence, runtime_checkable

# ── Client protocol ───────────────────────────────────────────────────────────


@runtime_checkable
class MemoryClient(Protocol):
    """The subset of the Lians client surface the harness depends on."""

    def add(
        self,
        agent_id: str,
        content: str,
        event_time: datetime,
        source: Optional[str] = ...,
        subject_id: Optional[str] = ...,
        metadata: Optional[dict[str, Any]] = ...,
        importance: float = ...,
    ) -> dict: ...

    def recall(
        self,
        agent_id: str,
        query: str,
        k: int = ...,
        as_of: Optional[datetime] = ...,
        filters: Optional[dict[str, Any]] = ...,
    ) -> dict: ...


# ── Result containers ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecalledMemory:
    """A single memory returned by recall, normalized to plain attributes."""

    content: Optional[str]
    event_time: Optional[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    source: Optional[str] = None
    id: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RecalledMemory":
        return cls(
            content=raw.get("content"),
            event_time=raw.get("event_time"),
            metadata=raw.get("metadata") or {},
            importance=raw.get("importance", 0.5),
            source=raw.get("source"),
            id=str(raw["id"]) if raw.get("id") is not None else None,
        )


@dataclass(frozen=True)
class TurnResult:
    """Everything a single harnessed turn produced — useful for audit/logging."""

    query: str
    context: str
    recalled: list[RecalledMemory]
    response: Any
    remembered: Optional[dict] = None


# ── Harness ───────────────────────────────────────────────────────────────────


class LiansMemoryHarness:
    """
    Wraps a Lians client with the recall-before / remember-after agent loop.

    Parameters
    ----------
    client:
        Any Lians client exposing ``add`` and ``recall`` (``LiansClient``,
        ``AsyncLiansClient`` is *not* supported here — use the sync clients).
    agent_id:
        The memory namespace for this agent/session. Required.
    subject_id:
        Default data-subject identifier applied to every write (e.g. a patient
        MRN, a client matter ID, a counterparty). Ties writes to one per-subject
        encryption key so GDPR/HIPAA crypto-shred can erase exactly this subject.
    barrier_group:
        Information-barrier label tagged onto every write's metadata under
        ``_barrier``. DB-layer Row-Level-Security enforcement is provisioned per
        agent on the server; this tag makes the intended wall auditable and
        filterable from the client side.
    source:
        Default provenance label for writes (e.g. ``"trading-agent"``).
    domain:
        Optional vertical hint (``"finance"`` | ``"healthcare"`` | ``"legal"``)
        recorded on writes under metadata ``_domain`` for downstream adapters.
    recall_k:
        Default number of memories to retrieve per recall.
    default_importance:
        Importance score applied to writes when not overridden (0.0–1.0).
    min_recall_score / min_importance:
        Reserved filters applied client-side to drop low-value recalls.
    """

    def __init__(
        self,
        client: MemoryClient,
        *,
        agent_id: str,
        subject_id: Optional[str] = None,
        barrier_group: Optional[str] = None,
        source: Optional[str] = "agent",
        domain: Optional[str] = None,
        recall_k: int = 5,
        default_importance: float = 0.5,
    ) -> None:
        if not agent_id:
            raise ValueError("agent_id is required")
        if not (hasattr(client, "add") and hasattr(client, "recall")):
            raise TypeError(
                "client must expose `add` and `recall` (use LiansClient or "
                "LocalLiansClient — AsyncLiansClient is not supported by the harness)"
            )
        self.client = client
        self.agent_id = agent_id
        self.subject_id = subject_id
        self.barrier_group = barrier_group
        self.source = source
        self.domain = domain
        self.recall_k = recall_k
        self.default_importance = default_importance

    # ── Recall ────────────────────────────────────────────────────────────────

    def recall(
        self,
        query: str,
        *,
        k: Optional[int] = None,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
    ) -> list[RecalledMemory]:
        """
        Return the current (non-stale) memories relevant to ``query``.

        Pass ``as_of`` for point-in-time recall — the compliance query that
        answers "what did this agent know on date X?" without contamination
        from facts learned later.
        """
        result = self.client.recall(
            agent_id=self.agent_id,
            query=query,
            k=k if k is not None else self.recall_k,
            as_of=as_of,
            filters=filters or {},
        )
        memories = result.get("memories", []) if isinstance(result, dict) else []
        return [RecalledMemory.from_dict(m) for m in memories]

    def recall_context(
        self,
        query: str,
        *,
        k: Optional[int] = None,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
        header: str = "Relevant facts from memory (most recent, non-stale):",
        empty_message: str = "(no relevant facts in memory yet)",
    ) -> str:
        """
        Recall and render memories as a context block ready to inject into a prompt.

        The block is plain text — drop it into a system message, a RAG context
        slot, or straight into the user turn. Each line carries the event time
        and source so the model can reason about recency and provenance.
        """
        memories = self.recall(query, k=k, as_of=as_of, filters=filters)
        if not memories:
            return f"{header}\n{empty_message}"
        lines = [header]
        for m in memories:
            if not m.content:
                continue  # erased (crypto-shredded) — content unrecoverable
            stamp = _short_time(m.event_time)
            prov = f" [{m.source}]" if m.source else ""
            lines.append(f"- ({stamp}){prov} {m.content}")
        return "\n".join(lines)

    # ── Remember ──────────────────────────────────────────────────────────────

    def remember(
        self,
        content: str,
        *,
        event_time: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: Optional[float] = None,
        subject_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> dict:
        """
        Persist a single fact/decision the agent produced.

        Supersession, audit-chain append, and per-subject encryption all happen
        server-side. ``event_time`` defaults to now; set it to the business time
        the fact refers to when that differs (critical for point-in-time recall
        and backtest-contamination checks).
        """
        return self.client.add(
            agent_id=self.agent_id,
            content=content,
            event_time=event_time or _now(),
            source=source or self.source,
            subject_id=subject_id or self.subject_id,
            metadata=self._scoped_metadata(metadata),
            importance=importance if importance is not None else self.default_importance,
        )

    def remember_messages(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        event_time: Optional[datetime] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: Optional[float] = None,
        subject_id: Optional[str] = None,
        source: Optional[str] = None,
        roles: Optional[list[str]] = None,
    ) -> dict:
        """
        Extract and persist facts from an OpenAI/LangChain-style message list.

        Only writes messages whose role is in ``roles`` (default: assistant).
        Requires the client to expose ``add_from_messages`` (all sync Lians
        clients do); raises ``AttributeError`` otherwise.
        """
        if not hasattr(self.client, "add_from_messages"):
            raise AttributeError(
                "client does not support add_from_messages; use remember() per-fact"
            )
        return self.client.add_from_messages(  # type: ignore[attr-defined]
            agent_id=self.agent_id,
            messages=list(messages),
            event_time=event_time or _now(),
            source=source or self.source,
            subject_id=subject_id or self.subject_id,
            metadata=self._scoped_metadata(metadata),
            importance=importance if importance is not None else self.default_importance,
            roles=roles,
        )

    # ── Combined turn ─────────────────────────────────────────────────────────

    def run_turn(
        self,
        query: str,
        generate: Callable[[str, str], Any],
        *,
        k: Optional[int] = None,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
        remember_response: bool = True,
        response_metadata: Optional[dict[str, Any]] = None,
        response_importance: Optional[float] = None,
        event_time: Optional[datetime] = None,
    ) -> Any:
        """
        Run one full memory-augmented turn and return the model's response.

        Steps: recall context → call ``generate(context, query)`` → persist the
        response (unless ``remember_response=False``). Use :meth:`turn` instead
        when you need the full :class:`TurnResult` for audit/logging.

        ``generate`` receives ``(context, query)`` and returns the response. The
        response is stringified before it is remembered.
        """
        return self.turn(
            query,
            generate,
            k=k,
            as_of=as_of,
            filters=filters,
            remember_response=remember_response,
            response_metadata=response_metadata,
            response_importance=response_importance,
            event_time=event_time,
        ).response

    def turn(
        self,
        query: str,
        generate: Callable[[str, str], Any],
        *,
        k: Optional[int] = None,
        as_of: Optional[datetime] = None,
        filters: Optional[dict[str, Any]] = None,
        remember_response: bool = True,
        response_metadata: Optional[dict[str, Any]] = None,
        response_importance: Optional[float] = None,
        event_time: Optional[datetime] = None,
    ) -> TurnResult:
        """Run one turn and return a :class:`TurnResult` with full provenance."""
        recalled = self.recall(query, k=k, as_of=as_of, filters=filters)
        context = self._render(recalled)
        response = generate(context, query)
        remembered: Optional[dict] = None
        if remember_response:
            text = response if isinstance(response, str) else str(response)
            if text.strip():
                remembered = self.remember(
                    text,
                    metadata=response_metadata,
                    importance=response_importance,
                    event_time=event_time,
                )
        return TurnResult(
            query=query,
            context=context,
            recalled=recalled,
            response=response,
            remembered=remembered,
        )

    # ── Relationship graph ────────────────────────────────────────────────────

    def relate(
        self,
        src_entity: str,
        rel_type: str,
        dst_entity: str,
        *,
        event_time: Optional[datetime] = None,
        exclusive: bool = False,
        subject_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        normalize: Optional[bool] = None,
    ) -> dict:
        """
        Assert a relationship edge for this agent's graph.

        ``normalize`` defaults to True for the finance domain (so company/ISIN/
        CUSIP forms collapse to one node) and False otherwise.
        """
        self._require("relate")
        return self.client.relate(  # type: ignore[attr-defined]
            agent_id=self.agent_id,
            src_entity=src_entity,
            rel_type=rel_type,
            dst_entity=dst_entity,
            event_time=event_time or _now(),
            exclusive=exclusive,
            subject_id=subject_id or self.subject_id,
            source=self.source,
            metadata=self._scoped_metadata(metadata),
            normalize=self.domain == "finance" if normalize is None else normalize,
        )

    def neighbors(self, entity: str, **kwargs: Any) -> dict:
        """Entities connected to ``entity`` within N hops (see client.neighbors)."""
        self._require("neighbors")
        return self.client.neighbors(agent_id=self.agent_id, entity=entity, **kwargs)  # type: ignore[attr-defined]

    def path(self, src_entity: str, dst_entity: str, **kwargs: Any) -> dict:
        """Shortest connection between two entities (COI / related-party query)."""
        self._require("path")
        return self.client.path(  # type: ignore[attr-defined]
            agent_id=self.agent_id, src_entity=src_entity, dst_entity=dst_entity, **kwargs
        )

    def recall_near(
        self,
        query: str,
        near_entity: str,
        *,
        near_key: str = "ticker",
        k: Optional[int] = None,
        as_of: Optional[datetime] = None,
    ) -> list[RecalledMemory]:
        """
        Recall with graph-proximity reranking — facts about entities near
        ``near_entity`` in the relationship graph are boosted.
        """
        self._require("recall_near")
        result = self.client.recall_near(  # type: ignore[attr-defined]
            agent_id=self.agent_id,
            query=query,
            near_entity=near_entity,
            near_key=near_key,
            k=k if k is not None else self.recall_k,
            as_of=as_of,
        )
        memories = result.get("memories", []) if isinstance(result, dict) else []
        return [RecalledMemory.from_dict(m) for m in memories]

    # ── Compliance pass-throughs ──────────────────────────────────────────────

    def snapshot(self, as_of: datetime, **kwargs: Any) -> dict:
        """Full knowledge-state reconstruction at ``as_of`` (audit/regulator demo)."""
        self._require("snapshot")
        return self.client.snapshot(agent_id=self.agent_id, as_of=as_of, **kwargs)  # type: ignore[attr-defined]

    def backtest_check(self, simulation_as_of: datetime) -> dict:
        """Detect lookahead bias — facts the agent held that it couldn't have known."""
        self._require("backtest_check")
        return self.client.backtest_check(  # type: ignore[attr-defined]
            agent_id=self.agent_id, simulation_as_of=simulation_as_of
        )

    def erase(self, subject_id: Optional[str] = None, *, request_ref: str) -> dict:
        """GDPR/HIPAA crypto-shred a data subject (defaults to the harness subject)."""
        self._require("erase")
        sid = subject_id or self.subject_id
        if not sid:
            raise ValueError("no subject_id to erase (set one on the harness or pass it)")
        return self.client.erase(subject_id=sid, request_ref=request_ref)  # type: ignore[attr-defined]

    # ── Pre-compaction flush ──────────────────────────────────────────────────

    def flush_before_compaction(
        self,
        messages: Optional[Sequence[dict[str, Any]]] = None,
        *,
        facts: Optional[Sequence[str]] = None,
        extract: Optional[Callable[[str], Sequence[str]]] = None,
        roles: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        importance: Optional[float] = None,
        event_time: Optional[datetime] = None,
    ) -> dict[str, Any]:
        """
        Persist durable facts NOW, before the host framework compacts or
        summarizes the conversation out of existence.

        Long-running agents lose granular facts at the context cliff: the
        framework summarizes old turns, and whatever the summary drops is gone.
        Call this when the conversation nears its context limit (see
        :class:`CompactionGuard` for automatic tracking) so the durable facts
        cross into governed memory first. Every write is tagged
        ``_flush: "pre_compaction"``, so the audit chain shows *when* the agent
        externalized what it knew — the flush itself is evidence.

        Provide exactly one source of facts, checked in this order:

        ``facts``
            Pre-extracted durable facts — one write per string.
        ``extract`` + ``messages``
            ``extract(transcript_text)`` returns the facts to write. Pass an
            LLM-backed extractor here to reproduce OpenClaw's "silent agentic
            turn" with your own model.
        ``messages``
            Fallback: persists messages whose role is in ``roles`` (default:
            assistant) via :meth:`remember_messages`, or one :meth:`remember`
            per message when the client lacks ``add_from_messages``.

        Returns ``{"flushed": <n writes>, "mode": <facts|extract|messages>}``.
        """
        meta = dict(metadata or {})
        meta.setdefault("_flush", "pre_compaction")

        if facts is not None:
            written = 0
            for fact in facts:
                if fact and str(fact).strip():
                    self.remember(
                        str(fact), metadata=meta,
                        importance=importance, event_time=event_time,
                    )
                    written += 1
            return {"flushed": written, "mode": "facts"}

        if messages is None:
            raise ValueError("flush_before_compaction needs `facts` or `messages`")

        if extract is not None:
            transcript = "\n".join(
                f"{m.get('role', '?')}: {m.get('content', '')}" for m in messages
            )
            extracted = [f for f in (extract(transcript) or []) if f and str(f).strip()]
            for fact in extracted:
                self.remember(
                    str(fact), metadata=meta,
                    importance=importance, event_time=event_time,
                )
            return {"flushed": len(extracted), "mode": "extract"}

        wanted = roles or ["assistant"]
        if hasattr(self.client, "add_from_messages"):
            self.remember_messages(
                messages, metadata=meta, importance=importance,
                event_time=event_time, roles=wanted,
            )
            written = sum(1 for m in messages if m.get("role") in wanted and m.get("content"))
        else:
            written = 0
            for m in messages:
                if m.get("role") in wanted and m.get("content"):
                    self.remember(
                        str(m["content"]), metadata=meta,
                        importance=importance, event_time=event_time,
                    )
                    written += 1
        return {"flushed": written, "mode": "messages"}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _scoped_metadata(self, extra: Optional[dict[str, Any]]) -> dict[str, Any]:
        meta: dict[str, Any] = dict(extra or {})
        if self.barrier_group and "_barrier" not in meta:
            meta["_barrier"] = self.barrier_group
        if self.domain and "_domain" not in meta:
            meta["_domain"] = self.domain
        return meta

    def _render(self, memories: list[RecalledMemory]) -> str:
        header = "Relevant facts from memory (most recent, non-stale):"
        if not memories:
            return f"{header}\n(no relevant facts in memory yet)"
        lines = [header]
        for m in memories:
            if not m.content:
                continue
            stamp = _short_time(m.event_time)
            prov = f" [{m.source}]" if m.source else ""
            lines.append(f"- ({stamp}){prov} {m.content}")
        return "\n".join(lines)

    def _require(self, attr: str) -> None:
        if not hasattr(self.client, attr):
            raise AttributeError(
                f"client does not support `{attr}` — use a hosted/local Lians client"
            )


# ── Compaction guard ───────────────────────────────────────────────────────────


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) — matches the server's budgeting."""
    return max(1, len(text) // 4)


class CompactionGuard:
    """
    Track conversation size and flush durable facts before the context cliff.

    Wire it into any agent loop: call :meth:`observe` with each turn's text (or
    :meth:`observe_messages` with the running message list), and when the
    estimated usage crosses ``threshold`` × ``context_limit_tokens`` the guard
    fires :meth:`LiansMemoryHarness.flush_before_compaction` — once — then
    waits for :meth:`reset` (call it after the host framework actually
    compacts/summarizes).

    Example::

        guard = CompactionGuard(harness, context_limit_tokens=128_000)

        for turn in conversation:
            ...
            flushed = guard.observe_and_maybe_flush(messages)
            if flushed:
                log.info("pre-compaction flush wrote %s facts", flushed["flushed"])
    """

    def __init__(
        self,
        harness: LiansMemoryHarness,
        *,
        context_limit_tokens: int,
        threshold: float = 0.8,
        estimate: Callable[[str], int] = _estimate_tokens,
        extract: Optional[Callable[[str], Sequence[str]]] = None,
        roles: Optional[list[str]] = None,
    ) -> None:
        if context_limit_tokens <= 0:
            raise ValueError("context_limit_tokens must be positive")
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        self.harness = harness
        self.context_limit_tokens = context_limit_tokens
        self.threshold = threshold
        self.estimate = estimate
        self.extract = extract
        self.roles = roles
        self.used_tokens = 0
        self._flushed_this_window = False

    def observe(self, *texts: str) -> None:
        """Accumulate token usage for ad-hoc text (prompts, responses, tools)."""
        for t in texts:
            if t:
                self.used_tokens += self.estimate(str(t))

    def observe_messages(self, messages: Sequence[dict[str, Any]]) -> None:
        """Set usage from a full running message list (idempotent per call)."""
        self.used_tokens = sum(
            self.estimate(str(m.get("content", ""))) for m in messages if m.get("content")
        )

    def should_flush(self) -> bool:
        return (
            not self._flushed_this_window
            and self.used_tokens >= self.threshold * self.context_limit_tokens
        )

    def observe_and_maybe_flush(
        self,
        messages: Sequence[dict[str, Any]],
        **flush_kwargs: Any,
    ) -> Optional[dict[str, Any]]:
        """
        One-call loop hook: update usage from ``messages`` and flush if the
        window crossed the threshold. Returns the flush result, or None.
        """
        self.observe_messages(messages)
        if not self.should_flush():
            return None
        result = self.harness.flush_before_compaction(
            messages,
            extract=flush_kwargs.pop("extract", self.extract),
            roles=flush_kwargs.pop("roles", self.roles),
            **flush_kwargs,
        )
        self._flushed_this_window = True
        return result

    def reset(self) -> None:
        """Call after the host framework compacts — starts a fresh window."""
        self.used_tokens = 0
        self._flushed_this_window = False


# ── Helpers ────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _short_time(value: Optional[str]) -> str:
    if not value:
        return "undated"
    # event_time is serialized ISO-8601; keep the date (and time if present)
    return str(value).replace("T", " ")[:16]
