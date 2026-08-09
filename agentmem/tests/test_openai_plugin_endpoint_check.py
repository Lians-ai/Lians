from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "check_openai_plugin_endpoint.py"
SPEC = importlib.util.spec_from_file_location("check_openai_plugin_endpoint", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ORIGIN = "https://mcp.example.com"
TOKEN = "private-test-bearer-token"


def response(
    status: int,
    payload: object = None,
    *,
    headers: dict[str, str] | None = None,
) -> object:
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    response_headers = {key.casefold(): value for key, value in (headers or {}).items()}
    if payload is not None:
        response_headers.setdefault("content-type", "application/json")
    return MODULE.Response(status=status, body=body, headers=response_headers)


def metadata_payload() -> dict[str, object]:
    return {
        "resource": f"{ORIGIN}/",
        "authorization_servers": ["https://auth.example.com/"],
        "scopes_supported": ["memory:read", "memory:write"],
        "bearer_methods_supported": ["header"],
        "resource_documentation": "https://www.example.com/privacy",
    }


def wire_tool(name: str) -> dict[str, object]:
    expected = copy.deepcopy(MODULE.EXPECTED_TOOLS[name])
    tool = {"name": name, **expected}
    # Pydantic/FastMCP adds presentational JSON Schema titles.  The checker
    # intentionally ignores only those titles while enforcing every constraint.
    tool["inputSchema"] = {"title": f"{name}Arguments", **expected["inputSchema"]}
    tool["outputSchema"] = {"title": f"{name}Output", **expected["outputSchema"]}
    tool["_meta"] = {"securitySchemes": copy.deepcopy(expected["securitySchemes"])}
    return tool


def initialized_payload() -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": MODULE.MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "lians-memory", "version": "0.5.0"},
        },
    }


def test_normalize_target_accepts_origin_or_explicit_endpoint() -> None:
    origin = MODULE.normalize_target(ORIGIN)
    endpoint = MODULE.normalize_target(f"{ORIGIN}/custom/mcp")

    assert origin.resource == f"{ORIGIN}/"
    assert origin.endpoint == f"{ORIGIN}/mcp"
    assert endpoint.resource == f"{ORIGIN}/"
    assert endpoint.endpoint == f"{ORIGIN}/custom/mcp"
    assert endpoint.metadata_url == f"{ORIGIN}/.well-known/oauth-protected-resource"


@pytest.mark.parametrize(
    "target",
    [
        "http://mcp.example.com/mcp",
        "https://user:password@mcp.example.com/mcp",
        "https://mcp.example.com/mcp?access_token=secret",
        "https://mcp.example.com/mcp#fragment",
        " https://mcp.example.com/mcp",
        "https://mcp.example.com//mcp",
    ],
)
def test_normalize_target_rejects_unsafe_or_non_https_urls(target: str) -> None:
    with pytest.raises(ValueError):
        MODULE.normalize_target(target)


def test_metadata_only_makes_no_mcp_or_token_request(monkeypatch) -> None:
    requested: list[tuple[str, dict[str, object]]] = []

    def fake_request(url: str, **kwargs: object):
        requested.append((url, kwargs))
        return response(200, metadata_payload())

    monkeypatch.setattr(MODULE, "request", fake_request)
    result = MODULE.run(ORIGIN, metadata_only=True)

    assert result["mode"] == "metadata-only"
    assert result["checks"] == {
        "https": "ok",
        "protected_resource_metadata": "ok",
    }
    assert requested == [
        (
            f"{ORIGIN}/.well-known/oauth-protected-resource",
            {"timeout": 20.0, "label": "Protected-resource metadata"},
        )
    ]


def test_no_token_mode_checks_metadata_and_401_challenge(monkeypatch) -> None:
    requested: list[tuple[str, dict[str, object]]] = []

    def fake_request(url: str, **kwargs: object):
        requested.append((url, kwargs))
        if len(requested) == 1:
            return response(200, metadata_payload())
        return response(
            401,
            {"error": "unauthorized"},
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://mcp.example.com/'
                    '.well-known/oauth-protected-resource"'
                )
            },
        )

    monkeypatch.setattr(MODULE, "request", fake_request)
    result = MODULE.run(f"{ORIGIN}/mcp")

    assert result["mode"] == "no-token"
    assert result["checks"]["unauthenticated_challenge"] == "ok"
    assert result["checks"]["authenticated_mcp"] == "skipped_no_token"
    assert len(requested) == 2
    assert "Authorization" not in requested[1][1]["headers"]


def test_authenticated_mode_initializes_and_checks_exact_tools(monkeypatch) -> None:
    requested: list[tuple[str, dict[str, object]]] = []

    def fake_request(url: str, **kwargs: object):
        requested.append((url, kwargs))
        call = len(requested)
        if call == 1:
            return response(200, metadata_payload())
        if call == 2:
            return response(
                401,
                {"error": "unauthorized"},
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="https://mcp.example.com/'
                        '.well-known/oauth-protected-resource"'
                    )
                },
            )
        if call == 3:
            return response(
                200,
                initialized_payload(),
                headers={"Mcp-Session-Id": "opaque-session"},
            )
        if call == 4:
            return response(202)
        return response(
            200,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [wire_tool(name) for name in MODULE.EXPECTED_TOOLS]},
            },
        )

    monkeypatch.setattr(MODULE, "request", fake_request)
    result = MODULE.run(f"{ORIGIN}/mcp", bearer_token=TOKEN)

    assert result["mode"] == "authenticated"
    assert result["tools"] == ["remember", "recall", "forget_memory"]
    assert result["checks"]["tool_contracts"] == "ok"
    assert len(requested) == 5
    assert "Authorization" not in requested[1][1]["headers"]
    for _, kwargs in requested[2:]:
        assert kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert requested[3][1]["headers"]["Mcp-Session-Id"] == "opaque-session"
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda tools: tools.append({"name": "unexpected"}),
            "exactly remember, recall, and forget_memory",
        ),
        (
            lambda tools: tools[0].update(
                {"securitySchemes": [{"type": "oauth2", "scopes": ["memory:read"]}]}
            ),
            "remember has an unexpected securitySchemes",
        ),
        (
            lambda tools: tools[0]["_meta"].update(
                {"securitySchemes": [{"type": "oauth2", "scopes": ["memory:read"]}]}
            ),
            "remember has an unexpected _meta.securitySchemes",
        ),
        (
            lambda tools: tools[1]["inputSchema"]["properties"].pop("max_tokens"),
            "recall has an unexpected inputSchema",
        ),
        (
            lambda tools: tools[2]["annotations"].update({"destructiveHint": False}),
            "forget_memory has an unexpected annotations",
        ),
    ],
)
def test_exact_tool_contract_rejects_drift(mutation, message: str) -> None:
    tools = [wire_tool(name) for name in MODULE.EXPECTED_TOOLS]
    mutation(tools)

    with pytest.raises(MODULE.EndpointCheckError, match=message):
        MODULE.validate_tool_contracts(tools)


def test_metadata_and_challenge_are_bound_to_exact_resource() -> None:
    target = MODULE.normalize_target(ORIGIN)
    wrong_metadata = metadata_payload()
    wrong_metadata["resource"] = "https://another.example.com/"

    with pytest.raises(MODULE.EndpointCheckError, match="wrong resource identifier"):
        MODULE.validate_protected_resource_metadata(response(200, wrong_metadata), target)
    with pytest.raises(MODULE.EndpointCheckError, match="wrong resource metadata"):
        MODULE.validate_unauthenticated_challenge(
            response(
                401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="https://another.example.com/'
                        '.well-known/oauth-protected-resource"'
                    )
                },
            ),
            target,
        )


def test_failures_and_cli_output_never_print_bearer_token(monkeypatch, capsys) -> None:
    reflected_body = {"detail": f"rejected Authorization: Bearer {TOKEN}"}

    def fake_request(url: str, **_kwargs: object):
        if url.endswith("oauth-protected-resource"):
            return response(200, metadata_payload())
        return response(500, reflected_body)

    monkeypatch.setattr(MODULE, "request", fake_request)
    exit_code = MODULE.main(["--resource-url", ORIGIN, "--bearer-token", TOKEN])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err
    assert "HTTP 500" in captured.err


def test_metadata_only_cli_does_not_read_token_environment(monkeypatch, capsys) -> None:
    monkeypatch.setenv(MODULE.TOKEN_ENVIRONMENT_VARIABLE, TOKEN)
    monkeypatch.setattr(
        MODULE,
        "request",
        lambda _url, **_kwargs: response(200, metadata_payload()),
    )

    exit_code = MODULE.main(["--resource-url", ORIGIN, "--metadata-only"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert TOKEN not in captured.out
    assert TOKEN not in captured.err
    assert json.loads(captured.out)["mode"] == "metadata-only"


def test_invalid_bearer_token_fails_before_any_request(monkeypatch) -> None:
    monkeypatch.setattr(
        MODULE,
        "request",
        lambda *_args, **_kwargs: pytest.fail("invalid token triggered a request"),
    )

    with pytest.raises(ValueError, match="invalid OAuth bearer-token format"):
        MODULE.run(ORIGIN, bearer_token="unsafe token\r\nX-Injected: yes")
