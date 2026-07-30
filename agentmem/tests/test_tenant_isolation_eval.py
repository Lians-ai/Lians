import json
from types import SimpleNamespace

import httpx
import pytest

from benchmarks.tenant_isolation_eval import run


@pytest.mark.asyncio
async def test_probe_proves_two_key_isolation(tmp_path):
    key_a = tmp_path / "a.key"
    key_b = tmp_path / "b.key"
    key_a.write_text("key-a", encoding="utf-8")
    key_b.write_text("key-b", encoding="utf-8")
    stores = {"key-a": [], "key-b": []}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers["X-API-Key"]
        body = json.loads(request.content)
        if request.url.path == "/v1/memories":
            stores[key].append(body["content"])
            return httpx.Response(200, json={"id": "written"})
        memories = [
            {"content": content}
            for content in stores[key]
        ]
        return httpx.Response(200, json={"memories": memories})

    args = SimpleNamespace(
        base_url="https://memory.test",
        key_a_file=key_a,
        key_b_file=key_b,
        agent_id="isolation-test",
        timeout_seconds=1.0,
    )
    report = await run(args, transport=httpx.MockTransport(handler))
    assert report["passed"]
    assert report["cross_tenant_retrievals"] == 0


@pytest.mark.asyncio
async def test_probe_rejects_reused_key(tmp_path):
    key_a = tmp_path / "a.key"
    key_b = tmp_path / "b.key"
    key_a.write_text("same-key", encoding="utf-8")
    key_b.write_text("same-key", encoding="utf-8")
    args = SimpleNamespace(
        base_url="https://memory.test",
        key_a_file=key_a,
        key_b_file=key_b,
        agent_id="isolation-test",
        timeout_seconds=1.0,
    )
    with pytest.raises(ValueError, match="different"):
        await run(args, transport=httpx.MockTransport(lambda _: None))
