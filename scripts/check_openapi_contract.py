"""Validate the public Lians OpenAPI identity and path conventions."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agentmem" / "src"))
from lians.main import app


def main() -> int:
    schema = app.openapi()
    errors: list[str] = []
    info = schema.get("info", {})
    if info.get("title") != "Lians":
        errors.append(f"OpenAPI title is {info.get('title')!r}, expected 'Lians'")

    paths = schema.get("paths", {})
    noncanonical = [
        path for path in paths
        if not path.startswith("/v1/") and path not in {"/metrics", "/v1"}
    ]
    if noncanonical:
        errors.append(f"Non-canonical documented API paths: {sorted(noncanonical)}")

    operation_ids = [
        spec.get("operationId")
        for methods in paths.values()
        for method, spec in methods.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
        and spec.get("operationId")
    ]
    duplicate_ids = [
        operation_id
        for operation_id, count in Counter(operation_ids).items()
        if count > 1
    ]
    if duplicate_ids:
        errors.append(f"Duplicate operation IDs: {duplicate_ids}")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(
        f"OpenAPI contract valid: {len(paths)} paths, "
        f"{len(operation_ids)} operations, version {info.get('version')}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
