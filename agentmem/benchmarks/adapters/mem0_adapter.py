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


def live_adapter():
    """
    Prefer executing mem0 OSS in its default documented configuration
    (`Memory()` — OpenAI LLM + embeddings, local vector store); that is the
    self-hosted deployment a regulated buyer would evaluate. Fall back to the
    mem0 Platform API when only MEM0_API_KEY is present.
    Returns (adapter_or_None, mode_description).
    """
    if os.getenv("OPENAI_API_KEY"):
        a = Mem0OSSAdapter()
        if a._client is not None:
            return a, "mem0 OSS, default config (OpenAI LLM + embeddings)"
    a = Mem0Adapter()
    if a._client is not None:
        return a, "mem0 Platform API"
    return None, None


class Mem0OSSAdapter:
    """
    mem0 OSS (`from mem0 import Memory`) in its default configuration.

    Fairness notes:
    - add() uses infer=True — mem0's advertised LLM fact-management pipeline,
      the mechanism its supersession partial-credit is based on.
    - mem0 2.x exposes `timestamp` / `reference_date` parameters, but its own
      docstring marks them "Platform-only temporal parameter. Not supported
      in OSS" — so the OSS as-of cell is a structural absence, not a harness
      limitation.
    """

    def __init__(self) -> None:
        self._client = None
        self._subjects: dict[str, str] = {}
        if not os.getenv("OPENAI_API_KEY"):
            return
        try:
            from mem0 import Memory  # type: ignore

            self._client = Memory()
        except Exception:
            self._client = None

    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        self._client.add(content, user_id=agent, metadata=metadata or {})
        if subject_id:
            self._subjects[subject_id] = agent

    def recall(self, agent, query, *, k=5):
        res = self._client.search(query, top_k=k, filters={"user_id": agent})
        hits = res.get("results", res) if isinstance(res, dict) else res
        return {"memories": [{"content": h.get("memory", "")} for h in (hits or [])]}

    def recall_at(self, agent, query, as_of, *, k=5):
        raise CapabilityAbsent(
            "mem0 OSS has no as-of recall: timestamp/reference_date are "
            "documented as 'Platform-only temporal parameter. Not supported in OSS'")

    def erase(self, subject_id, reason):
        # Real deletion (delete_all for the mapped user) — but no crypto-shred
        # and no certificate, so at best this scores "partial".
        agent = self._subjects.pop(subject_id, None)
        if agent is None:
            raise CapabilityAbsent("mem0 has no subject-level erasure concept")
        self._client.delete_all(user_id=agent)
        return {"deleted": True}   # no proof artifact keys

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("mem0 has no event-time / lookahead guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("mem0 has no point-in-time audit snapshot")


class Mem0Adapter:
    """Maps the harness interface onto the mem0 Platform SDK (MEM0_API_KEY)."""

    def __init__(self) -> None:
        self._client = None
        self._subjects: dict[str, str] = {}
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
        if subject_id:
            self._subjects[subject_id] = agent

    def recall(self, agent, query, *, k=5):
        if self._client is None:
            return {"memories": []}
        hits = self._client.search(query, user_id=agent, limit=k)
        return {"memories": [{"content": h.get("memory", "")} for h in hits]}

    # --- absent primitives: no API exists --------------------------------
    def recall_at(self, agent, query, as_of, *, k=5):
        raise CapabilityAbsent("mem0 has no as-of / valid-time recall primitive")

    def erase(self, subject_id, reason):
        # Real deletion where possible — no proof artifact, so "partial" at best.
        agent = self._subjects.pop(subject_id, None)
        if self._client is None or agent is None:
            raise CapabilityAbsent("mem0 has no subject-level erasure concept")
        self._client.delete_all(user_id=agent)
        return {"deleted": True}

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("mem0 has no event-time / lookahead guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("mem0 has no point-in-time audit snapshot")
