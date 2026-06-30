"""
Adapters that map each memory product onto the regulated-eval harness interface.

The harness (`benchmarks.regulated_eval.run_regulated_eval`) calls six primitives:

    add(agent, content, event_time, *, metadata=None, subject_id=None)
    recall(agent, query, *, k) -> {"memories": [{"content": ...}, ...]}
    recall_at(agent, query, as_of, *, k) -> {"memories": [...]}     # bitemporal as-of
    erase(subject_id, reason)                                        # provable shred
    backtest_check(agent, simulation_date) -> {"is_clean", "flags"}  # lookahead guard
    snapshot(agent, as_of) -> {"total": int}                         # audit reconstruction

Lians implements all six natively (see sdk/python LocalLiansClient).

mem0 and Zep do NOT expose all six. Their adapters below map each primitive to the
*real* public SDK call where one exists, and raise `CapabilityAbsent` where the
product has no such primitive. A thrown invariant is scored as a failure by the
harness — which is the literal truth: there is no API to call.

To run a competitor LIVE, install its SDK and export its key; the adapter will use
the real client. With no SDK/key present, the adapter still declares its capability
map (CAPABILITIES) so the comparison is reproducible and auditable without secrets.
"""
from __future__ import annotations


class CapabilityAbsent(NotImplementedError):
    """Raised when a product has no primitive for a regulated-eval invariant."""


# Per-invariant capability, derived from each product's public API surface.
#   "pass"    — a real primitive satisfies the invariant
#   "partial" — best-effort / non-deterministic / no proof artifact
#   "absent"  — no primitive exists; the call cannot be made
PASS, PARTIAL, ABSENT = "pass", "partial", "absent"

# Numeric weight per capability state for the comparison score.
SCORE = {PASS: 1.0, PARTIAL: 0.5, ABSENT: 0.0}
