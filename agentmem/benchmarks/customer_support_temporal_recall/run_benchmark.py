"""Run the deterministic synthetic support-memory benchmark."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BENCHMARK_DIR = Path(__file__).resolve().parent
AGENTMEM_ROOT = BENCHMARK_DIR.parents[1]
sys.path.insert(0, str(AGENTMEM_ROOT / "sdk" / "python"))
sys.path.insert(0, str(AGENTMEM_ROOT))

from lians import LocalLiansClient

AGENT_ID = "synthetic-customer-support"


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((BENCHMARK_DIR / name).read_text(encoding="utf-8"))


def _parse_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    return datetime.fromisoformat(value)


def _content(result: dict[str, Any]) -> list[str]:
    return [memory["content"] for memory in result["memories"]]


def run_benchmark() -> dict[str, Any]:
    fixture = _load_json("fixture.json")
    expected = _load_json("expected_receipt.json")
    records = sorted(fixture["records"], key=lambda item: _parse_time(item["ingestion_time"]))

    fixture_bytes = (BENCHMARK_DIR / "fixture.json").read_bytes()
    receipt: dict[str, Any] = {
        "schema_version": "lians.synthetic-support-result.v1",
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "checks": [],
    }

    with LocalLiansClient(embedding_provider="local") as memory:
        for record in records:
            memory.add(
                agent_id=AGENT_ID,
                content=record["content"],
                event_time=_parse_time(record["event_time"]),
                source="synthetic-support-fixture",
                metadata={
                    **record["metadata"],
                    "fixture_record_id": record["id"],
                    "declared_ingestion_time": record["ingestion_time"],
                },
            )

        for check in expected["checks"]:
            as_of = _parse_time(check["as_of"]) if check.get("as_of") else None
            result = (
                memory.recall_at(
                    agent_id=AGENT_ID,
                    query=check["query"],
                    as_of=as_of,
                    filters=check["metadata"],
                    k=5,
                )
                if as_of
                else memory.recall(
                    agent_id=AGENT_ID,
                    query=check["query"],
                    filters=check["metadata"],
                    k=5,
                )
            )
            content = _content(result)
            combined = "\n".join(content)
            passed = all(value in combined for value in check["include"]) and all(
                value not in combined for value in check["exclude"]
            )
            receipt["checks"].append(
                {
                    "id": check["id"],
                    "passed": passed,
                    "content": content,
                    "lians_receipt_sha256": result["receipt_sha256"],
                }
            )

    receipt["passed"] = all(check["passed"] for check in receipt["checks"])
    return receipt


def main() -> None:
    receipt = run_benchmark()
    print(json.dumps(receipt, indent=2))
    if not receipt["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
