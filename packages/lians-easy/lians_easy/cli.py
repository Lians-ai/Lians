"""One binary for the GUI, MCP runtime, diagnostics, and managed deployment."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .bridge import (
    BridgeApplication,
    context_for_event,
    run_hook,
    write_cursor_rule,
)
from .installer import client_targets, doctor, install, plan, uninstall
from .lifecycle import listen_for_windows_installer_shutdown
from .mcp import default_data_path, run
from .store import MemoryStore


def _keys(raw: str) -> list[str]:
    if raw == "detected":
        return [key for key, target in client_targets().items() if target.detected]
    if raw == "all":
        return list(client_targets())
    return [value.strip().lower() for value in raw.split(",") if value.strip()]


def _show(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
        return
    print(result.get("status", "Lians Memory"))
    for item in result.get("clients", []):
        if isinstance(item, dict):
            print(
                f"- {item.get('label') or item.get('key') or item.get('client')}: "
                f"{item.get('status') or item.get('config_path')}"
            )
    if result.get("next_step"):
        print(result["next_step"])


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="lians", description="Local memory for your AI")
    result.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = result.add_subparsers(dest="command")
    mcp = commands.add_parser("mcp", help="Run the local MCP memory server")
    mcp.add_argument("--data", type=Path)
    mcp.add_argument("--profile", default="personal")

    bridge = commands.add_parser("bridge", help="Run the local Lians App service")
    bridge.add_argument("--data", type=Path)
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=7317)
    bridge.add_argument("--app-dir", type=Path)

    app = commands.add_parser("app", help="Open the local Lians control center")
    app.add_argument("--data", type=Path)
    app.add_argument("--host", default="127.0.0.1")
    app.add_argument("--port", type=int, default=7317)

    hook = commands.add_parser("hook", help="Inject bounded memory into an AI prompt")
    hook.add_argument(
        "--client",
        choices=("antigravity", "claude", "codex", "cursor", "gemini"),
        required=True,
    )
    hook.add_argument("--data", type=Path)

    context = commands.add_parser("context", help="Preview a signed context pack")
    context.add_argument("--client", default="preview")
    context.add_argument("--cwd", type=Path, default=Path.cwd())
    context.add_argument("--prompt")
    context.add_argument("--data", type=Path)
    context.add_argument("--json", action="store_true")

    cursor_rule = commands.add_parser(
        "cursor-rule", help="Refresh Cursor's always-applied Lians project context"
    )
    cursor_rule.add_argument("--project", type=Path, default=Path.cwd())
    cursor_rule.add_argument("--data", type=Path)
    cursor_rule.add_argument("--json", action="store_true")

    for name in ("install", "uninstall"):
        command = commands.add_parser(name, help=f"{name.title()} supported AI client settings")
        command.add_argument(
            "--clients",
            default="detected",
            help=(
                "Comma-separated antigravity,claude,cursor,windsurf,gemini,codex; "
                "or detected/all"
            ),
        )
        command.add_argument("--yes", action="store_true", help="Confirm a non-interactive change")
        command.add_argument(
            "--plan", action="store_true", help="Show exact targets without changing them"
        )
        command.add_argument("--json", action="store_true")

    diagnostic = commands.add_parser("doctor", help="Show runtime and client detection")
    diagnostic.add_argument("--json", action="store_true")
    return result


def main(argv: list[str] | None = None) -> None:
    listen_for_windows_installer_shutdown()
    args = parser().parse_args(argv)
    if args.command == "mcp":
        run(args.data, profile=args.profile)
        return
    if args.command == "bridge":
        BridgeApplication(
            MemoryStore(args.data or default_data_path()),
            host=args.host,
            port=args.port,
            app_dir=args.app_dir,
        ).serve()
        return
    if args.command == "app":
        BridgeApplication(
            MemoryStore(args.data or default_data_path()),
            host=args.host,
            port=args.port,
        ).serve(open_browser=True)
        return
    if args.command == "hook":
        raise SystemExit(run_hook(client=args.client, data_path=args.data))
    if args.command == "context":
        prompt = args.prompt if args.prompt is not None else sys.stdin.read(1_000_001)
        if len(prompt) > 1_000_000:
            raise SystemExit("Prompt is too large")
        pack = context_for_event(
            {"prompt": prompt, "cwd": str(args.cwd)},
            client=args.client,
            store=MemoryStore(args.data or default_data_path()),
        )
        print(json.dumps(pack, ensure_ascii=False, indent=2) if args.json else pack["context"])
        return
    if args.command == "cursor-rule":
        result = write_cursor_rule(
            args.project, store=MemoryStore(args.data or default_data_path())
        )
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["path"])
        return
    if args.command == "doctor":
        _show(doctor(), as_json=args.json)
        return
    if args.command in {"install", "uninstall"}:
        keys = _keys(args.clients)
        if not keys:
            raise SystemExit("No supported AI clients were detected. Use --clients to choose one.")
        if args.plan:
            _show(plan(keys, action=args.command), as_json=args.json)
            return
        if not args.yes:
            raise SystemExit("Review the selected clients, then rerun with --yes (or use the GUI).")
        operation = install if args.command == "install" else uninstall
        _show(operation(keys), as_json=args.json)
        return
    if sys.platform == "win32":
        # The standalone app is a console binary so MCP receives real stdio
        # pipes. Hide the console only for a human double-clicking the GUI.
        window = ctypes.windll.kernel32.GetConsoleWindow()  # type: ignore[attr-defined]
        if window:
            ctypes.windll.user32.ShowWindow(window, 0)  # type: ignore[attr-defined]
    from .gui import launch

    launch()


if __name__ == "__main__":
    main()
