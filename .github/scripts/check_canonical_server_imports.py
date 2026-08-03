#!/usr/bin/env python3
"""Reject legacy source-qualified imports in server tests and operations."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_PACKAGE = "src" + ".lians"

# These scopes execute, document, deploy, or validate the server. They must use
# the exact top-level package name installed by the production wheel.
SCAN_ROOTS = (
    ROOT / "agentmem" / "tests",
    ROOT / "agentmem" / "alembic",
    ROOT / "agentmem" / "benchmarks",
    ROOT / "agentmem" / "scripts",
    ROOT / "agentmem" / "examples",
    ROOT / ".github",
    ROOT / "deploy",
    ROOT / "k8s",
    ROOT / "ops",
    ROOT / "scripts",
    ROOT / "docs",
)
SCAN_FILES = (
    ROOT / "Dockerfile",
    ROOT / "Dockerfile.glama",
    ROOT / "README.md",
    ROOT / "RELEASING.md",
    ROOT / "fly.toml",
    ROOT / "pyproject.toml",
    ROOT / "render.yaml",
    ROOT / "server.json",
)
TEXT_SUFFIXES = {
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
}

# The public SDK is intentionally a separately installed distribution. Its
# optional local engine vendors the server under a private package and maintains
# a legacy alias internally. The one integration benchmark below exercises that
# bridge directly; no other server-side scope may depend on it.
EXCLUDED_PREFIXES = (
    ROOT / "agentmem" / "sdk" / "python",
    ROOT / "sdk" / "python",
)
EXCLUDED_FILES = {
    ROOT / "agentmem" / "benchmarks" / "locomo_distill.py",
}


def _excluded(path: Path) -> bool:
    return path in EXCLUDED_FILES or any(
        path == prefix or prefix in path.parents for prefix in EXCLUDED_PREFIXES
    )


def _files() -> list[Path]:
    paths = {path for path in SCAN_FILES if path.is_file()}
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        paths.update(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
        )
    return sorted(path for path in paths if not _excluded(path))


def main() -> None:
    violations: list[str] = []
    files = _files()
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if LEGACY_PACKAGE in line:
                violations.append(f"{relative}:{line_number}: {line.strip()}")

    if violations:
        print(
            "Server tests and operational surfaces must import the canonical "
            "top-level 'lians' package. Found legacy package references:"
        )
        for violation in violations:
            print(f"  {violation}")
        raise SystemExit(1)

    print(
        f"canonical server import guard passed across {len(files)} scoped files; "
        "SDK vendoring exclusions remain isolated"
    )


if __name__ == "__main__":
    main()
