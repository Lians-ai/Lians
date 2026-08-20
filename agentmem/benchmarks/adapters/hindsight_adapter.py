"""
Hindsight (vectorize-io) adapter for the regulated-eval harness.

Hindsight is "agent memory that learns": retain / recall / reflect over a
per-agent memory bank. retain() accepts an event timestamp; recall() runs four
retrieval strategies in parallel (semantic, BM25, entity-graph, temporal
time-range filtering); reflect() revises observations and mental models, with
history preserved ("updated - not overwritten"). It is the strongest of the
dev-memory lane on temporal awareness, and is credited as such. What it does
NOT provide is the compliance layer: no as-of knowledge-state recall (temporal
filtering scopes *event* time ranges, not what-was-believed-at-T), **no delete
or forget API at all**, no erasure certificate, no lookahead guard, and no
audit snapshot.

Capability map (each cell justified against the public API):

  stale_revision_suppression   PARTIAL  reflect() revises observations when new
                                        evidence contradicts them and recall
                                        prefers current beliefs - but it is
                                        LLM-reflection-driven, not a keyed
                                        deterministic supersession; raw retained
                                        memories still accumulate.
  point_in_time_reconstruction PARTIAL  retain() stamps event timestamps and the
                                        temporal strategy answers time-range
                                        queries ("what happened in June") - but
                                        there is no as-of primitive returning
                                        the knowledge state as believed at T.
  erasure_proof                ABSENT   no delete / forget / erase API exists on
                                        the public surface; a bank cannot prove
                                        content unrecoverable.
  lookahead_contamination      ABSENT   no backtest / lookahead primitive.
  audit_state_reconstruction   ABSENT   history is preserved internally but no
                                        snapshot or audit-state API exposes it.

Set HINDSIGHT_API_URL (and `pip install hindsight-client`) to run the live
paths below against a local or hosted Hindsight service.
"""
from __future__ import annotations

import os

from . import CapabilityAbsent, PASS, PARTIAL, ABSENT

NAME = "Hindsight"

CAPABILITIES = {
    "stale_revision_suppression": PARTIAL,
    "point_in_time_reconstruction": PARTIAL,
    "erasure_proof": ABSENT,
    "lookahead_contamination_detection": ABSENT,
    "audit_state_reconstruction": ABSENT,
}


class HindsightAdapter:
    """Maps the harness interface onto the Hindsight SDK. Live when HINDSIGHT_API_URL is set."""

    def __init__(self) -> None:
        self._client = None
        if os.getenv("HINDSIGHT_API_URL"):
            try:
                from hindsight_client import HindsightClient  # type: ignore

                self._client = HindsightClient(base_url=os.environ["HINDSIGHT_API_URL"])
            except Exception:
                self._client = None

    # --- supported primitives (best-effort) -------------------------------
    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        if self._client is None:
            return
        self._client.retain(bank_id=agent, content=content,
                            timestamp=event_time.isoformat())

    def recall(self, agent, query, *, k=5):
        if self._client is None:
            return {"memories": []}
        hits = self._client.recall(bank_id=agent, query=query, limit=k)
        items = getattr(hits, "results", hits) or []
        out = []
        for h in list(items)[:k]:
            content = h.get("content", "") if isinstance(h, dict) else getattr(h, "content", "")
            out.append({"content": content})
        return {"memories": out}

    def recall_at(self, agent, query, as_of, *, k=5):
        # Temporal retrieval filters event-time ranges; there is no as-of
        # knowledge-state recall ("what was believed on date X") primitive.
        raise CapabilityAbsent("Hindsight temporal recall filters time ranges, not as-of knowledge state")

    # --- absent primitives: no API exists --------------------------------
    def erase(self, subject_id, reason):
        raise CapabilityAbsent("Hindsight exposes no delete / forget / erase API")

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("Hindsight has no lookahead / backtest guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("Hindsight has no point-in-time audit snapshot API")
