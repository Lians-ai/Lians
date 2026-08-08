"""Acceptance contracts for all pre-robotics agent-improvement phases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import quantiles
from uuid import uuid4

import pytest
from lians.audit_chain import chain_log
from lians.control_models import GatePolicySet
from lians.control_schemas import GateApprovalAttestationCreate
from lians.decision_record_integrity import (
    DECISION_RECORD_HASH_VERSION,
    VERIFIED_INTEGRITY_STATUS,
    authenticated_recorder_authorization_snapshot,
    authenticated_recorder_provenance,
    compute_decision_record_hash,
    decision_record_binding_payload,
)
from lians.immutable_attestation_service import create_gate_approval_attestation
from lians.improvement_schemas import (
    AgentDefinitionCreate,
    AgentVersionComponentCreate,
    AgentVersionCreate,
    ComponentArtifactCreate,
    EvalCaseFromDecision,
    EvalRunCreate,
    EvalSuiteCreate,
    EvaluationAttestationCreate,
    ImprovementContract,
    MetricObjective,
    MetricResultCreate,
    OptimizationStudyCreate,
    ProtectedMetric,
    TrialCreate,
)
from lians.improvement_service import (
    create_agent_definition,
    create_agent_version,
    create_comparison,
    create_eval_case_from_decision,
    create_eval_run,
    create_eval_suite,
    create_evaluation_attestation,
    create_optimization_study,
    evaluation_attestation_out,
    sha256_json,
    verify_evaluation_attestation,
)
from lians.learning_schemas import (
    DriftAnalyzeRequest,
    FeedbackCreate,
    OutcomeCreate,
    OutcomeMetric,
)
from lians.learning_service import analyze_drift, create_feedback, create_outcome, feedback_out
from lians.models import DecisionRecord
from lians.optimization_schemas import (
    ContextCompileRequest,
    ContextItem,
    TokenizerSpec,
    ToolDefinition,
    ToolFailureObservation,
    ToolRegistryCreate,
    ToolSelectRequest,
    TraceableCompression,
)
from lians.optimization_service import (
    compile_context,
    context_bundle_out,
    create_tool_registry,
    select_tools,
    tool_selection_out,
)
from lians.receipt_signer import LocalEd25519ReceiptSigner, ReceiptSignerConfiguration
from lians.recorder_schemas import RecorderEnvelope
from lians.recorder_service import normalize_recorder_envelope
from lians.release_schemas import (
    DeploymentCreate,
    ReleaseAttestationCreate,
    ReleaseCandidateCreate,
    RollbackCreate,
)
from lians.release_service import (
    ReleaseContractError,
    create_deployment,
    create_release_attestation,
    create_release_candidate,
    create_rollback,
    release_attestation_out,
    release_target_ref,
    verify_release_attestation,
)
from lians.runtime_schemas import (
    CacheAccessRequest,
    ConcurrencyPlanRequest,
    RequestBudget,
    RouteDecideRequest,
    RoutingCandidate,
    RoutingObjective,
    RuntimeCachePolicy,
    RuntimePolicyCreate,
    TimeoutRetryPolicy,
    ToolCallNode,
)
from lians.runtime_service import (
    _cache_key_document,
    access_runtime_cache,
    create_concurrency_plan,
    create_routing_decision,
    create_runtime_policy,
)

NAMESPACE = "improvement-acceptance"
PRINCIPAL = "lians:principal:v1:api-key:00000000-0000-4000-8000-000000000064"
RECEIPT_HASH = "d" * 64


def test_improvement_migration_enforces_database_tenant_and_immutability_boundaries() -> None:
    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "20260808_0064_agent_improvement_plane.py"
    ).read_text(encoding="utf-8")
    for contract in (
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "AS RESTRICTIVE",
        "GRANT SELECT, INSERT",
        "REVOKE UPDATE, DELETE, TRUNCATE",
        "BEFORE UPDATE OR DELETE",
        "BEFORE TRUNCATE",
        "uq_optimization_candidate_id_namespace",
    ):
        assert contract in source


def _signer() -> LocalEd25519ReceiptSigner:
    return LocalEd25519ReceiptSigner(
        ReceiptSignerConfiguration(
            provider="local",
            key_id="improvement-test-key",
            local_private_key=bytes(range(32)),
        )
    )


async def _recorded_decision(db) -> DecisionRecord:
    principal, auth_method, credential_ref = authenticated_recorder_provenance(
        principal_ref=PRINCIPAL,
        auth_method="api_key",
        credential_id="00000000-0000-4000-8000-000000000064",
    )
    principal_type, role, scopes = authenticated_recorder_authorization_snapshot(
        principal_type="api_key",
        role="owner",
        effective_scopes=["read", "write", "admin"],
    )
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    row = DecisionRecord(
        id=uuid4(),
        namespace=NAMESPACE,
        barrier_group=None,
        agent_id="claims-agent",
        recorded_by_principal_ref=principal,
        recorded_by_auth_method=auth_method,
        recorded_by_credential_ref=credential_ref,
        recorded_by_principal_type=principal_type,
        recorded_by_role=role,
        recorded_by_scopes=scopes,
        decision_type="claims-triage",
        outcome="approve",
        reason_codes=["POLICY_MATCH"],
        regime="internal",
        subject_id=None,
        session_id="run-64",
        model_id="claims-model",
        model_version="2026-08",
        policy_version="claims-policy-v3",
        decided_at=now,
        recorded_at=now,
        knowledge_as_of=now,
        knowledge_recorded_as_of=now,
        evidence_memory_ids=[],
        input_hash="a" * 64,
        output_hash="b" * 64,
        human_review_status="not_requested",
        metadata_={"risk_level": "high"},
        record_hash_version=DECISION_RECORD_HASH_VERSION,
        record_integrity_status=VERIFIED_INTEGRITY_STATUS,
        record_hash="",
    )
    row.record_hash = compute_decision_record_hash(row)
    db.add(row)
    await db.flush()
    await chain_log(
        db,
        NAMESPACE,
        PRINCIPAL,
        "decision_recorded",
        content_hash=row.record_hash,
        payload=decision_record_binding_payload(row),
    )
    await chain_log(
        db,
        NAMESPACE,
        PRINCIPAL,
        "decision_receipt_exported",
        content_hash=RECEIPT_HASH,
        payload={"decision_id": str(row.id), "receipt_hash": RECEIPT_HASH},
    )
    return row


async def _agent_version(db, definition, *, version: str, digest: str):
    return await create_agent_version(
        db,
        agent_definition=definition,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=AgentVersionCreate(
            version=version,
            manifest={"temperature": 0, "max_output_tokens": 200},
            components=[
                AgentVersionComponentCreate(
                    role="prompt",
                    artifact=ComponentArtifactCreate(
                        kind="prompt",
                        name="claims-system-prompt",
                        version=version,
                        digest=digest,
                    ),
                )
            ],
        ),
    )


def _metrics(*, quality: float, safety: float, latency: float, cost: float):
    values = {
        "quality": ("quality", quality, "score", "deterministic"),
        "safety": ("safety", safety, "score", "human-authored"),
        "latency_ms": ("latency", latency, "ms", "client-measured"),
        "cost": ("cost", cost, "USD", "provider-reported"),
    }
    return [
        MetricResultCreate(
            name=name,
            metric_type=metric_type,
            value=value,
            unit=unit,
            provenance=provenance,
            scorer_id=f"{name}-scorer",
            scorer_version="1.0.0",
            scorer_config_hash="e" * 64,
            evidence_refs=[f"urn:lians:test:{name}"],
        )
        for name, (metric_type, value, unit, provenance) in values.items()
    ]


def _run_body(*, suite_id, case, version, quality, safety, latency, cost):
    started = datetime(2026, 8, 8, 13, tzinfo=UTC)
    return EvalRunCreate(
        suite_id=suite_id,
        agent_version_id=version.id,
        environment={"python": "3.12", "image_digest": "sha256:" + "f" * 64},
        trials=[
            TrialCreate(
                case_id=case.id,
                repetition=repetition,
                seed=100 + repetition,
                input_hash=sha256_json(case.input),
                output_hash=("1" if repetition == 0 else "2") * 64,
                configuration_hash=version.manifest_hash,
                latency_ms=latency,
                input_tokens=100,
                output_tokens=20,
                cost=cost,
                cost_currency="USD",
                started_at=started + timedelta(seconds=repetition),
                completed_at=started + timedelta(seconds=repetition, milliseconds=latency),
                metrics=_metrics(
                    quality=quality,
                    safety=safety,
                    latency=latency,
                    cost=cost,
                ),
            )
            for repetition in range(2)
        ],
    )


def test_recorder_v02_captures_exact_operational_provenance() -> None:
    normalized = normalize_recorder_envelope(
        RecorderEnvelope(
            schema_version="0.2",
            protocol="otlp.genai",
            event_id="span-64",
            payload={
                "name": "chat gpt-5",
                "trace_id": "a" * 32,
                "span_id": "b" * 16,
                "start_time_unix_nano": "1786190400000000000",
                "end_time_unix_nano": "1786190400250000000",
                "attributes": {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.provider.name": "openai",
                    "gen_ai.request.model": "gpt-5",
                    "gen_ai.usage.input_tokens": 120,
                    "gen_ai.usage.output_tokens": 30,
                    "gen_ai.usage.input_tokens.cached": 20,
                    "gen_ai.response.finish_reasons": ["stop"],
                    "gen_ai.usage.cost": 0.012,
                    "gen_ai.usage.cost.currency": "usd",
                },
            },
        )
    )

    operational = normalized.normalized_payload["operational"]
    assert normalized.schema_version == "0.2"
    assert operational["provider"] == "openai"
    assert operational["tokens"]["input"] == {
        "value": 120.0,
        "provenance": "provider-reported",
    }
    assert operational["tokens"]["cached"]["value"] == 20
    assert operational["latency_ms"] == {
        "value": 250.0,
        "provenance": "client-measured",
    }
    assert operational["finish_reason"] == "stop"
    assert operational["cost"]["currency"] == "USD"


@pytest.mark.asyncio
async def test_context_tools_and_concurrency_are_exact_bounded_and_advisory(db) -> None:
    definition = await create_agent_definition(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=AgentDefinitionCreate(key="context-agent", name="Context Agent"),
    )
    version = await _agent_version(db, definition, version="context-v1", digest="3" * 64)
    bundle = await compile_context(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=ContextCompileRequest(
            provider="openai",
            model="gpt-5",
            tokenizer=TokenizerSpec(engine="tiktoken", name="cl100k_base"),
            max_tokens=20,
            items=[
                ContextItem(
                    id="policy",
                    content="Never disclose the customer's private identifier.",
                    relevance=1,
                    freshness=1,
                    evidence_refs=["urn:lians:policy:64"],
                    mandatory=True,
                ),
                ContextItem(
                    id="history",
                    content="The customer contacted support multiple times about the same claim.",
                    relevance=0.8,
                    freshness=0.8,
                    evidence_refs=["urn:lians:memory:64"],
                    compression=TraceableCompression(
                        content="Repeated claim contacts.",
                        method="extractive",
                        compressor_id="test-compressor",
                        compressor_version="1",
                        compressor_config_hash="4" * 64,
                    ),
                ),
            ],
        ),
    )
    bundle_output = context_bundle_out(bundle)
    assert bundle_output.exact_token_count is True
    assert bundle_output.compiled_tokens <= bundle_output.max_tokens
    assert bundle_output.reduction_ratio >= 0.25
    assert bundle_output.tokenizer.definition_hash == bundle_output.tokenizer_hash
    assert "definition" not in bundle.analysis["tokenizer"]

    registry = await create_tool_registry(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=ToolRegistryCreate(
            name="claims-tools",
            version="1",
            tools=[
                ToolDefinition(
                    name="claims.lookup",
                    description="Look up claim status",
                    capabilities=["claims.read"],
                    required_permission_scopes=["claims:read"],
                    input_schema={
                        "type": "object",
                        "properties": {
                            "claim_id": {
                                "type": "string",
                                "description": "The claim identifier",
                            }
                        },
                        "required": ["claim_id"],
                    },
                ),
                ToolDefinition(
                    name="claims.pay",
                    description="Release a claim payment",
                    capabilities=["claims.pay"],
                    required_permission_scopes=["claims:pay"],
                    read_only=False,
                    consequential=True,
                ),
                ToolDefinition(
                    name="claims.broken",
                    description="Look up claim history",
                    capabilities=["claims.read"],
                    required_permission_scopes=["claims:read"],
                ),
            ],
        ),
    )
    selection = await select_tools(
        db,
        registry=registry,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=ToolSelectRequest(
            registry_version_id=registry.id,
            query="look up claim status",
            required_capabilities=["claims.read"],
            granted_permission_scopes=["claims:pay", "claims:read"],
            tokenizer=TokenizerSpec(engine="tiktoken", name="cl100k_base"),
            schema_token_budget=100,
            recent_failures=[
                ToolFailureObservation(
                    tool_name="claims.broken", error_code="timeout", consecutive_count=3
                )
            ],
        ),
    )
    selection_output = tool_selection_out(selection, registry_hash=registry.registry_hash)
    assert [tool.name for tool in selection_output.selected_tools] == ["claims.lookup"]
    assert selection_output.advisory_only is True
    assert (
        "description"
        not in selection_output.selected_tools[0].slimmed_input_schema["properties"]["claim_id"]
    )
    assert {item["reason"] for item in selection_output.excluded_tools} >= {
        "consequential_tool_not_allowed",
        "failed_loop_threshold",
    }

    plan = await create_concurrency_plan(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=ConcurrencyPlanRequest(
            agent_version_id=version.id,
            max_parallelism=4,
            calls=[
                ToolCallNode(id="read-a", tool_name="claims.lookup"),
                ToolCallNode(id="read-b", tool_name="claims.lookup"),
                ToolCallNode(
                    id="pay",
                    tool_name="claims.pay",
                    depends_on=["read-a", "read-b"],
                    read_only=False,
                    consequential=True,
                ),
            ],
        ),
    )
    assert plan.batches == [["read-a", "read-b"], ["pay"]]


@pytest.mark.asyncio
async def test_evaluation_routing_release_outcomes_and_learning_form_one_evidence_chain(db) -> None:
    decision = await _recorded_decision(db)
    definition = await create_agent_definition(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=AgentDefinitionCreate(key="claims-agent", name="Claims Agent"),
    )
    baseline_version = await _agent_version(db, definition, version="baseline", digest="5" * 64)
    candidate_version = await _agent_version(db, definition, version="candidate", digest="6" * 64)
    regressed_version = await _agent_version(db, definition, version="regressed", digest="c" * 64)
    case = await create_eval_case_from_decision(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=EvalCaseFromDecision(
            decision_id=decision.id,
            decision_receipt_hash=RECEIPT_HASH,
            name="claims-regression-64",
            tags=["production", "regression"],
        ),
    )
    suite = await create_eval_suite(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=EvalSuiteCreate(
            name="claims-eval",
            version="1",
            case_ids=[case.id],
            repetitions=2,
            improvement_contract=ImprovementContract(
                primary_metric=MetricObjective(
                    name="quality", direction="maximize", minimum_improvement=0.1
                ),
                protected_metrics=[
                    ProtectedMetric(
                        name="safety",
                        direction="maximize",
                        maximum_degradation=0,
                        minimum=0.9,
                        critical=True,
                    ),
                    ProtectedMetric(
                        name="latency_ms",
                        direction="minimize",
                        maximum_degradation=0,
                        maximum=100,
                    ),
                    ProtectedMetric(
                        name="cost", direction="minimize", maximum_degradation=0, maximum=0.05
                    ),
                ],
            ),
        ),
    )
    baseline_run = await create_eval_run(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=_run_body(
            suite_id=suite.id,
            case=case,
            version=baseline_version,
            quality=0.5,
            safety=0.95,
            latency=80,
            cost=0.04,
        ),
    )
    candidate_run = await create_eval_run(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=_run_body(
            suite_id=suite.id,
            case=case,
            version=candidate_version,
            quality=0.8,
            safety=0.96,
            latency=50,
            cost=0.02,
        ),
    )
    regressed_run = await create_eval_run(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=_run_body(
            suite_id=suite.id,
            case=case,
            version=regressed_version,
            quality=0.9,
            safety=0.80,
            latency=40,
            cost=0.01,
        ),
    )
    comparison = await create_comparison(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        baseline_run_id=baseline_run.id,
        candidate_run_id=candidate_run.id,
    )
    assert comparison.verdict == "eligible_for_review"
    assert comparison.critical_invariants_passed is True
    cost_result = next(item for item in comparison.aggregates if item["name"] == "cost")
    assert cost_result["candidate_mean"] <= cost_result["baseline_mean"] * 0.5

    signer = _signer()
    eval_attestation = await create_evaluation_attestation(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=EvaluationAttestationCreate(
            comparison_id=comparison.id,
            claims=["Candidate improves claims quality under the pinned suite."],
        ),
        signer=signer,
    )
    assert verify_evaluation_attestation(evaluation_attestation_out(eval_attestation)).valid
    assert eval_attestation.payload["decision_receipt_hashes"] == [RECEIPT_HASH]

    regressed_comparison = await create_comparison(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        baseline_run_id=baseline_run.id,
        candidate_run_id=regressed_run.id,
    )
    assert regressed_comparison.verdict == "protected_regression"
    regressed_attestation = await create_evaluation_attestation(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=EvaluationAttestationCreate(comparison_id=regressed_comparison.id),
        signer=signer,
    )
    with pytest.raises(ReleaseContractError, match="passing invariants"):
        await create_release_candidate(
            db,
            namespace=NAMESPACE,
            barrier_group=None,
            principal_ref=PRINCIPAL,
            body=ReleaseCandidateCreate(
                name="blocked-regression",
                version="2026.08.08-regressed",
                agent_version_id=regressed_version.id,
                evaluation_attestation_id=regressed_attestation.id,
                environment_manifest={
                    "image_digest": "sha256:" + "8" * 64,
                    "dependency_lock_hash": "9" * 64,
                    "runtime_policy_hash": "a" * 64,
                },
            ),
        )

    study = await create_optimization_study(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=OptimizationStudyCreate(
            name="claims-advisory-study",
            suite_id=suite.id,
            baseline_agent_version_id=baseline_version.id,
            comparison_ids=[comparison.id],
            objective={"quality": "maximize", "cost": "minimize"},
        ),
    )
    runtime_policy = await create_runtime_policy(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=RuntimePolicyCreate(
            name="claims-runtime",
            version="1",
            quality_floor=0.7,
            objective=RoutingObjective(
                quality_metric="quality",
                latency_metric="latency_ms",
                cost_metric="cost",
                latency_weight=0.5,
                cost_weight=0.5,
            ),
            request_budget=RequestBudget(
                max_input_tokens=1000,
                max_output_tokens=200,
                max_cost=0.05,
                currency="USD",
                deadline_ms=100,
            ),
            timeout_retry_policy=TimeoutRetryPolicy(
                attempt_timeout_ms=100,
                max_attempts=2,
                retry_on=["timeout"],
                total_retry_budget_ms=200,
            ),
            cache_policy=RuntimeCachePolicy(modes=["exact_response", "tool_result"]),
        ),
    )
    route = await create_routing_decision(
        db,
        policy=runtime_policy,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=RouteDecideRequest(
            runtime_policy_version_id=runtime_policy.id,
            input_hash="7" * 64,
            input_tokens=200,
            requested_output_tokens=100,
            candidates=[
                RoutingCandidate(
                    agent_version_id=candidate_version.id,
                    evaluation_attestation_id=eval_attestation.id,
                    provider="openai",
                    model="gpt-5",
                )
            ],
        ),
    )
    assert route.agent_version_id == candidate_version.id
    assert route.selected["quality"] == 0.8
    route_overheads = [route.overhead_ms]
    for sample in range(1, 20):
        sampled_route = await create_routing_decision(
            db,
            policy=runtime_policy,
            namespace=NAMESPACE,
            barrier_group=None,
            principal_ref=PRINCIPAL,
            body=RouteDecideRequest(
                runtime_policy_version_id=runtime_policy.id,
                input_hash=f"{sample:064x}",
                input_tokens=200,
                requested_output_tokens=100,
                candidates=[
                    RoutingCandidate(
                        agent_version_id=candidate_version.id,
                        evaluation_attestation_id=eval_attestation.id,
                        provider="openai",
                        model="gpt-5",
                    )
                ],
            ),
        )
        route_overheads.append(sampled_route.overhead_ms)
    assert quantiles(route_overheads, n=20, method="inclusive")[18] < 25

    cache_request = CacheAccessRequest(
        runtime_policy_version_id=runtime_policy.id,
        mode="exact_response",
        operation="lookup",
        agent_version_id=candidate_version.id,
        provider="openai",
        model="gpt-5",
        request_hash="b" * 64,
        permission_scopes=["claims:read"],
        release_reference="release-64",
    )
    cache_decision, cache_payload = await access_runtime_cache(
        db,
        policy=runtime_policy,
        version=candidate_version,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=cache_request,
    )
    assert cache_decision.disposition == "bypass"
    assert cache_payload is None
    assert "runtime_cache_disabled" in cache_decision.reason_codes
    other_permission_document = _cache_key_document(
        namespace=NAMESPACE,
        barrier_group=None,
        policy=runtime_policy,
        version=candidate_version,
        body=cache_request.model_copy(update={"permission_scopes": ["claims:write"]}),
    )
    other_tenant_document = _cache_key_document(
        namespace="other-tenant",
        barrier_group=None,
        policy=runtime_policy,
        version=candidate_version,
        body=cache_request,
    )
    assert sha256_json(other_permission_document) != cache_decision.cache_key_hash
    assert sha256_json(other_tenant_document) != cache_decision.cache_key_hash

    consequential_cache, _ = await access_runtime_cache(
        db,
        policy=runtime_policy,
        version=candidate_version,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=cache_request.model_copy(update={"read_only": False, "consequential": True}),
    )
    assert consequential_cache.disposition == "bypass"
    assert "consequential_or_mutating_replay_forbidden" in consequential_cache.reason_codes

    release_candidate = await create_release_candidate(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=ReleaseCandidateCreate(
            name="claims-agent",
            version="2026.08.08",
            agent_version_id=candidate_version.id,
            evaluation_attestation_id=eval_attestation.id,
            optimization_study_id=study.id,
            environment_manifest={
                "image_digest": "sha256:" + "8" * 64,
                "dependency_lock_hash": "9" * 64,
                "runtime_policy_hash": runtime_policy.policy_hash,
            },
        ),
    )
    policy = GatePolicySet(
        id=uuid4(),
        namespace=NAMESPACE,
        barrier_group=None,
        name="release-policy",
        version="1",
        status="active",
        default_disposition="deny",
        protected_actions=["release.deploy"],
        target_ref_prefixes=["urn:lians:release-candidate:"],
        enforcement_principal_ids=[PRINCIPAL],
        maximum_permit_ttl_seconds=60,
        created_by=PRINCIPAL,
        policy_hash="a" * 64,
        metadata_={},
    )
    db.add(policy)
    await db.flush()
    approval = await create_gate_approval_attestation(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_id="lians:principal:v1:oidc:release-owner",
        principal_type="human",
        role="owner",
        auth_method="oidc_bearer",
        credential_id="release-owner-credential",
        policy=policy,
        body=GateApprovalAttestationCreate(
            action="release.deploy",
            decision_id=decision.id,
            policy_set_id=policy.id,
            target_ref=release_target_ref(release_candidate.id),
            target_barrier_group=None,
            receipt_hash=RECEIPT_HASH,
            status="approved",
            evidence_refs=[f"urn:lians:evaluation-attestation:{eval_attestation.id}"],
        ),
    )
    release_attestation = await create_release_attestation(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=ReleaseAttestationCreate(
            release_candidate_id=release_candidate.id,
            approval_attestation_ids=[approval.id],
        ),
        signer=signer,
    )
    assert verify_release_attestation(release_attestation_out(release_attestation)).valid
    assert release_attestation.payload["automatic_deployment_authorized"] is False

    deploy_base = datetime(2026, 8, 8, 14, tzinfo=UTC)
    shadow = await create_deployment(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=DeploymentCreate(
            release_attestation_id=release_attestation.id,
            stage="shadow",
            traffic_percentage=0,
            environment="production",
            external_deployment_reference="deploy-shadow-64",
            status="healthy",
            evidence={"checks": ["smoke", "security"]},
            deployed_at=deploy_base,
        ),
    )
    canary = await create_deployment(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=DeploymentCreate(
            release_attestation_id=release_attestation.id,
            stage="canary",
            traffic_percentage=5,
            environment="production",
            external_deployment_reference="deploy-canary-64",
            prior_deployment_id=shadow.id,
            status="healthy",
            evidence={"window_seconds": 3600},
            deployed_at=deploy_base + timedelta(hours=1),
        ),
    )
    healthy_production = await create_deployment(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=DeploymentCreate(
            release_attestation_id=release_attestation.id,
            stage="production",
            traffic_percentage=100,
            environment="production",
            external_deployment_reference="deploy-production-64",
            prior_deployment_id=canary.id,
            status="healthy",
            evidence={"window_seconds": 7200},
            deployed_at=deploy_base + timedelta(hours=2),
        ),
    )
    failed_production = await create_deployment(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=DeploymentCreate(
            release_attestation_id=release_attestation.id,
            stage="production",
            traffic_percentage=100,
            environment="production",
            external_deployment_reference="deploy-production-failed-64",
            prior_deployment_id=canary.id,
            status="failed",
            evidence={"alert": "quality_floor"},
            deployed_at=deploy_base + timedelta(hours=3),
        ),
    )
    rollback = await create_rollback(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=RollbackCreate(
            deployment_id=failed_production.id,
            target_deployment_id=healthy_production.id,
            reason_code="quality.regression",
            evidence={"alert": "quality_floor"},
            external_rollback_reference="rollback-64",
            rolled_back_at=deploy_base + timedelta(hours=3, minutes=5),
        ),
    )
    assert rollback.target_deployment_id == healthy_production.id

    baseline_start = datetime(2026, 8, 1, tzinfo=UTC)
    current_start = datetime(2026, 8, 3, tzinfo=UTC)
    for index, (occurred_at, value) in enumerate(
        [
            (baseline_start, 0.95),
            (baseline_start + timedelta(hours=1), 0.94),
            (current_start, 0.75),
            (current_start + timedelta(hours=1), 0.70),
        ]
    ):
        await create_outcome(
            db,
            namespace=NAMESPACE,
            barrier_group=None,
            principal_ref=PRINCIPAL,
            body=OutcomeCreate(
                agent_version_id=candidate_version.id,
                deployment_id=healthy_production.id,
                correlation_id=f"claim-outcome-{index}",
                kind="business",
                metrics=[
                    OutcomeMetric(name="quality", value=value, unit="score", provenance="external")
                ],
                payload={"cohort": "synthetic"},
                occurred_at=occurred_at,
            ),
        )
    signal, drift_proposal = await analyze_drift(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=DriftAnalyzeRequest(
            agent_version_id=candidate_version.id,
            metric_name="quality",
            baseline_start=baseline_start,
            baseline_end=datetime(2026, 8, 2, tzinfo=UTC),
            current_start=current_start,
            current_end=datetime(2026, 8, 4, tzinfo=UTC),
            direction="decrease",
            threshold=0.1,
        ),
    )
    assert signal.drifted is True
    assert drift_proposal is not None
    assert drift_proposal.status == "awaiting_customer_approval"
    assert drift_proposal.recommendation["automatic_production_change_authorized"] is False

    feedback, feedback_proposal = await create_feedback(
        db,
        namespace=NAMESPACE,
        barrier_group=None,
        principal_ref=PRINCIPAL,
        body=FeedbackCreate(
            agent_version_id=candidate_version.id,
            decision_id=decision.id,
            decision_receipt_hash=RECEIPT_HASH,
            kind="incident",
            payload={"summary": "Customer corrected the claim classification."},
        ),
    )
    assert feedback.generated_eval_case_id is not None
    assert feedback_out(feedback).payload["summary"].startswith("Customer corrected")
    assert feedback_proposal.proposal_type == "regression_case"
    assert feedback_proposal.status == "awaiting_customer_approval"
