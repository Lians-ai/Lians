"""
Graphiti OSS live adapter - runs getzep/graphiti in its default documented
configuration (OpenAI LLM + embeddings + reranker) on an embedded Kuzu graph,
so the Zep/Graphiti column can be *executed*, not capability-assessed.

Requirements to go live (all from Graphiti's own quickstart):
    pip install graphiti-core kuzu
    export OPENAI_API_KEY=...

Fairness notes:
- This is Graphiti exactly as its README configures it - default clients,
  default search. We do not swap in weaker local models for the extraction
  step its invalidation logic depends on.
- Primitives Graphiti does not expose as turnkey APIs (as-of recall, erasure
  certificate, backtest guard, audit snapshot) raise CapabilityAbsent; the
  comparison layer then keeps the *documented* capability credit (e.g. the
  PARTIAL for temporal edge filtering) rather than letting a live run zero
  a cell for the same reason the static map already discounted it.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from . import CapabilityAbsent

NAME = "Zep / Graphiti (OSS live)"


class GraphitiOSSAdapter:
    """Maps the harness interface onto graphiti-core with an embedded Kuzu DB."""

    def __init__(self) -> None:
        self._client = None
        if not os.getenv("OPENAI_API_KEY"):
            return
        try:
            from graphiti_core import Graphiti
            from graphiti_core.driver.kuzu_driver import KuzuDriver

            self._loop = asyncio.new_event_loop()
            db_path = os.path.join(tempfile.mkdtemp(prefix="graphiti-eval-"), "kuzu.db")
            driver = KuzuDriver(db=db_path)
            self._graphiti = Graphiti(graph_driver=driver)
            self._loop.run_until_complete(self._graphiti.build_indices_and_constraints())
            # graphiti-core 0.29.2: build_indices_and_constraints() is a no-op
            # on Kuzu and setup_schema() creates tables only - but the default
            # hybrid search issues QUERY_FTS_INDEX, so search crashes out of
            # the box. Complete their documented setup with graphiti's OWN
            # index statements (graph_queries.get_fulltext_indices).
            from graphiti_core.driver.driver import GraphProvider
            from graphiti_core.graph_queries import get_fulltext_indices

            self._loop.run_until_complete(driver.execute_query("INSTALL FTS; LOAD FTS;"))
            for stmt in get_fulltext_indices(GraphProvider.KUZU):
                self._loop.run_until_complete(driver.execute_query(stmt))
            self._episodes_by_subject: dict[str, list] = {}
            self._client = self._graphiti
        except Exception:
            self._client = None

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    # --- live primitives ---------------------------------------------------
    def add(self, agent, content, event_time, *, metadata=None, subject_id=None):
        # group_id=None → provider default group. Passing a real group_id
        # crashes graphiti-core 0.29.2 on Kuzu: add_episode reads
        # driver._database, which KuzuDriver.__init__ never sets, then tries
        # to clone the driver per-group (FalkorDB semantics). One shared
        # group in a throwaway per-run database is equivalent for this eval;
        # the agent name is prefixed into the episode name for traceability.
        from graphiti_core.nodes import EpisodeType

        result = self._run(self._graphiti.add_episode(
            name=f"{agent}-{uuid.uuid4().hex[:8]}",
            episode_body=content,
            source=EpisodeType.text,
            source_description="regulated-eval",
            reference_time=event_time,
        ))
        if subject_id:
            self._episodes_by_subject.setdefault(subject_id, []).append(result.episode)

    def recall(self, agent, query, *, k=5):
        # Facts are reported exactly as Graphiti's default search returns
        # them - including invalidated edges (it does not filter those out).
        # The `invalidated` flag passes Graphiti's own invalid_at/expired_at
        # marking through so the harness can credit marked-but-returned stale
        # facts as partial rather than scoring the invalidation as absent.
        results = self._run(self._graphiti.search(query=query, num_results=k))
        return {"memories": [
            {
                "content": getattr(e, "fact", "") or "",
                "invalidated": bool(getattr(e, "invalid_at", None)
                                    or getattr(e, "expired_at", None)),
            }
            for e in results
        ]}

    def erase(self, subject_id, reason):
        # remove_episode is real deletion of the episode and its derived
        # nodes/edges - but there is no crypto-shred and no certificate, so
        # at best this scores "partial" (behavioral deletion, no proof).
        episodes = self._episodes_by_subject.pop(subject_id, [])
        if not episodes:
            raise CapabilityAbsent(
                "Graphiti has no subject-level erasure; episode-level "
                "remove_episode only, and nothing was tracked for this subject")
        for ep in episodes:
            self._run(self._graphiti.remove_episode(ep.uuid))
        return {"deleted_episodes": len(episodes)}   # no proof artifact keys

    # --- no turnkey primitive ----------------------------------------------
    def recall_at(self, agent, query, as_of, *, k=5):
        raise CapabilityAbsent(
            "Graphiti stores bitemporal edges but exposes no as-of recall "
            "primitive; reconstructing state at T means filtering edges manually")

    def backtest_check(self, agent, simulation_date):
        raise CapabilityAbsent("Graphiti has no lookahead / backtest guard")

    def snapshot(self, agent, as_of):
        raise CapabilityAbsent("Graphiti has no point-in-time audit snapshot API")
