"""Keep the uploaded universal-plugin contract aligned with the MCP runtime."""

from __future__ import annotations

import json
import re
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


def _mapping_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_mapping_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_mapping_keys(item) for item in value))
    return set()


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

    forget = actual["forget_memory"]
    assert forget["description"] == (
        "Immediately crypto-shred one stored memory from active service storage by its "
        "reference. Encrypted provider backups may retain a recoverable copy for up to 5 "
        "days. Call only after the user explicitly confirms."
    )
    assert forget["inputSchema"]["properties"]["confirm"]["description"] == (
        "Must be true only after the user confirms immediate active-service "
        "crypto-shredding and the disclosed encrypted provider backup window of up to 5 days."
    )


def test_submission_cases_live_boundary_and_operator_policy_statuses_are_truthful():
    metadata = json.loads((BUNDLE / "submission" / "metadata.json").read_text(encoding="utf-8"))
    cases = json.loads((BUNDLE / "submission" / "test-cases.json").read_text(encoding="utf-8"))
    manifest = json.loads((BUNDLE / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

    expected_long_description = (
        "Lians Memory stores project facts and decisions that users explicitly choose to keep, "
        "recalls bounded context, and removes a selected memory from active storage after "
        "confirmation. It does not increase OpenAI usage quotas or bypass rate limits."
    )
    assert metadata["listing"]["longDescription"] == expected_long_description
    assert manifest["interface"]["longDescription"] == expected_long_description
    assert metadata["listing"]["developerName"] == "Lians, Ai"
    assert metadata["publisher"]["identity"] == "Lians, Ai"
    assert manifest["author"]["name"] == "Lians, Ai"
    assert manifest["interface"]["developerName"] == "Lians, Ai"
    assert metadata["listing"]["supportURL"] == "https://www.lians.ai/contact"
    assert "supportURL" not in manifest["interface"]

    assert len(cases["positive"]) == metadata["testing"]["positiveCaseCount"] == 5
    assert len(cases["negative"]) == metadata["testing"]["negativeCaseCount"] == 3
    assert metadata["testing"]["demoAccountFixtureStatus"] == "live_provisioned_and_verified"
    assert {record["fixtureId"]: record["memoryRef"] for record in cases["fixture"]["records"]} == {
        "architecture-current": "fde306da-3f06-4063-9535-acd1c03b226c",
        "region-current": "a5104b94-c427-4a33-b6eb-5362c568cf62",
    }
    serialized_cases = json.dumps(cases)
    for stale_ref in (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ):
        assert stale_ref not in serialized_cases

    case_4 = next(
        case for case in cases["positive"] if case["id"] == "positive-4-confirmed-forget"
    )
    assert case_4["requiredFixtureData"] == []
    assert [step["name"] for step in case_4["expectedToolSequence"]] == [
        "remember",
        "forget_memory",
        "forget_memory",
    ]
    remember_arguments = case_4["expectedToolSequence"][0]["arguments"]
    assert remember_arguments == {
        "content": "Atlas disposable review note for confirmed removal.",
        "project": "atlas",
    }
    assert "idempotency_key" not in remember_arguments
    first_forget = case_4["expectedToolSequence"][1]["arguments"]
    retry_forget = case_4["expectedToolSequence"][2]["arguments"]
    assert first_forget == retry_forget == {
        "memory_ref": "${remember.memory_ref}",
        "confirm": True,
    }
    assert "status not_found" in case_4["expectedResultShape"]
    assert "memories_erased 0" in case_4["expectedResultShape"]
    assert metadata["mcp"]["url"] == "https://mcp.lians.ai/mcp"
    assert metadata["mcp"]["urlStatus"] == "validated_live"
    assert metadata["mcp"]["liveVerification"] == {
        "status": "production_oauth_e2e_validated_portal_gates_pending",
        "workflowVerifiedAt": "2026-08-10T02:31:24Z",
        "workflowRunURL": ("https://github.com/Lians-ai/Lians/actions/runs/31349405257"),
        "buildSHA": "e72fad2c7f98ecf54b6553a90bf8d862046c1abc",
        "schemaRevision": "0030_force_hosted_mcp_rls",
        "verificationMode": "production_workflow_no_token_plus_operator_oauth_e2e",
        "authenticatedMcpStatus": "validated_production_oauth_e2e",
        "checks": {
            "https": "ok",
            "protectedResourceMetadata": "ok",
            "unauthenticatedChallenge": "ok",
            "authenticatedMcp": "skipped_no_token",
        },
        "authenticatedE2E": {
            "status": "passed",
            "verifiedAt": "2026-08-10T03:41:10.126400Z",
            "timestampSource": "sanitized_auth0_and_fly_event_timestamps",
            "reviewerLoginAt": "2026-08-10T03:40:58Z",
            "observedEvents": {
                "authorizationSuccessAt": "2026-08-10T03:41:01.140Z",
                "tokenExchangeAt": "2026-08-10T03:41:01.372Z",
                "unauthenticatedChallengeAt": "2026-08-10T03:41:04.346034Z",
                "authenticatedEndpointCheckerCompletedAt": "2026-08-10T03:41:05.084992Z",
                "canaryInitializedAt": "2026-08-10T03:41:05.582330Z",
                "rememberCompletedAt": "2026-08-10T03:41:07.879529Z",
                "recallCompletedAt": "2026-08-10T03:41:09.810661Z",
                "confirmedForgetCompletedAt": "2026-08-10T03:41:10.126400Z",
            },
            "toolCallTimestampMapping": (
                "fixed_harness_order_remember_recall_forget_provider_labels_generic"
            ),
            "checks": {
                "protectedResourceMetadata": "ok",
                "oidcDiscovery": "ok",
                "dynamicClientRegistration": "ok",
                "browserLogin": "ok",
                "authorizationCallback": "ok",
                "tokenExchange": "ok",
                "repositoryJwtVerification": "ok",
                "authenticatedEndpointChecker": "ok",
                "mcpRemember": "ok",
                "mcpRecall": "ok",
                "mcpConfirmedForget": "ok",
                "sessionCleanup": "ok",
            },
            "temporaryClientCleanup": {
                "dcrCleanupAdvertised": False,
                "manualDeletion": "ok",
                "registeredClientInventoryAfterDeletion": 0,
            },
            "evidencePolicy": (
                "sanitized_status_only_no_credentials_tokens_client_ids_"
                "memory_payloads_or_local_paths"
            ),
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
    assert metadata["mcp"]["authentication"] == {
        "type": "oauth2.1",
        "status": "production_oauth_e2e_validated",
        "scopes": ["memory:read", "memory:write"],
        "reviewerCredentialsStatus": (
            "login_and_live_fixture_validated_pending_secure_portal_entry"
        ),
        "reviewerLoginAt": "2026-08-10T03:40:58Z",
    }
    authenticated_e2e = metadata["mcp"]["liveVerification"]["authenticatedE2E"]
    assert authenticated_e2e["verifiedAt"] == "2026-08-10T03:41:10.126400Z"
    assert authenticated_e2e["temporaryClientCleanup"] == {
        "dcrCleanupAdvertised": False,
        "manualDeletion": "ok",
        "registeredClientInventoryAfterDeletion": 0,
    }
    assert (
        metadata["reviewArtifacts"]["reviewerAccountStatus"]
        == "login_and_live_fixture_validated_pending_secure_portal_delivery"
    )
    assert metadata["publisher"]["verificationStatus"] == "verified_openai_portal"
    assert (
        metadata["publisher"]["appsManagementWriteStatus"]
        == "validated_apps_management_owner"
    )
    assert (
        metadata["publisher"]["appsManagementReadStatus"]
        == "validated_apps_management_owner"
    )
    assert metadata["skills"][0]["scanStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["domainVerificationStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["toolScanStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["demoRecordingURL"] is None
    assert metadata["testing"]["developerModeRehearsalStatus"] == "pending_operator_action"
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


def test_authenticated_oauth_e2e_evidence_is_consistent_and_sanitized():
    metadata = json.loads((BUNDLE / "submission" / "metadata.json").read_text(encoding="utf-8"))
    authenticated_e2e = metadata["mcp"]["liveVerification"]["authenticatedE2E"]

    assert authenticated_e2e["verifiedAt"] == "2026-08-10T03:41:10.126400Z"
    assert authenticated_e2e["timestampSource"] == "sanitized_auth0_and_fly_event_timestamps"
    assert authenticated_e2e["reviewerLoginAt"] == "2026-08-10T03:40:58Z"
    assert authenticated_e2e["observedEvents"] == {
        "authorizationSuccessAt": "2026-08-10T03:41:01.140Z",
        "tokenExchangeAt": "2026-08-10T03:41:01.372Z",
        "unauthenticatedChallengeAt": "2026-08-10T03:41:04.346034Z",
        "authenticatedEndpointCheckerCompletedAt": "2026-08-10T03:41:05.084992Z",
        "canaryInitializedAt": "2026-08-10T03:41:05.582330Z",
        "rememberCompletedAt": "2026-08-10T03:41:07.879529Z",
        "recallCompletedAt": "2026-08-10T03:41:09.810661Z",
        "confirmedForgetCompletedAt": "2026-08-10T03:41:10.126400Z",
    }
    assert authenticated_e2e["toolCallTimestampMapping"] == (
        "fixed_harness_order_remember_recall_forget_provider_labels_generic"
    )
    assert set(authenticated_e2e["checks"]) == {
        "protectedResourceMetadata",
        "oidcDiscovery",
        "dynamicClientRegistration",
        "browserLogin",
        "authorizationCallback",
        "tokenExchange",
        "repositoryJwtVerification",
        "authenticatedEndpointChecker",
        "mcpRemember",
        "mcpRecall",
        "mcpConfirmedForget",
        "sessionCleanup",
    }
    assert set(authenticated_e2e["checks"].values()) == {"ok"}
    assert authenticated_e2e["temporaryClientCleanup"] == {
        "dcrCleanupAdvertised": False,
        "manualDeletion": "ok",
        "registeredClientInventoryAfterDeletion": 0,
    }
    unsafe_evidence_keys = {
        "credential",
        "credentials",
        "token",
        "bearertoken",
        "accesstoken",
        "refreshtoken",
        "idtoken",
        "clientid",
        "clientsecret",
        "memorycontent",
        "memorypayload",
        "memoryref",
        "localpath",
        "rawresponse",
    }
    normalized_evidence_keys = {
        re.sub(r"[^a-z0-9]", "", key.casefold()) for key in _mapping_keys(authenticated_e2e)
    }
    assert normalized_evidence_keys.isdisjoint(unsafe_evidence_keys)

    verification_materials = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        ROOT / "docs" / "production-release.md",
        BUNDLE / "README.md",
        BUNDLE / "submission" / "data-handling.md",
        BUNDLE / "submission" / "reviewer-guide.md",
        BUNDLE / "submission" / "release-notes.md",
    )
    for path in verification_materials:
        text = path.read_text(encoding="utf-8")
        assert "2026-08-10T03:41Z" in text, path
        assert "authenticated" in text.casefold(), path
        assert re.search(r"[A-Za-z]:\\Users\\", text) is None, path
        assert re.search(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}", text) is None, path
        assert re.search(r"\bBearer\s+[A-Za-z0-9._~-]{12,}", text, re.IGNORECASE) is None, path

    reviewer_materials = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        ROOT / "docs" / "production-release.md",
        BUNDLE / "README.md",
        BUNDLE / "submission" / "reviewer-guide.md",
        BUNDLE / "submission" / "release-notes.md",
    )
    for path in reviewer_materials:
        assert "2026-08-10T03:40:58Z" in path.read_text(encoding="utf-8"), path

    timestamp_provenance_materials = (
        ROOT / "docs" / "openai-universal-plugin-production.md",
        ROOT / "docs" / "production-release.md",
        BUNDLE / "submission" / "reviewer-guide.md",
    )
    for path in timestamp_provenance_materials:
        text = path.read_text(encoding="utf-8")
        assert "2026-08-10T03:41:10.126400Z" in text, path
        assert "fixed" in text.casefold() and "order" in text.casefold(), path

    assert metadata["draft"] is True
    assert metadata["testing"]["demoAccountFixtureStatus"] == "live_provisioned_and_verified"
    assert metadata["publisher"]["verificationStatus"] == "verified_openai_portal"
    assert metadata["publisher"]["appsManagementWriteStatus"] == (
        "validated_apps_management_owner"
    )
    assert metadata["publisher"]["appsManagementReadStatus"] == (
        "validated_apps_management_owner"
    )
    assert metadata["skills"][0]["scanStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["domainVerificationStatus"] == "pending_operator_action"
    assert metadata["reviewArtifacts"]["toolScanStatus"] == "pending_operator_action"
    assert metadata["testing"]["developerModeRehearsalStatus"] == "pending_operator_action"
    assert metadata["availability"]["status"] == "operator_selected_pending_submission"


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
