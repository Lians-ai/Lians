"""
Tests for POST/GET/DELETE/POST-rotate admin API key endpoints.

Uses httpx.AsyncClient with dependency_overrides to inject an in-memory
SQLite session, so no real database or network is needed.
"""
from __future__ import annotations
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

ADMIN_SECRET = "test-admin-secret-xyz"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def app_client(monkeypatch):
    """
    Return an httpx.AsyncClient wired to the FastAPI app with:
    - in-memory SQLite replacing the real database
    - ADMIN_SECRET set to a known test value
    """
    monkeypatch.setenv("ADMIN_SECRET", ADMIN_SECRET)

    from src.agentmem.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("ADMIN_SECRET", ADMIN_SECRET)
    # Re-clear after setting env var (lru_cache reads env at call time)
    get_settings.cache_clear()

    from src.agentmem.models import Base as AppBase
    from src.agentmem.main import app
    from src.agentmem.db import get_db

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Drop PG-only indexes before table creation
    pg_indexes = [
        idx for table in AppBase.metadata.tables.values()
        for idx in table.indexes
        if idx.dialect_kwargs.get("postgresql_using") is not None
    ]
    for idx in pg_indexes:
        idx.table.indexes.discard(idx)

    async with engine.begin() as conn:
        await conn.run_sync(AppBase.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

class TestProvision:

    @pytest.mark.asyncio
    async def test_provision_returns_key_once(self, app_client):
        resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "acme", "scopes": ["read", "write"], "label": "prod-agent"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["key"].startswith("agentmem_")
        assert len(data["key"]) > 20
        assert data["namespace"] == "acme"
        assert data["label"] == "prod-agent"
        assert set(data["scopes"]) == {"read", "write"}
        assert data["revoked_at"] is None
        assert data["rotated_at"] is None

    @pytest.mark.asyncio
    async def test_provision_without_label(self, app_client):
        resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "acme", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 201
        assert resp.json()["label"] is None

    @pytest.mark.asyncio
    async def test_provision_rejects_missing_admin_secret(self, app_client):
        resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "acme", "scopes": ["read"]},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_provision_rejects_wrong_admin_secret(self, app_client):
        resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "acme", "scopes": ["read"]},
            headers={"X-Admin-Secret": "totally-wrong"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_provisioned_key_authenticates(self, app_client):
        """A freshly provisioned key must work for the memory API immediately."""
        resp = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "acme", "scopes": ["read", "write"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 201
        raw_key = resp.json()["key"]

        # Use the key to hit a protected endpoint
        from datetime import datetime, timezone
        recall_resp = await app_client.post(
            "/v1/recall",
            json={"agent_id": "bot-1", "query": "test", "k": 1},
            headers={"X-API-Key": raw_key},
        )
        # 200 means auth passed (empty results expected — no memories yet)
        assert recall_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_two_provisions_produce_different_keys(self, app_client):
        async def _make():
            r = await app_client.post(
                "/v1/admin/api-keys",
                json={"namespace": "acme", "scopes": ["read"]},
                headers={"X-Admin-Secret": ADMIN_SECRET},
            )
            return r.json()["key"]

        k1 = await _make()
        k2 = await _make()
        assert k1 != k2


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class TestList:

    @pytest.mark.asyncio
    async def test_list_returns_active_keys(self, app_client):
        for label in ["key-a", "key-b"]:
            await app_client.post(
                "/v1/admin/api-keys",
                json={"namespace": "list-ns", "scopes": ["read"], "label": label},
                headers={"X-Admin-Secret": ADMIN_SECRET},
            )

        resp = await app_client.get(
            "/v1/admin/api-keys",
            params={"namespace": "list-ns"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        labels = {k["label"] for k in resp.json()}
        assert labels == {"key-a", "key-b"}

    @pytest.mark.asyncio
    async def test_list_excludes_revoked_by_default(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "list-ns2", "scopes": ["read"], "label": "to-revoke"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        key_id = r.json()["id"]

        await app_client.delete(
            f"/v1/admin/api-keys/{key_id}",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )

        resp = await app_client.get(
            "/v1/admin/api-keys",
            params={"namespace": "list-ns2"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        assert all(k["revoked_at"] is None for k in resp.json())

    @pytest.mark.asyncio
    async def test_list_includes_revoked_when_requested(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "list-ns3", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        key_id = r.json()["id"]

        await app_client.delete(
            f"/v1/admin/api-keys/{key_id}",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )

        resp = await app_client.get(
            "/v1/admin/api-keys",
            params={"namespace": "list-ns3", "include_revoked": "true"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        assert any(k["revoked_at"] is not None for k in resp.json())

    @pytest.mark.asyncio
    async def test_list_requires_admin_secret(self, app_client):
        resp = await app_client.get("/v1/admin/api-keys")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_list_no_namespace_filter_returns_all(self, app_client):
        for ns in ["ns-x", "ns-y"]:
            await app_client.post(
                "/v1/admin/api-keys",
                json={"namespace": ns, "scopes": ["read"]},
                headers={"X-Admin-Secret": ADMIN_SECRET},
            )
        resp = await app_client.get(
            "/v1/admin/api-keys",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        namespaces = {k["namespace"] for k in resp.json()}
        assert "ns-x" in namespaces and "ns-y" in namespaces


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------

class TestRevoke:

    @pytest.mark.asyncio
    async def test_revoke_makes_key_invalid(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rev-ns", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        data = r.json()
        key_id, raw_key = data["id"], data["key"]

        del_resp = await app_client.delete(
            f"/v1/admin/api-keys/{key_id}",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert del_resp.status_code == 204

        recall_resp = await app_client.post(
            "/v1/recall",
            json={"agent_id": "bot-1", "query": "test", "k": 1},
            headers={"X-API-Key": raw_key},
        )
        assert recall_resp.status_code == 401

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_key_returns_404(self, app_client):
        resp = await app_client.delete(
            "/v1/admin/api-keys/00000000-0000-0000-0000-000000000000",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_returns_409(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rev-ns2", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        key_id = r.json()["id"]

        await app_client.delete(
            f"/v1/admin/api-keys/{key_id}",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        second = await app_client.delete(
            f"/v1/admin/api-keys/{key_id}",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert second.status_code == 409

    @pytest.mark.asyncio
    async def test_revoke_requires_admin_secret(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rev-ns3", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        key_id = r.json()["id"]
        resp = await app_client.delete(f"/v1/admin/api-keys/{key_id}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

class TestRotate:

    @pytest.mark.asyncio
    async def test_rotate_returns_new_key(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rot-ns", "scopes": ["read", "write"], "label": "svc-a"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        old_data = r.json()
        old_id, old_key = old_data["id"], old_data["key"]

        rot = await app_client.post(
            f"/v1/admin/api-keys/{old_id}/rotate",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert rot.status_code == 201
        new_data = rot.json()
        assert new_data["key"].startswith("agentmem_")
        assert new_data["key"] != old_key
        assert new_data["namespace"] == "rot-ns"
        assert new_data["label"] == "svc-a"
        assert set(new_data["scopes"]) == {"read", "write"}
        assert new_data["id"] != old_id

    @pytest.mark.asyncio
    async def test_rotate_revokes_old_key(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rot-ns2", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        old_data = r.json()
        old_id, old_key = old_data["id"], old_data["key"]

        await app_client.post(
            f"/v1/admin/api-keys/{old_id}/rotate",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )

        recall_resp = await app_client.post(
            "/v1/recall",
            json={"agent_id": "bot-1", "query": "test", "k": 1},
            headers={"X-API-Key": old_key},
        )
        assert recall_resp.status_code == 401

    @pytest.mark.asyncio
    async def test_rotate_new_key_is_valid(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rot-ns3", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        old_id = r.json()["id"]

        rot = await app_client.post(
            f"/v1/admin/api-keys/{old_id}/rotate",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        new_key = rot.json()["key"]

        recall_resp = await app_client.post(
            "/v1/recall",
            json={"agent_id": "bot-1", "query": "test", "k": 1},
            headers={"X-API-Key": new_key},
        )
        assert recall_resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_key_returns_404(self, app_client):
        resp = await app_client.post(
            "/v1/admin/api-keys/00000000-0000-0000-0000-000000000000/rotate",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rotate_already_revoked_returns_409(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rot-ns4", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        old_id = r.json()["id"]

        await app_client.delete(
            f"/v1/admin/api-keys/{old_id}",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )

        resp = await app_client.post(
            f"/v1/admin/api-keys/{old_id}/rotate",
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_rotate_requires_admin_secret(self, app_client):
        r = await app_client.post(
            "/v1/admin/api-keys",
            json={"namespace": "rot-ns5", "scopes": ["read"]},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        old_id = r.json()["id"]
        resp = await app_client.post(f"/v1/admin/api-keys/{old_id}/rotate")
        assert resp.status_code == 401
