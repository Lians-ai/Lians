"""Focused contracts for mediated, one-time Gate execution permits."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from lians.api import routes_control
from lians.api.deps import AuthContext
from lians.control_models import (
    GateDecisionRecord,
    GateExecutionPermit,
    GateExecutionPermitConsumption,
    GatePolicySet,
)
from lians.control_schemas import (
    GateDecisionOut,
    GateEvaluationOut,
    GateEvaluationRequest,
    GateExecutionPermitConsume,
    GateExecutionPermitIssued,
    GatePolicySetCreate,
)
from lians.control_service import (
    GatePermitRedemptionError,
    consume_gate_execution_permit,
    evaluate_gate,
    policy_definition_payload,
    sha256_json,
)
from lians.governance_service import GovernanceViolation
from pydantic import ValidationError

MEDIATOR = "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000001"
EVALUATOR = "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000002"
REQUEST_HASH = "a" * 64


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, results=()):
        self.results = iter(results)
        self.added = []
        self.execute_count = 0
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    def get_bind(self):
        return self.bind

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def execute(self, _statement):
        self.execute_count += 1
        return _ScalarResult(next(self.results))


class _RouteSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.refreshed = []

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, row):
        self.refreshed.append(row)


def _policy(*, ttl: int = 60) -> GatePolicySet:
    return GatePolicySet(
        id=uuid4(),
        namespace="tenant-a",
        barrier_group=None,
        name="broker-bound-release",
        version="1",
        status="active",
        default_disposition="deny",
        protected_actions=["order.release"],
        target_ref_prefixes=["urn:lians:order:"],
        enforcement_principal_ids=[MEDIATOR],
        maximum_permit_ttl_seconds=ttl,
        created_by=EVALUATOR,
        policy_hash="b" * 64,
    )


def _request(**updates) -> GateEvaluationRequest:
    values = {
        "principal_id": EVALUATOR,
        "action": "order.release",
        "target_ref": "urn:lians:order:123",
        "decision_id": uuid4(),
        "enforcement_principal_id": MEDIATOR,
        "permit_ttl_seconds": 30,
        "execution_request_hash": REQUEST_HASH,
    }
    values.update(updates)
    return GateEvaluationRequest(**values)


def _pass_rule():
    return SimpleNamespace(
        id=uuid4(),
        name="explicit-pass",
        description=None,
        priority=1,
        enabled=True,
        action_on_failure="deny",
        applies_to_decision_types=[],
        applies_to_risk_levels=[],
        required_receipt_grade=None,
        require_trusted_issuer=False,
        require_sources_current=False,
        require_policy_attached=False,
        required_principal_scopes=[],
        minimum_approval_count=0,
        required_approval_roles=[],
        allowed_approval_principal_types=[],
        maximum_approval_age_seconds=None,
        require_information_barrier_match=False,
        block_untrusted_content=False,
        max_untrusted_content_score=None,
    )


def test_policy_requires_canonical_unique_mediator_identities_and_bounded_ttl():
    common = {
        "name": "release",
        "version": "1",
        "protected_actions": ["order.release"],
        "target_ref_prefixes": ["urn:lians:order:"],
        "rules": [{"name": "pass"}],
    }
    with pytest.raises(ValidationError):
        GatePolicySetCreate(**common, enforcement_principal_ids=[])
    with pytest.raises(ValidationError):
        GatePolicySetCreate(
            **common, enforcement_principal_ids=["mediator"], maximum_permit_ttl_seconds=60
        )
    with pytest.raises(ValidationError):
        GatePolicySetCreate(
            **common,
            enforcement_principal_ids=[MEDIATOR, MEDIATOR],
            maximum_permit_ttl_seconds=60,
        )
    with pytest.raises(ValidationError):
        GatePolicySetCreate(
            **common,
            enforcement_principal_ids=[MEDIATOR],
            maximum_permit_ttl_seconds=301,
        )
    for noncanonical_target in (
        "https://broker.example/orders/café",
        "https://broker.example/orders/%2fadmin",
        "https://broker.example/orders\\admin",
    ):
        with pytest.raises(ValidationError):
            GatePolicySetCreate(
                **{**common, "target_ref_prefixes": [noncanonical_target]},
                enforcement_principal_ids=[MEDIATOR],
            )


def test_policy_hash_covers_mediator_allowlist_and_permit_ttl():
    common = {
        "name": "release",
        "version": "1",
        "protected_actions": ["order.release"],
        "target_ref_prefixes": ["urn:lians:order:"],
        "rules": [{"name": "pass"}],
    }
    short = GatePolicySetCreate(
        **common,
        enforcement_principal_ids=[MEDIATOR],
        maximum_permit_ttl_seconds=30,
    )
    long = short.model_copy(update={"maximum_permit_ttl_seconds": 60})
    other_mediator = short.model_copy(
        update={
            "enforcement_principal_ids": [
                "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000003"
            ]
        }
    )
    short_hash = sha256_json(policy_definition_payload(short, None))
    assert short_hash != sha256_json(policy_definition_payload(long, None))
    assert short_hash != sha256_json(
        policy_definition_payload(other_mediator, None)
    )


def test_read_models_never_expose_execution_permit_material():
    assert "execution_permit" not in GateDecisionOut.model_json_schema()["properties"]
    assert "execution_permit" in GateEvaluationOut.model_json_schema()["properties"]


def test_permit_consume_token_is_secret_in_validation_and_representations():
    body = GateExecutionPermitConsume(
        permit_id=uuid4(),
        token="lians_permit_v1_" + ("x" * 43),
        action="order.release",
        target_ref="urn:lians:order:123",
        decision_id=uuid4(),
        execution_request_hash=REQUEST_HASH,
    )
    assert body.token.get_secret_value().startswith("lians_permit_v1_")
    assert body.token.get_secret_value() not in repr(body)


@pytest.mark.asyncio
async def test_allow_atomically_creates_exactly_one_digest_only_permit():
    db = _FakeSession()
    request = _request()
    verdict, issued = await evaluate_gate(
        db,
        namespace="tenant-a",
        barrier_group=None,
        policy=_policy(),
        rules=[_pass_rule()],
        request=request,
    )

    assert verdict.disposition == "allow"
    assert issued is not None
    assert issued.token.startswith("lians_permit_v1_")
    assert issued.token not in repr(issued)
    assert issued.row.token_digest == hashlib.sha256(
        issued.token.encode("ascii")
    ).hexdigest()
    assert issued.row.execution_request_hash == REQUEST_HASH
    assert [type(row) for row in db.added] == [GateDecisionRecord, GateExecutionPermit]


@pytest.mark.asyncio
async def test_restrictive_verdict_never_creates_a_permit():
    db = _FakeSession()
    verdict, issued = await evaluate_gate(
        db,
        namespace="tenant-a",
        barrier_group=None,
        policy=_policy(),
        rules=[],
        request=_request(),
    )
    assert verdict.disposition == "deny"
    assert issued is None
    assert [type(row) for row in db.added] == [GateDecisionRecord]


@pytest.mark.asyncio
async def test_evaluator_cannot_name_itself_as_the_enforcement_boundary():
    db = _FakeSession()
    with pytest.raises(ValueError, match="must be separate identities"):
        await evaluate_gate(
            db,
            namespace="tenant-a",
            barrier_group=None,
            policy=_policy(),
            rules=[_pass_rule()],
            request=_request(principal_id=MEDIATOR),
        )
    assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "target_ref"),
    (("retired", "urn:lians:order:123"), ("active", "urn:lians:other:123")),
)
async def test_service_rejects_inactive_or_out_of_selector_policy(
    status: str, target_ref: str
):
    db = _FakeSession()
    policy = _policy()
    policy.status = status
    with pytest.raises(ValueError, match="authoritatively covers"):
        await evaluate_gate(
            db,
            namespace="tenant-a",
            barrier_group=None,
            policy=policy,
            rules=[_pass_rule()],
            request=_request(target_ref=target_ref),
        )
    assert db.added == []


@pytest.mark.asyncio
async def test_mediator_consumption_checks_all_bindings_and_appends_without_mutation():
    token = "lians_permit_v1_" + ("x" * 43)
    now = datetime.now(UTC)
    evaluation = GateDecisionRecord(
        id=uuid4(),
        namespace="tenant-a",
        barrier_group=None,
        policy_set_id=uuid4(),
        policy_name="release",
        policy_version="1",
        policy_hash="b" * 64,
        principal_id=EVALUATOR,
        action="order.release",
        target_ref="urn:lians:order:123",
        enforcement_principal_id=MEDIATOR,
        execution_request_hash=REQUEST_HASH,
        decision_id=uuid4(),
        disposition="allow",
        reasons=[],
        applied_rules=[],
        input_snapshot={},
        request_hash="c" * 64,
        evaluation_hash="d" * 64,
        evaluated_at=now,
    )
    permit = GateExecutionPermit(
        id=uuid4(),
        namespace="tenant-a",
        barrier_group=None,
        evaluation_id=evaluation.id,
        policy_set_id=evaluation.policy_set_id,
        decision_id=evaluation.decision_id,
        enforcement_principal_id=MEDIATOR,
        action=evaluation.action,
        target_ref=evaluation.target_ref,
        execution_request_hash=REQUEST_HASH,
        token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(seconds=60),
        grant_hash="e" * 64,
    )
    body = GateExecutionPermitConsume(
        permit_id=permit.id,
        token=token,
        action=permit.action,
        target_ref=permit.target_ref,
        decision_id=permit.decision_id,
        execution_request_hash=REQUEST_HASH,
    )
    db = _FakeSession(results=[permit, evaluation, None])

    consumption = await consume_gate_execution_permit(
        db,
        namespace="tenant-a",
        caller_barrier=None,
        principal_id=MEDIATOR,
        body=body,
    )

    assert isinstance(consumption, GateExecutionPermitConsumption)
    assert consumption.permit_id == permit.id
    assert consumption.token_digest == permit.token_digest
    assert db.added == [consumption]


@pytest.mark.asyncio
async def test_wrong_mediator_fails_with_the_same_non_oracular_error():
    token = "lians_permit_v1_" + ("x" * 43)
    now = datetime.now(UTC)
    permit = GateExecutionPermit(
        id=uuid4(),
        namespace="tenant-a",
        evaluation_id=uuid4(),
        policy_set_id=uuid4(),
        decision_id=uuid4(),
        enforcement_principal_id=MEDIATOR,
        action="order.release",
        target_ref="urn:lians:order:123",
        execution_request_hash=REQUEST_HASH,
        token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
        issued_at=now,
        expires_at=now + timedelta(seconds=60),
        grant_hash="e" * 64,
    )
    db = _FakeSession(results=[permit, None, None])
    with pytest.raises(
        GatePermitRedemptionError, match="Execution permit is invalid or unusable"
    ) as caught:
        await consume_gate_execution_permit(
            db,
            namespace="tenant-a",
            caller_barrier=None,
            principal_id=EVALUATOR,
            body=GateExecutionPermitConsume(
                permit_id=permit.id,
                token=token,
                action=permit.action,
                target_ref=permit.target_ref,
                decision_id=permit.decision_id,
                execution_request_hash=REQUEST_HASH,
            ),
        )
    assert caught.value.outcome == "mismatched"
    assert db.added == []


@pytest.mark.asyncio
async def test_unknown_permit_uses_the_same_fixed_lookup_shape():
    db = _FakeSession(results=[None, None, None])
    with pytest.raises(
        GatePermitRedemptionError, match="Execution permit is invalid or unusable"
    ) as caught:
        await consume_gate_execution_permit(
            db,
            namespace="tenant-a",
            caller_barrier=None,
            principal_id=MEDIATOR,
            body=GateExecutionPermitConsume(
                permit_id=uuid4(),
                token="malformed-but-secret",
                action="order.release",
                target_ref="urn:lians:order:123",
                decision_id=uuid4(),
                execution_request_hash=REQUEST_HASH,
            ),
        )
    assert caught.value.outcome == "rejected"
    assert db.execute_count == 3
    assert db.added == []


@pytest.mark.asyncio
async def test_successful_route_consumption_stages_one_protected_action_before_commit(
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime.now(UTC)
    row = GateExecutionPermitConsumption(
        id=uuid4(),
        namespace="tenant-a",
        barrier_group=None,
        permit_id=uuid4(),
        evaluation_id=uuid4(),
        policy_set_id=uuid4(),
        decision_id=uuid4(),
        consuming_principal_id=MEDIATOR,
        action="order.release",
        target_ref="urn:lians:order:123",
        execution_request_hash=REQUEST_HASH,
        grant_hash="e" * 64,
        token_digest="f" * 64,
        consumed_at=now,
        consumption_hash="1" * 64,
    )
    consume = AsyncMock(return_value=row)
    reserve = AsyncMock(return_value=None)
    audit = AsyncMock()
    meter = AsyncMock(return_value=None)
    monkeypatch.setattr(routes_control, "consume_gate_execution_permit", consume)
    monkeypatch.setattr(routes_control, "reserve_namespace_usage", reserve)
    monkeypatch.setattr(routes_control, "_audit", audit)
    monkeypatch.setattr(
        routes_control,
        "enqueue_protected_action_usage_event",
        meter,
    )
    db = _RouteSession()
    body = GateExecutionPermitConsume(
        permit_id=row.permit_id,
        token="lians_permit_v1_" + ("x" * 43),
        action=row.action,
        target_ref=row.target_ref,
        decision_id=row.decision_id,
        execution_request_hash=row.execution_request_hash,
    )

    result = await routes_control.consume_runtime_gate_permit(
        body=body,
        response=Response(),
        auth=AuthContext(
            "tenant-a",
            ["write"],
            principal_id=MEDIATOR,
        ),
        db=db,  # type: ignore[arg-type]
    )

    assert result.permit_id == row.permit_id
    reserve.assert_awaited_once_with(
        db,
        namespace="tenant-a",
        protected_actions=1,
    )
    audit.assert_awaited_once()
    meter.assert_awaited_once_with(
        db,
        namespace="tenant-a",
        permit_id=row.permit_id,
        occurred_at=now,
    )
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.refreshed == [row]


@pytest.mark.asyncio
async def test_protected_action_quota_denial_leaves_permit_unspent_and_unmetered(
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime.now(UTC)
    row = GateExecutionPermitConsumption(
        id=uuid4(),
        namespace="tenant-a",
        barrier_group=None,
        permit_id=uuid4(),
        evaluation_id=uuid4(),
        policy_set_id=uuid4(),
        decision_id=uuid4(),
        consuming_principal_id=MEDIATOR,
        action="order.release",
        target_ref="urn:lians:order:123",
        execution_request_hash=REQUEST_HASH,
        grant_hash="e" * 64,
        token_digest="f" * 64,
        consumed_at=now,
        consumption_hash="1" * 64,
    )
    consume = AsyncMock(return_value=row)
    reserve = AsyncMock(
        side_effect=GovernanceViolation(
            status_code=429,
            code="namespace_daily_quota_exceeded",
            message="The namespace daily quota for protected_actions would be exceeded.",
            namespace="tenant-a",
            extra={"metric": "protected_actions"},
        )
    )
    audit = AsyncMock()
    meter = AsyncMock()
    monkeypatch.setattr(routes_control, "consume_gate_execution_permit", consume)
    monkeypatch.setattr(routes_control, "reserve_namespace_usage", reserve)
    monkeypatch.setattr(routes_control, "_audit", audit)
    monkeypatch.setattr(routes_control, "enqueue_protected_action_usage_event", meter)
    db = _RouteSession()

    with pytest.raises(GovernanceViolation) as caught:
        await routes_control.consume_runtime_gate_permit(
            body=GateExecutionPermitConsume(
                permit_id=row.permit_id,
                token="lians_permit_v1_" + ("x" * 43),
                action=row.action,
                target_ref=row.target_ref,
                decision_id=row.decision_id,
                execution_request_hash=row.execution_request_hash,
            ),
            response=Response(),
            auth=AuthContext("tenant-a", ["write"], principal_id=MEDIATOR),
            db=db,  # type: ignore[arg-type]
        )

    assert caught.value.status_code == 429
    assert caught.value.detail["metric"] == "protected_actions"
    audit.assert_not_awaited()
    meter.assert_not_awaited()
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["rejected", "expired", "replayed", "mismatched"])
async def test_failed_or_replayed_redemption_never_stages_protected_action(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
):
    consume = AsyncMock(side_effect=GatePermitRedemptionError(outcome))
    audit = AsyncMock()
    meter = AsyncMock()
    monkeypatch.setattr(routes_control, "consume_gate_execution_permit", consume)
    monkeypatch.setattr(routes_control, "_audit", audit)
    monkeypatch.setattr(
        routes_control,
        "enqueue_protected_action_usage_event",
        meter,
    )
    db = _RouteSession()
    body = GateExecutionPermitConsume(
        permit_id=uuid4(),
        token="lians_permit_v1_" + ("x" * 43),
        action="order.release",
        target_ref="urn:lians:order:123",
        decision_id=uuid4(),
        execution_request_hash=REQUEST_HASH,
    )

    with pytest.raises(HTTPException) as caught:
        await routes_control.consume_runtime_gate_permit(
            body=body,
            response=Response(),
            auth=AuthContext(
                "tenant-a",
                ["write"],
                principal_id=MEDIATOR,
            ),
            db=db,  # type: ignore[arg-type]
        )

    assert caught.value.status_code == 403
    audit.assert_not_awaited()
    meter.assert_not_awaited()
    assert db.commits == 0
    assert db.rollbacks == 1


def test_evaluate_response_token_field_is_sensitive_read_only_and_not_repr_visible():
    issued = GateExecutionPermitIssued(
        permit_id=uuid4(),
        evaluation_id=uuid4(),
        enforcement_principal_id=MEDIATOR,
        action="order.release",
        target_ref="urn:lians:order:123",
        decision_id=uuid4(),
        execution_request_hash=REQUEST_HASH,
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        token="lians_permit_v1_" + ("x" * 43),
    )
    assert issued.token not in repr(issued)
    token_schema = issued.model_json_schema()["properties"]["token"]
    assert token_schema["readOnly"] is True
    assert token_schema["x-sensitive"] is True
