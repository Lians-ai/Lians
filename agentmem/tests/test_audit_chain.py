"""
Tests for the SEC 17a-4 audit log hash chain.


Covers:
  - Chain is built correctly when writing memories (each row's prev_hash
    points to the previous row's row_hash)
  - The genesis row has prev_hash == GENESIS_HASH
  - An unmodified chain passes verification
  - A tampered row (content changed) is flagged as hash_mismatch
  - A deleted row is flagged as orphaned_parent
  - The verify endpoint returns 200 for a clean chain
  - The verify endpoint returns a tampered report for a broken chain
"""
from __future__ import annotations

import hashlib
import uuid
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text

from src.lian.main import app
from src.lian.db import get_db
from src.lian.models import ApiKey, EventLog
from src.lian.audit_chain import (
    chain_log,
    compute_row_hash,
    get_chain_tip,
    verify_chain,
    GENESIS_HASH,
    _fmt_dt,
)

TEST_NS = "chain-test-ns"
AGENT = "chain-agent"
ADMIN_SECRET = "dev-admin-secret-change-in-prod"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client(db):
    hashed = hashlib.sha256(b"chain-test-key").hexdigest()
    db.add(ApiKey(hashed_key=hashed, namespace=TEST_NS, scopes=["read", "write", "admin"]))
    await db.commit()

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


def _mem(content: str) -> dict:
    return {
        "agent_id": AGENT,
        "content": content,
        "event_time": T0.isoformat(),
        "metadata": {},
    }


# â”€â”€ Unit tests: chain_log + verify_chain â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestChainLogUnit:

    async def test_first_row_prev_hash_is_genesis(self, db):
        row = await chain_log(db, namespace="unit-ns-1", agent_id="a", op="add",
                              content_hash="abc123")
        await db.commit()
        assert row.prev_hash == GENESIS_HASH

    async def test_second_row_prev_hash_points_to_first(self, db):
        import asyncio
        r1 = await chain_log(db, namespace="unit-ns-2", agent_id="a", op="add",
                              content_hash="h1")
        await asyncio.sleep(0.025)          # guarantee r2.created_at > r1.created_at on Windows
        r2 = await chain_log(db, namespace="unit-ns-2", agent_id="a", op="recall")
        await db.commit()
        assert r2.prev_hash == r1.row_hash

    def test_row_hash_is_deterministic(self):
        """compute_row_hash is a pure function â€” same inputs always give same output."""
        row = EventLog()
        row.id = uuid.UUID("12345678-1234-5678-1234-567812345678")
        row.namespace = "test-ns"
        row.agent_id = "agent-1"
        row.op = "add"
        row.content_hash = "det"
        row.memory_id = None
        row.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        h1 = compute_row_hash(row, GENESIS_HASH)
        h2 = compute_row_hash(row, GENESIS_HASH)
        assert h1 == h2
        assert len(h1) == 64

    def test_fmt_dt_normalises_timezone_aware_and_naive_to_same_string(self):
        """Timezone-aware UTC datetime and its naive SQLite counterpart hash identically."""
        aware = datetime(2026, 6, 19, 12, 0, 0, 123456, tzinfo=timezone.utc)
        naive = datetime(2026, 6, 19, 12, 0, 0, 123456)
        assert _fmt_dt(aware) == _fmt_dt(naive)
        assert _fmt_dt(aware) == "2026-06-19T12:00:00.123456"

    def test_fmt_dt_handles_none(self):
        assert _fmt_dt(None) == "null"

    async def test_chain_tip_advances_after_flush(self, db):
        tip_before = await get_chain_tip(db, "unit-ns-4")
        assert tip_before == GENESIS_HASH
        row = await chain_log(db, namespace="unit-ns-4", agent_id="a", op="add")
        tip_after = await get_chain_tip(db, "unit-ns-4")
        assert tip_after == row.row_hash

    async def test_three_row_chain_is_linear(self, db):
        import asyncio
        ns = "unit-ns-5"
        r1 = await chain_log(db, ns, "a", "add",    content_hash="h1")
        await asyncio.sleep(0.025)          # guarantee distinct created_at for each row on Windows
        r2 = await chain_log(db, ns, "a", "recall", content_hash="h2")
        await asyncio.sleep(0.025)
        r3 = await chain_log(db, ns, "a", "erase",  content_hash="h3")
        await db.commit()

        assert r1.prev_hash == GENESIS_HASH
        assert r2.prev_hash == r1.row_hash
        assert r3.prev_hash == r2.row_hash
        assert len({r1.row_hash, r2.row_hash, r3.row_hash}) == 3

    async def test_different_namespaces_are_independent_chains(self, db):
        ra = await chain_log(db, "ns-alpha", "a", "add", content_hash="ha")
        rb = await chain_log(db, "ns-beta",  "b", "add", content_hash="hb")
        await db.commit()
        assert ra.prev_hash == GENESIS_HASH
        assert rb.prev_hash == GENESIS_HASH


# â”€â”€ Unit tests: verify_chain â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestVerifyChain:

    async def test_empty_namespace_is_ok(self, db):
        report = await verify_chain(db, "empty-ns")
        assert report["status"] == "ok"
        assert report["rows_checked"] == 0
        assert report["violations"] == []

    async def test_clean_chain_passes(self, db):
        import asyncio
        ns = "verify-clean"
        await chain_log(db, ns, "a", "add",    content_hash="h1")
        await asyncio.sleep(0.025)
        await chain_log(db, ns, "a", "recall", content_hash="h2")
        await asyncio.sleep(0.025)
        await chain_log(db, ns, "a", "erase",  content_hash="h3")
        await db.commit()

        report = await verify_chain(db, ns)
        assert report["status"] == "ok"
        assert report["rows_checked"] == 3
        assert report["violations"] == []

    async def test_tampered_row_detected_as_hash_mismatch(self, db):
        ns = "verify-tamper"
        r1 = await chain_log(db, ns, "a", "add", content_hash="original")
        await chain_log(db, ns, "a", "recall")
        await db.commit()

        # Simulate a DBA silently changing the op field.
        # SQLite stores UUIDs as hex without dashes â€” use .hex to match.
        await db.execute(
            text("UPDATE event_log SET op='FORGED' WHERE id=:id"),
            {"id": r1.id.hex},
        )
        await db.commit()

        # Expire the identity-map cache so verify_chain reads the tampered DB state
        await db.run_sync(lambda s: s.expire_all())

        report = await verify_chain(db, ns)
        assert report["status"] == "tampered"
        violations = report["violations"]
        assert any(v["kind"] == "hash_mismatch" for v in violations), violations
        tampered_ids = {v["row_id"] for v in violations if v["kind"] == "hash_mismatch"}
        assert str(r1.id) in tampered_ids

    async def test_deleted_row_detected_as_orphaned_parent(self, db):
        import asyncio
        ns = "verify-delete"
        r1 = await chain_log(db, ns, "a", "add", content_hash="h1")
        await asyncio.sleep(0.025)          # ensure get_chain_tip picks r2 (not r1) as r3's prev
        await chain_log(db, ns, "a", "recall")
        await asyncio.sleep(0.025)
        r3 = await chain_log(db, ns, "a", "erase", content_hash="h3")
        await db.commit()

        # Delete the middle row (r2, i.e. the recall).
        # SQLite stores UUIDs as hex without dashes â€” use .hex to match.
        await db.execute(
            text("DELETE FROM event_log WHERE id != :a AND id != :b AND namespace = :ns"),
            {"a": r1.id.hex, "b": r3.id.hex, "ns": ns},
        )
        await db.commit()
        await db.run_sync(lambda s: s.expire_all())

        report = await verify_chain(db, ns)
        assert report["status"] == "tampered"
        kinds = {v["kind"] for v in report["violations"]}
        assert "orphaned_parent" in kinds

    async def test_null_hashes_are_skipped(self, db):
        """Rows inserted before migration 0006 (NULL hashes) are not flagged."""
        ns = "verify-legacy"
        # Use the 32-char hex UUID format that SQLite stores (no dashes).
        # Must start with a hex letter to avoid SQLite treating it as a number.
        await db.execute(text(
            "INSERT INTO event_log (id, namespace, agent_id, op, payload, created_at) "
            "VALUES ('aabbccdd0011223344556677aabbccdd', :ns, 'a', 'add', '{}', datetime('now'))"
        ), {"ns": ns})
        await db.commit()

        report = await verify_chain(db, ns)
        assert report["status"] == "ok"
        assert report["violations"] == []


# â”€â”€ Integration tests: HTTP endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestVerifyEndpoint:

    async def test_verify_clean_chain_returns_ok(self, client, db):
        await client.post(
            "/v1/memories",
            json=_mem("NVDA Q3 guidance $36B"),
            headers={"X-API-Key": "chain-test-key"},
        )

        resp = await client.get(
            "/v1/admin/audit/verify",
            params={"namespace": TEST_NS},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["rows_checked"] >= 1
        assert body["violations"] == []
        assert body["namespace"] == TEST_NS

    async def test_verify_requires_admin_secret(self, client):
        resp = await client.get(
            "/v1/admin/audit/verify",
            params={"namespace": TEST_NS},
        )
        assert resp.status_code == 401

    async def test_verify_empty_namespace_returns_ok(self, client):
        resp = await client.get(
            "/v1/admin/audit/verify",
            params={"namespace": "nonexistent-ns"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["rows_checked"] == 0

    async def test_verify_reports_tampered_chain(self, client, db):
        await client.post(
            "/v1/memories",
            json=_mem("AAPL guidance tamper test"),
            headers={"X-API-Key": "chain-test-key"},
        )

        # Tamper with the first event_log row for this namespace.
        # SQLite stores UUIDs as hex without dashes â€” use .hex to match.
        result = await db.execute(
            select(EventLog)
            .where(EventLog.namespace == TEST_NS)
            .order_by(EventLog.created_at.asc())
            .limit(1)
        )
        row = result.scalar_one()
        row_id_hex = row.id.hex  # 32-char hex, no dashes â€” matches SQLite storage

        await db.execute(
            text("UPDATE event_log SET op='FORGED' WHERE id=:id"),
            {"id": row_id_hex},
        )
        await db.commit()
        await db.run_sync(lambda s: s.expire_all())

        resp = await client.get(
            "/v1/admin/audit/verify",
            params={"namespace": TEST_NS},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "tampered"
        assert len(body["violations"]) >= 1
        assert body["violations"][0]["kind"] in ("hash_mismatch", "orphaned_parent")

    async def test_verify_response_shape(self, client):
        resp = await client.get(
            "/v1/admin/audit/verify",
            params={"namespace": "shape-test-ns"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "namespace" in body
        assert "rows_checked" in body
        assert "status" in body
        assert "violations" in body
        assert isinstance(body["violations"], list)


# â”€â”€ Audit export (service + HTTP endpoint) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class TestAuditExport:

    async def test_export_returns_all_events_in_namespace(self, db):
        from src.lian.audit_chain import export_audit_log
        ns = "export-ns-1"
        await chain_log(db, ns, "a", "add",    content_hash="h1")
        await chain_log(db, ns, "a", "recall", content_hash="h2")
        await chain_log(db, ns, "a", "erase",  content_hash="h3")
        await db.commit()

        result = await export_audit_log(db, namespace=ns)
        assert result["total_rows"] == 3
        assert result["namespace"] == ns
        assert len(result["events"]) == 3
        ops = [e["op"] for e in result["events"]]
        # "add" is always first; "recall" and "erase" may swap if sub-Âµs collision
        assert ops[0] == "add"
        assert set(ops) == {"add", "recall", "erase"}

    async def test_export_events_have_hash_chain_fields(self, db):
        from src.lian.audit_chain import export_audit_log
        ns = "export-ns-2"
        await chain_log(db, ns, "a", "add", content_hash="h1")
        await db.commit()

        result = await export_audit_log(db, namespace=ns)
        evt = result["events"][0]
        assert "prev_hash" in evt
        assert "row_hash" in evt
        assert evt["prev_hash"] == GENESIS_HASH
        assert len(evt["row_hash"]) == 64

    async def test_export_filters_by_from_dt(self, db):
        import asyncio
        from src.lian.audit_chain import export_audit_log
        from datetime import timedelta
        ns = "export-ns-3"
        r1 = await chain_log(db, ns, "a", "add", content_hash="h1")
        t1 = r1.created_at
        await asyncio.sleep(0.025)          # ensure r2 gets a strictly later timestamp on Windows
        await chain_log(db, ns, "a", "recall")
        await db.commit()

        # from_dt = slightly after the first row's created_at
        result = await export_audit_log(db, namespace=ns, from_dt=t1 + timedelta(microseconds=1))
        assert result["total_rows"] == 1
        assert result["events"][0]["op"] == "recall"

    async def test_export_filters_by_to_dt(self, db):
        import asyncio
        from src.lian.audit_chain import export_audit_log
        ns = "export-ns-4"
        r1 = await chain_log(db, ns, "a", "add", content_hash="h1")
        t1 = r1.created_at
        await asyncio.sleep(0.025)          # ensure r2 gets a strictly later timestamp on Windows
        await chain_log(db, ns, "a", "recall")
        await db.commit()

        result = await export_audit_log(db, namespace=ns, to_dt=t1)
        assert result["total_rows"] == 1
        assert result["events"][0]["op"] == "add"

    async def test_export_empty_namespace_returns_zero_rows(self, db):
        from src.lian.audit_chain import export_audit_log
        result = await export_audit_log(db, namespace="export-empty")
        assert result["total_rows"] == 0
        assert result["events"] == []
        assert result["chain_status"] is None

    async def test_export_with_verify_includes_chain_status(self, db):
        from src.lian.audit_chain import export_audit_log
        ns = "export-ns-5"
        await chain_log(db, ns, "a", "add", content_hash="h1")
        await db.commit()

        result = await export_audit_log(db, namespace=ns, include_chain_status=True)
        assert result["chain_status"] == "ok"
        assert result["chain_violations"] == []

    async def test_export_excludes_other_namespaces(self, db):
        from src.lian.audit_chain import export_audit_log
        await chain_log(db, "export-ns-6a", "a", "add", content_hash="h1")
        await chain_log(db, "export-ns-6b", "b", "add", content_hash="h2")
        await db.commit()

        result_a = await export_audit_log(db, namespace="export-ns-6a")
        result_b = await export_audit_log(db, namespace="export-ns-6b")
        assert result_a["total_rows"] == 1
        assert result_b["total_rows"] == 1
        assert result_a["events"][0]["agent_id"] == "a"
        assert result_b["events"][0]["agent_id"] == "b"


class TestAuditExportEndpoint:

    async def test_export_endpoint_returns_events(self, client):
        await client.post(
            "/v1/memories",
            json=_mem("NVDA guidance export test"),
            headers={"X-API-Key": "chain-test-key"},
        )

        resp = await client.get(
            "/v1/admin/audit/export",
            params={"namespace": TEST_NS},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["namespace"] == TEST_NS
        assert body["total_rows"] >= 1
        assert isinstance(body["events"], list)
        assert len(body["events"]) >= 1

    async def test_export_event_shape(self, client):
        await client.post(
            "/v1/memories",
            json=_mem("AAPL shape test"),
            headers={"X-API-Key": "chain-test-key"},
        )

        resp = await client.get(
            "/v1/admin/audit/export",
            params={"namespace": TEST_NS},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        evt = resp.json()["events"][0]
        required = {"id", "namespace", "agent_id", "op", "content_hash",
                    "payload", "created_at", "prev_hash", "row_hash"}
        assert required.issubset(evt.keys())

    async def test_export_requires_admin_secret(self, client):
        resp = await client.get(
            "/v1/admin/audit/export",
            params={"namespace": TEST_NS},
        )
        assert resp.status_code == 401

    async def test_export_with_verify_flag(self, client):
        await client.post(
            "/v1/memories",
            json=_mem("verify flag test"),
            headers={"X-API-Key": "chain-test-key"},
        )

        resp = await client.get(
            "/v1/admin/audit/export",
            params={"namespace": TEST_NS, "verify": "true"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["chain_status"] == "ok"
        assert body["chain_violations"] == []

    async def test_export_empty_namespace(self, client):
        resp = await client.get(
            "/v1/admin/audit/export",
            params={"namespace": "export-empty-ns"},
            headers={"X-Admin-Secret": ADMIN_SECRET},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_rows"] == 0
        assert body["events"] == []
        assert body["chain_status"] is None
