"""
Supermemory adapter for the regulated-eval harness.

Supermemory is a universal memory API: fast ingestion (`client.add`), hybrid
retrieval (`client.search.documents` with metadata AND/OR filters), document
management (`client.documents.list/delete`), and a consolidated user profile
(`client.profile`, static + dynamic facts). It is built for speed and breadth
of ingestion. What it does NOT provide is the compliance layer: search has no
as-of / valid-time parameter, writes are accumulate-and-rank (no supersession
decision on ingest), and there is no erasure certificate, lookahead guard, or
audit snapshot.

Capability map (each cell justified against the public API):

  stale_revision_suppression   PARTIAL  the dynamic user profile consolidates and
                                        revises profile facts as new evidence
                                        arrives — but document search itself is
                                        accumulate-and-rank: both revisions stay
                                        retrievable, with no keyed guarantee the
                                        stale one is excluded.
  point_in_time_reconstruction ABSENT   search.documents(q, filters) has no
                                        as-of / valid-time / timestamp filter.
  erasure_proof                PARTIAL  documents.delete removes the document,
                                        but there is no crypto-shred and no
                                        erasure certificate.
  lookahead_contamination      ABSENT   no event-time model; no backtest
                                        primitive.
  audit_state_reconstruction   ABSENT   no point-in-time snapshot of stored
                                        knowledge state.

Set SUPERMEMORY_API_KEY (and `pip install supermemory`) to run the live paths
below.
"""
from __future__ import annotations

import os

from . import CapabilityAbsent, PARTIAL, ABSENT

NAME = "Supermemory"

CAPABILITIES = {
    "stale_revision_suppression": PARTIAL,
    "point_in_time_reconstruction": ABSENT,
    "erasure_proof": PARTIAL,
    "lookahead_contamination_detection": ABSENT,
    "audit_state_reconstruction": ABSENT,
}


class SupermemoryAdapter:
    """Maps the harness interface onto the Supermemory SDK. Live when SUPERMEMORY_API_KEY is set."""

    def __init__(self) -> None:
        self._client = None
        if os.getenv("SUPERMEMORY_API_KEY"):
            try:
                from supermemory import Supermemory  # type: ignore

                self._client = Supermemory(api_key=os.environ["SUPERMEMORY_API_KEY"])
            except Exception:
                self._client = None

    # --- supported primitives (best-effort) -------------------------------
    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        if self._client is None:
            return
        self._client.add(content=content, container_tags=[agent], metadata=metadata or {})

    def recall(self, agent, query, *, k=5):
        if self._client is None:
            return {"memories": []}
        res = self._client.search.documents(q=query, container_tags=[agent])
        hits = getattr(res, "results", res) or []
        out = []
        for h in list(hits)[:k]:
            content = h.get("content", "") if isinstance(h, dict) else getattr(h, "content", "")
            out.append({"content": content})
        return {"memories": out}

    # --- absent primitives: no API exists --------------------------------
    def recall_at(self, agent, query, as_of, *, k=5):
        raise CapabilityAbsent("Supermemory search has no as-of / valid-time filter")

    def erase(self, subject_id, reason):
        raise CapabilityAbsent("Supermemory documents.delete removes rows but emits no erasure proof")

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("Supermemory has no event-time model / lookahead guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("Supermemory has no point-in-time audit snapshot")
