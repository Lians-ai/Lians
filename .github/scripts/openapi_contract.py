#!/usr/bin/env python3
"""Render or verify a deterministic OpenAPI contract for one API surface."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surface", choices=("public", "admin"), required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--check", type=pathlib.Path)
    destination.add_argument("--output", type=pathlib.Path)
    destination.add_argument("--stdout", action="store_true")
    return parser.parse_args()


def _render(surface: str) -> str:
    # Settings are read while the app module is imported. Each surface must be
    # rendered in its own process so cached settings cannot blend route sets.
    os.environ["API_SURFACE"] = surface
    os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "development")
    # Import the same top-level package that the production wheel exposes.
    # Falling back to the source tree keeps explicit local regeneration usable
    # without creating a second ``lians`` module identity.
    package_root = str(ROOT / "agentmem" / "src")
    if package_root not in sys.path:
        sys.path.insert(0, package_root)

    from lians.main import app

    document = app.openapi()
    document["info"]["x-lians-api-surface"] = surface
    return json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def _write_atomic(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        handle.write(content)
        temporary = pathlib.Path(handle.name)
    temporary.replace(path)


def main() -> None:
    args = _arguments()
    rendered = _render(args.surface)
    if args.stdout:
        sys.stdout.buffer.write(rendered.encode("utf-8"))
        return
    if args.output is not None:
        _write_atomic(args.output.resolve(), rendered)
        print(f"wrote {args.surface} OpenAPI contract to {args.output}")
        return

    expected_path = args.check.resolve()
    try:
        expected = expected_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"OpenAPI contract is missing: {expected_path}") from exc
    if expected == rendered:
        print(f"{args.surface} OpenAPI contract is current")
        return
    diff = "".join(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(expected_path),
            tofile=f"generated:{args.surface}",
            n=3,
        )
    )
    sys.stderr.buffer.write(diff[:200_000].encode("utf-8"))
    if len(diff) > 200_000:
        sys.stderr.buffer.write(
            b"\n... OpenAPI diff truncated at 200000 characters ...\n"
        )
    raise SystemExit(
        f"{args.surface} OpenAPI contract drifted; review it and regenerate explicitly"
    )


if __name__ == "__main__":
    main()
