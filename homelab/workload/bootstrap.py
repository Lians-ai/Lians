"""Provision the homelab tenant and seed a validated local sample scenario."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from common import (
    HttpFailure,
    atomic_write_bytes,
    atomic_write_json,
    emit,
    endpoint,
    env_float,
    http_json,
    wait_for_http,
)
from scenario import LoadedScenario, load_scenario

LIANS_URL = os.getenv("LIANS_URL", "http://lians:8000")
ADMIN_SECRET = os.getenv(
    "ADMIN_SECRET",
    os.getenv("LIANS_ADMIN_SECRET", "dev-admin-secret-change-in-prod"),
)
NAMESPACE = os.getenv("NAMESPACE", "lians-homelab")
STATE_DIR = Path(os.getenv("STATE_DIR", "/state"))
API_KEY_PATH = STATE_DIR / "api-key"
SAMPLE_MANIFEST_PATH = STATE_DIR / "sample-manifest.json"
SAMPLE_PATH = Path(os.getenv("SAMPLE_PATH", "/sample/input.json"))
STARTUP_TIMEOUT = env_float("STARTUP_TIMEOUT_SECONDS", 180.0, minimum=1.0)


def api_headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def validate_key(key: str, agent_id: str) -> bool:
    try:
        result = http_json(
            "POST",
            endpoint(LIANS_URL, "/v1/recall"),
            json_body={"agent_id": agent_id, "query": "homelab key validation", "k": 1},
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


def ensure_key(agent_id: str) -> str:
    if API_KEY_PATH.is_file():
        key = API_KEY_PATH.read_text(encoding="utf-8").strip()
        if key and validate_key(key, agent_id):
            os.chmod(API_KEY_PATH, 0o600)
            emit("api_key_reused", namespace=NAMESPACE)
            return key
        emit("api_key_invalid", level="warning", namespace=NAMESPACE)
    return provision_key()


def seed_scenario(key: str, loaded: LoadedScenario) -> None:
    scenario = loaded.data
    agent_id = scenario["agent_id"]
    recall_filters = {
        **scenario["recall_filters"],
        "lab_sample_sha256": loaded.sample_sha256,
    }
    for index, revision in enumerate(scenario["memories"]):
        body = {
            key: value
            for key, value in revision.items()
            if key != "idempotency_key"
        }
        body["agent_id"] = agent_id
        body["metadata"] = {
            **revision["metadata"],
            "lab_sample_sha256": loaded.sample_sha256,
        }
        memory = http_json(
            "POST",
            endpoint(LIANS_URL, "/v1/memories"),
            json_body=body,
            headers={
                **api_headers(key),
                "Idempotency-Key": (
                    f"homelab-{loaded.sample_sha256[:16]}-{index:02d}"
                ),
            },
        )
        if not isinstance(memory, dict) or not memory.get("id"):
            raise RuntimeError("memory seed response did not contain an id")
        emit(
            "memory_seeded",
            sample_sha256=loaded.sample_sha256,
            sample_index=index,
            memory_id=memory["id"],
        )

    current = http_json(
        "POST",
        endpoint(LIANS_URL, "/v1/recall"),
        json_body={
            "agent_id": agent_id,
            "query": scenario["query"],
            "k": min(10, len(scenario["memories"])),
            "filters": recall_filters,
        },
        headers=api_headers(key),
    )
    memories = current.get("memories", []) if isinstance(current, dict) else []
    if not any(
        scenario["expected_marker"] in (item.get("content") or "") for item in memories
    ):
        raise RuntimeError("seed verification did not recall the expected sample marker")
    atomic_write_json(SAMPLE_MANIFEST_PATH, loaded.manifest)
    emit(
        "scenario_ready",
        namespace=NAMESPACE,
        agent_id=agent_id,
        sample_sha256=loaded.sample_sha256,
        memories=len(scenario["memories"]),
    )


def main() -> int:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        loaded = load_scenario(
            SAMPLE_PATH,
            acknowledgement=os.getenv("LAB_SAMPLE_POLICY_ACK"),
        )
        emit("sample_accepted", **loaded.manifest)
        wait_for_http(
            endpoint(LIANS_URL, "/readyz"),
            "lians",
            timeout=STARTUP_TIMEOUT,
        )
        seed_scenario(ensure_key(loaded.data["agent_id"]), loaded)
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level container boundary
        emit("bootstrap_failed", level="error", error=str(exc), error_type=type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
