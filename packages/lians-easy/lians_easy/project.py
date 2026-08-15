"""Stable, privacy-preserving project identity for cross-tool memory."""

from __future__ import annotations

import configparser
import hashlib
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    root: str
    origin: str | None

    def public(self) -> dict[str, Any]:
        return asdict(self)


def _repository_root(start: Path) -> Path:
    candidate = start if start.is_dir() else start.parent
    candidate = candidate.resolve()
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    return candidate


def _origin(root: Path) -> str | None:
    git = root / ".git"
    config_path = git / "config" if git.is_dir() else None
    if git.is_file():
        match = re.search(r"^gitdir:\s*(.+)$", git.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            location = Path(match.group(1).strip())
            config_path = (location if location.is_absolute() else root / location) / "config"
    if not config_path or not config_path.is_file():
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
        raw = parser.get('remote "origin"', "url", fallback="").strip()
    except (configparser.Error, OSError):
        return None
    if not raw:
        return None
    normalized = raw.removesuffix(".git")
    if normalized.startswith("git@") and ":" in normalized:
        host, path = normalized[4:].split(":", 1)
        normalized = f"{host}/{path}"
    normalized = re.sub(r"^https?://", "", normalized, flags=re.IGNORECASE)
    return normalized.lower().rstrip("/")


def detect_project(cwd: str | Path | None = None) -> Project:
    root = _repository_root(Path(cwd or Path.cwd()).expanduser())
    origin = _origin(root)
    identity = origin or os.path.normcase(str(root))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    name = origin.rsplit("/", 1)[-1] if origin else root.name
    return Project(id=f"project-{digest}", name=name or "Project", root=str(root), origin=origin)
