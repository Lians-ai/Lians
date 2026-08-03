"""Focused contracts for authoritative Gate action/target routing."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from lians.api.routes_control import (
    _assert_policy_selection,
    _matching_prefix_length,
    _policy_selectors_overlap,
)
from lians.control_models import GatePolicySet
from lians.control_schemas import GateEvaluationRequest, GatePolicySetCreate
from pydantic import ValidationError

MEDIATOR = "lians:principal:v1:api-key:00000000-0000-0000-0000-000000000001"
REQUEST_HASH = "a" * 64


def _policy(*, name: str, actions: list[str], prefixes: list[str]) -> GatePolicySet:
    return GatePolicySet(
        id=uuid4(),
        namespace="tenant-a",
        name=name,
        version="1",
        status="active",
        default_disposition="deny",
        protected_actions=actions,
        target_ref_prefixes=prefixes,
        enforcement_principal_ids=[MEDIATOR],
        maximum_permit_ttl_seconds=60,
        created_by="principal:v1:test",
        policy_hash="0" * 64,
    )


def test_policy_contract_requires_authoritative_selectors():
    with pytest.raises(ValidationError):
        GatePolicySetCreate(
            name="release",
            version="1",
            rules=[{"name": "deny-on-failure"}],
        )


def test_evaluation_requires_decision_and_target_but_not_policy_choice():
    decision_id = uuid4()
    request = GateEvaluationRequest(
        action="order.release",
        target_ref="urn:lians:order:123",
        decision_id=decision_id,
        enforcement_principal_id=MEDIATOR,
        permit_ttl_seconds=30,
        execution_request_hash=REQUEST_HASH,
    )
    assert request.policy_set_id is None
    with pytest.raises(ValidationError):
        GateEvaluationRequest(action="order.release", decision_id=decision_id)


def test_longest_prefix_is_specific_and_action_must_match_exactly():
    policy = _policy(
        name="release",
        actions=["order.release"],
        prefixes=["urn:lians:order:", "urn:lians:order:regulated:"],
    )
    assert (
        _matching_prefix_length(
            policy, "order.release", "urn:lians:order:regulated:123"
        )
        == len("urn:lians:order:regulated:")
    )
    assert _matching_prefix_length(policy, "order.read", "urn:lians:order:123") == -1


def test_prefix_matching_requires_an_explicit_resource_boundary():
    exact = _policy(
        name="production",
        actions=["order.release"],
        prefixes=["https://broker.example/orders/prod"],
    )
    descendants = _policy(
        name="production-tree",
        actions=["order.release"],
        prefixes=["https://broker.example/orders/prod/"],
    )
    assert (
        _matching_prefix_length(
            exact, "order.release", "https://broker.example/orders/production"
        )
        == -1
    )
    assert _matching_prefix_length(
        descendants, "order.release", "https://broker.example/orders/prod/123"
    ) == len("https://broker.example/orders/prod/")


def test_overlapping_action_and_prefix_mappings_are_detected():
    broad = _policy(
        name="broad",
        actions=["order.release"],
        prefixes=["urn:lians:order:"],
    )
    narrow = _policy(
        name="narrow",
        actions=["order.release"],
        prefixes=["urn:lians:order:regulated:"],
    )
    unrelated = _policy(
        name="read",
        actions=["order.read"],
        prefixes=["urn:lians:order:"],
    )
    assert _policy_selectors_overlap(broad, narrow) is True
    assert _policy_selectors_overlap(broad, unrelated) is False


def test_caller_policy_fields_are_assertions_only():
    policy = _policy(
        name="release",
        actions=["order.release"],
        prefixes=["urn:lians:order:"],
    )
    request = GateEvaluationRequest(
        action="order.release",
        target_ref="urn:lians:order:123",
        decision_id=uuid4(),
        enforcement_principal_id=MEDIATOR,
        permit_ttl_seconds=30,
        execution_request_hash=REQUEST_HASH,
        policy_set_id=uuid4(),
    )
    with pytest.raises(HTTPException) as error:
        _assert_policy_selection(request, policy)
    assert error.value.status_code == 409
