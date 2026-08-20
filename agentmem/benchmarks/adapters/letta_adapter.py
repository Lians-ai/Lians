"""
Letta adapter for the regulated-eval harness.

Letta (MemGPT lineage) is the agent-native memory leader: agents self-manage
in-context memory blocks (core memory) and an out-of-context archival store of
passages with hybrid semantic + full-text search (`client.agents.passages.*`:
insert, search, list, update, delete). It is excellent at agent-driven context
management. What it does NOT provide is the compliance layer: archival search
has no as-of / valid-time parameter, memory-block edits are silent LLM-driven
overwrites, and there is no erasure certificate, lookahead guard, or
point-in-time audit snapshot.

Capability map (each cell justified against the public API):

  stale_revision_suppression   PARTIAL  the agent can rewrite core-memory blocks
                                        (memory_replace / rethink) so a revision
                                        can displace an old value - but it is
                                        LLM-decided and archival passages simply
                                        accumulate; no keyed guarantee the stale
                                        revision is excluded from search.
  point_in_time_reconstruction ABSENT   passages.search(query, tags, page) has no
                                        as_of / valid-time filter; conversation
                                        history is a log, not as-of recall.
  erasure_proof                PARTIAL  passages.delete / agent delete remove the
                                        rows, but there is no crypto-shred and no
                                        erasure certificate.
  lookahead_contamination      ABSENT   no event-time model on passages; no
                                        backtest primitive.
  audit_state_reconstruction   ABSENT   no point-in-time snapshot of what the
                                        agent knew at T.

Set LETTA_API_KEY (and `pip install letta-client`) to run the live paths below.
"""
from __future__ import annotations

import os

from . import CapabilityAbsent, PASS, PARTIAL, ABSENT

NAME = "Letta"

CAPABILITIES = {
    "stale_revision_suppression": PARTIAL,
    "point_in_time_reconstruction": ABSENT,
    "erasure_proof": PARTIAL,
    "lookahead_contamination_detection": ABSENT,
    "audit_state_reconstruction": ABSENT,
}


class LettaAdapter:
    """Maps the harness interface onto the Letta SDK. Live when LETTA_API_KEY is set."""

    def __init__(self) -> None:
        self._client = None
        self._agents: dict[str, str] = {}
        if os.getenv("LETTA_API_KEY"):
            try:
                from letta_client import Letta  # type: ignore

                self._client = Letta(token=os.environ["LETTA_API_KEY"])
            except Exception:
                self._client = None

    def _agent_id(self, agent: str) -> str:
        # One Letta agent per harness agent name; passages attach to an agent.
        if agent not in self._agents:
            created = self._client.agents.create(name=f"regulated-eval-{agent}")
            self._agents[agent] = created.id
        return self._agents[agent]

    # --- supported primitives (best-effort) -------------------------------
    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        if self._client is None:
            return
        self._client.agents.passages.create(agent_id=self._agent_id(agent), text=content)

    def recall(self, agent, query, *, k=5):
        if self._client is None:
            return {"memories": []}
        res = self._client.agents.passages.search(agent_id=self._agent_id(agent), query=query)
        hits = getattr(res, "results", res) or []
        out = []
        for h in list(hits)[:k]:
            out.append({"content": getattr(h, "text", "") or getattr(h, "content", "")})
        return {"memories": out}

    # --- absent primitives: no API exists --------------------------------
    def recall_at(self, agent, query, as_of, *, k=5):
        raise CapabilityAbsent("Letta passages.search has no as-of / valid-time parameter")

    def erase(self, subject_id, reason):
        raise CapabilityAbsent("Letta passages.delete removes rows but emits no erasure proof")

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("Letta has no event-time model / lookahead guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("Letta has no point-in-time audit snapshot")
