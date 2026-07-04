"""
Regulated-memory eval — head-to-head comparison renderer.

Runs the regulated-eval harness LIVE against Lians and renders a comparison table
against mem0, Zep, Letta, Hindsight, and Supermemory. Lians results are *executed*.
Competitor cells are a capability assessment derived from each product's public API
surface (see benchmarks/adapters/*_adapter.py); with that product's SDK + key
present, its column is executed live instead.

    python -m benchmarks.compare_regulated            # print table
    python -m benchmarks.compare_regulated --write    # also write docs/regulated-eval-results.md

Scoring: pass = 1.0, partial = 0.5, absent/fail = 0.0.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk" / "python"))

from benchmarks.regulated_eval import run_regulated_eval  # noqa: E402
from benchmarks.adapters import PASS, PARTIAL, ABSENT, SCORE  # noqa: E402
from benchmarks.adapters import (  # noqa: E402
    hindsight_adapter,
    letta_adapter,
    mem0_adapter,
    supermemory_adapter,
    zep_adapter,
)

INVARIANTS = [
    ("stale_revision_suppression", "Stale revision suppressed"),
    ("point_in_time_reconstruction", "Point-in-time (as-of) recall"),
    ("erasure_proof", "Provable erasure (crypto-shred + cert)"),
    ("lookahead_contamination_detection", "Lookahead / backtest guard"),
    ("audit_state_reconstruction", "Audit-state snapshot at T"),
]

GLYPH = {PASS: "✅ pass", PARTIAL: "🟡 partial", ABSENT: "❌ absent"}


_STATUS_TO_CAP = {"pass": PASS, "partial": PARTIAL, "fail": ABSENT}


def _lians_live() -> dict[str, str]:
    """Execute the harness against Lians and return per-invariant status."""
    from lians import LocalLiansClient

    with LocalLiansClient() as client:
        report = run_regulated_eval(client)
    return {r["check"]: _STATUS_TO_CAP[r["status"]] for r in report["checks"]}


def _merge_live(static_caps: dict[str, str], report: dict) -> dict[str, str]:
    """
    Merge a live run over the documented capability map — fairly, in both
    directions:

    - A check that RAN gets its live result (pass/partial/fail), whether that
      is better or worse than the documented score.
    - A check that raised CapabilityAbsent carries no new behavioral evidence
      ("no turnkey API" is exactly what the static map already scored), so the
      documented credit is preserved. A live run must never demote a cell for
      the same reason the static map already discounted it.
    """
    merged = {}
    for r in report["checks"]:
        if r.get("capability_absent"):
            merged[r["check"]] = static_caps.get(r["check"], ABSENT)
        else:
            merged[r["check"]] = _STATUS_TO_CAP[r["status"]]
    return merged


def _competitor(adapter_mod) -> tuple[str, dict[str, str], bool | str]:
    """
    Return (name, capability-map, live). `live` is False for a capability
    assessment, or a short string describing exactly what executed.
    """
    name = adapter_mod.NAME
    caps = dict(adapter_mod.CAPABILITIES)
    factory = getattr(adapter_mod, "live_adapter", None)
    if factory is not None:
        adapter, mode = factory()
    else:
        cls = next(v for k, v in vars(adapter_mod).items()
                   if k.endswith("Adapter") and isinstance(v, type))
        adapter = cls()
        adapter = adapter if getattr(adapter, "_client", None) is not None else None
        mode = "live API" if adapter is not None else None
    if adapter is None:
        return name, caps, False
    try:
        rep = run_regulated_eval(adapter)
        return name, _merge_live(caps, rep), (mode or "live")
    except Exception:
        return name, caps, False


def build_table():
    lians = _lians_live()
    columns = [("Lians", lians, "LocalLiansClient — same engine as the server")]
    columns.append(_competitor(zep_adapter))
    columns.append(_competitor(letta_adapter))
    columns.append(_competitor(hindsight_adapter))
    columns.append(_competitor(supermemory_adapter))
    columns.append(_competitor(mem0_adapter))
    return columns


def render_markdown(columns) -> str:
    names = [c[0] for c in columns]
    lines = []
    lines.append("| Regulated invariant | " + " | ".join(names) + " |")
    lines.append("|---|" + "|".join([":--:"] * len(names)) + "|")
    for key, label in INVARIANTS:
        row = [label]
        for _, caps, _ in columns:
            row.append(GLYPH[caps.get(key, ABSENT)])
        lines.append("| " + " | ".join(row) + " |")
    score_row = ["**Score (pass=1, partial=½)**"]
    for _, caps, _ in columns:
        s = sum(SCORE[caps.get(k, ABSENT)] for k, _ in INVARIANTS)
        score_row.append(f"**{s:.1f} / {len(INVARIANTS)}**")
    lines.append("| " + " | ".join(score_row) + " |")
    return "\n".join(lines)


def render_doc(columns) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    table = render_markdown(columns)
    exec_note = ", ".join(
        f"{n} (executed live: {live})" if live else f"{n} (capability-assessed)"
        for n, _, live in columns)
    return f"""# Regulated-memory eval — head-to-head results

_Generated by `python -m benchmarks.compare_regulated --write` on {ts}._

General memory benchmarks (LoCoMo, LongMemEval) measure conversational recall. They
do **not** measure what a regulated buyer must guarantee. This eval scores five hard
invariants that an accumulate-everything store fails *by design*. Each is a turnkey
primitive: either the product has an API that satisfies it, or it does not.

{table}

**Run status:** {exec_note}.

## What each invariant means

1. **Stale revision suppressed** — after a fact is revised, the superseded value must
   not be retrieved. Lians does this with deterministic keyed supersession; competitors
   attempt it via LLM/graph/reflection invalidation (non-deterministic, extraction-dependent).
2. **Point-in-time (as-of) recall** — recall *as it was known* on a past date. Requires
   a bitemporal model with an as-of query primitive.
3. **Provable erasure** — erased content is cryptographically unrecoverable and the
   system emits an erasure certificate (GDPR Art. 17 / HIPAA). Deleting a row is not
   the same as proving it is gone.
4. **Lookahead / backtest guard** — facts unknowable at a simulation date are flagged,
   so a backtest or model isn't contaminated by future information.
5. **Audit-state snapshot at T** — the full knowledge state at any past time is
   reproducible for an examiner.

A sixth invariant — **barrier-group (Chinese-wall) leakage** — is verified separately
against PostgreSQL Row-Level Security with a non-superuser role
(`agentmem/tests/test_pgvector.py`), because it is a database-layer guarantee, not an
application-API call.

## Methodology & honesty notes

- **Lians is executed live** by this script against `LocalLiansClient` (the same
  bitemporal/audit engine as the server). Its score is a real test run, not a claim.
- **Competitor columns execute live when credentials permit.** With
  `OPENAI_API_KEY` set, the mem0 column runs **mem0 OSS in its default
  documented configuration** (`Memory()`, OpenAI LLM + embeddings) and the
  Zep/Graphiti column runs **Graphiti OSS in its default configuration**
  (OpenAI LLM/embeddings/reranker, embedded Kuzu) — the self-hosted
  deployments a regulated buyer would actually evaluate, configured exactly
  as their own quickstarts configure them. Remaining columns are scored from
  documented capabilities encoded in `benchmarks/adapters/*_adapter.py`,
  with a one-line justification per cell.
- **Live merges are fair in both directions.** A check that *runs* gets its
  live result — better or worse than the documented score. A check that
  raises `CapabilityAbsent` ("no turnkey API") carries no new behavioral
  evidence, so the documented credit (e.g. Graphiti's partial for temporal
  edge filtering) is preserved — a live run never zeroes a cell for the same
  reason the static map already discounted it.
- **A full erasure pass requires the proof artifact.** Behavioral deletion
  (content stops being retrievable) scores *partial*; *pass* additionally
  requires an erasure certificate / request reference. This applies to Lians
  and competitors identically.
- **mem0's temporal parameters are Platform-only.** mem0 2.x exposes
  `timestamp` and `reference_date` in its SDK signatures, but its own
  docstrings mark them "Platform-only temporal parameter. Not supported in
  OSS" — the OSS as-of cell reflects that, per mem0's documentation.
- **Anyone can re-run any column.** Install the competitor SDK, export the
  relevant key (`OPENAI_API_KEY` for the OSS columns; `MEM0_API_KEY` /
  `ZEP_API_KEY` / `LETTA_API_KEY` / `HINDSIGHT_API_URL` /
  `SUPERMEMORY_API_KEY` for hosted APIs), and re-run — the adapter switches
  from the static capability map to live execution and overwrites that column.
- **Competitors are credited where they're strong.** Zep's bitemporal graph earns
  partials on temporal recall and stale-edge invalidation; mem0's LLM fact management
  earns a partial on supersession; Letta's agent-driven memory edits earn a partial on
  supersession; Hindsight's timestamped retain + temporal retrieval earns a partial on
  point-in-time recall and its belief revision a partial on supersession; Supermemory's
  profile consolidation earns a partial on supersession. This is not a strawman — the
  gaps are the compliance primitives (provable erasure, lookahead guard, audit
  snapshot) that the dev-memory, agent-memory, and temporal-graph lanes were never
  built to provide. Hindsight is the only column with **no deletion API at all**, so
  its erasure cell is absent rather than partial.

## Reproduce

```bash
cd agentmem
python -m benchmarks.compare_regulated            # print the table
python -m benchmarks.compare_regulated --write    # regenerate this file

# live OSS columns (mem0 OSS + Graphiti OSS, default configs — one key):
pip install mem0ai graphiti-core kuzu
export OPENAI_API_KEY=...                                # then re-run

# optional hosted-API columns:
pip install mem0ai && export MEM0_API_KEY=...            # mem0 Platform
pip install zep-cloud && export ZEP_API_KEY=...          # Zep Cloud
pip install letta-client && export LETTA_API_KEY=...     # then re-run
pip install hindsight-client && export HINDSIGHT_API_URL=...  # then re-run
pip install supermemory && export SUPERMEMORY_API_KEY=...     # then re-run
```
"""


def main() -> None:
    # Windows consoles default to cp1252, which can't print the glyphs.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    columns = build_table()
    print(render_markdown(columns))
    print()
    for name, _, live in columns:
        print(f"  {name}: " + (f"executed live: {live}" if live
                               else "capability-assessed from public API"))
    if "--write" in sys.argv:
        out = ROOT.parent / "docs" / "regulated-eval-results.md"
        out.write_text(render_doc(columns), encoding="utf-8")
        print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
