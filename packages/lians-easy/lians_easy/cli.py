"""One binary for the GUI, MCP runtime, diagnostics, and managed deployment."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
from pathlib import Path
from typing import Any

from .installer import client_targets, doctor, install, plan, uninstall
from .mcp import run


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
            print(f"- {item.get('label') or item.get('key') or item.get('client')}: "
                  f"{item.get('status') or item.get('config_path')}")
    if result.get("next_step"):
        print(result["next_step"])


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="lians-easy", description="Local memory for your AI")
    commands = result.add_subparsers(dest="command")
    mcp = commands.add_parser("mcp", help="Run the local MCP memory server")
    mcp.add_argument("--data", type=Path)
    mcp.add_argument("--profile", default="personal")

    for name in ("install", "uninstall"):
        command = commands.add_parser(name, help=f"{name.title()} supported AI client settings")
        command.add_argument(
            "--clients",
            default="detected",
            help="Comma-separated claude,cursor,windsurf,gemini,codex; or detected/all",
        )
        command.add_argument("--yes", action="store_true", help="Confirm a non-interactive change")
        command.add_argument("--plan", action="store_true", help="Show exact targets without changing them")
        command.add_argument("--json", action="store_true")

    diagnostic = commands.add_parser("doctor", help="Show runtime and client detection")
    diagnostic.add_argument("--json", action="store_true")
    return result


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "mcp":
        run(args.data, profile=args.profile)
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
