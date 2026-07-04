"""
Deterministic synthetic dataset for the lookahead-bias demo.

Generates two files under data/:

  prices.csv   — daily close-to-close returns for 6 fictional tickers over
                 ~6 months of trading days (weekdays, 2026-01-05 → 2026-06-26)
  notes.jsonl  — timestamped research notes an agent would accumulate in its
                 memory layer: earnings previews, earnings/guidance/rating
                 outcomes, next-day analyst reactions, and neutral macro notes

The market is synthetic ON PURPOSE — the demo is about a *mechanism*, not
alpha. What is real, and what the whole demo hinges on, is the causal
structure every market has:

  1. An event happens on day E (earnings beat, guidance cut, downgrade).
  2. The price jumps on day E.
  3. The note DESCRIBING the outcome cannot exist before day E.

So a note like "AVLN beats Q1 consensus" carries event_time = day E. If a
backtest making a decision for day E (using information through day E-1)
can retrieve that note, its memory layer is leaking the future. Synthetic
prices make the leak *measurable* — we control exactly which information
was knowable when — and make the repo reproducible with zero API keys.

Fictional tickers are used so no real company's facts are misstated.

Run:  python generate_dataset.py     (rewrites data/ deterministically, seed=42)
"""
from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

SEED = 42
DATA = Path(__file__).resolve().parent / "data"

TICKERS = ["AVLN", "CRDX", "HLIO", "NMBS", "QNTA", "VYTR"]

START = date(2026, 1, 5)
END = date(2026, 6, 26)

BASE_DRIFT = 0.0003          # daily
BASE_VOL = 0.011             # daily
JUMP_LO, JUMP_HI = 0.035, 0.075

QUARTER_NAMES = {2: "Q4", 5: "Q1"}   # reporting month -> quarter reported


def trading_days() -> list[date]:
    days, d = [], START
    while d <= END:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def month_name(d: date) -> str:
    return d.strftime("%B")


def utc(d: date, hh: int = 12, mm: int = 0) -> str:
    return datetime.combine(d, time(hh, mm), tzinfo=timezone.utc).isoformat()


def pick_event_days(rng: random.Random, days: list[date]) -> dict[str, list[tuple[date, str]]]:
    """For each ticker: two earnings dates (Feb & May windows) + two surprise
    events (guidance revision, rating action) at least 12 trading days from
    any other event of the same ticker."""
    by_month: dict[int, list[date]] = {}
    for d in days:
        by_month.setdefault(d.month, []).append(d)

    events: dict[str, list[tuple[date, str]]] = {}
    for t in TICKERS:
        picked: list[tuple[date, str]] = []
        # earnings: one day in Feb, one in May (staggered by ticker)
        for m in (2, 5):
            pool = by_month[m][3:-3]
            picked.append((rng.choice(pool), "earnings"))

        def far_enough(cand: date) -> bool:
            return all(abs((cand - e).days) >= 16 for e, _ in picked)

        for kind in ("guidance", "rating"):
            while True:
                cand = rng.choice(days[5:-5])
                if far_enough(cand):
                    picked.append((cand, kind))
                    break
        picked.sort()
        events[t] = picked
    return events


POS_OUTCOME = {
    "earnings": ("{t} beats {q} consensus — EPS ${a:.2f} vs ${e:.2f} expected; revenue ahead of "
                 "street; management raises full-year guidance. ({mon} {day})"),
    "guidance": ("{t} raises FY outlook in an unscheduled {mon} update; cites strong demand and "
                 "expanding margins."),
    "rating":   ("Moody's upgrades {t} one notch in {mon}, citing improved leverage and cash "
                 "flow durability."),
}
NEG_OUTCOME = {
    "earnings": ("{t} misses {q} consensus — EPS ${a:.2f} vs ${e:.2f} expected; revenue light; "
                 "management cuts full-year guidance. ({mon} {day})"),
    "guidance": ("{t} cuts FY outlook in an unscheduled {mon} update; flags weak demand and "
                 "margin pressure."),
    "rating":   ("Moody's downgrades {t} one notch in {mon}, citing deteriorating leverage and "
                 "weak free cash flow."),
}
POS_ANALYST = ("Sell-side upgrades {t} to Overweight after the {mon} news; price targets raised "
               "across the street.")
NEG_ANALYST = ("Sell-side downgrades {t} to Underweight after the {mon} news; price targets cut "
               "across the street.")

MACRO_NOTES = [
    "Desk note: Fed minutes read broadly neutral for risk assets; no change to positioning.",
    "Desk note: CPI in line with expectations; rates vol subdued.",
    "Desk note: breadth improving across large-cap tech; watching credit spreads.",
    "Desk note: quarter-end rebalancing flows expected this week; liquidity thin.",
    "Desk note: ISM services slightly soft; no read-through to our coverage.",
]


def main() -> None:
    rng = random.Random(SEED)
    days = trading_days()
    events = pick_event_days(rng, days)

    # --- event outcomes & jumps -------------------------------------------
    jump: dict[tuple[str, date], float] = {}
    notes: list[dict] = []

    for t in TICKERS:
        for eday, kind in events[t]:
            direction = rng.choice([1, -1])
            magnitude = rng.uniform(JUMP_LO, JUMP_HI) * direction
            jump[(t, eday)] = magnitude

            eid = f"{t}-{kind}-{eday.isoformat()}"
            mon, dnum = month_name(eday), eday.day

            if kind == "earnings":
                q = QUARTER_NAMES[eday.month]
                est = rng.uniform(0.30, 1.20)
                act = est + (0.09 if direction > 0 else -0.09) * est
                # preview — knowable BEFORE the event (neutral)
                pday = eday - timedelta(days=4)
                notes.append({
                    "ticker": t, "kind": "preview", "event_id": eid,
                    "event_time": utc(pday, 14),
                    "content": (f"{t} {q} earnings scheduled for {mon} {dnum}; street consensus "
                                f"EPS ${est:.2f}. Positioning into the print is a judgment call."),
                })
                outcome = (POS_OUTCOME if direction > 0 else NEG_OUTCOME)[kind].format(
                    t=t, q=q, a=act, e=est, mon=mon, day=dnum)
            else:
                outcome = (POS_OUTCOME if direction > 0 else NEG_OUTCOME)[kind].format(
                    t=t, mon=mon)

            # outcome — published the morning of the event (pre-open)
            notes.append({
                "ticker": t, "kind": "outcome", "event_id": eid,
                "event_time": utc(eday, 12),
                "content": outcome,
            })
            # analyst reaction — next calendar day
            notes.append({
                "ticker": t, "kind": "analyst", "event_id": eid,
                "event_time": utc(eday + timedelta(days=1), 13, 30),
                "content": (POS_ANALYST if direction > 0 else NEG_ANALYST).format(t=t, mon=mon),
            })

    # --- neutral macro notes ----------------------------------------------
    for i, txt in enumerate(MACRO_NOTES * 3):
        d = days[rng.randrange(len(days))]
        notes.append({
            "ticker": None, "kind": "macro", "event_id": f"macro-{i}",
            "event_time": utc(d, 11), "content": txt,
        })

    notes.sort(key=lambda n: n["event_time"])

    # --- price path ---------------------------------------------------------
    returns: dict[str, list[float]] = {t: [] for t in TICKERS}
    for d in days:
        for t in TICKERS:
            r = rng.gauss(BASE_DRIFT, BASE_VOL) + jump.get((t, d), 0.0)
            returns[t].append(round(r, 6))

    # --- write --------------------------------------------------------------
    DATA.mkdir(exist_ok=True)
    with open(DATA / "prices.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date"] + TICKERS)
        for i, d in enumerate(days):
            w.writerow([d.isoformat()] + [returns[t][i] for t in TICKERS])

    with open(DATA / "notes.jsonl", "w", encoding="utf-8") as f:
        for n in notes:
            f.write(json.dumps(n) + "\n")

    n_events = sum(len(v) for v in events.values())
    print(f"wrote {len(days)} trading days x {len(TICKERS)} tickers, "
          f"{n_events} events, {len(notes)} notes -> {DATA}")


if __name__ == "__main__":
    main()
