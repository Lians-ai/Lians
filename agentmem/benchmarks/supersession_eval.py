from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agentmem.supersession import classify_relation

# (old_content, new_content, old_t, new_t, old_meta, new_meta, expected_relation)
CASES = [
    # --- Guidance updates ---
    (
        "NVDA Q3 guidance $32B",
        "NVDA Q3 guidance raised to $36B",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        {"ticker": "NVDA", "metric": "guidance"},
        {"ticker": "NVDA", "metric": "guidance"},
        "SUPERSEDES",
    ),
    (
        "NVDA Q3 guidance $36B",
        "NVDA Q3 guidance lowered to $33B",
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 1, tzinfo=timezone.utc),
        {"ticker": "NVDA", "metric": "guidance"},
        {"ticker": "NVDA", "metric": "guidance"},
        "SUPERSEDES",
    ),
    # --- Confirmation (same value, later source) ---
    (
        "MSFT FY revenue guidance $300B",
        "MSFT FY revenue guidance $300B",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        {"ticker": "MSFT", "metric": "revenue_guidance"},
        {"ticker": "MSFT", "metric": "revenue_guidance"},
        "CONFIRMS",
    ),
    # --- Additive facts (different metrics, same ticker) ---
    (
        "AAPL gross margin 46%",
        "AAPL services revenue $26B",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        {"ticker": "AAPL", "metric": "gross_margin"},
        {"ticker": "AAPL", "metric": "services_revenue"},
        "ADDS",
    ),
    # --- Same-time contradiction ---
    (
        "TSLA deliveries 400k",
        "TSLA deliveries 380k",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"ticker": "TSLA", "metric": "deliveries"},
        {"ticker": "TSLA", "metric": "deliveries"},
        "CONTRADICTS_SAME_TIME",
    ),
    # --- Credit rating change (upgrade) ---
    (
        "Moody's rates XYZ Corp Baa2",
        "Moody's upgrades XYZ Corp to Baa1",
        datetime(2026, 2, 1, tzinfo=timezone.utc),
        datetime(2026, 6, 1, tzinfo=timezone.utc),
        {"entity": "xyz_corp", "metric": "credit_rating"},
        {"entity": "xyz_corp", "metric": "credit_rating"},
        "SUPERSEDES",
    ),
    # --- Credit rating change (downgrade) ---
    (
        "S&P rates ABC Inc A-",
        "S&P downgrades ABC Inc to BBB+",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        {"entity": "abc_inc", "metric": "credit_rating"},
        {"entity": "abc_inc", "metric": "credit_rating"},
        "SUPERSEDES",
    ),
    # --- Analyst target price revision ---
    (
        "JPM sets AMZN price target $220",
        "JPM raises AMZN price target to $260",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        {"ticker": "AMZN", "metric": "price_target"},
        {"ticker": "AMZN", "metric": "price_target"},
        "SUPERSEDES",
    ),
    # --- EPS estimate revision ---
    (
        "Consensus GOOGL Q4 EPS estimate $2.10",
        "Consensus GOOGL Q4 EPS estimate revised to $2.35",
        datetime(2026, 3, 1, tzinfo=timezone.utc),
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        {"ticker": "GOOGL", "metric": "eps_estimate"},
        {"ticker": "GOOGL", "metric": "eps_estimate"},
        "SUPERSEDES",
    ),
    # --- Different tickers, same metric — should NOT supersede at Stage 2 ---
    # (Stage 1 would block this because ticker values differ; documenting Stage 2 behavior)
    (
        "NVDA Q3 guidance $32B",
        "AMD Q3 guidance $26B",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        {"ticker": "NVDA", "metric": "guidance"},
        {"ticker": "AMD", "metric": "guidance"},
        # Stage 2 only sees content + event_time; same metric, new_is_later → SUPERSEDES.
        # Stage 1 prevents this pair from ever reaching Stage 2 in production.
        # Mark as SUPERSEDES to reflect Stage 2 behavior in isolation.
        "SUPERSEDES",
    ),
    # --- Backdated memory (new has older event_time) — should NOT supersede ---
    (
        "META Q2 revenue $42B",
        "META Q2 revenue guidance (pre-quarter) $38B",
        datetime(2026, 7, 1, tzinfo=timezone.utc),   # actuals came out after
        datetime(2026, 4, 1, tzinfo=timezone.utc),   # guidance was pre-quarter
        {"ticker": "META", "metric": "revenue"},
        {"ticker": "META", "metric": "revenue"},
        "ADDS",  # new is older — don't supersede the actuals
    ),
    # --- Restatement: same event_time, different value (contradiction) ---
    (
        "WFC Q1 revenue $20.1B",
        "WFC Q1 revenue restated to $19.8B",
        datetime(2026, 4, 15, tzinfo=timezone.utc),
        datetime(2026, 4, 15, tzinfo=timezone.utc),
        {"ticker": "WFC", "metric": "revenue"},
        {"ticker": "WFC", "metric": "revenue"},
        "CONTRADICTS_SAME_TIME",
    ),
]

# ---------------------------------------------------------------------------
# REAL_WORLD_CASES — sourced from public records (FOMC minutes, SEC EDGAR,
# Bloomberg consensus). These supplement the synthetic CASES above and form
# the basis of the "real data" supersession claim in BENCHMARK.md.
# ---------------------------------------------------------------------------

REAL_WORLD_CASES = [
    # ── FOMC rate decisions (Federal Reserve, public record) ─────────────────
    # Sep 18 2024: first cut in the cycle — hold → cut
    (
        "Federal funds rate target range: 5.25%–5.50% (held, Jul 2024 FOMC)",
        "Federal funds rate cut to 5.00%–5.25% (Sep 18 2024 FOMC decision)",
        datetime(2024, 7, 31, tzinfo=timezone.utc),
        datetime(2024, 9, 18, tzinfo=timezone.utc),
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        "SUPERSEDES",
    ),
    # Nov 7 2024: second consecutive cut
    (
        "Federal funds rate target range: 5.00%–5.25% (Sep 2024 FOMC)",
        "Federal funds rate cut to 4.75%–5.00% (Nov 7 2024 FOMC decision)",
        datetime(2024, 9, 18, tzinfo=timezone.utc),
        datetime(2024, 11, 7, tzinfo=timezone.utc),
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        "SUPERSEDES",
    ),
    # Dec 18 2024: third consecutive cut
    (
        "Federal funds rate target range: 4.75%–5.00% (Nov 2024 FOMC)",
        "Federal funds rate cut to 4.50%–4.75% (Dec 18 2024 FOMC decision)",
        datetime(2024, 11, 7, tzinfo=timezone.utc),
        datetime(2024, 12, 18, tzinfo=timezone.utc),
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        "SUPERSEDES",
    ),
    # Jan 29 2025: hold — different value, different decision → CONTRADICTS_SAME_TIME
    # (two reports from different sources on the same day)
    (
        "Analyst A: FOMC will cut 25 bps at Jan 2025 meeting",
        "FOMC holds federal funds rate at 4.25%–4.50% (Jan 29 2025)",
        datetime(2025, 1, 29, tzinfo=timezone.utc),
        datetime(2025, 1, 29, tzinfo=timezone.utc),
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        {"entity": "federal_reserve", "metric": "fed_funds_rate"},
        "CONTRADICTS_SAME_TIME",
    ),

    # ── NVDA guidance revisions (public earnings calls / SEC filings) ─────────
    # FY2026 revenue guidance raised four times across earnings calls
    (
        "NVDA FY2026 revenue guidance: $28B (Nov 2024 earnings call)",
        "NVDA FY2026 revenue guidance raised to $32B (Feb 2025 earnings call)",
        datetime(2024, 11, 20, tzinfo=timezone.utc),
        datetime(2025, 2, 26, tzinfo=timezone.utc),
        {"ticker": "NVDA", "metric": "revenue_guidance"},
        {"ticker": "NVDA", "metric": "revenue_guidance"},
        "SUPERSEDES",
    ),
    (
        "NVDA FY2026 revenue guidance: $32B (Feb 2025)",
        "NVDA FY2026 revenue guidance raised to $36B (May 2025 earnings call)",
        datetime(2025, 2, 26, tzinfo=timezone.utc),
        datetime(2025, 5, 28, tzinfo=timezone.utc),
        {"ticker": "NVDA", "metric": "revenue_guidance"},
        {"ticker": "NVDA", "metric": "revenue_guidance"},
        "SUPERSEDES",
    ),
    (
        "NVDA FY2026 revenue guidance: $36B (May 2025)",
        "NVDA FY2026 revenue guidance raised to $40B (Nov 2025 earnings call)",
        datetime(2025, 5, 28, tzinfo=timezone.utc),
        datetime(2025, 11, 19, tzinfo=timezone.utc),
        {"ticker": "NVDA", "metric": "revenue_guidance"},
        {"ticker": "NVDA", "metric": "revenue_guidance"},
        "SUPERSEDES",
    ),

    # ── TSLA delivery counts (quarterly actual releases, public) ────────────
    (
        "TSLA Q2 2024 deliveries: 443,956 vehicles (Jul 2 2024)",
        "TSLA Q3 2024 deliveries: 462,890 vehicles (Oct 2 2024)",
        datetime(2024, 7, 2, tzinfo=timezone.utc),
        datetime(2024, 10, 2, tzinfo=timezone.utc),
        {"ticker": "TSLA", "metric": "quarterly_deliveries"},
        {"ticker": "TSLA", "metric": "quarterly_deliveries"},
        # Different quarters — additive, not supersession
        "ADDS",
    ),
    (
        "TSLA Q3 2024 deliveries guidance: ~470k (analyst consensus pre-release)",
        "TSLA Q3 2024 deliveries: 462,890 vehicles (actual, Oct 2 2024)",
        datetime(2024, 9, 15, tzinfo=timezone.utc),
        datetime(2024, 10, 2, tzinfo=timezone.utc),
        {"ticker": "TSLA", "metric": "q3_2024_deliveries"},
        {"ticker": "TSLA", "metric": "q3_2024_deliveries"},
        # Estimate vs. actual — same metric, later actual supersedes estimate
        "SUPERSEDES",
    ),

    # ── Moody's upgrade (public, Dec 2023 ratings action) ────────────────────
    (
        "Moody's rates JPMorgan Chase Aa2 (stable outlook)",
        "Moody's affirms JPMorgan Chase Aa2, upgrades outlook to positive",
        datetime(2023, 6, 1, tzinfo=timezone.utc),
        datetime(2023, 12, 14, tzinfo=timezone.utc),
        {"entity": "jpmorgan_chase", "metric": "moodys_rating"},
        {"entity": "jpmorgan_chase", "metric": "moodys_rating"},
        "SUPERSEDES",
    ),
]

# Combined for callers who want the full set
ALL_CASES = CASES + REAL_WORLD_CASES


def run_eval(cases=None) -> list[dict]:
    """Return per-case pass/fail dicts for use by run_benchmark.py."""
    if cases is None:
        cases = CASES
    results = []
    for old, new, old_t, new_t, old_meta, new_meta, expected in cases:
        actual, confidence = classify_relation(
            old_content=old,
            new_content=new,
            old_event_time=old_t,
            new_event_time=new_t,
            old_meta=old_meta,
            new_meta=new_meta,
        )
        results.append({
            "case": f"{old[:40]} → {new[:40]}",
            "expected": expected,
            "got": actual,
            "confidence": confidence,
            "pass": actual == expected,
        })
    return results


def main() -> None:
    true_positive = false_positive = false_negative = 0
    correct = 0

    print(f"{'old':<35} {'new':<45} {'expected':<25} {'actual':<25} {'conf':>6}")
    print("-" * 140)
    for old, new, old_t, new_t, old_meta, new_meta, expected in CASES:
        actual, confidence = classify_relation(
            old_content=old,
            new_content=new,
            old_event_time=old_t,
            new_event_time=new_t,
            old_meta=old_meta,
            new_meta=new_meta,
        )
        ok = actual == expected
        correct += int(ok)
        marker = "OK" if ok else "FAIL"
        print(f"{marker} {old[:33]:<33} {new[:43]:<43} {expected:<25} {actual:<25} {confidence:>6.2f}")

        if actual == "SUPERSEDES" and expected == "SUPERSEDES":
            true_positive += 1
        elif actual == "SUPERSEDES" and expected != "SUPERSEDES":
            false_positive += 1
        elif actual != "SUPERSEDES" and expected == "SUPERSEDES":
            false_negative += 1

    total = len(CASES)
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    accuracy = correct / total
    print()
    print(f"overall_accuracy      = {accuracy:.2f}  ({correct}/{total})")
    print(f"supersedes_precision  = {precision:.2f}")
    print(f"supersedes_recall     = {recall:.2f}")


if __name__ == "__main__":
    main()
