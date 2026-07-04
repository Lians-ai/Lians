# Results

| run | retrieval | total return | Sharpe | max drawdown |
|---|---|---:|---:|---:|
| **Contaminated** | `recall()` — present-time, full history visible | +44.0% | **4.6** | -4.3% |
| **Honest** | `recall_at(as_of=decision_time)` | -4.2% | -0.6 | -12.8% |
| Buy & hold | — | +1.4% | 0.4 | -5.4% |

Contaminated retrievals: **918** (see `receipts.md` / `receipts.csv`).

## The programmatic proof: `backtest_check()`

Lians ships a contamination detector. One call, before you trust any backtest:

```python
report = mem.backtest_check(agent_id="strategy", simulation_as_of=checkpoint)
```

Against the simulation checkpoint 2026-04-01 it returned:

```
memories_checked   = 75
flags              = 75   (33 future_event, 42 late_revision)
contamination_rate = 1.0
is_clean           = False
```

`future_event` flags are the leak this demo trades on: the underlying event
had not happened yet at the checkpoint. `late_revision` flags mark memories
whose event is old but whose *ingestion* postdates the checkpoint — in this
replayed demo that is every note (we ingested the whole history today), which
is exactly what a replayed backtest looks like and why the detector treats a
replay as contaminated until retrieval is pinned with `as_of`.

Example flag (future_event):

```
{
  "contamination_type": "future_event",
  "event_time": "2026-04-02T11:00:00",
  "delta_days": 0.58,
  "content_preview": "Desk note: CPI in line with expectations; rates vol subdued."
}
```

The same `as_of` machinery that fixes the backtest is the audit answer:
"what did the system know at decision time?" is the examiner's question too.
