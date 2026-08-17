"""Stable, privacy-preserving project identity for cross-tool memory."""

from __future__ import annotations

import configparser
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCP_REMOTE = re.compile(
    r"^(?:[^@/:\s]+@)?(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):(?P<path>[^?#\s]+)$"
)
_MAX_PATH_CHARS = 32_768
_MAX_GIT_POINTER_BYTES = 4_096
_MAX_GIT_CONFIG_BYTES = 1_048_576


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    root: str
    origin: str | None
    trusted_root: Path | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "root": self.root,
            "origin": self.origin,
        }


def _normalized_project_hint(value: str | Path, *, launched: Path) -> str:
    raw = os.fspath(value)
    if not raw or len(raw) > _MAX_PATH_CHARS or _CONTROL.search(raw):
        raise ValueError("project path must be a bounded local path")
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(str(launched), expanded)
    return os.path.normpath(expanded)


def _inside_workspace(value: str, *, root: Path) -> bool:
    try:
        requested = os.path.normcase(value)
        boundary = os.path.normcase(str(root))
        return os.path.commonpath([requested, boundary]) == boundary
    except (OSError, ValueError):
        return False


def _repository_root(start: Path) -> Path:
    candidate = start if start.is_dir() else start.parent
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return parent
    return candidate


def _bounded_text(path: Path, *, max_bytes: int) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _worktree_config(git_file: Path) -> Path | None:
    pointer = _bounded_text(git_file, max_bytes=_MAX_GIT_POINTER_BYTES)
    if pointer is None:
        return None
    match = re.fullmatch(r"gitdir:\s*([^\r\n]+)\s*", pointer)
    if match is None or _CONTROL.search(match.group(1)):
        return None
    location = Path(match.group(1).strip())
    try:
        git_dir = (location if location.is_absolute() else git_file.parent / location).resolve(
            strict=True
        )
    except (OSError, RuntimeError):
        return None
    if not git_dir.is_dir() or git_dir.is_symlink():
        return None

    common_pointer = _bounded_text(
        git_dir / "commondir", max_bytes=_MAX_GIT_POINTER_BYTES
    )
    if common_pointer is None:
        return None
    common_raw = common_pointer.strip()
    if not common_raw or _CONTROL.search(common_raw) or "\n" in common_raw or "\r" in common_raw:
        return None
    common_location = Path(common_raw)
    try:
        common_dir = (
            common_location if common_location.is_absolute() else git_dir / common_location
        ).resolve(strict=True)
        worktrees_dir = git_dir.parent.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if (
        not common_dir.is_dir()
        or common_dir.is_symlink()
        or worktrees_dir.name != "worktrees"
        or worktrees_dir.parent != common_dir
    ):
        return None
    return common_dir / "config"


def _git_config(root: Path) -> Path | None:
    git = root / ".git"
    if git.is_symlink():
        return None
    config_path = git / "config" if git.is_dir() else None
    if git.is_file():
        config_path = _worktree_config(git)
    if config_path is None or config_path.is_symlink() or not config_path.is_file():
        return None
    return config_path


def _safe_origin(raw: str) -> str | None:
    value = raw.strip()
    if not value or len(value) > 4_096 or _CONTROL.search(value):
        return None

    scp = (
        _SCP_REMOTE.fullmatch(value)
        if "://" not in value and not re.match(r"^[A-Za-z]:[\\/]", value)
        else None
    )
    if scp is not None:
        host = scp.group("host").lower().rstrip(".")
        path = scp.group("path").strip("/").removesuffix(".git")
    else:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https", "ssh", "git"} or not parsed.hostname:
            return None
        host = parsed.hostname.lower().rstrip(".")
        if port is not None:
            host = f"{host}:{port}"
        path = parsed.path.strip("/").removesuffix(".git")

    pieces = path.split("/") if path else []
    if (
        not host
        or not pieces
        or any(part in {"", ".", ".."} for part in pieces)
        or any(_CONTROL.search(part) or any(character.isspace() for character in part) for part in pieces)
    ):
        return None
    return f"{host}/{'/'.join(pieces)}".lower().rstrip("/")


def _origin(root: Path) -> str | None:
    config_path = _git_config(root)
    if config_path is None:
        return None
    source = _bounded_text(config_path, max_bytes=_MAX_GIT_CONFIG_BYTES)
    if source is None:
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(source)
        raw = parser.get('remote "origin"', "url", fallback="").strip()
    except configparser.Error:
        return None
    return _safe_origin(raw)


def detect_project(cwd: str | Path | None = None) -> Project:
    try:
        launched = Path.cwd().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("launched workspace must exist and be accessible") from exc
    launched_root = _repository_root(launched)
    requested = (
        str(launched)
        if cwd is None
        else _normalized_project_hint(cwd, launched=launched)
    )
    trusted_root = launched_root if _inside_workspace(requested, root=launched_root) else None
    root = str(trusted_root) if trusted_root is not None else requested
    origin = _origin(trusted_root) if trusted_root is not None else None
    identity = origin or os.path.normcase(root)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    name = origin.rsplit("/", 1)[-1] if origin else os.path.basename(root)
    return Project(
        id=f"project-{digest}",
        name=name or "Project",
        root=root,
        origin=origin,
        trusted_root=trusted_root,
    )
