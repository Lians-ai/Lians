"""Probe namespace isolation on a running Lians deployment.

Two API keys must belong to different namespaces. The keys are read only from
files and are never emitted. The probe writes one random sentinel per namespace
under a unique agent, verifies same-tenant recall, and fails if either tenant
can retrieve the other tenant's sentinel.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import httpx


def _contents(payload: dict) -> list[str]:
    return [
        str(memory.get("content") or "")
        for memory in payload.get("memories", [])
    ]


async def run(args, *, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    key_a = args.key_a_file.read_text(encoding="utf-8").strip()
    key_b = args.key_b_file.read_text(encoding="utf-8").strip()
    if not key_a or not key_b:
        raise ValueError("both API key files must be non-empty")
    if key_a == key_b:
        raise ValueError("the two API keys must be different")

    marker = secrets.token_hex(12)
    agent_id = args.agent_id or f"tenant-isolation-{marker}"
    sentinel_a = f"LIANS-ISOLATION-A-{marker}"
    sentinel_b = f"LIANS-ISOLATION-B-{marker}"
    now = datetime.now(timezone.utc).isoformat()
    timeout = httpx.Timeout(args.timeout_seconds)

    def client_for(key: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=args.base_url.rstrip("/"),
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    client_a = client_for(key_a)
    client_b = client_for(key_b)
    async with client_a, client_b:
        writes = await asyncio.gather(
            client_a.post("/v1/memories", json={
                "agent_id": agent_id,
                "content": sentinel_a,
                "event_time": now,
                "source": "benchmark://tenant-isolation/a",
            }),
            client_b.post("/v1/memories", json={
                "agent_id": agent_id,
                "content": sentinel_b,
                "event_time": now,
                "source": "benchmark://tenant-isolation/b",
            }),
        )
        recalls = await asyncio.gather(
            client_a.post("/v1/recall", json={
                "agent_id": agent_id,
                "query": f"{sentinel_a} {sentinel_b}",
                "k": 20,
                "mode": "deep",
            }),
            client_b.post("/v1/recall", json={
                "agent_id": agent_id,
                "query": f"{sentinel_a} {sentinel_b}",
                "k": 20,
                "mode": "deep",
            }),
        )

    write_ok = all(response.status_code == 200 for response in writes)
    recall_ok = all(response.status_code == 200 for response in recalls)
    contents_a = _contents(recalls[0].json()) if recalls[0].status_code == 200 else []
    contents_b = _contents(recalls[1].json()) if recalls[1].status_code == 200 else []
    checks = {
        "writes_succeeded": write_ok,
        "recalls_succeeded": recall_ok,
        "tenant_a_reads_own": sentinel_a in contents_a,
        "tenant_b_reads_own": sentinel_b in contents_b,
        "tenant_a_cannot_read_b": sentinel_b not in contents_a,
        "tenant_b_cannot_read_a": sentinel_a not in contents_b,
    }
    return {
        "benchmark": "lians-tenant-isolation-v1",
        "environment": args.base_url,
        "agent_id": agent_id,
        "api_key_source": "two files",
        "checks": checks,
        "cross_tenant_retrievals": int(sentinel_b in contents_a) + int(sentinel_a in contents_b),
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--key-a-file", required=True, type=Path)
    parser.add_argument("--key-b-file", required=True, type=Path)
    parser.add_argument("--agent-id")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Tenant isolation gate: {'passed' if report['passed'] else 'failed'}")
    print(f"Receipt: {args.out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
