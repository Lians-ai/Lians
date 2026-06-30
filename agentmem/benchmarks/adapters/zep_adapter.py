"""
Zep / Graphiti adapter for the regulated-eval harness.

Zep is the temporal-knowledge-graph leader: Graphiti extracts entities/edges and
stamps them with bitemporal validity (`valid_at` / `invalid_at`, plus ingestion
time). It is the strongest competitor on time-aware recall, and it is credited as
such below. What it does NOT provide is the *compliance* layer: provable erasure
with a certificate, a lookahead/backtest guard, or a first-class audit snapshot
API. Its temporal queries can approximate as-of recall but require manual edge
filtering rather than an `as_of` recall primitive.

Capability map (each cell justified against the public API):

  stale_revision_suppression   PARTIAL  Graphiti invalidates contradicted edges
                                        (sets invalid_at); search prefers valid
                                        edges. Strong, but LLM-extraction-dependent,
                                        not a deterministic keyed supersession.
  point_in_time_reconstruction PARTIAL  Bitemporal edges support as-of filtering —
                                        Zep's headline strength — but the SDK exposes
                                        it as temporal edge filtering, not a single
                                        `recall_at` call.
  erasure_proof                PARTIAL  delete removes nodes/edges, but there is no
                                        crypto-shred and no erasure certificate.
  lookahead_contamination      ABSENT   bitemporal model could support it, but there
                                        is no built-in lookahead/backtest flagging.
  audit_state_reconstruction   PARTIAL  valid_at filtering can reconstruct graph
                                        state at T; there is no first-class snapshot
                                        / audit-state API or count guarantee.

Set ZEP_API_KEY (and `pip install zep-cloud`) to run the live paths below.
"""
from __future__ import annotations

import os

from . import CapabilityAbsent, PASS, PARTIAL, ABSENT

NAME = "Zep / Graphiti"

CAPABILITIES = {
    "stale_revision_suppression": PARTIAL,
    "point_in_time_reconstruction": PARTIAL,
    "erasure_proof": PARTIAL,
    "lookahead_contamination_detection": ABSENT,
    "audit_state_reconstruction": PARTIAL,
}


class ZepAdapter:
    """Maps the harness interface onto the Zep SDK. Live when ZEP_API_KEY is set."""

    def __init__(self) -> None:
        self._client = None
        if os.getenv("ZEP_API_KEY"):
            try:
                from zep_cloud.client import Zep  # type: ignore

                self._client = Zep(api_key=os.environ["ZEP_API_KEY"])
            except Exception:
                self._client = None

    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        if self._client is None:
            return
        self._client.graph.add(user_id=agent, type="text", data=content)

    def recall(self, agent, query, *, k=5):
        if self._client is None:
            return {"memories": []}
        res = self._client.graph.search(user_id=agent, query=query, limit=k)
        edges = getattr(res, "edges", []) or []
        return {"memories": [{"content": getattr(e, "fact", "")} for e in edges]}

    def recall_at(self, agent, query, as_of, *, k=5):
        # Temporal filtering is possible but not a single SDK primitive; the harness
        # requires an as-of recall call. Treated as best-effort/manual → not satisfied
        # as a turnkey invariant.
        raise CapabilityAbsent("Zep supports temporal edges but exposes no as-of recall primitive")

    def erase(self, subject_id, reason):
        raise CapabilityAbsent("Zep delete removes data but emits no erasure proof / certificate")

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("Zep has no lookahead / backtest guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("Zep has no first-class point-in-time audit snapshot API")
