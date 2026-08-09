import json
import re
from pathlib import Path

import pytest
from benchmarks.codex_installed_plugin_latency_evidence import (
    TIMING_PAIRS_MS,
    build_report,
    main,
    publication_safety_violations,
    render_report,
    summarize_ms,
)

REPORT_PATH = (
    Path(__file__).parents[2]
    / "docs"
    / "benchmarks"
    / "codex-installed-plugin-latency-evidence-2026-08-09.json"
)


def test_installed_cache_statistics_recompute_from_all_raw_pairs():
    assert len(TIMING_PAIRS_MS) == 20
    report = build_report()
    run = report["installed_cache_run"]

    assert run["prewarm_wall_ms"] == 6090.554
    assert run["hook_process_wall"] == {
        "p50_ms": 960.664,
        "p95_ms": 1071.088,
        "max_ms": 1109.455,
    }
    assert run["receipt_elapsed"] == {
        "p50_ms": 631.0,
        "p95_ms": 695.95,
        "max_ms": 752,
    }
    assert run["timing_pairs_ms"] == [
        {"hook_process_wall_ms": wall, "receipt_elapsed_ms": receipt}
        for wall, receipt in TIMING_PAIRS_MS
    ]


def test_every_sample_passes_the_recorded_quality_gates():
    report = build_report()
    gates = report["installed_cache_run"]["quality_gates"]

    assert gates == {
        "evidence_form": "aggregate receipt checks; raw receipts withheld",
        "samples": 20,
        "injected": 20,
        "daemon_transport": 20,
        "non_degraded": 20,
        "same_top_evidence": 20,
        "same_full_context": 20,
        "candidate_window_complete": 20,
        "graph_search_complete": 20,
        "all_passed": True,
    }


def test_installed_hashes_distinguish_windows_line_ending_normalization():
    report = build_report()

    assert report["source_identity"] == {
        "run_hook_cmd_git_blob_sha256": (
            "68e0e04a774e0f5440abba5623394873c5d142ee1f11e419fac7c311d5eae853"
        ),
        "run_hook_cmd_installed_cache_line_endings": "CRLF",
        "run_hook_cmd_git_blob_line_endings": "LF",
        "run_hook_cmd_code_equivalent_after_line_ending_normalization": True,
        "other_reported_runtime_artifacts_byte_identical": True,
    }


def test_wrapper_microbenchmark_is_separate_and_narrowly_labeled():
    wrapper = build_report()["wrapper_only_microbenchmark"]

    assert wrapper["separate_from_installed_cache_run"] is True
    assert wrapper["real_plugin_profile_database_daemon_model_or_network_access"] is False
    assert wrapper["cmd_ms"] == {"p50_ms": 94.684, "p95_ms": 102.885}
    assert wrapper["powershell_ms"] == {"p50_ms": 327.479, "p95_ms": 403.875}


def test_claim_boundary_explicitly_excludes_unmeasured_surfaces():
    boundary = build_report()["claim_boundary"]

    assert boundary["universal_claim_supported"] is False
    assert boundary["not_measured"] == [
        "overall model or agent response time",
        "concurrent load",
        "disk-cold startup or retrieval",
        "Codex host hook dispatch or end-to-end plugin loading",
        "other workloads, machines, models, or prompts",
    ]
    assert boundary["warm_hook_p95_under_3_5_seconds_supported"] is True
    assert boundary["prewarm_under_3_5_seconds_supported"] is False


def test_rendered_evidence_is_sanitized_and_contains_no_secret_like_values():
    report = build_report()
    rendered = render_report()

    assert publication_safety_violations(report) == []
    assert "local-user-sentinel" not in rendered.lower()
    assert not re.search(r"(?i)\b[a-z]:[\\/]", rendered)
    assert not re.search(r"(?i)(?:/home/|/users/)", rendered)
    assert not re.search(r"(?i)(?:bearer\s+|\bsk-[a-z0-9]{16,})", rendered)


def test_checked_in_report_is_exact_generator_output():
    assert REPORT_PATH.read_text(encoding="utf-8") == render_report()
    assert json.loads(REPORT_PATH.read_text(encoding="utf-8")) == build_report()


def test_cli_can_write_the_same_deterministic_report(tmp_path):
    output = tmp_path / "evidence.json"

    assert main(["--output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == render_report()


def test_summary_rejects_an_empty_sample_set():
    with pytest.raises(ValueError, match="at least one timing"):
        summarize_ms([])
