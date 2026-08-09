"""Render sanitized evidence from the frozen installed-cache latency measurement.

This module does not execute Codex, a model, the network, or a retrieval benchmark. It
recomputes deterministic summaries from the 20 recorded timing pairs so the checked-in
evidence can be independently audited.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPORT_DATE = "2026-08-08"
PLUGIN_VERSION = "0.1.0+codex.20260809022314"
PREWARM_WALL_MS = 6871.190

# Sequential fresh-process observations after one prewarm of a shared local daemon.
TIMING_PAIRS_MS: tuple[tuple[float, int], ...] = (
    (1139.624, 581),
    (1045.945, 549),
    (1137.620, 596),
    (1051.287, 531),
    (1150.947, 610),
    (1187.182, 645),
    (1055.058, 543),
    (1072.905, 569),
    (1054.731, 547),
    (1051.788, 543),
    (1034.522, 545),
    (1149.264, 595),
    (1130.178, 599),
    (1119.984, 559),
    (1197.135, 631),
    (1057.966, 536),
    (1072.782, 545),
    (1111.982, 554),
    (1074.222, 531),
    (1057.550, 563),
)

_PUBLICATION_DENYLIST = (
    re.compile(r"(?i)\b[a-z]:[\\/]"),
    re.compile(r"(?i)(?:/home/|/users/)"),
    re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token|authorization)\s*[=:]"),
)


def _percentile_ms(values: Sequence[int | float], quantile: Decimal) -> float:
    """Return a Type-7 percentile rounded half-up to three decimal places."""

    if not values:
        raise ValueError("at least one timing value is required")
    ordered = sorted(Decimal(str(value)) for value in values)
    rank = Decimal(len(ordered) - 1) * quantile
    lower_index = math.floor(rank)
    upper_index = math.ceil(rank)
    fraction = rank - Decimal(lower_index)
    value = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction
    return float(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def summarize_ms(values: Sequence[int | float]) -> dict[str, object]:
    """Summarize timings using the report's declared percentile method."""

    if not values:
        raise ValueError("at least one timing value is required")
    return {
        "p50_ms": _percentile_ms(values, Decimal("0.50")),
        "p95_ms": _percentile_ms(values, Decimal("0.95")),
        "max_ms": max(values),
    }


def publication_safety_violations(payload: object) -> list[str]:
    """Return redaction-rule names triggered by a rendered publication payload."""

    serialized = json.dumps(payload, ensure_ascii=False)
    return [pattern.pattern for pattern in _PUBLICATION_DENYLIST if pattern.search(serialized)]


def build_report() -> dict[str, object]:
    """Build the deterministic, publication-safe latency evidence record."""

    sample_count = len(TIMING_PAIRS_MS)
    wall_ms = [pair[0] for pair in TIMING_PAIRS_MS]
    receipt_ms = [pair[1] for pair in TIMING_PAIRS_MS]
    report: dict[str, object] = {
        "schema": "lians.codex-installed-cache-latency-evidence.v1",
        "report_date": REPORT_DATE,
        "workload": {
            "id": "locomo-conversation-0-bge-onnx",
            "plugin_version": PLUGIN_VERSION,
            "memories": 419,
            "live_facts": 419,
            "operating_system": "Windows",
            "storage_and_embedding": "local SQLite and BGE ONNX",
        },
        "methodology": {
            "measurement_surface": "installed-cache hook entry point invoked directly",
            "prewarm_processes": 1,
            "fresh_hook_processes": sample_count,
            "execution": "sequential",
            "shared_prewarmed_daemon": True,
            "percentile_method": "Type 7 linear interpolation, h=(n-1)*p",
            "model_calls": 0,
            "network_calls": 0,
        },
        "installed_cache_run": {
            "prewarm_wall_ms": PREWARM_WALL_MS,
            "timing_pairs_ms": [
                {"hook_process_wall_ms": wall, "receipt_elapsed_ms": receipt}
                for wall, receipt in TIMING_PAIRS_MS
            ],
            "hook_process_wall": summarize_ms(wall_ms),
            "receipt_elapsed": summarize_ms(receipt_ms),
            "quality_gates": {
                "samples": sample_count,
                "injected": sample_count,
                "daemon_transport": sample_count,
                "non_degraded": sample_count,
                "same_top_evidence": sample_count,
                "all_passed": True,
            },
        },
        "wrapper_only_microbenchmark": {
            "separate_from_installed_cache_run": True,
            "scope": "wrapper, fixed native Python startup, and no-op temporary launcher",
            "method": "20 interleaved samples per wrapper",
            "real_plugin_profile_database_daemon_model_or_network_access": False,
            "cmd_ms": {"p50_ms": 94.684, "p95_ms": 102.885},
            "powershell_ms": {"p50_ms": 327.479, "p95_ms": 403.875},
        },
        "claim_boundary": {
            "supported": (
                "For this 419-memory sequential installed-cache workload, after one measured "
                "prewarm, all 20 fresh hook processes injected the same top evidence through "
                "the daemon without degraded retrieval; the reported wall and receipt timings "
                "describe only those hook invocations."
            ),
            "not_measured": [
                "overall model or agent response time",
                "concurrent load",
                "disk-cold startup or retrieval",
                "public-install end-to-end plugin loading",
                "other workloads, machines, models, or prompts",
            ],
            "universal_claim_supported": False,
        },
        "limitations": {
            "operating_system_page_cache_flushed": False,
            "machine_isolation_controlled": False,
            "same_identity_daemon_present_before_prewarm": False,
            "other_local_background_processes_present": True,
            "fresh_hook_processes_shared_one_deliberately_prewarmed_daemon": True,
        },
    }
    violations = publication_safety_violations(report)
    if violations:
        raise ValueError(f"publication-safety validation failed: {violations}")
    return report


def render_report() -> str:
    """Serialize the evidence deterministically."""

    return json.dumps(build_report(), indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    rendered = render_report()
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
