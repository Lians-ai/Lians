"""Keep the uploaded universal-plugin contract aligned with the MCP runtime."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.lians import openai_mcp

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "plugins" / "lians-memory-universal"


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        hosted_mcp_enabled=True,
        hosted_mcp_resource_url="https://mcp.lians.ai",
        hosted_mcp_issuer_url="https://issuer.example",
        hosted_mcp_jwks_url="https://issuer.example/.well-known/jwks.json",
        hosted_mcp_service_documentation_url="https://www.lians.ai/privacy",
        hosted_mcp_jwt_algorithms="RS256",
        hosted_mcp_jwt_leeway_seconds=30,
        hosted_mcp_max_token_lifetime_seconds=3600,
        hosted_mcp_tenant_claim="tenant_id",
        hosted_mcp_allowed_hosts="",
        hosted_mcp_allowed_origins="https://chatgpt.com",
        hosted_mcp_retention_days=365,
        hosted_mcp_audit_retention_days=365,
        hosted_mcp_tool_timeout_seconds=30,
        hosted_mcp_max_concurrent_inference=1,
        hosted_mcp_inference_queue_timeout_seconds=0.1,
        hosted_mcp_rate_limit_per_minute=60,
        hosted_mcp_max_memories_per_tenant=10_000,
        hosted_mcp_max_stored_bytes_per_tenant=40_000_000,
        hosted_mcp_max_write_bytes_per_day=1_000_000,
        hosted_mcp_max_audit_events_per_day=5_000,
        retention_prune_interval_hours=24,
        embedding_provider="sentence-transformers",
        sentence_transformer_revision="d4aa6901d3a41ba39fb536a557fa166f842b0e09",
        api_secret_seed="test-only-hosted-namespace-secret-32-bytes",
        openai_apps_challenge_token="",
    )


def _without_titles(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_titles(item) for key, item in value.items() if key != "title"}
    if isinstance(value, list):
        return [_without_titles(item) for item in value]
    return value


async def test_submission_tool_contracts_match_runtime_exactly():
    metadata = json.loads((BUNDLE / "submission" / "metadata.json").read_text(encoding="utf-8"))
    submitted = {tool["name"]: tool for tool in metadata["mcp"]["tools"]}
    runtime = openai_mcp.build_openai_mcp_runtime(_settings())
    actual = {
        tool.name: tool.model_dump(by_alias=True, exclude_none=True)
        for tool in await runtime.server.list_tools()
    }

    assert set(submitted) == set(actual) == {"remember", "recall", "forget_memory"}
    for name in submitted:
        for field in ("title", "description", "securitySchemes", "annotations"):
            assert submitted[name][field] == actual[name][field]
        for field in ("inputSchema", "outputSchema"):
            assert _without_titles(submitted[name][field]) == _without_titles(actual[name][field])
        assert actual[name]["_meta"]["securitySchemes"] == actual[name]["securitySchemes"]


def test_submission_cases_endpoint_and_operator_policy_statuses_are_truthful():
    metadata = json.loads((BUNDLE / "submission" / "metadata.json").read_text(encoding="utf-8"))
    cases = json.loads((BUNDLE / "submission" / "test-cases.json").read_text(encoding="utf-8"))

    assert len(cases["positive"]) == metadata["testing"]["positiveCaseCount"] == 5
    assert len(cases["negative"]) == metadata["testing"]["negativeCaseCount"] == 3
    assert metadata["mcp"]["url"] == "https://mcp.lians.ai/mcp"
    assert metadata["mcp"]["urlStatus"] == "planned_canonical_not_live"
    assert metadata["draft"] is True
    assert (
        metadata["reviewArtifacts"]["auditRetentionLifecycleStatus"]
        == "approved_and_publicly_disclosed_indefinite_pseudonymous_content_free"
    )
    assert (
        metadata["reviewArtifacts"]["backupDeletionWindowStatus"]
        == "provider_verified_and_publicly_disclosed_encrypted_fly_snapshots_5_days"
    )


def test_initial_launch_countries_are_consistent_across_submission_materials():
    metadata = json.loads((BUNDLE / "submission" / "metadata.json").read_text(encoding="utf-8"))

    assert metadata["availability"] == {
        "countries": ["United States", "United Kingdom"],
        "status": "operator_selected_pending_submission",
    }

    materials = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        BUNDLE / "submission" / "reviewer-guide.md",
        BUNDLE / "submission" / "release-notes.md",
    )
    for path in materials:
        text = path.read_text(encoding="utf-8")
        assert "United States" in text, path
        assert "United Kingdom" in text, path
