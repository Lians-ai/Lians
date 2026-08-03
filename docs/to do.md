Historical implementation note. The two gaps recorded below have since been
closed in the current Lians tree; this file is retained only as provenance for
the Governor integration work.

Gap claims at the time — both now resolved

- REFINE/narrowing relation: resolved by the first-class `REFINES` relation in `supersession.py`.
- Vagueness pre-filter: resolved by `admission.is_too_vague`, the `vague` risk tag, and enforce-mode rejection.

The original claims no longer describe the current product surface.

Phase 1 — done and verified

Extracted a MemoryStore protocol so the engine is backend-pluggable (the prerequisite for a LiansStore). Four files changed in the Governor repo:

┌─────────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│      File       │                                                     Change
├─────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ memory.py       │ _episode_event_time no longer reaches into store.connection; Memory.store typed as MemoryStore                 │
├─────────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ audit.py        │ append_event/verify_chain typed against MemoryStore                                                            │
└─────────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

Verification: ruff clean; 74 tests pass, 0 regressions. The 3 audit-chain failures fail identically on the pristine checkout — I stashed my changes and confirmed they pre-exist my work.

Committed on branch phase1-memorystore-protocol (8ca9f8f). Since it's your partner's separate GitHub repo (and a session-scratch clone I can't push from), I exported it as a patch:

- C:\Users\jedie\Downloads\governor-phase1-memorystore.patch — your partner applies with git am < governor-phase1-memorystore.patch on their side.

One thing worth your attention

While testing I found a real pre-existing bug: the Governor's list_audit_events orders by created_at, id, so on Windows (coarse timestamp resolution) same-tick events tie-break on a random UUID, scrambling insertion order and breaking hash-chain verification. That's the product's headline tamper-evidence feature failing. It's out of scope for Phase 1 (not caused by my change), but it should be an early fix — order by an insertion sequence, not wall-clock time. I flagged it in the commit message and saved it to memory.

Next

Phase 2 — the LiansStore bridge: implement this same protocol against your production engine, then run the Governor's HTTP/MCP surface against real Lians data as the demo you put in front of your partner. Want me to start it? It depends on the still-open "monorepo vs separate repo" decision, so if you'd rather settle that first, that's the fork.
