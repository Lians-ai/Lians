from __future__ import annotations

import importlib.util
import json
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


def _benchmark_module():
    path = PLUGIN / "benchmarks" / "claude_locomo_ab.py"
    spec = importlib.util.spec_from_file_location("claude_locomo_ab", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _usage_module():
    path = REPO / "agentmem" / "benchmarks" / "provider_usage_extension.py"
    spec = importlib.util.spec_from_file_location("provider_usage_extension", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plugin_mcp_uses_bounded_core_profile() -> None:
    config = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    lians = config["mcpServers"]["lians"]
    env = lians["env"]

    assert lians["type"] == "stdio"
    assert lians["timeout"] == 120_000
    assert not lians.get("alwaysLoad", False)
    assert env["LIANS_MCP_PROJECT_ROOT"] == "${CLAUDE_PROJECT_DIR}"
    assert env["LIANS_AGENT_ID"] == "${LIANS_AGENT_ID:-}"
    assert env["LIANS_NAMESPACE"] == "${LIANS_NAMESPACE:-}"
    assert env["LIANS_MCP_PREWARM"] == "background"
    assert env["LIANS_MCP_ENABLED_TOOLS"].split(",") == [
        "remember",
        "recall",
        "recall_at",
    ]
    assert env["LIANS_MCP_RECALL_K"] == "50"
    assert env["LIANS_MCP_CONTEXT_MAX_TOKENS"] == "2650"


def test_claude_locomo_dry_run_meets_target() -> None:
    benchmark = _benchmark_module()
    question = (
        REPO
        / "memory-benchmarks"
        / "results"
        / "locomo"
        / "predicted_lians_arctic"
        / "conv0_q0.json"
    )
    report = benchmark.build_report(
        question,
        top_k=20,
        target_reduction=0.85,
        model="sonnet",
        max_budget_usd=0.25,
        dry_run=True,
    )

    assert report["semantic_context"]["target_met"] is True
    assert report["semantic_context"]["reduction"] >= 0.85
    assert report["recorded_lians_cutoff_result"]["judgment"] == "CORRECT"


def test_claude_manifest_matches_marketplace_version() -> None:
    plugin = json.loads(
        (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    listing = next(item for item in marketplace["plugins"] if item["name"] == "lians")
    assert plugin["version"] == listing["version"]


def test_claude_desktop_launcher_requests_same_core_profile() -> None:
    launcher = (REPO / "integrations" / "mcpb" / "src" / "server.py").read_text(
        encoding="utf-8"
    )
    assert '"LIANS_MCP_ENABLED_TOOLS", "remember,recall,recall_at"' in launcher
    assert '"LIANS_MCP_RECALL_K", "50"' in launcher
    assert '"LIANS_MCP_CONTEXT_MAX_TOKENS", "2650"' in launcher
    assert '"LIANS_MCP_PREWARM", "background"' in launcher


def test_recorded_claude_pair_clears_usage_extension_target() -> None:
    case = json.loads(
        (
            PLUGIN
            / "benchmarks"
            / "results"
            / "claude-usage-extension-case-2026-08-08.json"
        ).read_text(encoding="utf-8")
    )
    report = _usage_module().evaluate_case(case)

    assert report["quality_gate"]["passed"] is True
    assert report["observed"]["same_budget_usage_multiplier"] == 10.112509
    assert report["verdict"]["qualified_target_met"] is True
