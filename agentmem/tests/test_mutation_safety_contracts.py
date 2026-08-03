"""Mutation retry and optimistic-concurrency contracts (definitions only)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from lians import memory_service
from lians.api.routes_admin import router as admin_router
from lians.api.routes_admissions import router as admissions_router
from lians.api.routes_conflicts import router as conflicts_router
from lians.api.routes_control import router as control_router
from lians.api.routes_graph import router as graph_router
from lians.api.routes_identity import admin_router as identity_admin_router
from lians.api.routes_integrations import router as integrations_router
from lians.api.routes_scim import admin_router as scim_admin_router
from lians.api.routes_supersessions import router as supersessions_router
from lians.api.routes_validmind import ValidMindUpdate
from lians.api.routes_webhooks import WebhookUpdateRequest
from lians.api.routes_webhooks import router as webhooks_router
from lians.api.routes_workload_credentials import router as workload_router
from lians.control_schemas import (
    ClosureAttestationCreate,
    InvestigationCaseUpdate,
    RemediationTaskCreate,
    RemediationTaskUpdate,
)
from lians.governance_schemas import (
    NamespaceGovernancePolicyUpdate,
    NamespaceGovernanceStatusUpdate,
)
from lians.governance_service import put_governance_policy
from lians.graph_service import relate
from lians.models import Relationship
from lians.mutation_safety import (
    MutationVersionConflict,
    assert_expected_updated_at,
    reject_non_replayable_idempotency_key,
)
from lians.schemas import (
    BarrierGroupAssign,
    NamespaceBillingIn,
    RetentionPolicyIn,
    SupersessionAction,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def test_timestamp_precondition_compares_the_persisted_utc_instant() -> None:
    # Some database drivers return a naive value for a UTC timestamp column.
    persisted = datetime(2026, 8, 2, 12, 30, 15, 123456)  # noqa: DTZ001
    same_instant = datetime(
        2026,
        8,
        2,
        8,
        30,
        15,
        123456,
        tzinfo=timezone(-timedelta(hours=4)),
    )

    assert_expected_updated_at(persisted, same_instant)

    with pytest.raises(MutationVersionConflict, match="Resource version conflict"):
        assert_expected_updated_at(
            persisted,
            same_instant + timedelta(microseconds=1),
        )


def test_non_replayable_dependency_rejects_retry_metadata() -> None:
    assert reject_non_replayable_idempotency_key(None) is None

    with pytest.raises(HTTPException) as captured:
        reject_non_replayable_idempotency_key("retry-secret-or-destructive-call")

    assert captured.value.status_code == 400
    assert "reconcile authoritative state" in str(captured.value.detail)


@pytest.mark.asyncio
async def test_cached_subject_key_still_acquires_the_erasure_fence(monkeypatch) -> None:
    calls: list[tuple[object, str, str]] = []

    async def fence(db: object, subject_id: str, namespace: str) -> str:
        calls.append((db, subject_id, namespace))
        return subject_id

    cached = b"cached-subject-key"
    monkeypatch.setattr(memory_service, "assert_subject_not_erased", fence)
    monkeypatch.setattr(memory_service, "get_cached_dek", lambda *_args: cached)
    db = object()

    resolved = await memory_service._resolve_subject_key(
        db,  # type: ignore[arg-type]
        "lians:subject:v2:hmac-sha256:scope:digest",
        "tenant-a",
    )

    assert resolved == cached
    assert calls == [
        (db, "lians:subject:v2:hmac-sha256:scope:digest", "tenant-a")
    ]


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (BarrierGroupAssign, "expected_group_name"),
        (RetentionPolicyIn, "expected_updated_at"),
        (NamespaceBillingIn, "expected_updated_at"),
        (SupersessionAction, "expected_superseded_by"),
        (NamespaceGovernancePolicyUpdate, "expected_version"),
        (NamespaceGovernanceStatusUpdate, "expected_version"),
        (WebhookUpdateRequest, "expected_updated_at"),
        (ValidMindUpdate, "expected_updated_at"),
        (InvestigationCaseUpdate, "expected_updated_at"),
        (RemediationTaskCreate, "expected_case_updated_at"),
        (RemediationTaskUpdate, "expected_updated_at"),
        (ClosureAttestationCreate, "expected_updated_at"),
    ],
)
def test_mutable_contracts_require_a_caller_observed_precondition(model, field: str) -> None:
    assert model.model_fields[field].is_required()


def _dependency_calls(router, path: str, method: str) -> set[object]:
    matches = [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and method in route.methods
    ]
    assert len(matches) == 1, f"expected one {method} {path} route"
    return {dependency.call for dependency in matches[0].dependant.dependencies}


@pytest.mark.parametrize(
    ("router", "path", "method"),
    [
        (admin_router, "/v1/admin/api-keys", "POST"),
        (admin_router, "/v1/admin/api-keys/{key_id}", "DELETE"),
        (admin_router, "/v1/admin/api-keys/{key_id}/rotate", "POST"),
        (admin_router, "/v1/admin/barriers/{agent_id}", "DELETE"),
        (admin_router, "/v1/admin/retention/{namespace}/prune", "POST"),
        (
            admin_router,
            "/v1/admin/billing-metering/events/{event_id}/replay",
            "POST",
        ),
        (admissions_router, "/v1/admissions/{pending_id}/resolve", "POST"),
        (conflicts_router, "/v1/conflicts/{conflict_id}/resolve", "POST"),
        (supersessions_router, "/v1/supersessions/{memory_id}", "PATCH"),
        (graph_router, "/v1/graph/relate", "POST"),
        (graph_router, "/v1/graph/extract", "POST"),
        (graph_router, "/v1/graph/unrelate", "POST"),
        (webhooks_router, "/v1/webhooks", "POST"),
        (webhooks_router, "/v1/webhooks/{endpoint_id}", "DELETE"),
        (identity_admin_router, "/v1/admin/identity/providers/{provider_id}", "DELETE"),
        (identity_admin_router, "/v1/admin/identity/bindings/{binding_id}", "DELETE"),
        (workload_router, "/v1/identity/workload-credentials", "POST"),
        (
            workload_router,
            "/v1/identity/workload-credentials/{credential_id}/rotate",
            "POST",
        ),
        (
            workload_router,
            "/v1/identity/workload-credentials/{credential_id}",
            "DELETE",
        ),
        (integrations_router, "/v1/integrations/destinations", "POST"),
        (
            integrations_router,
            "/v1/integrations/destinations/{destination_id}/rotate-secrets",
            "POST",
        ),
        (
            integrations_router,
            "/v1/integrations/destinations/{destination_id}",
            "DELETE",
        ),
        (
            integrations_router,
            "/v1/integrations/deliveries/{delivery_id}/replay",
            "POST",
        ),
        (scim_admin_router, "/v1/admin/enterprise/scim/tenants", "POST"),
        (
            scim_admin_router,
            "/v1/admin/enterprise/scim/tenants/{tenant_id}",
            "DELETE",
        ),
        (
            scim_admin_router,
            "/v1/admin/enterprise/scim/tenants/{tenant_id}/credentials/{credential_id}/rotate",
            "POST",
        ),
        (
            scim_admin_router,
            "/v1/admin/enterprise/scim/tenants/{tenant_id}/credentials/{credential_id}",
            "DELETE",
        ),
        (control_router, "/v1/control/trust/issuers/{issuer_id}/revoke", "POST"),
        (
            control_router,
            "/v1/control/trust/issuers/{issuer_id}/keys/{key_id}/rotate",
            "POST",
        ),
        (
            control_router,
            "/v1/control/trust/issuers/{issuer_id}/keys/{key_id}/revoke",
            "POST",
        ),
        (control_router, "/v1/control/gate/approvals", "POST"),
        (
            control_router,
            "/v1/control/gate/approvals/{approval_id}/supersede",
            "POST",
        ),
        (control_router, "/v1/control/gate/evaluate", "POST"),
        (control_router, "/v1/control/gate/permits/consume", "POST"),
        (
            control_router,
            "/v1/control/investigations/tasks/{task_id}/close",
            "POST",
        ),
        (
            control_router,
            "/v1/control/investigations/cases/{case_id}/close",
            "POST",
        ),
    ],
)
def test_non_replayable_routes_reject_idempotency_keys(
    router,
    path: str,
    method: str,
) -> None:
    assert reject_non_replayable_idempotency_key in _dependency_calls(
        router,
        path,
        method,
    )


TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_postgres = pytest.mark.skipif(
    not (TEST_DB_URL and "postgresql" in TEST_DB_URL),
    reason="TEST_DATABASE_URL is not a PostgreSQL URL",
)


@requires_postgres
@pytest.mark.asyncio
async def test_parallel_first_governance_writers_have_one_winner() -> None:
    """The advisory missing-row boundary makes expected_version=0 a real CAS."""
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    namespace = f"mutation-contract-{uuid4().hex}"

    async def write(limit: int):
        async with sessions() as db:
            await db.execute(
                text("SELECT set_config('app.current_namespace', '__admin__', false)")
            )
            await db.execute(
                text("SELECT set_config('agentmem.barrier_group', '', false)")
            )
            return await put_governance_policy(
                db,
                namespace,
                NamespaceGovernancePolicyUpdate(
                    expected_version=0,
                    memory_writes_daily_limit=limit,
                ),
                actor_id="mutation-contract-test",
            )

    try:
        results = await asyncio.gather(write(1), write(2), return_exceptions=True)
    finally:
        await engine.dispose()

    winners = [result for result in results if not isinstance(result, BaseException)]
    conflicts = [
        result
        for result in results
        if isinstance(result, HTTPException) and result.status_code == 409
    ]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert winners[0].policy_version == 1


@requires_postgres
@pytest.mark.asyncio
async def test_parallel_identical_graph_writers_converge_on_one_live_edge() -> None:
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    namespace = f"graph-mutation-contract-{uuid4().hex}"
    agent_id = "graph-writer"
    event_time = datetime.now(timezone.utc)

    async def write():
        async with sessions() as db:
            await db.execute(
                text("SELECT set_config('app.current_namespace', '__admin__', false)")
            )
            await db.execute(
                text("SELECT set_config('agentmem.barrier_group', '', false)")
            )
            return await relate(
                db,
                namespace,
                agent_id=agent_id,
                src_entity="alpha",
                rel_type="controls",
                dst_entity="beta",
                event_time=event_time,
            )

    try:
        first, second = await asyncio.gather(write(), write())
        async with sessions() as db:
            await db.execute(
                text("SELECT set_config('app.current_namespace', '__admin__', false)")
            )
            count = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(Relationship)
                        .where(
                            Relationship.namespace == namespace,
                            Relationship.agent_id == agent_id,
                            Relationship.src_entity == "alpha",
                            Relationship.rel_type == "controls",
                            Relationship.dst_entity == "beta",
                            Relationship.valid_to.is_(None),
                        )
                    )
                ).scalar_one()
            )
    finally:
        await engine.dispose()

    assert first.id == second.id
    assert count == 1
