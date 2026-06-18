"""
Supersession classification accuracy benchmark.

Evaluates Stage 1+2 of the supersession engine against a labeled dataset
of 30 memory pairs covering the full taxonomy:

  SUPERSEDES           — same entity+attribute, newer event, different value
  CONFIRMS             — same entity+attribute, same value (duplicate source)
  ADDS                 — related topic or different attribute
  CONTRADICTS_SAME_TIME — conflicting values at the same event time

Metrics reported: per-class Precision, Recall, F1; overall Accuracy.
Target: Accuracy >= 0.90, F1(SUPERSEDES) >= 0.90 — the class that matters
most for preventing stale data from reaching the agent.

Comparison notes:
  mem0  — no structured supersession; relies on the LLM to avoid hallucination
           from stale memories.  That's prompt engineering, not memory hygiene.
  Zep   — extracts facts with an LLM pass and deduplicates by entity handle;
           lacks temporal ordering and the CONTRADICTS_SAME_TIME distinction.
  AgentMem — deterministic Stage 1+2 with no LLM call; Stage 3 is additive.
"""
from __future__ import annotations
import pytest
from datetime import datetime, timezone, timedelta

from src.agentmem.supersession import classify_relation, _metadata_overlap

# ---------------------------------------------------------------------------
# Labeled dataset
# ---------------------------------------------------------------------------

T0 = datetime(2026, 1,  1, tzinfo=timezone.utc)
T1 = datetime(2026, 4,  1, tzinfo=timezone.utc)
T2 = datetime(2026, 7,  1, tzinfo=timezone.utc)
T3 = datetime(2026, 10, 1, tzinfo=timezone.utc)

_NVDA_G = {"ticker": "NVDA", "metric": "guidance"}
_NVDA_R = {"ticker": "NVDA", "metric": "revenue"}
_AAPL_R = {"ticker": "AAPL", "metric": "revenue"}
_AAPL_M = {"ticker": "AAPL", "metric": "gross_margin"}
_TSLA_D = {"ticker": "TSLA", "metric": "deliveries"}
_TSLA_P = {"ticker": "TSLA", "metric": "production"}
_FED_RT = {"entity": "FED",  "metric": "rate"}
_BLK_AU = {"entity": "BlackRock", "metric": "aum"}
_CUSIP  = {"cusip": "037833100", "metric": "price"}

# Each entry: (old_content, new_content, old_meta, new_meta, old_t, new_t, expected_relation)
LABELED_PAIRS = [
    # ---- SUPERSEDES (12 cases) ----------------------------------------
    ("NVDA Q3 guidance $32B", "NVDA Q3 guidance raised to $36B",
     _NVDA_G, _NVDA_G, T0, T1, "SUPERSEDES"),

    ("NVDA Q3 guidance $36B", "NVDA Q3 guidance raised to $40B",
     _NVDA_G, _NVDA_G, T1, T2, "SUPERSEDES"),

    ("AAPL Q1 revenue $90B", "AAPL Q2 revenue $95B",
     _AAPL_R, _AAPL_R, T0, T1, "SUPERSEDES"),

    ("AAPL gross margin 44%", "AAPL gross margin expanded to 46%",
     _AAPL_M, _AAPL_M, T1, T2, "SUPERSEDES"),

    ("TSLA deliveries Q1 400k", "TSLA deliveries Q2 430k",
     _TSLA_D, _TSLA_D, T0, T1, "SUPERSEDES"),

    ("TSLA deliveries Q2 430k", "TSLA deliveries Q3 460k",
     _TSLA_D, _TSLA_D, T1, T2, "SUPERSEDES"),

    ("Fed rate 4.75%", "Fed rate hiked to 5.00%",
     _FED_RT, _FED_RT, T0, T1, "SUPERSEDES"),

    ("Fed rate 5.00%", "Fed rate held at 5.00% — vote 12-0",
     # same value? No — "held" means different info (decision vs level)
     # new_content differs from old → SUPERSEDES
     _FED_RT, _FED_RT, T1, T2, "SUPERSEDES"),

    ("BlackRock AUM $9T", "BlackRock AUM $10T Q2 record",
     _BLK_AU, _BLK_AU, T0, T1, "SUPERSEDES"),

    ("NVDA revenue Q1 $22B", "NVDA revenue Q2 $26B",
     _NVDA_R, _NVDA_R, T0, T1, "SUPERSEDES"),

    ("CUSIP 037833100 price $180", "CUSIP 037833100 price $195",
     _CUSIP, _CUSIP, T0, T1, "SUPERSEDES"),

    ("TSLA deliveries Q3 460k", "TSLA deliveries Q4 480k",
     _TSLA_D, _TSLA_D, T2, T3, "SUPERSEDES"),

    # ---- CONFIRMS (8 cases) -------------------------------------------
    ("NVDA Q3 guidance raised to $36B", "NVDA Q3 guidance raised to $36B",
     _NVDA_G, _NVDA_G, T0, T1, "CONFIRMS"),

    ("AAPL Q1 revenue $90B", "AAPL Q1 revenue $90B",
     _AAPL_R, _AAPL_R, T0, T1, "CONFIRMS"),

    ("TSLA Q2 deliveries 430k", "TSLA Q2 deliveries 430k",
     _TSLA_D, _TSLA_D, T1, T2, "CONFIRMS"),

    ("Fed rate 5.25%", "Fed rate 5.25%",
     _FED_RT, _FED_RT, T1, T2, "CONFIRMS"),

    ("BlackRock AUM $10T", "BlackRock AUM $10T",
     _BLK_AU, _BLK_AU, T2, T3, "CONFIRMS"),

    ("NVDA Q2 revenue $26B", "NVDA Q2 revenue $26B",
     _NVDA_R, _NVDA_R, T1, T2, "CONFIRMS"),

    ("CUSIP 037833100 price $195", "CUSIP 037833100 price $195",
     _CUSIP, _CUSIP, T1, T2, "CONFIRMS"),

    ("AAPL gross margin 46%", "AAPL gross margin 46%",
     _AAPL_M, _AAPL_M, T2, T3, "CONFIRMS"),

    # ---- ADDS (6 cases) -----------------------------------------------
    # New memory is actually older — temporal direction reversed
    ("NVDA Q2 guidance $38B", "NVDA Q1 guidance $30B",
     _NVDA_G, _NVDA_G, T1, T0, "ADDS"),

    # Different metric on same ticker
    ("NVDA Q3 guidance $40B", "NVDA Q3 revenue $26B",
     _NVDA_G, _NVDA_R, T0, T1, "ADDS"),

    ("AAPL Q2 revenue $95B", "AAPL Q2 gross margin 46%",
     _AAPL_R, _AAPL_M, T1, T2, "ADDS"),

    # Different entity entirely — Stage 2 only sees "metric" overlap
    # (Stage 1 would filter by structured key mismatch for cross-ticker pairs)
    ("TSLA deliveries 400k", "TSLA production 490k",
     _TSLA_D, _TSLA_P, T0, T1, "ADDS"),

    # New is older
    ("AAPL Q3 revenue $100B", "AAPL Q1 revenue $90B",
     _AAPL_R, _AAPL_R, T2, T0, "ADDS"),

    # New is older (CUSIP)
    ("CUSIP 037833100 price $195", "CUSIP 037833100 price $180",
     _CUSIP, _CUSIP, T1, T0, "ADDS"),

    # ---- CONTRADICTS_SAME_TIME (4 cases) ------------------------------
    ("NVDA Q3 guidance $36B", "NVDA Q3 guidance lowered to $28B",
     _NVDA_G, _NVDA_G, T1, T1, "CONTRADICTS_SAME_TIME"),

    ("AAPL Q1 revenue $90B", "AAPL Q1 revenue $88B revised",
     _AAPL_R, _AAPL_R, T0, T0, "CONTRADICTS_SAME_TIME"),

    ("Fed rate 5.00%", "Fed rate cut to 4.75%",
     _FED_RT, _FED_RT, T2, T2, "CONTRADICTS_SAME_TIME"),

    ("TSLA Q2 deliveries 430k", "TSLA Q2 deliveries 415k revised",
     _TSLA_D, _TSLA_D, T1, T1, "CONTRADICTS_SAME_TIME"),
]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def _run_benchmark() -> dict:
    """Run all labeled pairs and return per-class confusion counts + accuracy."""
    classes = ["SUPERSEDES", "CONFIRMS", "ADDS", "CONTRADICTS_SAME_TIME"]
    tp = {c: 0 for c in classes}
    fp = {c: 0 for c in classes}
    fn = {c: 0 for c in classes}

    total = len(LABELED_PAIRS)
    correct = 0

    for old_c, new_c, old_m, new_m, old_t, new_t, expected in LABELED_PAIRS:
        got, _ = classify_relation(old_c, new_c, old_t, new_t, old_m, new_m)
        if got == expected:
            correct += 1
            tp[expected] += 1
        else:
            fp[got] += 1
            fn[expected] += 1

    accuracy = correct / total

    metrics = {}
    for c in classes:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) > 0 else 0.0
        r = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) > 0 else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
        metrics[c] = {"precision": p, "recall": r, "f1": f1, "support": tp[c] + fn[c]}

    return {"accuracy": accuracy, "per_class": metrics, "n": total}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSupersessionAccuracy:

    def test_overall_accuracy_above_90_percent(self):
        """Stage 1+2 must classify >= 90% of labeled pairs correctly."""
        result = _run_benchmark()
        acc = result["accuracy"]
        assert acc >= 0.90, (
            f"Overall accuracy {acc:.1%} is below the 90% target "
            f"({int(acc * result['n'])}/{result['n']} correct)"
        )

    def test_supersedes_f1_above_90_percent(self):
        """
        SUPERSEDES is the most critical class: a false negative means the agent
        keeps stale data; a false positive means it discards valid information.
        F1 >= 0.90 is required.
        """
        result = _run_benchmark()
        f1 = result["per_class"]["SUPERSEDES"]["f1"]
        assert f1 >= 0.90, (
            f"SUPERSEDES F1 = {f1:.2f}; must be >= 0.90 to reliably "
            "prevent stale facts from persisting in memory"
        )

    def test_confirms_precision_above_80_percent(self):
        """
        False-positive CONFIRMS means we silently discard a genuine update.
        Precision >= 0.80 prevents this.
        """
        result = _run_benchmark()
        p = result["per_class"]["CONFIRMS"]["precision"]
        assert p >= 0.80, (
            f"CONFIRMS precision = {p:.2f}; false positives here "
            "mean real updates are lost"
        )

    def test_no_class_has_zero_recall(self):
        """Every class must be detected at least once — no total blind spot."""
        result = _run_benchmark()
        for cls, m in result["per_class"].items():
            if m["support"] > 0:
                assert m["recall"] > 0.0, (
                    f"Zero recall on class {cls!r} with {m['support']} samples — "
                    "the engine has a complete blind spot for this case"
                )

    def test_older_event_time_never_supersedes(self):
        """
        A new memory with an older event_time must NEVER produce SUPERSEDES.
        This is the temporal-ordering invariant that mem0 and Zep lack.
        """
        older_cases = [
            (old_c, new_c, old_m, new_m, old_t, new_t)
            for old_c, new_c, old_m, new_m, old_t, new_t, expected in LABELED_PAIRS
            if new_t < old_t  # new is actually older
        ]
        assert older_cases, "Fixture must include at least one reversed-time pair"

        for old_c, new_c, old_m, new_m, old_t, new_t in older_cases:
            got, _ = classify_relation(old_c, new_c, old_t, new_t, old_m, new_m)
            assert got != "SUPERSEDES", (
                f"Temporal ordering violated: new_t={new_t.date()} < old_t={old_t.date()} "
                f"but engine returned SUPERSEDES for '{new_c[:40]}'"
            )

    def test_same_value_never_supersedes(self):
        """
        Identical content cannot supersede — it can only CONFIRM.
        Prevents spurious invalidation when the same fact arrives twice.
        """
        same_value_cases = [
            (old_c, new_c, old_m, new_m, old_t, new_t)
            for old_c, new_c, old_m, new_m, old_t, new_t, expected in LABELED_PAIRS
            if old_c.strip().lower() == new_c.strip().lower()
        ]
        assert same_value_cases, "Fixture must include at least one CONFIRMS pair"

        for old_c, new_c, old_m, new_m, old_t, new_t in same_value_cases:
            got, _ = classify_relation(old_c, new_c, old_t, new_t, old_m, new_m)
            assert got == "CONFIRMS", (
                f"Identical content must CONFIRM, not {got!r}: '{new_c[:40]}'"
            )

    def test_different_metric_never_supersedes(self):
        """
        A memory with a different 'metric' key cannot supersede another.
        Cross-attribute supersession would corrupt the agent's knowledge graph.
        """
        cross_metric_cases = [
            (old_c, new_c, old_m, new_m, old_t, new_t)
            for old_c, new_c, old_m, new_m, old_t, new_t, expected in LABELED_PAIRS
            if old_m.get("metric") and new_m.get("metric")
            and old_m["metric"] != new_m["metric"]
        ]
        assert cross_metric_cases, "Fixture must include at least one cross-metric pair"

        for old_c, new_c, old_m, new_m, old_t, new_t in cross_metric_cases:
            got, _ = classify_relation(old_c, new_c, old_t, new_t, old_m, new_m)
            assert got != "SUPERSEDES", (
                f"Cross-metric pair must not produce SUPERSEDES: "
                f"metric={old_m['metric']!r} vs {new_m['metric']!r}"
            )

    def test_contradicts_same_time_requires_equal_event_time(self):
        """
        CONTRADICTS_SAME_TIME only fires when both event_times are equal.
        With different times, temporal ordering takes precedence.
        """
        for old_c, new_c, old_m, new_m, old_t, new_t, expected in LABELED_PAIRS:
            if expected != "CONTRADICTS_SAME_TIME":
                continue
            assert old_t == new_t, (
                "Test fixture bug: CONTRADICTS_SAME_TIME must have equal event_times"
            )

        # Non-equal times should not produce CONTRADICTS_SAME_TIME
        got, _ = classify_relation(
            "NVDA guidance $36B", "NVDA guidance $28B",
            T0, T1,  # different times
            _NVDA_G, _NVDA_G,
        )
        assert got != "CONTRADICTS_SAME_TIME", (
            "Different event_times must resolve via temporal ordering, "
            "not CONTRADICTS_SAME_TIME"
        )

    def test_chain_of_five_all_supersede_correctly(self):
        """
        Five consecutive revisions all produce SUPERSEDES when chained.
        Validates that the engine is consistent under repeated application.
        """
        values = ["$28B", "$32B", "$36B", "$38B", "$40B"]
        times  = [T0, T1, T2, T3, T3 + timedelta(days=90)]

        for i in range(len(values) - 1):
            got, conf = classify_relation(
                values[i], values[i + 1],
                times[i], times[i + 1],
                _NVDA_G, _NVDA_G,
            )
            assert got == "SUPERSEDES", (
                f"Step {i}→{i+1}: expected SUPERSEDES, got {got!r} "
                f"({values[i]} → {values[i+1]})"
            )
            assert conf >= 0.8, f"Low confidence {conf} at step {i}→{i+1}"


class TestMetadataOverlapCoverage:
    """Unit tests for the _metadata_overlap helper used in Stage 1 candidate filtering."""

    def test_all_structured_keys_recognized(self):
        from src.agentmem.supersession import _STRUCTURED_KEYS
        expected = {"ticker", "metric", "entity", "instrument", "cusip", "isin", "field"}
        assert _STRUCTURED_KEYS == expected, (
            f"Structured key set changed: {_STRUCTURED_KEYS} vs {expected}"
        )

    def test_isin_key_recognized(self):
        m1 = {"isin": "US0378331005", "metric": "price"}
        m2 = {"isin": "US0378331005", "metric": "price"}
        assert _metadata_overlap(m1, m2) == {"isin", "metric"}

    def test_field_key_recognized(self):
        m1 = {"field": "eps", "ticker": "AAPL"}
        m2 = {"field": "eps", "ticker": "AAPL"}
        assert _metadata_overlap(m1, m2) == {"field", "ticker"}

    def test_non_structured_keys_ignored(self):
        m1 = {"source": "bloomberg", "note": "Q3 call", "ticker": "AAPL"}
        m2 = {"source": "reuters",   "note": "Q3 call", "ticker": "AAPL"}
        # Only "ticker" is in _STRUCTURED_KEYS; source and note are not
        overlap = _metadata_overlap(m1, m2)
        assert "source" not in overlap
        assert "note"   not in overlap
        assert "ticker" in overlap

    def test_value_mismatch_excluded_from_overlap(self):
        m1 = {"ticker": "AAPL", "metric": "revenue"}
        m2 = {"ticker": "TSLA", "metric": "revenue"}
        # ticker keys match by name but NOT by value → excluded
        overlap = _metadata_overlap(m1, m2)
        assert "ticker" not in overlap
        assert "metric" in overlap
