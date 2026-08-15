"""Safe, idempotent client configuration for Lians Easy."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANAGED_START = "# >>> Lians Memory (managed by Lians Easy)"
MANAGED_END = "# <<< Lians Memory (managed by Lians Easy)"
ANTIGRAVITY_PLUGIN_NAME = "lians-memory"


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
    antigravity_config = (
        home
        / ".gemini"
        / "config"
        / "plugins"
        / ANTIGRAVITY_PLUGIN_NAME
        / "mcp_config.json"
    )
    cline_config = home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    opencode_config = config_root / "opencode" / "opencode.json"
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        paths = {
            "claude": ("Claude Desktop", roaming / "Claude" / "claude_desktop_config.json"),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            "antigravity": (
                "Antigravity CLI",
                antigravity_config,
            ),
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
            "cline": ("Cline CLI", cline_config),
            "opencode": ("OpenCode", opencode_config),
        }
    elif sys.platform == "darwin":
        paths = {
            "claude": (
                "Claude Desktop",
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            ),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            "antigravity": (
                "Antigravity CLI",
                antigravity_config,
            ),
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
            "cline": ("Cline CLI", cline_config),
            "opencode": ("OpenCode", opencode_config),
        }
    else:
        config = config_root
        paths = {
            "claude": ("Claude Desktop", config / "Claude" / "claude_desktop_config.json"),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
            "antigravity": (
                "Antigravity CLI",
                antigravity_config,
            ),
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
            "cline": ("Cline CLI", cline_config),
            "opencode": ("OpenCode", opencode_config),
        }
    targets: dict[str, ClientTarget] = {}
    for key, (label, path) in paths.items():
        if key == "antigravity":
            detected = path.exists() or shutil.which("agy") is not None
        elif key == "gemini":
            detected = path.exists() or shutil.which("gemini") is not None
        else:
            detected = path.exists() or path.parent.exists()
        configured = False
        if path.exists():
            try:
                if key == "codex":
                    configured = MANAGED_START in path.read_text(encoding="utf-8")
                elif key == "opencode":
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    configured = isinstance(existing.get("mcp"), dict) and (
                        "lians" in existing["mcp"]
                    )
                else:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    configured = isinstance(existing.get("mcpServers"), dict) and (
                        "lians" in existing["mcpServers"]
                    )
                    if key == "antigravity" and configured:
                        configured = _antigravity_plugin_registered(home, path.parent.parent)
            except (AttributeError, json.JSONDecodeError, UnicodeDecodeError):
                configured = False
        targets[key] = ClientTarget(
            key=key,
            label=label,
            config_path=path,
            kind=(
                "toml"
                if key == "codex"
                else "antigravity_plugin"
                if key == "antigravity"
                else "json"
            ),
            detected=detected,
            configured=configured,
        )
    return targets


def runtime_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        installed = user_data_dir() / ("LiansMemory.exe" if sys.platform == "win32" else "lians-memory")
        return str(installed), ["mcp"]
    return sys.executable, ["-m", "lians_easy", "mcp"]


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    # datetime.UTC is unavailable on the package's supported Python 3.10.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")  # noqa: UP017
    backup = path.with_name(f"{path.name}.lians-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


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
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _install_runtime() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    target_dir = user_data_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / ("LiansMemory.exe" if sys.platform == "win32" else "lians-memory")
    source = Path(sys.executable).resolve()
    if source != destination.resolve():
        shutil.copy2(source, destination)
        if sys.platform != "win32":
            destination.chmod(0o755)
    return destination


def _json_config(path: Path, command: str, args: list[str]) -> Path | None:
    backup = _backup(path)
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
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
    _write_text(path, json.dumps(document, indent=2) + "\n")
    return backup


def _read_json_object(path: Path, *, description: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"Cannot safely update invalid JSON {description}: {path}") from exc
    if not isinstance(document, dict):
        raise TypeError(f"Cannot safely update non-object JSON {description}: {path}")
    return document


def _antigravity_registry_path(home: Path) -> Path:
    return home / ".gemini" / "config" / "plugins.json"


def _antigravity_plugin_registered(home: Path, plugin_root: Path) -> bool:
    registry_path = _antigravity_registry_path(home)
    if not registry_path.exists():
        return False
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return False
    entries = registry.get("entries") if isinstance(registry, dict) else None
    if not isinstance(entries, list):
        return False
    expected = plugin_root.resolve()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        try:
            registered = Path(entry["path"]).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if registered != expected:
            continue
        include_only = entry.get("include_only")
        return include_only is None or (
            isinstance(include_only, list) and ANTIGRAVITY_PLUGIN_NAME in include_only
        )
    return False


def _antigravity_plugin_config(
    home: Path,
    command: str,
    args: list[str],
) -> list[Path]:
    """Install through Antigravity's plugin loader, which exposes MCP tools to agents."""

    plugin_dir = (
        home / ".gemini" / "config" / "plugins" / ANTIGRAVITY_PLUGIN_NAME
    )
    manifest_path = plugin_dir / "plugin.json"
    mcp_path = plugin_dir / "mcp_config.json"
    registry_path = _antigravity_registry_path(home)
    manifest = _read_json_object(manifest_path, description="Antigravity plugin manifest")
    manifest["name"] = ANTIGRAVITY_PLUGIN_NAME
    mcp_document = _read_json_object(mcp_path, description="Antigravity MCP configuration")
    servers = mcp_document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise TypeError(f"mcpServers must be an object in {mcp_path}")
    servers["lians"] = {"command": command, "args": args}

    registry = _read_json_object(registry_path, description="Antigravity plugin registry")
    entries = registry.setdefault("entries", [])
    if not isinstance(entries, list):
        raise TypeError(f"entries must be an array in {registry_path}")
    plugin_root = str(plugin_dir.parent.resolve()).replace("\\", "/")
    matching = [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("path") == plugin_root
    ]
    if matching:
        entry = matching[0]
        include_only = entry.get("include_only")
        if include_only is not None:
            if not isinstance(include_only, list) or not all(
                isinstance(value, str) for value in include_only
            ):
                raise TypeError(f"include_only must be a string array in {registry_path}")
            if ANTIGRAVITY_PLUGIN_NAME not in include_only:
                include_only.append(ANTIGRAVITY_PLUGIN_NAME)
    else:
        entries.append(
            {"path": plugin_root, "include_only": [ANTIGRAVITY_PLUGIN_NAME]}
        )

    backups = [
        candidate
        for candidate in (
            _backup(manifest_path),
            _backup(mcp_path),
            _backup(registry_path),
        )
        if candidate is not None
    ]
    _write_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    _write_text(mcp_path, json.dumps(mcp_document, indent=2) + "\n")
    _write_text(registry_path, json.dumps(registry, indent=2) + "\n")
    return backups


def _remove_antigravity_plugin(home: Path) -> list[Path]:
    plugin_dir = (
        home / ".gemini" / "config" / "plugins" / ANTIGRAVITY_PLUGIN_NAME
    )
    manifest_path = plugin_dir / "plugin.json"
    mcp_path = plugin_dir / "mcp_config.json"
    registry_path = _antigravity_registry_path(home)

    manifest = (
        _read_json_object(manifest_path, description="Antigravity plugin manifest")
        if manifest_path.exists()
        else None
    )
    mcp_document = (
        _read_json_object(mcp_path, description="Antigravity MCP configuration")
        if mcp_path.exists()
        else None
    )
    registry = (
        _read_json_object(registry_path, description="Antigravity plugin registry")
        if registry_path.exists()
        else None
    )
    if registry is not None:
        entries = registry.get("entries")
        if not isinstance(entries, list):
            raise TypeError(f"entries must be an array in {registry_path}")
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            include_only = entry.get("include_only")
            if include_only is not None and (
                not isinstance(include_only, list)
                or not all(isinstance(value, str) for value in include_only)
            ):
                raise TypeError(f"include_only must be a string array in {registry_path}")
    backups = [
        candidate
        for candidate in (
            _backup(manifest_path),
            _backup(mcp_path),
            _backup(registry_path),
        )
        if candidate is not None
    ]

    if mcp_document is not None:
        servers = mcp_document.get("mcpServers")
        if isinstance(servers, dict):
            servers.pop("lians", None)
        if mcp_document == {"mcpServers": {}}:
            mcp_path.unlink()
        else:
            _write_text(mcp_path, json.dumps(mcp_document, indent=2) + "\n")

    if registry is not None:
        entries = registry.get("entries")
        if not isinstance(entries, list):
            raise TypeError(f"entries must be an array in {registry_path}")
        plugin_root = str(plugin_dir.parent.resolve()).replace("\\", "/")
        updated_entries = []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("path") != plugin_root:
                updated_entries.append(entry)
                continue
            include_only = entry.get("include_only")
            if include_only is None:
                # A broad user-managed root registration should keep loading other plugins.
                updated_entries.append(entry)
                continue
            if not isinstance(include_only, list) or not all(
                isinstance(value, str) for value in include_only
            ):
                raise TypeError(f"include_only must be a string array in {registry_path}")
            remaining = [
                value for value in include_only if value != ANTIGRAVITY_PLUGIN_NAME
            ]
            if remaining:
                updated = dict(entry)
                updated["include_only"] = remaining
                updated_entries.append(updated)
        registry["entries"] = updated_entries
        _write_text(registry_path, json.dumps(registry, indent=2) + "\n")

    if manifest is not None and manifest.get("name") == ANTIGRAVITY_PLUGIN_NAME:
        remaining_components = [
            path
            for path in plugin_dir.iterdir()
            if path.name != manifest_path.name and ".lians-backup-" not in path.name
        ]
        if not remaining_components:
            manifest_path.unlink()
    if plugin_dir.exists() and not any(plugin_dir.iterdir()):
        plugin_dir.rmdir()
    return backups


def _opencode_config(path: Path, command: str, args: list[str]) -> Path | None:
    """OpenCode uses 'mcp' key with a different structure than other clients."""
    backup = _backup(path)
    if path.exists():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Cannot safely update invalid JSON: {path}") from exc
        if not isinstance(document, dict):
            raise ValueError(f"Cannot safely update non-object JSON: {path}")
    else:
        document = {}
    mcp = document.setdefault("mcp", {})
    if not isinstance(mcp, dict):
        raise TypeError(f"mcp must be an object in {path}")
    mcp["lians"] = {
        "type": "local",
        "command": [command] + args,
        "enabled": True,
        "environment": {
            "LIANS_MCP_ENABLED_TOOLS": "remember,recall,list_memories,correct_memory,forget_memory"
        },
    }
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


def _toml_config(path: Path, command: str, args: list[str]) -> Path | None:
    backup = _backup(path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    content = _strip_managed_toml(content)
    updated = f"{content}\n\n" if content else ""
    updated += _managed_toml(command, args) + "\n"
    _write_text(path, updated)
    return backup


def install(keys: list[str], *, home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
    targets = client_targets(home)
    unknown = sorted(set(keys) - set(targets))
    if unknown:
        raise ValueError("Unknown clients: " + ", ".join(unknown))
    _install_runtime()
    command, args = runtime_command()
    results = []
    for key in keys:
        target = targets[key]
        if key == "opencode":
            backup = _opencode_config(target.config_path, command, args)
            backups = [backup] if backup else []
        elif target.kind == "toml":
            backups = [backup] if (backup := _toml_config(target.config_path, command, args)) else []
        elif target.kind == "antigravity_plugin":
            backups = _antigravity_plugin_config(home, command, args)
        else:
            backups = [backup] if (backup := _json_config(target.config_path, command, args)) else []
        results.append(
            {
                "client": key,
                "label": target.label,
                "config": str(target.config_path),
                "backup": str(backups[0]) if backups else None,
                "backups": [str(backup) for backup in backups],
                "status": "installed",
            }
        )
    return {
        "status": "installed",
        "clients": results,
        "database": str(user_data_dir() / "memory.sqlite3"),
        "next_step": "Restart each selected AI client, then ask it to remember something.",
    }


def plan(keys: list[str], *, action: str, home: Path | None = None) -> dict[str, Any]:
    home = home or Path.home()
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
    home = home or Path.home()
    targets = client_targets(home)
    unknown = sorted(set(keys) - set(targets))
    if unknown:
        raise ValueError("Unknown clients: " + ", ".join(unknown))
    results = []
    for key in keys:
        target = targets[key]
        if target.kind == "antigravity_plugin":
            backups = _remove_antigravity_plugin(home)
            results.append(
                {
                    "client": key,
                    "status": "removed" if backups else "not_configured",
                    "backup": str(backups[0]) if backups else None,
                    "backups": [str(backup) for backup in backups],
                }
            )
            continue
        if not target.config_path.exists():
            results.append({"client": key, "status": "not_configured"})
            continue
        backup = _backup(target.config_path)
        if target.kind == "toml":
            content = _strip_managed_toml(target.config_path.read_text(encoding="utf-8"))
            _write_text(target.config_path, content + ("\n" if content else ""))
        elif key == "opencode":
            document = json.loads(target.config_path.read_text(encoding="utf-8"))
            mcp = document.get("mcp")
            if isinstance(mcp, dict):
                mcp.pop("lians", None)
            _write_text(target.config_path, json.dumps(document, indent=2) + "\n")
        else:
            document = json.loads(target.config_path.read_text(encoding="utf-8"))
            servers = document.get("mcpServers")
            if isinstance(servers, dict):
                servers.pop("lians", None)
            _write_text(target.config_path, json.dumps(document, indent=2) + "\n")
        results.append(
            {"client": key, "status": "removed", "backup": str(backup) if backup else None}
        )
    return {
        "status": "uninstalled",
        "clients": results,
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
