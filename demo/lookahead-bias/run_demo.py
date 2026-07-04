"""
Lookahead-bias demo: the same backtest, run twice against the same memory.

  Run 1 (CONTAMINATED)  mem.recall(...)                # present-time recall,
                                                       # i.e. what every plain
                                                       # vector store does
  Run 2 (HONEST)        mem.recall_at(..., as_of=D)    # point-in-time recall,
                                                       # pinned to decision time

That is the entire diff — one parameter. Everything else (data, strategy,
scoring, thresholds) is byte-identical between the runs.

The strategy is deliberately dumb: each morning, for each ticker, ask memory
"what do I know about this name?", score the retrieved notes with a keyword
lexicon (+beats/raises/upgrade, -misses/cuts/downgrade), weight by how close
the note's date is to today, and go long/short/flat. The strategy is not the
star. The leak is the star.

Outputs (results/):
  equity_curves.png   the money shot
  equity_curves.csv   raw curves
  receipts.csv        every contaminated retrieval (decision time, note, delta)
  receipts.md         the receipts table, formatted
  summary.md          metrics + the backtest_check() report

Run:  python run_demo.py            (~30s, no API keys, no network)
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"

# Use the in-repo SDK when running from the Lians monorepo; installed package otherwise.
_sdk = HERE.parents[1] / "agentmem" / "sdk" / "python"
if _sdk.exists():
    sys.path.insert(0, str(_sdk))

from lians import LocalLiansClient  # noqa: E402

AGENT = "strategy"
K = 6
POSITION_THRESHOLD = 0.15
PROXIMITY_HALF_LIFE_DAYS = 7.0

POSITIVE = {"beats", "raises", "upgrade", "upgrades", "ahead", "overweight", "improved", "strong"}
NEGATIVE = {"misses", "cuts", "downgrade", "downgrades", "light", "underweight",
            "deteriorating", "weak"}

_word = re.compile(r"[a-z]+")


def _aware(iso: str) -> datetime:
    """Parse an ISO timestamp as UTC-aware (SQLite strips tz info)."""
    dt = datetime.fromisoformat(iso)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def polarity(text: str) -> int:
    words = set(_word.findall(text.lower()))
    score = len(words & POSITIVE) - len(words & NEGATIVE)
    return (score > 0) - (score < 0)


def load_prices() -> tuple[list[date], list[str], dict[str, list[float]]]:
    with open(DATA / "prices.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    tickers = rows[0][1:]
    days = [date.fromisoformat(r[0]) for r in rows[1:]]
    returns = {t: [float(r[i + 1]) for r in rows[1:]] for i, t in enumerate(tickers)}
    return days, tickers, returns


def ingest(mem: LocalLiansClient) -> int:
    n = 0
    with open(DATA / "notes.jsonl", encoding="utf-8") as f:
        for line in f:
            note = json.loads(line)
            meta = {"kind": note["kind"], "event_id": note["event_id"]}
            if note["ticker"]:
                meta["symbol"] = note["ticker"]
            mem.add(
                agent_id=AGENT,
                content=note["content"],
                event_time=datetime.fromisoformat(note["event_time"]),
                source="research-desk",
                metadata=meta,
            )
            n += 1
    return n


def decide(memories: list[dict], ticker: str, decision_day: date) -> tuple[int, list[dict]]:
    """Score retrieved notes -> position in {-1, 0, +1}. Returns (position, used_notes)."""
    score = 0.0
    used = []
    for m in memories:
        if (m.get("metadata") or {}).get("symbol") != ticker:
            continue
        pol = polarity(m["content"] or "")
        if pol == 0:
            continue
        note_time = _aware(m["event_time"])
        delta_days = abs((note_time.date() - decision_day).days)
        weight = math.exp(-math.log(2) * delta_days / PROXIMITY_HALF_LIFE_DAYS)
        score += pol * weight
        used.append(m)
    pos = 1 if score > POSITION_THRESHOLD else (-1 if score < -POSITION_THRESHOLD else 0)
    return pos, used


def run_backtest(mem: LocalLiansClient, days: list[date], tickers: list[str],
                 returns: dict[str, list[float]], honest: bool):
    """One pass over the trading days. Returns (daily_returns, receipts)."""
    daily: list[float] = []
    receipts: list[dict] = []

    for i in range(1, len(days)):
        d = days[i]
        decision_time = datetime.combine(days[i - 1], time(21, 0), tzinfo=timezone.utc)
        month = d.strftime("%B")
        day_pnl, n_pos = 0.0, 0

        for t in tickers:
            query = f"{t} earnings outlook {month}"
            if honest:
                res = mem.recall_at(agent_id=AGENT, query=query, as_of=decision_time, k=K)
            else:
                res = mem.recall(agent_id=AGENT, query=query, k=K)  # present-time = naive store

            pos, used = decide(res.get("memories", []), t, d)

            if not honest:
                for m in used:
                    note_time = _aware(m["event_time"])
                    if note_time > decision_time:
                        receipts.append({
                            "decision_time": decision_time.isoformat(),
                            "ticker": t,
                            "retrieved_note": (m["content"] or "")[:110],
                            "note_event_time": m["event_time"],
                            "days_in_future": round(
                                (note_time - decision_time).total_seconds() / 86400, 1),
                            "position_taken": pos,
                            "next_day_return": returns[t][i],
                        })

            if pos != 0:
                day_pnl += pos * returns[t][i]
                n_pos += 1

        daily.append(day_pnl / n_pos if n_pos else 0.0)

    return daily, receipts


def sharpe(daily: list[float]) -> float:
    if not daily:
        return 0.0
    mu = sum(daily) / len(daily)
    var = sum((r - mu) ** 2 for r in daily) / max(len(daily) - 1, 1)
    sd = math.sqrt(var)
    return (mu / sd) * math.sqrt(252) if sd > 0 else 0.0


def equity(daily: list[float]) -> list[float]:
    curve, level = [1.0], 1.0
    for r in daily:
        level *= 1 + r
        curve.append(level)
    return curve


def max_drawdown(curve: list[float]) -> float:
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


# ── chart ────────────────────────────────────────────────────────────────────

SURFACE = "#fcfcfb"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
RED = "#e34948"    # contaminated
BLUE = "#2a78d6"   # honest


def plot(days: list[date], curves: dict[str, list[float]], sharpes: dict[str, float]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    x = [days[0] - timedelta(days=1)] + days[1:]  # equity has a leading 1.0
    fig, ax = plt.subplots(figsize=(10.4, 5.4), dpi=160)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    ax.plot(x, curves["benchmark"], lw=1.6, ls=(0, (4, 3)), color=MUTED, zorder=2,
            label="Buy & hold (equal weight)")
    ax.plot(x, curves["honest"], lw=2, color=BLUE, zorder=3,
            label=f"Honest — as-of recall · Sharpe {sharpes['honest']:.1f}")
    ax.plot(x, curves["contaminated"], lw=2, color=RED, zorder=4,
            label=f"Contaminated — naive recall · Sharpe {sharpes['contaminated']:.1f}")

    for key, label in (("contaminated", "contaminated"), ("honest", "honest"),
                       ("benchmark", "buy & hold")):
        ax.annotate(f" {label}", (x[-1], curves[key][-1]), xytext=(4, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK_2)

    ax.set_title("Same strategy, same data, same memory store — one parameter apart",
                 loc="left", fontsize=13, color=INK, pad=18, fontweight="bold")
    ax.text(0, 1.02, "Growth of $1 · retrieval pinned to decision time (as_of) vs "
                     "present-time retrieval over the full history",
            transform=ax.transAxes, fontsize=9.5, color=INK_2)

    ax.yaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.2f}")
    ax.legend(loc="upper left", frameon=False, fontsize=9.5, labelcolor=INK_2)
    ax.margins(x=0.06)

    fig.tight_layout()
    fig.savefig(RESULTS / "equity_curves.png", facecolor=SURFACE, bbox_inches="tight")
    print(f"chart  -> {RESULTS / 'equity_curves.png'}")


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    days, tickers, returns = load_prices()

    print("ingesting notes into Lians (local, in-memory) ...")
    with LocalLiansClient(namespace="lookahead-demo") as mem:
        n = ingest(mem)
        print(f"  {n} notes ingested")

        print("run 1/2: CONTAMINATED (present-time recall — the naive store) ...")
        dirty, receipts = run_backtest(mem, days, tickers, returns, honest=False)
        print(f"  {len(receipts)} future-information retrievals recorded")

        print("run 2/2: HONEST (recall_at pinned to decision time) ...")
        clean, honest_receipts = run_backtest(mem, days, tickers, returns, honest=True)
        assert not honest_receipts

        # the kicker: Lians flags the contamination programmatically
        checkpoint = datetime.combine(days[len(days) // 2], time(21, 0), tzinfo=timezone.utc)
        report = mem.backtest_check(agent_id=AGENT, simulation_as_of=checkpoint)

    bench = [sum(returns[t][i] for t in tickers) / len(tickers) for i in range(1, len(days))]
    curves = {"contaminated": equity(dirty), "honest": equity(clean), "benchmark": equity(bench)}
    sharpes = {k: sharpe(v) for k, v in
               (("contaminated", dirty), ("honest", clean), ("benchmark", bench))}

    # ── receipts ────────────────────────────────────────────────────────────
    with open(RESULTS / "receipts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(receipts[0].keys()))
        w.writeheader()
        w.writerows(receipts)

    lines = [
        "# Receipts — future information retrieved during the contaminated run\n",
        f"Every row is a memory retrieved at decision time that **did not exist yet**. "
        f"Total: **{len(receipts)}** contaminated retrievals across "
        f"{len(days) - 1} decision days.\n",
        "| decision time | ticker | retrieved note (created later) | note timestamp | days in future | position | next-day return |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for r in receipts[:20]:
        lines.append(
            f"| {r['decision_time'][:10]} | {r['ticker']} | {r['retrieved_note']} "
            f"| {r['note_event_time'][:10]} | {r['days_in_future']} "
            f"| {r['position_taken']:+d} | {r['next_day_return'] * 100:+.1f}% |")
    if len(receipts) > 20:
        lines.append(f"\n…and {len(receipts) - 20} more — see `receipts.csv`.")
    (RESULTS / "receipts.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── summary ─────────────────────────────────────────────────────────────
    def total(curve: list[float]) -> str:
        return f"{(curve[-1] - 1) * 100:+.1f}%"

    flags = report.get("flags", [])
    n_flags = len(flags)
    n_future = sum(1 for f in flags if f["contamination_type"] == "future_event")
    n_late = n_flags - n_future
    sample = next((f for f in flags if f["contamination_type"] == "future_event"),
                  flags[0] if flags else {})
    summary = f"""# Results

| run | retrieval | total return | Sharpe | max drawdown |
|---|---|---:|---:|---:|
| **Contaminated** | `recall()` — present-time, full history visible | {total(curves['contaminated'])} | **{sharpes['contaminated']:.1f}** | {max_drawdown(curves['contaminated']) * 100:.1f}% |
| **Honest** | `recall_at(as_of=decision_time)` | {total(curves['honest'])} | {sharpes['honest']:.1f} | {max_drawdown(curves['honest']) * 100:.1f}% |
| Buy & hold | — | {total(curves['benchmark'])} | {sharpes['benchmark']:.1f} | {max_drawdown(curves['benchmark']) * 100:.1f}% |

Contaminated retrievals: **{len(receipts)}** (see `receipts.md` / `receipts.csv`).

## The programmatic proof: `backtest_check()`

Lians ships a contamination detector. One call, before you trust any backtest:

```python
report = mem.backtest_check(agent_id="{AGENT}", simulation_as_of=checkpoint)
```

Against the simulation checkpoint {checkpoint.date()} it returned:

```
memories_checked   = {report.get('memories_checked')}
flags              = {n_flags}   ({n_future} future_event, {n_late} late_revision)
contamination_rate = {report.get('contamination_rate')}
is_clean           = {report.get('is_clean')}
```

`future_event` flags are the leak this demo trades on: the underlying event
had not happened yet at the checkpoint. `late_revision` flags mark memories
whose event is old but whose *ingestion* postdates the checkpoint — in this
replayed demo that is every note (we ingested the whole history today), which
is exactly what a replayed backtest looks like and why the detector treats a
replay as contaminated until retrieval is pinned with `as_of`.

Example flag ({sample.get('contamination_type', '-')}):

```
{json.dumps({k: sample.get(k) for k in ('contamination_type', 'event_time', 'delta_days', 'content_preview')}, indent=2)}
```

The same `as_of` machinery that fixes the backtest is the audit answer:
"what did the system know at decision time?" is the examiner's question too.
"""
    (RESULTS / "summary.md").write_text(summary, encoding="utf-8")

    with open(RESULTS / "equity_curves.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "contaminated", "honest", "benchmark"])
        xs = [days[0] - timedelta(days=1)] + days[1:]
        for i, d in enumerate(xs):
            w.writerow([d.isoformat()] + [f"{curves[k][i]:.6f}"
                                          for k in ("contaminated", "honest", "benchmark")])

    plot(days, curves, sharpes)

    print()
    print(f"contaminated: {total(curves['contaminated'])}  Sharpe {sharpes['contaminated']:.2f}")
    print(f"honest:       {total(curves['honest'])}  Sharpe {sharpes['honest']:.2f}")
    print(f"benchmark:    {total(curves['benchmark'])}  Sharpe {sharpes['benchmark']:.2f}")
    print(f"receipts: {len(receipts)} contaminated retrievals")
    print(f"backtest_check: {n_flags} flags, is_clean={report.get('is_clean')}")
    print(f"results -> {RESULTS}")


if __name__ == "__main__":
    main()
