"""Extract Prometheus rule groups from rendered Kubernetes objects.

`promtool check rules` accepts a Prometheus rule file, not a PrometheusRule CRD.
This small release helper keeps the extraction deterministic and fails closed if
the rendered chart contains zero or multiple rule objects.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def _documents(path: Path) -> list[dict[str, Any]]:
    return [
        value
        for value in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(value, dict)
    ]


def extract(source: Path, destination: Path) -> None:
    rules = [value for value in _documents(source) if value.get("kind") == "PrometheusRule"]
    if len(rules) != 1:
        raise SystemExit(
            f"expected exactly one PrometheusRule in {source}, found {len(rules)}"
        )
    groups = rules[0].get("spec", {}).get("groups")
    if not isinstance(groups, list) or not groups:
        raise SystemExit("rendered PrometheusRule has no non-empty spec.groups")
    destination.write_text(
        yaml.safe_dump({"groups": groups}, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    extract(args.source, args.destination)


if __name__ == "__main__":
    main()
