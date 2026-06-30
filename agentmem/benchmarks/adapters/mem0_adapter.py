"""
mem0 adapter for the regulated-eval harness.

mem0's public surface (mem0ai SDK / Platform): add, search, get_all, update,
delete, delete_all, history. It is a developer-memory API: great DX, LLM-driven
ADD/UPDATE/DELETE fact management, vector recall. It has **no bitemporal as-of
query, no lookahead/backtest guard, and no point-in-time audit snapshot.**

Capability map (each cell justified against the public API):

  stale_revision_suppression   PARTIAL  mem0's LLM may UPDATE/supersede a fact on
                                        add(), but it is content-similarity-based
                                        and non-deterministic — no keyed guarantee
                                        that the stale revision is excluded.
  point_in_time_reconstruction ABSENT   search() has no `as_of`/valid-time filter;
                                        history() is a change log, not as-of recall.
  erasure_proof                PARTIAL  delete()/delete_all() removes the row so it
                                        stops being retrieved, but there is no
                                        crypto-shred and no erasure certificate.
  lookahead_contamination      ABSENT   no event-time model; no backtest primitive.
  audit_state_reconstruction   ABSENT   no point-in-time snapshot of knowledge state.

Set MEM0_API_KEY (and `pip install mem0ai`) to run the live paths below.
"""
from __future__ import annotations

import os

from . import CapabilityAbsent, PASS, PARTIAL, ABSENT

NAME = "mem0"

CAPABILITIES = {
    "stale_revision_suppression": PARTIAL,
    "point_in_time_reconstruction": ABSENT,
    "erasure_proof": PARTIAL,
    "lookahead_contamination_detection": ABSENT,
    "audit_state_reconstruction": ABSENT,
}


class Mem0Adapter:
    """Maps the harness interface onto the mem0 SDK. Live when MEM0_API_KEY is set."""

    def __init__(self) -> None:
        self._client = None
        if os.getenv("MEM0_API_KEY"):
            try:
                from mem0 import MemoryClient  # type: ignore

                self._client = MemoryClient()
            except Exception:
                self._client = None

    # --- supported primitives (best-effort) -------------------------------
    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        if self._client is None:
            return
        self._client.add(content, user_id=agent, metadata=metadata or {})

    def recall(self, agent, query, *, k=5):
        if self._client is None:
            return {"memories": []}
        hits = self._client.search(query, user_id=agent, limit=k)
        return {"memories": [{"content": h.get("memory", "")} for h in hits]}

    # --- absent primitives: no API exists --------------------------------
    def recall_at(self, agent, query, as_of, *, k=5):
        raise CapabilityAbsent("mem0 has no as-of / valid-time recall")

    def erase(self, subject_id, reason):
        # delete exists, but there is no provable shred / certificate.
        raise CapabilityAbsent("mem0 delete() removes rows but emits no erasure proof")

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("mem0 has no event-time / lookahead guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("mem0 has no point-in-time audit snapshot")
