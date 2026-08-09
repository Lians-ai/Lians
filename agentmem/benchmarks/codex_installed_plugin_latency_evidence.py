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

REPORT_DATE = "2026-08-09"
PLUGIN_VERSION = "0.1.0+codex.20260809040627"
PREWARM_WALL_MS = 6090.554

# Sequential fresh-process observations after one prewarm of a shared local daemon.
TIMING_PAIRS_MS: tuple[tuple[float, int], ...] = (
    (985.761, 583),
    (979.816, 634),
    (900.277, 592),
    (952.490, 628),
    (959.412, 641),
    (912.003, 580),
    (955.570, 628),
    (1069.069, 693),
    (1038.770, 669),
    (961.916, 630),
    (941.582, 598),
    (979.407, 652),
    (959.252, 644),
    (911.507, 591),
    (996.538, 645),
    (965.297, 632),
    (1109.455, 752),
    (919.430, 609),
    (1005.880, 652),
    (853.975, 540),
)

ARTIFACT_SHA256 = {
    "run_hook_cmd": "2a7be63b4fdb6bb6d940411fdb2e3dbe26f7a83714b8739c32644e3cb900c568",
    "lians_plugin_py": "4762ff64dc327a3d0eb5f3d35061de6a5d340031a1c8d02a8d10d42ce7cc45e4",
    "user_prompt_submit_recall_py": "629ec392e93fcf6bfd503d3825f167b614226f8de929f797c3be716cbc5153ed",
    "local_recall_daemon_py": "c2336f7ede6427473329c32d01c7a76b409c311ff4462c5c829479b69280619a",
}

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
            "embedding_dimensions": 1024,
            "fixture_sha256": "36970399263d3cc6f34357c02b85d1172320a6c76599b3061af9dd81ffea0da9",
        },
        "methodology": {
            "measurement_surface": "installed-cache hook entry point invoked directly",
            "prewarm_processes": 1,
            "fresh_hook_processes": sample_count,
            "execution": "sequential",
            "shared_prewarmed_daemon": True,
            "percentile_method": "Type 7 linear interpolation, h=(n-1)*p",
            "model_calls": 0,
            "external_network_calls": 0,
            "loopback_daemon_transport": True,
            "reindexed": False,
            "fixture_mutation": "namespace and agent identity remap only",
        },
        "measured_installed_cache_artifact_sha256": ARTIFACT_SHA256,
        "source_identity": {
            "run_hook_cmd_git_blob_sha256": (
                "68e0e04a774e0f5440abba5623394873c5d142ee1f11e419fac7c311d5eae853"
            ),
            "run_hook_cmd_installed_cache_line_endings": "CRLF",
            "run_hook_cmd_git_blob_line_endings": "LF",
            "run_hook_cmd_code_equivalent_after_line_ending_normalization": True,
            "other_reported_runtime_artifacts_byte_identical": True,
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
                "evidence_form": "aggregate receipt checks; raw receipts withheld",
                "samples": sample_count,
                "injected": sample_count,
                "daemon_transport": sample_count,
                "non_degraded": sample_count,
                "same_top_evidence": sample_count,
                "same_full_context": sample_count,
                "candidate_window_complete": sample_count,
                "graph_search_complete": sample_count,
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
                "describe only those hook invocations. Warm hook wall time was 1.071 seconds "
                "p95 and 1.109 seconds maximum."
            ),
            "not_measured": [
                "overall model or agent response time",
                "concurrent load",
                "disk-cold startup or retrieval",
                "Codex host hook dispatch or end-to-end plugin loading",
                "other workloads, machines, models, or prompts",
            ],
            "warm_hook_p95_under_3_5_seconds_supported": True,
            "prewarm_under_3_5_seconds_supported": False,
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
