"""Safe, idempotent client configuration for Lians Easy."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from stat import S_IMODE
from typing import Any

from . import __version__

MANAGED_START = "# >>> Lians Memory (managed by Lians Easy)"
MANAGED_END = "# <<< Lians Memory (managed by Lians Easy)"
HOOK_STATUS = "Recalling Lians memory"
LIANS_HOOK_NAME = "lians-memory-recall"


@dataclass(frozen=True)
class ClientTarget:
    key: str
    label: str
    config_path: Path
    kind: str = "json"
    detected: bool = False
    configured: bool = False

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["config_path"] = str(self.config_path)
        return result


@dataclass(frozen=True)
class FileSnapshot:
    """Exact pre-install state for one file in a client transaction."""

    path: Path
    existed: bool
    content: bytes | None
    mode: int | None

    @classmethod
    def capture(cls, path: Path) -> FileSnapshot:
        if not path.exists():
            return cls(path=path, existed=False, content=None, mode=None)
        return cls(
            path=path,
            existed=True,
            content=path.read_bytes(),
            mode=S_IMODE(path.stat().st_mode),
        )

    def restore(self) -> None:
        if not self.existed:
            self.path.unlink(missing_ok=True)
            return
        assert self.content is not None
        _write_bytes(self.path, self.content, mode=self.mode)


def user_data_dir() -> Path:
    override = os.environ.get("LIANS_EASY_HOME")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Lians"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Lians"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "lians"


def client_targets(home: Path | None = None) -> dict[str, ClientTarget]:
    home = home or Path.home()
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        paths = {
            "antigravity": (
                "Google Antigravity",
                home / ".gemini" / "config" / "mcp_config.json",
            ),
            "claude": ("Claude Desktop", roaming / "Claude" / "claude_desktop_config.json"),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
        }
    elif sys.platform == "darwin":
        paths = {
            "antigravity": (
                "Google Antigravity",
                home / ".gemini" / "config" / "mcp_config.json",
            ),
            "claude": (
                "Claude Desktop",
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            ),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
        }
    else:
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        paths = {
            "antigravity": (
                "Google Antigravity",
                home / ".gemini" / "config" / "mcp_config.json",
            ),
            "claude": ("Claude Desktop", config / "Claude" / "claude_desktop_config.json"),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
        }
    targets: dict[str, ClientTarget] = {}
    for key, (label, path) in paths.items():
        detected = path.exists() or path.parent.exists()
        configured = False
        if path.exists():
            try:
                if key == "codex":
                    configured = MANAGED_START in path.read_text(encoding="utf-8")
                else:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    configured = isinstance(existing.get("mcpServers"), dict) and (
                        "lians" in existing["mcpServers"]
                    )
            except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                configured = False
        targets[key] = ClientTarget(
            key=key,
            label=label,
            config_path=path,
            kind="toml" if key == "codex" else "json",
            detected=detected,
            configured=configured,
        )
    return targets


def runtime_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        installed = user_data_dir() / (
            "LiansMemory.exe" if sys.platform == "win32" else "lians-memory"
        )
        return str(installed), ["mcp"]
    return sys.executable, ["-m", "lians_easy", "mcp"]


def _runtime_argv(*args: str) -> list[str]:
    command, prefix = runtime_command()
    if getattr(sys, "frozen", False):
        return [command, *args]
    return [command, *prefix[:-1], *args]


def _shell_command(argv: list[str], *, windows: bool) -> str:
    return subprocess.list2cmdline(argv) if windows else shlex.join(argv)


def _hook_path(client: str, home: Path) -> Path:
    if client == "antigravity":
        return home / ".gemini" / "config" / "hooks.json"
    if client == "claude":
        return home / ".claude" / "settings.json"
    if client == "codex":
        return home / ".codex" / "hooks.json"
    if client == "gemini":
        return home / ".gemini" / "settings.json"
    raise ValueError(f"{client} does not support a Lians prompt hook")


def _backup(
    path: Path, *, on_created: Callable[[Path], None] | None = None
) -> Path | None:
    if not path.exists():
        return None
    # datetime.UTC is unavailable on the package's supported Python 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")  # noqa: UP017
    backup = path.with_name(f"{path.name}.lians-backup-{stamp}")
    _write_bytes(
        backup,
        path.read_bytes(),
        mode=S_IMODE(path.stat().st_mode),
    )
    if on_created is not None:
        on_created(backup)
    return backup


def _sync_directory(path: Path) -> None:
    """Persist a POSIX directory entry after an atomic rename."""
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_replace(source: Path, destination: Path) -> None:
    """Atomically replace and request durable rename metadata from the OS."""
    if os.name == "nt":
        import ctypes

        move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move_file.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        move_file.restype = ctypes.c_int
        movefile_replace_existing = 0x1
        movefile_write_through = 0x8
        if not move_file(
            str(source),
            str(destination),
            movefile_replace_existing | movefile_write_through,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    os.replace(source, destination)
    _sync_directory(destination.parent)


def _write_bytes(path: Path, content: bytes, *, mode: int | None = None) -> None:
    """Atomically replace a file while retaining its original permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.lians-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        elif mode is not None:
            temporary.chmod(mode)
        _durable_replace(temporary, path)
        if mode is not None:
            path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text(path: Path, content: str) -> None:
    """Atomically replace a config so interruption cannot leave partial JSON/TOML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.lians-", dir=path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, temporary)
        _durable_replace(temporary, path)
        if (
            os.environ.get("LIANS_EASY_TEST_MODE") == "crash-recovery"
            and os.environ.get("LIANS_EASY_TEST_CRASH_AFTER_WRITE") == path.name
        ):
            # Release-artifact fault injection. The variable is used only by the
            # crash-recovery harness and has no effect unless explicitly set.
            os._exit(86)
    finally:
        temporary.unlink(missing_ok=True)


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def _transaction_dir() -> Path:
    return user_data_dir() / "setup-transactions"


@dataclass
class SetupJournal:
    """Durable rollback metadata for one client setup transaction."""

    path: Path
    client: str
    entries: list[dict[str, Any]]

    @classmethod
    def begin(cls, client: str, paths: list[Path]) -> SetupJournal:
        directory = _transaction_dir()
        directory.mkdir(parents=True, exist_ok=True)
        journal = cls(
            path=directory / f"{client}-{uuid.uuid4().hex}.json",
            client=client,
            entries=[
                {
                    "path": str(path.absolute()),
                    "existed": path.exists(),
                    "original_backup": None,
                    "transaction_backups": [],
                }
                for path in paths
            ],
        )
        journal.persist()
        return journal

    @classmethod
    def load(cls, path: Path) -> SetupJournal:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("An interrupted Lians setup report is invalid") from exc
        if not isinstance(document, dict) or document.get("version") != 1:
            raise ValueError("An interrupted Lians setup report is unsupported")
        client = document.get("client")
        entries = document.get("entries")
        if not isinstance(client, str) or not isinstance(entries, list):
            raise TypeError("An interrupted Lians setup report is incomplete")
        return cls(path=path, client=client, entries=entries)

    def persist(self) -> None:
        _write_text(
            self.path,
            json.dumps(
                {"version": 1, "client": self.client, "entries": self.entries},
                indent=2,
            )
            + "\n",
        )

    def record_backup(self, backup: Path) -> None:
        matches = []
        for entry in self.entries:
            target = Path(entry["path"])
            if (
                _path_key(backup.parent) == _path_key(target.parent)
                and backup.name.startswith(f"{target.name}.lians-backup-")
            ):
                matches.append(entry)
        if len(matches) != 1:
            raise ValueError("Lians could not safely journal a settings backup")
        entry = matches[0]
        backups = entry["transaction_backups"]
        rendered = str(backup.absolute())
        if rendered not in backups:
            backups.append(rendered)
        if entry["original_backup"] is None:
            entry["original_backup"] = rendered
        self.persist()

    def finish(self) -> None:
        self.path.unlink(missing_ok=True)


@contextmanager
def _setup_lock() -> Iterator[None]:
    """Allow only one setup mutation process while releasing automatically on crash."""
    directory = user_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "setup.lock"
    handle = lock_path.open("a+b")
    try:
        if lock_path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Lians Setup is already running on this computer") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _install_runtime() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    target_dir = user_data_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / ("LiansMemory.exe" if sys.platform == "win32" else "lians-memory")
    source = Path(sys.executable).resolve()
    if source != destination.resolve():
        _write_bytes(
            destination,
            source.read_bytes(),
            mode=None if sys.platform == "win32" else 0o755,
        )
    return destination


def _json_config(
    path: Path,
    command: str,
    args: list[str],
    *,
    on_backup: Callable[[Path], None] | None = None,
) -> Path | None:
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
            document = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot safely update invalid JSON: {path}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"Cannot safely update non-object JSON: {path}")
    else:
        document = {}
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise TypeError(f"mcpServers must be an object in {path}")
    servers["lians"] = {"command": command, "args": args}
    backup = _backup(path, on_created=on_backup)
    _write_text(path, json.dumps(document, indent=2) + "\n")
    return backup


def _lians_hook_group(client: str) -> dict[str, Any]:
    argv = _runtime_argv("hook", "--client", client)
    if client == "gemini":
        return {
            "matcher": "*",
            "sequential": True,
            "hooks": [
                {
                    "name": LIANS_HOOK_NAME,
                    "type": "command",
                    "command": _shell_command(argv, windows=sys.platform == "win32"),
                    "timeout": 8000,
                    "description": "Inject bounded project memory before Gemini starts the turn",
                }
            ],
        }
    hook: dict[str, Any] = {
        "type": "command",
        "command": _shell_command(argv, windows=sys.platform == "win32"),
        "timeout": 8,
        "statusMessage": HOOK_STATUS,
    }
    if client == "codex":
        hook["commandWindows"] = _shell_command(argv, windows=True)
        hook["additionalContextLimit"] = 2048
    return {"hooks": [hook]}


def _is_lians_hook_group(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("hooks"), list):
        return False
    return any(
        isinstance(hook, dict)
        and (
            hook.get("statusMessage") == HOOK_STATUS
            or hook.get("name") == LIANS_HOOK_NAME
        )
        for hook in value["hooks"]
    )


def _hook_config(
    path: Path,
    client: str,
    *,
    remove: bool = False,
    on_backup: Callable[[Path], None] | None = None,
) -> Path | None:
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot safely update invalid JSON: {path}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"Cannot safely update non-object JSON: {path}")
    else:
        document = {}
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise TypeError(f"hooks must be an object in {path}")
    event_name = "BeforeAgent" if client == "gemini" else "UserPromptSubmit"
    prompt_hooks = hooks.setdefault(event_name, [])
    if not isinstance(prompt_hooks, list):
        raise TypeError(f"hooks.{event_name} must be an array in {path}")
    prompt_hooks[:] = [group for group in prompt_hooks if not _is_lians_hook_group(group)]
    if not remove:
        prompt_hooks.append(_lians_hook_group(client))
    if remove and not prompt_hooks:
        hooks.pop(event_name, None)
    if remove and not hooks:
        document.pop("hooks", None)
    backup = _backup(path, on_created=on_backup)
    _write_text(path, json.dumps(document, indent=2) + "\n")
    return backup


def _antigravity_hook_config(
    path: Path,
    *,
    remove: bool = False,
    on_backup: Callable[[Path], None] | None = None,
) -> Path | None:
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8")
            document = json.loads(raw) if raw.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot safely update invalid JSON: {path}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"Cannot safely update non-object JSON: {path}")
    else:
        document = {}
    if remove:
        document.pop(LIANS_HOOK_NAME, None)
    else:
        argv = _runtime_argv("hook", "--client", "antigravity")
        document[LIANS_HOOK_NAME] = {
            "PreInvocation": [
                {
                    "type": "command",
                    "command": _shell_command(argv, windows=sys.platform == "win32"),
                    "timeout": 8,
                }
            ]
        }
    backup = _backup(path, on_created=on_backup)
    _write_text(path, json.dumps(document, indent=2) + "\n")
    return backup


def _managed_toml(command: str, args: list[str]) -> str:
    rendered_args = ", ".join(json.dumps(value) for value in args)
    return (
        f"{MANAGED_START}\n"
        "[mcp_servers.lians]\n"
        f"command = {json.dumps(command)}\n"
        f"args = [{rendered_args}]\n"
        f"{MANAGED_END}"
    )


def _strip_managed_toml(content: str) -> str:
    start = content.find(MANAGED_START)
    end = content.find(MANAGED_END)
    if start == -1 and end == -1:
        return content.rstrip()
    if start == -1 or end == -1 or end < start:
        raise ValueError("Codex config contains an incomplete Lians managed block")
    end += len(MANAGED_END)
    return (content[:start].rstrip() + "\n" + content[end:].lstrip()).strip()


def _toml_config(
    path: Path,
    command: str,
    args: list[str],
    *,
    on_backup: Callable[[Path], None] | None = None,
) -> Path | None:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    content = _strip_managed_toml(content)
    updated = f"{content}\n\n" if content else ""
    updated += _managed_toml(command, args) + "\n"
    backup = _backup(path, on_created=on_backup)
    _write_text(path, updated)
    return backup


def _client_paths(key: str, target: ClientTarget, home: Path) -> list[Path]:
    paths = [target.config_path]
    if key in {"antigravity", "claude", "codex", "gemini"}:
        paths.append(_hook_path(key, home))
    return list(dict.fromkeys(paths))


def _validated_journal_entries(
    journal: SetupJournal, *, home: Path
) -> list[dict[str, Any]]:
    targets = client_targets(home)
    if journal.client not in targets:
        raise ValueError("An interrupted Lians setup report names an unknown AI app")
    allowed_paths = _client_paths(journal.client, targets[journal.client], home)
    allowed = {_path_key(path): path for path in allowed_paths}
    if len(journal.entries) != len(allowed):
        raise ValueError("An interrupted Lians setup report has unexpected targets")

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_entry in journal.entries:
        if not isinstance(raw_entry, dict):
            raise TypeError("An interrupted Lians setup report has an invalid target")
        raw_path = raw_entry.get("path")
        existed = raw_entry.get("existed")
        original_backup = raw_entry.get("original_backup")
        transaction_backups = raw_entry.get("transaction_backups")
        if (
            not isinstance(raw_path, str)
            or not isinstance(existed, bool)
            or (original_backup is not None and not isinstance(original_backup, str))
            or not isinstance(transaction_backups, list)
            or not all(isinstance(value, str) for value in transaction_backups)
        ):
            raise ValueError("An interrupted Lians setup report has invalid rollback data")
        key = _path_key(Path(raw_path))
        if key not in allowed or key in seen:
            raise ValueError("An interrupted Lians setup report targets an unexpected file")
        seen.add(key)
        target = allowed[key]

        backups: list[Path] = []
        for raw_backup in transaction_backups:
            backup = Path(raw_backup)
            if (
                _path_key(backup.parent) != _path_key(target.parent)
                or not backup.name.startswith(f"{target.name}.lians-backup-")
            ):
                raise ValueError("An interrupted Lians setup report has an unsafe backup")
            backups.append(backup)
        original = Path(original_backup) if original_backup is not None else None
        if original is not None and all(
            _path_key(original) != _path_key(backup) for backup in backups
        ):
            raise ValueError("An interrupted Lians setup report lost its original backup")
        validated.append(
            {
                "path": target,
                "existed": existed,
                "original_backup": original,
                "transaction_backups": backups,
            }
        )
    if seen != set(allowed):
        raise ValueError("An interrupted Lians setup report is missing a target")
    return validated


def _recover_interrupted_transactions(*, home: Path) -> list[str]:
    directory = _transaction_dir()
    if not directory.is_dir():
        return []
    recovered: list[str] = []
    for journal_path in sorted(directory.glob("*.json")):
        journal = SetupJournal.load(journal_path)
        entries = _validated_journal_entries(journal, home=home)
        for entry in reversed(entries):
            target = entry["path"]
            if entry["existed"]:
                backup = entry["original_backup"]
                if backup is None:
                    # Every mutating writer records its backup before replacement,
                    # so a missing reference proves this target was not changed.
                    continue
                if not backup.is_file():
                    raise RuntimeError(
                        "Lians found an interrupted setup but its protected backup is missing"
                    )
                _write_bytes(
                    target,
                    backup.read_bytes(),
                    mode=S_IMODE(backup.stat().st_mode),
                )
            else:
                target.unlink(missing_ok=True)
        for entry in entries:
            for backup in entry["transaction_backups"]:
                backup.unlink(missing_ok=True)
        journal.finish()
        recovered.append(journal.client)
    return list(dict.fromkeys(recovered))


def _verify_client(key: str, *, home: Path) -> None:
    target = client_targets(home)[key]
    if not target.configured:
        raise RuntimeError(f"Lians could not verify {target.label}")
    if key not in {"antigravity", "claude", "codex", "gemini"}:
        return
    hook_path = _hook_path(key, home)
    try:
        raw = hook_path.read_text(encoding="utf-8")
        document = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Lians could not verify automatic recall for {target.label}") from exc
    if not isinstance(document, dict):
        raise TypeError(f"Lians could not verify automatic recall for {target.label}")
    if key == "antigravity":
        verified = LIANS_HOOK_NAME in document
    else:
        event_name = "BeforeAgent" if key == "gemini" else "UserPromptSubmit"
        hooks = document.get("hooks", {})
        groups = hooks.get(event_name, []) if isinstance(hooks, dict) else []
        verified = isinstance(groups, list) and any(_is_lians_hook_group(group) for group in groups)
    if not verified:
        raise RuntimeError(f"Lians could not verify automatic recall for {target.label}")


def _install_client(
    key: str,
    *,
    target: ClientTarget,
    home: Path,
    command: str,
    args: list[str],
    on_backup: Callable[[Path], None],
) -> dict[str, Any]:
    backup = (
        _toml_config(target.config_path, command, args, on_backup=on_backup)
        if target.kind == "toml"
        else _json_config(target.config_path, command, args, on_backup=on_backup)
    )
    item = {
        "client": key,
        "label": target.label,
        "config": str(target.config_path),
        "backup": str(backup) if backup else None,
        "status": "installed",
        "automatic_recall": key in {"antigravity", "claude", "codex", "cursor", "gemini"},
    }
    if key in {"antigravity", "claude", "codex", "gemini"}:
        hook_path = _hook_path(key, home)
        hook_backup = (
            _antigravity_hook_config(hook_path, on_backup=on_backup)
            if key == "antigravity"
            else _hook_config(hook_path, key, on_backup=on_backup)
        )
        item["hook_config"] = str(hook_path)
        item["hook_backup"] = str(hook_backup) if hook_backup else None
    if key == "cursor":
        item["recall_mode"] = "always-applied project rule, refreshed after memory changes"
    _verify_client(key, home=home)
    return item


def _restore_client(snapshots: list[FileSnapshot]) -> list[str]:
    restore_errors: list[str] = []
    for snapshot in reversed(snapshots):
        try:
            snapshot.restore()
        except OSError as exc:
            restore_errors.append(f"{snapshot.path}: {exc}")
    return restore_errors


def _cleanup_backups(backups: list[Path]) -> list[str]:
    errors: list[str] = []
    for backup in backups:
        try:
            backup.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{backup}: {exc}")
    return errors


def _record_setup_backup(
    path: Path, *, backups: list[Path], journal: SetupJournal
) -> None:
    backups.append(path)
    journal.record_backup(path)


def install(
    keys: list[str],
    *,
    home: Path | None = None,
    on_progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    with _setup_lock():
        return _install_unlocked(keys, home=home, on_progress=on_progress)


def _install_unlocked(
    keys: list[str],
    *,
    home: Path | None = None,
    on_progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    home = home or Path.home()
    keys = list(dict.fromkeys(keys))
    if not keys:
        raise ValueError("Choose at least one AI client")
    targets = client_targets(home)
    unknown = sorted(set(keys) - set(targets))
    if unknown:
        raise ValueError("Unknown clients: " + ", ".join(unknown))

    def progress(stage: str, detail: str) -> None:
        if on_progress is not None:
            on_progress(stage, detail)

    recovered_clients = _recover_interrupted_transactions(home=home)
    progress(
        "protecting",
        "Restored an interrupted setup and protected your existing settings"
        if recovered_clients
        else "Protecting your existing settings",
    )
    _install_runtime()
    command, args = runtime_command()
    results: list[dict[str, Any]] = []
    for key in keys:
        target = targets[key]
        progress("connecting", f"Connecting {target.label}")
        backups: list[Path] = []
        snapshots: list[FileSnapshot] | None = None
        journal: SetupJournal | None = None
        try:
            paths = _client_paths(key, target, home)
            snapshots = [FileSnapshot.capture(path) for path in paths]
            journal = SetupJournal.begin(key, paths)
            item = _install_client(
                key,
                target=target,
                home=home,
                command=command,
                args=args,
                on_backup=partial(
                    _record_setup_backup, backups=backups, journal=journal
                ),
            )
            journal.finish()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            restore_errors = _restore_client(snapshots) if snapshots else []
            journal_errors: list[str] = []
            if journal is not None and not restore_errors:
                try:
                    journal.finish()
                except OSError as journal_error:
                    journal_errors.append(f"{journal.path}: {journal_error}")
            cleanup_errors = (
                _cleanup_backups(backups)
                if not restore_errors and not journal_errors
                else []
            )
            item = {
                "client": key,
                "label": target.label,
                "config": str(target.config_path),
                "status": "failed",
                "error": str(exc),
                "rolled_back": not restore_errors,
                "retryable": not restore_errors and not journal_errors,
            }
            if restore_errors:
                item["rollback_errors"] = restore_errors
            if journal_errors:
                item["journal_errors"] = journal_errors
            if cleanup_errors:
                item["cleanup_errors"] = cleanup_errors
        results.append(item)

    progress("verifying", "Checking that memory is ready")
    installed = [item for item in results if item["status"] == "installed"]
    failed = [item for item in results if item["status"] == "failed"]
    status = "installed" if not failed else "partial" if installed else "failed"
    progress(
        "complete" if status == "installed" else "partial",
        "Lians is ready"
        if status == "installed"
        else f"{len(installed)} connected, {len(failed)} need another try",
    )
    return {
        "status": status,
        "clients": results,
        "recovered_clients": recovered_clients,
        "retry_clients": [item["client"] for item in failed if item["retryable"]],
        "database": str(user_data_dir() / "memory.sqlite3"),
        "requires_trust": [item["client"] for item in installed if item["client"] == "codex"],
        "next_step": (
            "Restart each selected AI client. In Codex, review and trust the Lians hooks in "
            "/hooks. Then ask Cursor to remember one project preference."
            if status == "installed"
            else "Retry only the failed AI apps. Successful connections were not repeated."
        ),
    }


def plan(keys: list[str], *, action: str, home: Path | None = None) -> dict[str, Any]:
    targets = client_targets(home)
    unknown = sorted(set(keys) - set(targets))
    if unknown:
        raise ValueError("Unknown clients: " + ", ".join(unknown))
    if action not in {"install", "uninstall"}:
        raise ValueError("action must be install or uninstall")
    command, args = runtime_command()
    return {
        "status": "plan",
        "action": action,
        "runtime": {"command": command, "args": args},
        "clients": [targets[key].public() for key in keys],
        "changes_made": False,
    }


def uninstall(keys: list[str], *, home: Path | None = None) -> dict[str, Any]:
    with _setup_lock():
        return _uninstall_unlocked(keys, home=home)


def _uninstall_unlocked(
    keys: list[str], *, home: Path | None = None
) -> dict[str, Any]:
    home = home or Path.home()
    targets = client_targets(home)
    unknown = sorted(set(keys) - set(targets))
    if unknown:
        raise ValueError("Unknown clients: " + ", ".join(unknown))
    recovered_clients = _recover_interrupted_transactions(home=home)
    results = []
    for key in keys:
        target = targets[key]
        backup = _backup(target.config_path) if target.config_path.exists() else None
        if target.config_path.exists() and target.kind == "toml":
            content = _strip_managed_toml(target.config_path.read_text(encoding="utf-8"))
            _write_text(target.config_path, content + ("\n" if content else ""))
        elif target.config_path.exists():
            document = json.loads(target.config_path.read_text(encoding="utf-8"))
            servers = document.get("mcpServers")
            if isinstance(servers, dict):
                servers.pop("lians", None)
            _write_text(target.config_path, json.dumps(document, indent=2) + "\n")
        item = {
            "client": key,
            "status": "removed" if target.config_path.exists() else "not_configured",
            "backup": str(backup) if backup else None,
        }
        if key in {"antigravity", "claude", "codex", "gemini"}:
            hook_path = _hook_path(key, home)
            if hook_path.exists():
                hook_backup = (
                    _antigravity_hook_config(hook_path, remove=True)
                    if key == "antigravity"
                    else _hook_config(hook_path, key, remove=True)
                )
                item["hook_backup"] = str(hook_backup) if hook_backup else None
        results.append(item)
    return {
        "status": "uninstalled",
        "clients": results,
        "recovered_clients": recovered_clients,
        "data_preserved": str(user_data_dir() / "memory.sqlite3"),
    }


def doctor(home: Path | None = None) -> dict[str, Any]:
    command, args = runtime_command()
    targets = client_targets(home)
    return {
        "runtime": {
            "command": command,
            "args": args,
            "installed": Path(command).exists(),
            "running_from": sys.executable,
            "standalone": bool(getattr(sys, "frozen", False)),
        },
        "database": str(user_data_dir() / "memory.sqlite3"),
        "clients": [target.public() for target in targets.values()],
        "chatgpt_note": (
            "ChatGPT does not load local stdio MCP servers; use a hosted Lians connector when available."
        ),
    }


def support_report(
    *, home: Path | None = None, setup_result: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a shareable diagnostic report without settings or memory content."""
    home = home or Path.home()
    database = user_data_dir() / "memory.sqlite3"
    targets = client_targets(home)
    report: dict[str, Any] = {
        "schema": "lians-support-report/v1",
        # datetime.UTC is unavailable on the package's supported Python 3.10.
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "lians_version": __version__,
        "runtime": {
            "platform": sys.platform,
            "os_release": platform.release(),
            "machine": platform.machine(),
            "standalone": bool(getattr(sys, "frozen", False)),
        },
        "memory_store": {
            "exists": database.is_file(),
            "size_bytes": database.stat().st_size if database.is_file() else 0,
        },
        "setup_recovery": {
            "pending_transactions": (
                len(list(_transaction_dir().glob("*.json")))
                if _transaction_dir().is_dir()
                else 0
            )
        },
        "clients": [
            {
                "key": target.key,
                "label": target.label,
                "detected": target.detected,
                "configured": target.configured,
            }
            for target in targets.values()
        ],
    }
    if setup_result is not None:
        clients = setup_result.get("clients", [])
        report["last_setup"] = {
            "status": setup_result.get("status", "unknown"),
            "clients": [
                {
                    key: item[key]
                    for key in (
                        "client",
                        "label",
                        "status",
                        "automatic_recall",
                        "rolled_back",
                        "retryable",
                    )
                    if key in item
                }
                for item in clients
                if isinstance(item, dict)
            ],
            "retry_clients": [
                value
                for value in setup_result.get("retry_clients", [])
                if isinstance(value, str)
            ],
        }
    return report


def write_support_report(
    destination: Path,
    *,
    home: Path | None = None,
    setup_result: dict[str, Any] | None = None,
) -> Path:
    """Write a redacted support report atomically to a user-selected path."""
    report = support_report(home=home, setup_result=setup_result)
    _write_text(destination, json.dumps(report, indent=2) + "\n")
    return destination
