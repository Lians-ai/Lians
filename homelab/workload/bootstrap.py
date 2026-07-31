"""Provision the homelab tenant and seed a deterministic revision scenario."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from common import (
    HttpFailure,
    atomic_write_bytes,
    emit,
    endpoint,
    env_float,
    http_json,
    wait_for_http,
)

LIANS_URL = os.getenv("LIANS_URL", "http://lians:8000")
ADMIN_SECRET = os.getenv(
    "ADMIN_SECRET",
    os.getenv("LIANS_ADMIN_SECRET", "dev-admin-secret-change-in-prod"),
)
NAMESPACE = os.getenv("NAMESPACE", "lians-homelab")
AGENT_ID = os.getenv("AGENT_ID", "risk-demo")
STATE_DIR = Path(os.getenv("STATE_DIR", "/state"))
API_KEY_PATH = STATE_DIR / "api-key"
STARTUP_TIMEOUT = env_float("STARTUP_TIMEOUT_SECONDS", 180.0, minimum=1.0)


SCENARIO: tuple[dict[str, Any], ...] = (
    {
        "idempotency_key": "lians-homelab-nvda-exposure-limit-r1",
        "body": {
            "agent_id": AGENT_ID,
            "content": (
                "NVDA counterparty exposure limit is USD 50 million for 2026 Q3 "
                "under risk policy v1."
            ),
            "event_time": "2026-07-01T12:00:00Z",
            "source": "homelab://risk-engine/policy-feed",
            "metadata": {
                "ticker": "NVDA",
                "metric": "counterparty_exposure_limit",
                "period": "2026-Q3",
                "scenario": "lians-homelab",
                "currency": "USD",
                "revision": 1,
            },
            "importance": 0.9,
        },
    },
    {
        "idempotency_key": "lians-homelab-nvda-exposure-limit-r2",
        "body": {
            "agent_id": AGENT_ID,
            "content": (
                "NVDA counterparty exposure limit revised to USD 35 million for "
                "2026 Q3 under risk policy v2 after the volatility review."
            ),
            "event_time": "2026-07-15T12:00:00Z",
            "source": "homelab://risk-engine/policy-feed",
            "metadata": {
                "ticker": "NVDA",
                "metric": "counterparty_exposure_limit",
                "period": "2026-Q3",
                "scenario": "lians-homelab",
                "currency": "USD",
                "revision": 2,
            },
            "importance": 0.95,
        },
    },
)


def api_headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def validate_key(key: str) -> bool:
    try:
        result = http_json(
            "POST",
            endpoint(LIANS_URL, "/v1/recall"),
            json_body={"agent_id": AGENT_ID, "query": "homelab key validation", "k": 1},
            headers=api_headers(key),
        )
        return isinstance(result, dict) and "memories" in result
    except HttpFailure as exc:
        if exc.status in {401, 403}:
            return False
        raise


def provision_key() -> str:
    created = http_json(
        "POST",
        endpoint(LIANS_URL, "/v1/admin/api-keys"),
        json_body={
            "namespace": NAMESPACE,
            "scopes": ["read", "write"],
            "label": "lians-homelab-workload",
        },
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    if not isinstance(created, dict) or not isinstance(created.get("key"), str):
        raise TypeError("API-key provisioning response did not contain a raw key")
    key = created["key"]
    # The receiver configuration reads this file verbatim; never add a newline.
    atomic_write_bytes(API_KEY_PATH, key.encode("utf-8"))
    emit("api_key_provisioned", namespace=NAMESPACE, key_id=created.get("id"))
    return key


def ensure_key() -> str:
    if API_KEY_PATH.is_file():
        key = API_KEY_PATH.read_text(encoding="utf-8").strip()
        if key and validate_key(key):
            os.chmod(API_KEY_PATH, 0o600)
            emit("api_key_reused", namespace=NAMESPACE)
            return key
        emit("api_key_invalid", level="warning", namespace=NAMESPACE)
    return provision_key()


def seed_scenario(key: str) -> None:
    for revision in SCENARIO:
        memory = http_json(
            "POST",
            endpoint(LIANS_URL, "/v1/memories"),
            json_body=revision["body"],
            headers={
                **api_headers(key),
                "Idempotency-Key": revision["idempotency_key"],
            },
        )
        if not isinstance(memory, dict) or not memory.get("id"):
            raise RuntimeError("memory seed response did not contain an id")
        emit(
            "memory_seeded",
            revision=revision["body"]["metadata"]["revision"],
            memory_id=memory["id"],
        )

    current = http_json(
        "POST",
        endpoint(LIANS_URL, "/v1/recall"),
        json_body={
            "agent_id": AGENT_ID,
            "query": "current NVDA counterparty exposure limit",
            "k": 5,
            "filters": {
                "ticker": "NVDA",
                "metric": "counterparty_exposure_limit",
            },
        },
        headers=api_headers(key),
    )
    memories = current.get("memories", []) if isinstance(current, dict) else []
    if not any("USD 35 million" in (item.get("content") or "") for item in memories):
        raise RuntimeError("seed verification did not recall the active risk-policy revision")
    emit("scenario_ready", namespace=NAMESPACE, agent_id=AGENT_ID, revisions=2)


def main() -> int:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        wait_for_http(
            endpoint(LIANS_URL, "/readyz"),
            "lians",
            timeout=STARTUP_TIMEOUT,
        )
        seed_scenario(ensure_key())
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level container boundary
        emit("bootstrap_failed", level="error", error=str(exc), error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
