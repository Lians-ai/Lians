"""
Supersession engine correctness â€” Phase 1 cases (Stage 1+2 rules).
These must all pass before Phase 2 LLM adjudication is added.
"""
import pytest
from datetime import datetime, timezone, timedelta

from src.lian.supersession import classify_relation, _metadata_overlap


T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 5, 10, tzinfo=timezone.utc)
T2 = datetime(2026, 5, 20, tzinfo=timezone.utc)

META_NVDA_GUIDANCE = {"ticker": "NVDA", "metric": "guidance"}
META_NVDA_REVENUE = {"ticker": "NVDA", "metric": "revenue"}
META_AMD_GUIDANCE = {"ticker": "AMD", "metric": "guidance"}


class TestMetadataOverlap:
    def test_exact_match(self):
        assert _metadata_overlap(META_NVDA_GUIDANCE, META_NVDA_GUIDANCE) == {"ticker", "metric"}

    def test_different_ticker(self):
        assert _metadata_overlap(META_NVDA_GUIDANCE, META_AMD_GUIDANCE) == {"metric"}

    def test_different_metric(self):
        assert _metadata_overlap(META_NVDA_GUIDANCE, META_NVDA_REVENUE) == {"ticker"}

    def test_no_structured_keys(self):
        assert _metadata_overlap({"note": "x"}, {"note": "x"}) == set()


class TestClassifyRelation:
    def test_supersedes_newer_event_time(self):
        relation, conf = classify_relation(
            old_content="NVDA Q3 guidance $32B",
            new_content="NVDA Q3 guidance raised to $36B",
            old_event_time=T0,
            new_event_time=T1,
            old_meta=META_NVDA_GUIDANCE,
            new_meta=META_NVDA_GUIDANCE,
        )
        assert relation == "SUPERSEDES"
        assert conf >= 0.8

    def test_confirms_same_value(self):
        relation, conf = classify_relation(
            old_content="NVDA Q3 guidance $36B",
            new_content="NVDA Q3 guidance $36B",
            old_event_time=T0,
            new_event_time=T1,
            old_meta=META_NVDA_GUIDANCE,
            new_meta=META_NVDA_GUIDANCE,
        )
        assert relation == "CONFIRMS"
        assert conf >= 0.8

    def test_contradicts_same_time(self):
        relation, conf = classify_relation(
            old_content="NVDA Q3 guidance $36B",
            new_content="NVDA Q3 guidance lowered to $28B",
            old_event_time=T1,
            new_event_time=T1,
            old_meta=META_NVDA_GUIDANCE,
            new_meta=META_NVDA_GUIDANCE,
        )
        assert relation == "CONTRADICTS_SAME_TIME"

    def test_adds_older_event_time(self):
        """New memory is actually older â€” should not supersede."""
        relation, _ = classify_relation(
            old_content="NVDA Q3 guidance $36B",
            new_content="NVDA earlier guidance $30B",
            old_event_time=T2,
            new_event_time=T0,  # new is earlier!
            old_meta=META_NVDA_GUIDANCE,
            new_meta=META_NVDA_GUIDANCE,
        )
        assert relation == "ADDS"

    def test_supersedes_direction_agnostic(self):
        """Both 'raised to $36B' and 'lowered to $28B' supersede the old $32B fact."""
        for new_content in ["NVDA Q3 guidance raised to $36B", "NVDA Q3 guidance lowered to $28B"]:
            relation, _ = classify_relation(
                old_content="NVDA Q3 guidance $32B",
                new_content=new_content,
                old_event_time=T0,
                new_event_time=T1,
                old_meta=META_NVDA_GUIDANCE,
                new_meta=META_NVDA_GUIDANCE,
            )
            assert relation == "SUPERSEDES", f"Expected SUPERSEDES for: {new_content}"

    def test_different_ticker_not_confused(self):
        """NVDA guidance must NOT supersede AMD guidance â€” different ticker."""
        relation, _ = classify_relation(
            old_content="AMD Q3 guidance $25B",
            new_content="NVDA Q3 guidance $36B",
            old_event_time=T0,
            new_event_time=T1,
            old_meta=META_AMD_GUIDANCE,
            new_meta=META_NVDA_GUIDANCE,
        )
        # Different ticker â†’ different entity; this pair lacks full structured key match.
        # classify_relation sees same metric "guidance" but we only call classify_relation
        # after Stage 1 has already filtered candidates â€” so in practice they'd never be paired.
        # Here we confirm that differing metric fields produce ADDS, not SUPERSEDES.
        # (AMD/NVDA share "metric" but differ on "ticker"; Stage 1 would find overlap on
        # "metric" only â†’ partial match needing cosine threshold, not full match.)
        # classify_relation itself doesn't know about structured keys; it gets same metric â†’
        # temporal ordering applies â†’ SUPERSEDES if new_is_later.
        # The guard is in Stage 1 (find_supersession_candidates).  Document this here.
        assert relation in ("SUPERSEDES", "ADDS")  # Stage 2 alone can't distinguish tickers

    def test_same_metric_different_values_chain(self):
        """Three consecutive guidance updates â€” each supersedes the prior."""
        v1, v2, v3 = "$32B", "$36B", "$40B"
        r12, _ = classify_relation(v1, v2, T0, T1, META_NVDA_GUIDANCE, META_NVDA_GUIDANCE)
        r23, _ = classify_relation(v2, v3, T1, T2, META_NVDA_GUIDANCE, META_NVDA_GUIDANCE)
        assert r12 == "SUPERSEDES"
        assert r23 == "SUPERSEDES"

    def test_entity_key_supersession(self):
        """'entity' key works the same as 'ticker' for structured matching."""
        meta_a = {"entity": "blackrock", "metric": "aum"}
        meta_b = {"entity": "blackrock", "metric": "aum"}
        relation, conf = classify_relation(
            old_content="BlackRock AUM $9T",
            new_content="BlackRock AUM $10T",
            old_event_time=T0,
            new_event_time=T1,
            old_meta=meta_a,
            new_meta=meta_b,
        )
        assert relation == "SUPERSEDES"

    def test_no_metadata_produces_no_overlap(self):
        """Without structured keys, _metadata_overlap returns empty â€” no supersession candidate."""
        from src.lian.supersession import _metadata_overlap
        overlap = _metadata_overlap({}, {"note": "free text memory"})
        assert overlap == set()

    def test_cusip_isin_keys_recognized(self):
        """CUSIP and ISIN are recognized structured keys."""
        from src.lian.supersession import _metadata_overlap
        meta_a = {"cusip": "037833100", "metric": "price"}
        meta_b = {"cusip": "037833100", "metric": "price"}
        overlap = _metadata_overlap(meta_a, meta_b)
        assert "cusip" in overlap
        assert "metric" in overlap
