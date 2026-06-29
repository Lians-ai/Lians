---
description: Run a Lians compliance/audit operation — snapshot, chain verify, backtest, or erasure
argument-hint: snapshot <agent> <date> | verify | backtest <agent> <date> | erase <subject> <ref>
---

# /lians-audit

Run the compliance surfaces that make Lians defensible to a regulator, examiner,
or opposing counsel. Parse the operation from **$ARGUMENTS** and execute the
matching call. Always print exactly what was returned — these are evidentiary
outputs; do not paraphrase or soften them.

## Operations

### `snapshot <agent_id> <YYYY-MM-DD>`
Exhaustive knowledge-state reconstruction — *every* fact valid at that instant,
not a ranked top-k. The one-call demo for "show me everything the agent knew on
2025-03-14."
```python
snap = mem.snapshot(agent_id="<agent>", as_of=datetime(...))
print(snap["total"], "facts valid at", snap["as_of"])
```

### `verify`
Verify the SEC 17a-4 tamper-evident SHA-256 hash chain. A broken link means the
audit log was altered.
```python
print(mem.verify_chain())   # {"status": "ok", "rows_checked": N, "violations": []}
```

### `backtest <agent_id> <YYYY-MM-DD>`
Lookahead-bias detection — flags any fact the agent held that it could not have
known at the simulation date. A clean report is the proof a risk committee needs.
```python
r = mem.backtest_check(agent_id="<agent>", simulation_as_of=datetime(...))
print("clean" if r["is_clean"] else f"{len(r['flags'])} contamination flags")
```

### `erase <subject_id> <request_ref>`
GDPR Art. 17 / HIPAA crypto-shred. Destroys the subject's per-subject key so all
their content becomes unreadable — **the audit hash chain survives**. This is
irreversible. Confirm with the user before running, and record the `request_ref`.
```python
print(mem.erase(subject_id="<subject>", request_ref="<ref>"))
```

If the operation isn't recognized, list these four and ask which one.
