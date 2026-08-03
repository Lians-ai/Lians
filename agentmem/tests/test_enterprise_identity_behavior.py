"""Behavioral smoke coverage for enterprise identity and SCIM lifecycles."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from lians.api.deps import AuthContext, get_auth
from lians.config import get_settings
from lians.db import get_db
from lians.main import app


ADMIN_SECRET = "enterprise-behavior-admin-secret"
ADMIN_HEADERS = {"X-Admin-Secret": ADMIN_SECRET}


@pytest.mark.asyncio
async def test_enterprise_identity_workload_and_scim_lifecycle(db, monkeypatch):
    monkeypatch.setenv("ADMIN_SECRET", ADMIN_SECRET)
    monkeypatch.setenv("SCIM_RECONCILIATION_WORKER_ENABLED", "false")
    get_settings.cache_clear()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            provider_response = await client.post(
                "/v1/admin/identity/providers",
                headers=ADMIN_HEADERS,
                json={
                    "name": "Enterprise IdP",
                    "issuer": "https://idp.example.test",
                    "jwks_uri": "https://idp.example.test/.well-known/jwks.json",
                    "audiences": ["lians-api"],
                },
            )
            assert provider_response.status_code == 201, provider_response.text
            provider = provider_response.json()

            binding_response = await client.post(
                "/v1/admin/identity/bindings",
                headers=ADMIN_HEADERS,
                json={
                    "provider_id": provider["id"],
                    "external_subject": "enterprise-owner-1",
                    "namespace": "enterprise-behavior",
                    "principal_type": "human",
                    "role": "owner",
                },
            )
            assert binding_response.status_code == 201, binding_response.text
            binding = binding_response.json()
            listed_bindings = await client.get(
                "/v1/admin/identity/bindings?namespace=enterprise-behavior",
                headers=ADMIN_HEADERS,
            )
            assert listed_bindings.status_code == 200
            assert [row["id"] for row in listed_bindings.json()] == [binding["id"]]

            oidc_admin = AuthContext(
                namespace="enterprise-behavior",
                scopes=["read", "write", "admin", "erase"],
                principal_id="lians:principal:v1:oidc:test-owner",
                principal_type="human",
                role="owner",
                auth_method="oidc_bearer",
                credential_id=binding["id"],
            )

            async def override_oidc_auth():
                return oidc_admin

            app.dependency_overrides[get_auth] = override_oidc_auth
            created_response = await client.post(
                "/v1/identity/workload-credentials",
                json={
                    "label": "readonly-worker",
                    "role": "readonly",
                    "ttl_seconds": 3600,
                },
            )
            assert created_response.status_code == 201, created_response.text
            created = created_response.json()
            assert created["secret"].startswith("lians_wk_")
            assert created["status"] == "active"

            workload_inventory = await client.get(
                "/v1/identity/workload-credentials"
            )
            assert workload_inventory.status_code == 200
            assert len(workload_inventory.json()) == 1
            assert "secret" not in workload_inventory.json()[0]

            rotated_response = await client.post(
                f"/v1/identity/workload-credentials/{created['id']}/rotate",
                json={"expected_version": 1, "ttl_seconds": 3600},
            )
            assert rotated_response.status_code == 201, rotated_response.text
            rotated = rotated_response.json()
            assert rotated["rotated_from_id"] == created["id"]
            assert rotated["secret"] != created["secret"]

            app.dependency_overrides.pop(get_auth)
            old_whoami = await client.get(
                "/v1/identity/whoami",
                headers={"X-API-Key": created["secret"]},
            )
            new_whoami = await client.get(
                "/v1/identity/whoami",
                headers={"X-API-Key": rotated["secret"]},
            )
            assert old_whoami.status_code == 401
            assert new_whoami.status_code == 200, new_whoami.text
            assert new_whoami.json()["namespace"] == "enterprise-behavior"
            assert new_whoami.json()["principal_type"] == "workload"

            app.dependency_overrides[get_auth] = override_oidc_auth
            revoked_response = await client.delete(
                f"/v1/identity/workload-credentials/{rotated['id']}?expected_version=1"
            )
            assert revoked_response.status_code == 204, revoked_response.text
            app.dependency_overrides.pop(get_auth)
            revoked_whoami = await client.get(
                "/v1/identity/whoami",
                headers={"X-API-Key": rotated["secret"]},
            )
            assert revoked_whoami.status_code == 401

            tenant_response = await client.post(
                "/v1/admin/enterprise/scim/tenants",
                headers=ADMIN_HEADERS,
                json={
                    "namespace": "enterprise-behavior",
                    "provider_id": provider["id"],
                    "subject_attribute": "externalId",
                },
            )
            assert tenant_response.status_code == 201, tenant_response.text
            tenant_created = tenant_response.json()
            tenant = tenant_created["tenant"]
            scim_headers = {
                "Authorization": f"Bearer {tenant_created['bearer_token']}"
            }
            base = tenant_created["scim_base_path"]

            assert (await client.get(f"{base}/ServiceProviderConfig")).status_code == 401
            assert (
                await client.get(f"{base}/ServiceProviderConfig", headers=scim_headers)
            ).status_code == 200

            user_response = await client.post(
                f"{base}/Users",
                headers=scim_headers,
                json={
                    "externalId": "scim-subject-1",
                    "userName": "scim.user@example.test",
                    "displayName": "SCIM User",
                    "emails": [
                        {
                            "value": "scim.user@example.test",
                            "type": "work",
                            "primary": True,
                        }
                    ],
                    "active": True,
                },
            )
            assert user_response.status_code == 201, user_response.text
            user = user_response.json()
            assert user_response.headers["etag"] == 'W/"1"'

            group_response = await client.post(
                f"{base}/Groups",
                headers=scim_headers,
                json={
                    "externalId": "engineering",
                    "displayName": "Engineering",
                    "members": [{"value": user["id"]}],
                },
            )
            assert group_response.status_code == 201, group_response.text
            groups_response = await client.get(
                f"{base}/Groups?startIndex=1&count=1",
                headers=scim_headers,
            )
            assert groups_response.status_code == 200, groups_response.text
            groups = groups_response.json()
            assert groups["totalResults"] == 1
            assert groups["Resources"][0]["members"][0]["value"] == user["id"]

            disabled_response = await client.patch(
                f"/v1/admin/enterprise/scim/tenants/{tenant['id']}",
                headers=ADMIN_HEADERS,
                json={"expected_version": 1, "enabled": False},
            )
            assert disabled_response.status_code == 202, disabled_response.text
            job_location = disabled_response.headers["location"]
            job_response = await client.get(job_location, headers=ADMIN_HEADERS)
            assert job_response.status_code == 200, job_response.text
            assert job_response.json()["status"] == "pending"
            assert job_response.json()["snapshot_user_count"] == 1
            assert (
                await client.get(f"{base}/Users", headers=scim_headers)
            ).status_code == 401
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()
