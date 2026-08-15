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
    cline_config = home / ".cline" / "data" / "settings" / "cline_mcp_settings.json"
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    opencode_config = config_root / "opencode" / "opencode.json"
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        paths = {
            "claude": ("Claude Desktop", roaming / "Claude" / "claude_desktop_config.json"),
            "cursor": ("Cursor", home / ".cursor" / "mcp.json"),
            "windsurf": ("Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
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
            "gemini": ("Gemini CLI", home / ".gemini" / "settings.json"),
            "codex": ("Codex", home / ".codex" / "config.toml"),
            "cline": ("Cline CLI", cline_config),
            "opencode": ("OpenCode", opencode_config),
        }
    targets: dict[str, ClientTarget] = {}
    for key, (label, path) in paths.items():
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
        elif target.kind == "toml":
            backup = _toml_config(target.config_path, command, args)
        else:
            backup = _json_config(target.config_path, command, args)
        results.append(
            {
                "client": key,
                "label": target.label,
                "config": str(target.config_path),
                "backup": str(backup) if backup else None,
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
    targets = client_targets(home)
    unknown = sorted(set(keys) - set(targets))
    if unknown:
        raise ValueError("Unknown clients: " + ", ".join(unknown))
    results = []
    for key in keys:
        target = targets[key]
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
