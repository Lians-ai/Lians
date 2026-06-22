# agentmem-backtest-check

**Detect lookahead bias in AI agent backtests. Zero dependencies.**

When you run a backtest of an AI agent, there is a subtle danger: the agent's memory may contain facts it couldn't have known at the simulated point in time. This is lookahead bias, and it makes backtest results unreliable.

There are two classes of contamination:

| Type | Condition | Example |
|------|-----------|---------|
| `future_event` | `event_time > simulation_as_of` | Agent has Nov earnings data; backtest is "as of" Sep |
| `late_revision` | `event_time ≤ as_of` AND `ingestion_time > as_of` | Revenue figure was restated 3 days after the simulated date; agent has the restated number |

The `late_revision` case is the subtle one. Pure vector stores miss it entirely — they only index *when an event happened*, not *when a revised version of that event arrived*.

---

## Install

```bash
pip install agentmem-backtest-check
```

No dependencies. Works on Python 3.9+.

---

## Quick start

```python
from agentmem_backtest_check import check_contamination
from datetime import datetime, timezone

memories = [
    {
        "id": "m1",
        "content": "NVDA Q3 2025 revenue: $35.1B",
        "event_time":     datetime(2025, 8, 27, tzinfo=timezone.utc),
        "ingestion_time": datetime(2025, 8, 27, tzinfo=timezone.utc),
    },
    {
        "id": "m2",
        "content": "NVDA FY2026 guidance raised to $40B",
        "event_time":     datetime(2025, 11, 19, tzinfo=timezone.utc),
        "ingestion_time": datetime(2025, 11, 19, tzinfo=timezone.utc),
    },
    {
        "id": "m3",
        "content": "Fed funds rate cut to 5.00–5.25% (Sep 2025 FOMC)",
        "event_time":     datetime(2025, 9, 18, tzinfo=timezone.utc),
        "ingestion_time": datetime(2025, 9, 18, tzinfo=timezone.utc),
    },
]

# Simulate a backtest "as of" September 1, 2025
result = check_contamination(
    memories,
    as_of=datetime(2025, 9, 1, tzinfo=timezone.utc),
)

print(result.summary())
# CONTAMINATED — 2/3 memories flagged (66.7%) as of 2025-09-01

for flag in result.flags:
    print(f"[{flag.contamination_type}] +{flag.delta_days:.0f} days: {flag.content_preview}")
# [future_event] +79.0 days: NVDA FY2026 guidance raised to $40B
# [future_event] +17.0 days: Fed funds rate cut to 5.00–5.25% (Sep 2025 FOMC)
```

---

## The subtle case: late revisions

```python
from agentmem_backtest_check import check_contamination, LATE_REVISION
from datetime import datetime, timezone

# Earnings were originally reported on Apr 15; a restatement landed Apr 20.
# Backtest is "as of" Apr 17 — the restated figure didn't exist yet.
memories = [
    {
        "id": "original",
        "content": "WFC Q1 revenue: $20.1B",
        "event_time":     datetime(2025, 4, 15, tzinfo=timezone.utc),
        "ingestion_time": datetime(2025, 4, 15, tzinfo=timezone.utc),
    },
    {
        "id": "restated",
        "content": "WFC Q1 revenue restated to $19.8B",
        "event_time":     datetime(2025, 4, 15, tzinfo=timezone.utc),  # same event date
        "ingestion_time": datetime(2025, 4, 20, tzinfo=timezone.utc),  # arrived 5 days later
    },
]

result = check_contamination(
    memories,
    as_of=datetime(2025, 4, 17, tzinfo=timezone.utc),
)

assert not result.is_clean
flag = result.flags[0]
assert flag.contamination_type == LATE_REVISION    # not future_event — event_time is in the past
assert flag.delta_days == 3.0                      # ingestion was 3 days after as_of
```

---

## Custom field names

If your records use different field names:

```python
result = check_contamination(
    my_records,
    as_of=checkpoint,
    event_time_field="occurred_at",
    ingestion_time_field="received_at",
    id_field="memory_id",
    content_field="text",
)
```

---

## Object-style records

The library accepts both dicts and objects with attributes:

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Memory:
    id: str
    content: str
    event_time: datetime
    ingestion_time: datetime

result = check_contamination(my_memory_objects, as_of=checkpoint)
```

---

## `ContaminationReport` fields

| Field | Type | Description |
|-------|------|-------------|
| `simulation_as_of` | `datetime` | The checkpoint you passed in |
| `memories_checked` | `int` | Total records scanned |
| `flags` | `list[ContaminationFlag]` | One entry per contaminated record |
| `contamination_rate` | `float` | `len(flags) / memories_checked` |
| `is_clean` | `bool` | `True` if zero flags |

## `ContaminationFlag` fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | any | Original record id |
| `event_time` | `datetime` | When the underlying event occurred |
| `ingestion_time` | `datetime` | When this version of the fact arrived |
| `contamination_type` | `str` | `"future_event"` or `"late_revision"` |
| `delta_days` | `float` | Days past the simulation checkpoint |
| `content_preview` | `str \| None` | First 120 chars of content |
| `metadata` | `dict` | Passthrough from the original record |

---

## Why this matters

Lookahead bias invalidates backtests. An AI agent that ingested a revised earnings figure on T+5 and then ran a strategy backtest "as of" T+2 used data it couldn't have seen. Any alpha it appears to generate may be entirely illusory.

This library makes contamination auditable in a single call, with no database or external service required.

---

## Full AgentMem

This package is a thin primitive extracted from [AgentMem](https://github.com/ebeirne/Lian) — a bitemporal memory layer for regulated AI agents. AgentMem adds:

- Bitemporal storage with automatic supersession (old facts can't contaminate recall)
- Point-in-time reconstruction of any agent's full knowledge state
- SEC 17a-4 tamper-evident audit chain
- GDPR crypto-shred erasure (content unrecoverable; audit survives)
- Information barriers (Chinese walls) between agent groups
- MCP server, Python SDK, TypeScript SDK

---

## License

MIT
