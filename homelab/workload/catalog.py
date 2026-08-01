"""Print the versioned plug-and-play integration catalog without extra packages."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python catalog.py CATALOG.json", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        integrations = payload["integrations"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"integration catalog could not be read: {type(exc).__name__}", file=sys.stderr)
        return 1
    if not isinstance(integrations, list):
        print("integration catalog is invalid", file=sys.stderr)
        return 1
    print("ID             STATUS       INPUT")
    for item in integrations:
        if not isinstance(item, dict):
            print("integration catalog is invalid", file=sys.stderr)
            return 1
        identifier = str(item.get("id", ""))
        status = str(item.get("status", ""))
        input_contract = str(item.get("input", ""))
        print(f"{identifier:<14} {status:<12} {input_contract}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
