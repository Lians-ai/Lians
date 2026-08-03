"""
Relationship-graph layer tests — bitemporal edges, traversal, point-in-time,
and graph-proximity reranking. Exercised against the real LocalLiansClient.
"""
from datetime import datetime, timezone

from lians import LiansMemoryHarness, LocalLiansClient

AG = "graph-agent"


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


class TestRelateAndNeighbors:
    def test_relate_creates_edge(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "FundA", "owns", "IssuerX", event_time=_dt(2026, 1, 1))
            res = mem.neighbors(AG, "FundA", depth=1)
            assert "IssuerX" in {n["entity"] for n in res["neighbors"]}

    def test_relate_is_idempotent(self):
        with LocalLiansClient() as mem:
            e1 = mem.relate(AG, "A", "knows", "B", event_time=_dt(2026, 1, 1))
            e2 = mem.relate(AG, "A", "knows", "B", event_time=_dt(2026, 2, 1))
            assert e1["id"] == e2["id"]  # same live edge returned
            assert len(mem.neighbors(AG, "A")["direct_edges"]) == 1

    def test_undirected_neighbors_both_ways(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "controls", "B", event_time=_dt(2026, 1, 1))
            res = mem.neighbors(AG, "B", direction="any")
            assert "A" in {n["entity"] for n in res["neighbors"]}

    def test_multi_hop(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "r", "B", event_time=_dt(2026, 1, 1))
            mem.relate(AG, "B", "r", "C", event_time=_dt(2026, 1, 1))
            d1 = {n["entity"]: n["depth"] for n in mem.neighbors(AG, "A", depth=1)["neighbors"]}
            d2 = {n["entity"]: n["depth"] for n in mem.neighbors(AG, "A", depth=2)["neighbors"]}
            assert "C" not in d1
            assert d2["C"] == 2


class TestExclusiveAndUnrelate:
    def test_exclusive_supersedes_prior(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "Alice", "works_at", "Acme", event_time=_dt(2025, 1, 1), exclusive=True)
            mem.relate(AG, "Alice", "works_at", "Globex", event_time=_dt(2026, 1, 1), exclusive=True)
            cur = {n["entity"] for n in mem.neighbors(AG, "Alice")["neighbors"]}
            assert "Globex" in cur
            assert "Acme" not in cur

    def test_unrelate_invalidates(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "r", "B", event_time=_dt(2026, 1, 1))
            assert mem.unrelate(AG, "A", "r", "B")["invalidated"] == 1
            assert mem.neighbors(AG, "A")["neighbors"] == []

    def test_unrelate_missing_is_zero(self):
        with LocalLiansClient() as mem:
            assert mem.unrelate(AG, "A", "r", "B")["invalidated"] == 0


class TestPath:
    def test_path_finds_connection(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "Attorney", "represented", "ClientX", event_time=_dt(2026, 1, 1))
            mem.relate(AG, "ClientX", "adverse_to", "PartyY", event_time=_dt(2026, 1, 1))
            res = mem.path(AG, "Attorney", "PartyY", max_depth=4)
            assert res["connected"] is True
            assert res["hops"] == 2
            assert len(res["path"]) == 2

    def test_path_unconnected(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "r", "B", event_time=_dt(2026, 1, 1))
            res = mem.path(AG, "A", "Z")
            assert res["connected"] is False
            assert res["path"] == []

    def test_path_respects_max_depth(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "r", "B", event_time=_dt(2026, 1, 1))
            mem.relate(AG, "B", "r", "C", event_time=_dt(2026, 1, 1))
            mem.relate(AG, "C", "r", "D", event_time=_dt(2026, 1, 1))
            assert mem.path(AG, "A", "D", max_depth=2)["connected"] is False
            assert mem.path(AG, "A", "D", max_depth=3)["connected"] is True


class TestPointInTime:
    def test_as_of_sees_historical_edge(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "works_at", "Acme", event_time=_dt(2025, 1, 1), exclusive=True)
            mem.relate(AG, "A", "works_at", "Globex", event_time=_dt(2026, 1, 1), exclusive=True)
            now = {n["entity"] for n in mem.neighbors(AG, "A")["neighbors"]}
            assert now == {"Globex"}
            past = {n["entity"] for n in mem.neighbors(AG, "A", as_of=_dt(2025, 6, 1))["neighbors"]}
            assert "Acme" in past

    def test_path_as_of(self):
        with LocalLiansClient() as mem:
            mem.relate(AG, "A", "r", "B", event_time=_dt(2025, 1, 1))
            mem.unrelate(AG, "A", "r", "B", event_time=_dt(2025, 12, 1))
            assert mem.path(AG, "A", "B")["connected"] is False
            assert mem.path(AG, "A", "B", as_of=_dt(2025, 6, 1))["connected"] is True


class TestProximityRerank:
    def test_recall_near_boosts_connected_entity(self):
        with LocalLiansClient() as mem:
            # Identical content, different tickers → equal semantic score, both live.
            mem.add(AG, "quarterly earnings update", _dt(2026, 1, 1), metadata={"ticker": "AAA", "metric": "eps"})
            mem.add(AG, "quarterly earnings update", _dt(2026, 1, 1), metadata={"ticker": "BBB", "metric": "eps"})
            mem.relate(AG, "ANCHOR", "covers", "AAA", event_time=_dt(2026, 1, 1))

            res = mem.recall_near(AG, "earnings", near_entity="ANCHOR", near_key="ticker", k=5)
            tickers = [m["metadata"].get("ticker") for m in res["memories"]]
            assert tickers[0] == "AAA"  # proximity broke the tie in AAA's favor

    def test_plain_recall_unaffected(self):
        with LocalLiansClient() as mem:
            mem.add(AG, "AAPL guidance raised", _dt(2026, 1, 1), metadata={"ticker": "AAPL", "metric": "g"})
            out = mem.recall(AG, "AAPL guidance")
            assert any("AAPL" in m["content"] for m in out["memories"])


class TestHarnessGraph:
    def test_harness_relate_and_path(self):
        with LocalLiansClient() as mem:
            h = LiansMemoryHarness(mem, agent_id="local", domain="legal")
            h.relate("Attorney", "prior_rep", "ClientX")
            h.relate("ClientX", "adverse_to", "PartyY")
            res = h.path("Attorney", "PartyY")
            assert res["connected"] is True
