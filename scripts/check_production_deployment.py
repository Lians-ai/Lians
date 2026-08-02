"""Secret-free smoke checks for a deployed Lians API."""

from __future__ import annotations

import argparse
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Production base URL must be an absolute HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Production base URL must not contain credentials or parameters")
    return value.rstrip("/") + "/"


def request(base_url: str, path: str, *, timeout: float = 20.0) -> Response:
    url = urljoin(base_url, path.lstrip("/"))
    req = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "lians-production-smoke/1"},
    )
    try:
        with urlopen(
            req,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as response:
            return Response(
                status=response.status,
                body=response.read(2_000_000),
                content_type=response.headers.get_content_type(),
            )
    except HTTPError as exc:
        return Response(
            status=exc.code,
            body=exc.read(100_000),
            content_type=exc.headers.get_content_type(),
        )


def json_body(response: Response, label: str) -> dict[str, Any]:
    if response.content_type != "application/json":
        raise RuntimeError(f"{label} did not return JSON")
    try:
        value = json.loads(response.body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label} returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise TypeError(f"{label} must return a JSON object")
    return value


def validate_health(response: Response, label: str) -> None:
    if response.status != 200:
        raise RuntimeError(f"{label} returned HTTP {response.status}")
    payload = json_body(response, label)
    if payload != {"status": "ok"}:
        raise RuntimeError(f"{label} did not return the sanitized healthy response")


def validate_hidden(response: Response, label: str) -> None:
    if response.status != 404:
        raise RuntimeError(f"{label} was publicly exposed with HTTP {response.status}")


def run(base_url: str, *, health_only: bool = False) -> dict[str, Any]:
    normalized = validate_base_url(base_url)
    live = request(normalized, "/livez")
    if live.status != 200 or json_body(live, "Liveness").get("status") != "alive":
        raise RuntimeError("Liveness check failed")

    validate_health(request(normalized, "/health"), "Health")
    validate_health(request(normalized, "/readyz"), "Readiness")

    result: dict[str, Any] = {
        "base_url": normalized.rstrip("/"),
        "health": "ok",
        "liveness": "ok",
        "readiness": "ok",
    }
    if not health_only:
        validate_hidden(request(normalized, "/docs"), "Docs")
        validate_hidden(request(normalized, "/openapi.json"), "OpenAPI")
        protected = request(normalized, "/v1/decision-envelopes")
        if protected.status != 401:
            raise RuntimeError(
                "Protected endpoint did not reject missing credentials with HTTP 401"
            )
        result["authentication_boundary"] = "ok"
        result["documentation_boundary"] = "ok"
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--health-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(run(args.base_url, health_only=args.health_only), sort_keys=True))


if __name__ == "__main__":
    main()
