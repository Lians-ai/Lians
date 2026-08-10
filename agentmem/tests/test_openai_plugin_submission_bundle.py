"""Keep the uploaded universal-plugin contract aligned with the MCP runtime."""

from __future__ import annotations

import json
from datetime import datetime
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


def test_submission_cases_live_boundary_and_operator_policy_statuses_are_truthful():
    metadata = json.loads((BUNDLE / "submission" / "metadata.json").read_text(encoding="utf-8"))
    cases = json.loads((BUNDLE / "submission" / "test-cases.json").read_text(encoding="utf-8"))

    assert len(cases["positive"]) == metadata["testing"]["positiveCaseCount"] == 5
    assert len(cases["negative"]) == metadata["testing"]["negativeCaseCount"] == 3
    assert metadata["mcp"]["url"] == "https://mcp.lians.ai/mcp"
    assert metadata["mcp"]["urlStatus"] == "validated_live"
    assert metadata["mcp"]["liveVerification"] == {
        "status": "unauthenticated_boundary_validated_authenticated_mcp_pending",
        "verifiedAt": "2026-08-10T02:31:24Z",
        "workflowRunURL": ("https://github.com/Lians-ai/Lians/actions/runs/31349405257"),
        "buildSHA": "e72fad2c7f98ecf54b6553a90bf8d862046c1abc",
        "schemaRevision": "0030_force_hosted_mcp_rls",
        "verificationMode": "no-token",
        "authenticatedMcpStatus": "pending_operator_action",
        "checks": {
            "https": "ok",
            "protectedResourceMetadata": "ok",
            "unauthenticatedChallenge": "ok",
            "authenticatedMcp": "skipped_no_token",
        },
        "coldBoot": {
            "status": "qualified_three_run_production_rehearsal",
            "requiredPassingRuns": 3,
            "passingRuns": 3,
            "timingBasis": "machine_start_to_first_ready_1_of_1_passing",
            "startupTimeoutSeconds": 360,
            "maxObservedSeconds": 197.963,
            "allObservedBelowStartupTimeout": True,
            "postMcpHealth": {
                "passingRuns": 3,
                "checkMode": "single_immediate_workflow_result_per_run",
                "extendedObservationStatus": "not_attested_by_cited_workflows",
                "status": "passed_immediate_checks",
            },
            "flyGracePeriod": {
                "configuredSeconds": 420,
                "effectiveSeconds": 60,
                "configuredValueHonored": False,
                "warning": (
                    "Service HTTP check has a grace period greater than 1 minute "
                    "(7m0s); this will be lowered to 1 minute"
                ),
            },
            "runs": [
                {
                    "workflowRunID": 31347743399,
                    "workflowRunURL": (
                        "https://github.com/Lians-ai/Lians/actions/runs/31347743399"
                    ),
                    "machineID": "28691d1b640298",
                    "completedAt": "2026-08-10T01:59:04Z",
                    "status": "success",
                    "machineStartedAt": "2026-08-10T01:55:07.0366956Z",
                    "firstReadyAt": "2026-08-10T01:58:24.5647632Z",
                    "machineStartToFirstReadySeconds": 197.528,
                    "readyChecksPassing": "1/1",
                    "postMcpHealthCheckedAt": "2026-08-10T01:59:01.1331951Z",
                    "postMcpHealthResult": "health_liveness_readiness_ok",
                },
                {
                    "workflowRunID": 31348671152,
                    "workflowRunURL": (
                        "https://github.com/Lians-ai/Lians/actions/runs/31348671152"
                    ),
                    "machineID": "7841659cd4d6e8",
                    "completedAt": "2026-08-10T02:15:28Z",
                    "status": "success",
                    "machineStartedAt": "2026-08-10T02:11:27.3152732Z",
                    "firstReadyAt": "2026-08-10T02:14:45.2623950Z",
                    "machineStartToFirstReadySeconds": 197.947,
                    "readyChecksPassing": "1/1",
                    "postMcpHealthCheckedAt": "2026-08-10T02:15:24.2071289Z",
                    "postMcpHealthResult": "health_liveness_readiness_ok",
                },
                {
                    "workflowRunID": 31349405257,
                    "workflowRunURL": (
                        "https://github.com/Lians-ai/Lians/actions/runs/31349405257"
                    ),
                    "machineID": "8dd9e0ce170928",
                    "completedAt": "2026-08-10T02:31:23Z",
                    "status": "success",
                    "machineStartedAt": "2026-08-10T02:27:24.4377031Z",
                    "firstReadyAt": "2026-08-10T02:30:42.4003935Z",
                    "machineStartToFirstReadySeconds": 197.963,
                    "readyChecksPassing": "1/1",
                    "postMcpHealthCheckedAt": "2026-08-10T02:31:18.7557165Z",
                    "postMcpHealthResult": "health_liveness_readiness_ok",
                },
            ],
        },
    }
    cold_boot = metadata["mcp"]["liveVerification"]["coldBoot"]
    cold_boot_runs = cold_boot["runs"]
    observed_seconds = [run["machineStartToFirstReadySeconds"] for run in cold_boot_runs]
    assert len({run["workflowRunID"] for run in cold_boot_runs}) == 3
    assert len({run["machineID"] for run in cold_boot_runs}) == 3
    for run in cold_boot_runs:
        machine_started_at = datetime.fromisoformat(run["machineStartedAt"])
        first_ready_at = datetime.fromisoformat(run["firstReadyAt"])
        assert (
            round((first_ready_at - machine_started_at).total_seconds(), 3)
            == (run["machineStartToFirstReadySeconds"])
        )
    assert max(observed_seconds) == cold_boot["maxObservedSeconds"]
    assert max(observed_seconds) < cold_boot["startupTimeoutSeconds"] == 360
    assert all(run["readyChecksPassing"] == "1/1" for run in cold_boot_runs)
    assert all(
        run["postMcpHealthResult"] == "health_liveness_readiness_ok" for run in cold_boot_runs
    )
    assert cold_boot["postMcpHealth"]["checkMode"] == ("single_immediate_workflow_result_per_run")
    assert cold_boot["postMcpHealth"]["extendedObservationStatus"] == (
        "not_attested_by_cited_workflows"
    )
    assert metadata["draft"] is True
    assert metadata["mcp"]["authentication"]["status"] == "pending_operator_action"
    assert (
        metadata["mcp"]["authentication"]["reviewerCredentialsStatus"] == "pending_operator_action"
    )
    for field in (
        "verificationStatus",
        "appsManagementWriteStatus",
        "appsManagementReadStatus",
    ):
        assert metadata["publisher"][field] == "pending_operator_action"
    assert metadata["skills"][0]["scanStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["domainVerificationStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["toolScanStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["reviewerAccountStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["demoRecordingURL"] is None
    assert (
        metadata["reviewArtifacts"]["auditRetentionLifecycleStatus"]
        == "approved_and_publicly_disclosed_indefinite_pseudonymous_content_free"
    )
    assert (
        metadata["reviewArtifacts"]["backupDeletionWindowStatus"]
        == "provider_verified_and_publicly_disclosed_encrypted_fly_snapshots_5_days"
    )


def test_live_deployment_evidence_is_consistent_across_submission_materials():
    build_sha = "e72fad2c7f98ecf54b6553a90bf8d862046c1abc"
    schema_revision = "0030_force_hosted_mcp_rls"
    newest_run_url = "https://github.com/Lians-ai/Lians/actions/runs/31349405257"
    build_materials = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        ROOT / "docs" / "production-release.md",
        BUNDLE / "README.md",
        BUNDLE / "skills" / "lians-memory" / "agents" / "openai.yaml",
        BUNDLE / "submission" / "data-handling.md",
        BUNDLE / "submission" / "reviewer-guide.md",
        BUNDLE / "submission" / "release-notes.md",
    )

    for path in build_materials:
        text = path.read_text(encoding="utf-8")
        assert build_sha in text, path

    schema_materials = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        ROOT / "docs" / "production-release.md",
        BUNDLE / "submission" / "data-handling.md",
        BUNDLE / "submission" / "reviewer-guide.md",
        BUNDLE / "submission" / "release-notes.md",
    )
    for path in schema_materials:
        text = path.read_text(encoding="utf-8")
        assert schema_revision in text, path

    production_docs = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        ROOT / "docs" / "production-release.md",
    )
    for path in production_docs:
        text = path.read_text(encoding="utf-8")
        assert newest_run_url in text, path
        assert "197.528" in text, path
        assert "197.947" in text, path
        assert "197.963" in text, path
        assert "420" in text, path
        assert "one minute" in text.casefold(), path
        assert "not honored" in text.casefold(), path
        assert "single immediate" in text.casefold(), path
        assert "does not attest" in text.casefold(), path

    unsupported_stability_claims = ("at least 60", "zero degradation")
    for path in build_materials:
        text = path.read_text(encoding="utf-8").casefold()
        for unsupported_claim in unsupported_stability_claims:
            assert unsupported_claim not in text, (path, unsupported_claim)

    submission_materials = (
        BUNDLE / "README.md",
        BUNDLE / "skills" / "lians-memory" / "agents" / "openai.yaml",
        BUNDLE / "submission" / "data-handling.md",
        BUNDLE / "submission" / "reviewer-guide.md",
        BUNDLE / "submission" / "release-notes.md",
        ROOT / "docs" / "openai-universal-plugin-production.md",
    )
    stale_live_claims = (
        "mcp route not live",
        "endpoint is currently marked planned",
        "planned canonical endpoint",
        "planned_canonical_not_live",
        "planned and is not live yet",
        "route remains disabled and is not live yet",
        "does not assert that it is live yet",
    )
    for path in submission_materials:
        text = path.read_text(encoding="utf-8").casefold()
        for stale_claim in stale_live_claims:
            assert stale_claim not in text, (path, stale_claim)


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
