"""Render the vendor-neutral RIAD-1 comparison and machine-readable receipt.

Lians is executed end-to-end. Competitor cells are capability assessments
against public product surfaces unless a future adapter records an executed
receipt. The output deliberately distinguishes PASS, PARTIAL, N/A, and NOT RUN.

    python agentmem/benchmarks/riad_comparison.py
    python agentmem/benchmarks/riad_comparison.py --write
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from decision_reconstruction_eval import run_benchmark


ROOT = Path(__file__).resolve().parents[2]
RESULT_JSON = ROOT / "docs" / "benchmarks" / "riad-1-results.json"
RESULT_MD = ROOT / "docs" / "benchmarks" / "riad-1-comparison.md"

PASS = "pass"
PARTIAL = "partial"
NA = "not_available"
NOT_RUN = "not_run"

CHECKS = [
    ("exact_point_in_time_reconstruction", "Decision-time reconstruction"),
    ("provenance_coverage_100_percent", "Required decision provenance"),
    ("evidence_pack_hash_valid", "Hashed evidence-pack export"),
    ("otlp_genai_span_accepted", "Authenticated GenAI OTLP ingestion"),
    ("audit_tamper_detected", "Audit-payload tamper detection"),
    ("local_p95_under_3000_ms", "Evidence-pack replay latency"),
]


def _cell(status: str, evidence: str, mode: str = "capability_assessed") -> dict[str, str]:
    return {"status": status, "evidence": evidence, "mode": mode}


COMPETITORS: dict[str, dict[str, dict[str, str]]] = {
    "Mem0 OSS": {
        "exact_point_in_time_reconstruction": _cell(
            NA,
            "No OSS as-of recall or exhaustive historical decision snapshot API.",
        ),
        "provenance_coverage_100_percent": _cell(
            NA, "No consequential-decision record with model, policy, cutoff, and cited evidence."
        ),
        "evidence_pack_hash_valid": _cell(NA, "No decision evidence-pack export API."),
        "otlp_genai_span_accepted": _cell(NA, "No authenticated OTLP trace receiver."),
        "audit_tamper_detected": _cell(NA, "No append-only payload hash-chain verifier."),
        "local_p95_under_3000_ms": _cell(
            NOT_RUN, "Not comparable because the evidence-pack operation is unavailable."
        ),
    },
    "Graphiti OSS": {
        "exact_point_in_time_reconstruction": _cell(
            PARTIAL,
            "Bitemporal graph edges support historical graph state, but not a complete "
            "decision-time evidence boundary or cited decision snapshot.",
        ),
        "provenance_coverage_100_percent": _cell(
            NA, "No first-class consequential-decision record with the RIAD provenance contract."
        ),
        "evidence_pack_hash_valid": _cell(NA, "No decision evidence-pack export API."),
        "otlp_genai_span_accepted": _cell(NA, "No authenticated OTLP trace receiver."),
        "audit_tamper_detected": _cell(NA, "No append-only payload hash-chain verifier."),
        "local_p95_under_3000_ms": _cell(
            NOT_RUN, "Not comparable because the evidence-pack operation is unavailable."
        ),
    },
    "Letta": {
        "exact_point_in_time_reconstruction": _cell(
            NA, "Passage search has no as-of validity filter or exhaustive historical snapshot."
        ),
        "provenance_coverage_100_percent": _cell(
            NA, "No RIAD-compatible consequential-decision record and cited-evidence contract."
        ),
        "evidence_pack_hash_valid": _cell(NA, "No decision evidence-pack export API."),
        "otlp_genai_span_accepted": _cell(NA, "No authenticated OTLP trace receiver."),
        "audit_tamper_detected": _cell(NA, "No append-only payload hash-chain verifier."),
        "local_p95_under_3000_ms": _cell(
            NOT_RUN, "Not comparable because the evidence-pack operation is unavailable."
        ),
    },
}

SOURCES = {
    "Mem0 OSS": "https://github.com/mem0ai/mem0",
    "Graphiti OSS": "https://github.com/getzep/graphiti",
    "Letta": "https://docs.letta.com/api/typescript/resources/agents/subresources/passages",
}
for _system_name, _cells in COMPETITORS.items():
    for _capability in _cells.values():
        _capability["source"] = SOURCES[_system_name]


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


async def build_report(repetitions: int = 3) -> dict[str, Any]:
    lians_run = await run_benchmark(repetitions=repetitions)
    lians = {
        key: _cell(
            PASS if lians_run["checks"][key] else "fail",
            "Executed by RIAD-1 against the public Lians API on ephemeral SQLite.",
            mode="executed",
        )
        for key, _ in CHECKS
    }
    for cell in lians.values():
        cell["source"] = (
            "https://github.com/Lians-ai/Lians/blob/master/"
            "agentmem/benchmarks/decision_reconstruction_eval.py"
        )
    systems = {"Lians": lians, **COMPETITORS}
    return {
        "schema": "https://lians.ai/schemas/riad-comparison/v1",
        "benchmark": "RIAD-1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _commit(),
        "systems": systems,
        "lians_metrics": lians_run["metrics"],
        "methodology": {
            "executed": ["Lians"],
            "capability_assessed": list(COMPETITORS),
            "rule": "N/A means the public product surface lacks the operation; it is not a failed live call.",
        },
    }


GLYPH = {PASS: "PASS", PARTIAL: "PARTIAL", NA: "N/A", NOT_RUN: "NOT RUN", "fail": "FAIL"}


def render_markdown(report: dict[str, Any]) -> str:
    names = list(report["systems"])
    lines = [
        "# RIAD-1 comparison",
        "",
        f"_Generated from commit `{report['source_commit']}` at {report['generated_at']}._",
        "",
        "| Check | " + " | ".join(names) + " |",
        "|---|" + "|".join([":--:"] * len(names)) + "|",
    ]
    for key, label in CHECKS:
        values = [GLYPH[report["systems"][name][key]["status"]] for name in names]
        lines.append("| " + " | ".join([label, *values]) + " |")
    lines.extend(
        [
            "",
            "**Evidence mode:** Lians was executed end-to-end. Mem0 OSS, Graphiti OSS, "
            "and Letta were capability-assessed against their public product surfaces. "
            "N/A means the RIAD operation is not exposed; NOT RUN means a latency number "
            "would be meaningless without that operation.",
            "",
            "## Per-cell evidence",
            "",
        ]
    )
    for name, cells in report["systems"].items():
        lines.append(f"### {name}")
        lines.append("")
        for key, label in CHECKS:
            cell = cells[key]
            lines.append(
                f"- **{label} — {GLYPH[cell['status']]} ({cell['mode']}):** "
                f"{cell['evidence']} [Source]({cell['source']})"
            )
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    report = await build_report(repetitions=args.repetitions)
    rendered = render_markdown(report)
    print(rendered)
    if args.write:
        RESULT_JSON.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        RESULT_MD.write_text(rendered + "\n", encoding="utf-8")
        print(f"\nwrote {RESULT_JSON}")
        print(f"wrote {RESULT_MD}")


if __name__ == "__main__":
    asyncio.run(main())
