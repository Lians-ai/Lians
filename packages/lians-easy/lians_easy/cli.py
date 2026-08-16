"""One binary for the GUI, MCP runtime, diagnostics, and managed deployment."""

from __future__ import annotations

import argparse
import ctypes
import getpass
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
from .claude_experiment import (
    ClaudeExperimentError,
    build_experiment_plan,
    run_claude_experiment,
)
from .installer import client_targets, doctor, install, plan, uninstall
from .lifecycle import listen_for_windows_installer_shutdown
from .mcp import default_data_path, run
from .portability import export_backup, import_backup, verify_backup
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
    print(result.get("headline") or result.get("status", "Lians"))
    for item in result.get("clients", []):
        if isinstance(item, dict):
            print(
                f"- {item.get('label') or item.get('key') or item.get('client')}: "
                f"{item.get('status') or item.get('config_path')}"
            )
    efficiency = result.get("efficiency")
    if isinstance(efficiency, dict):
        avoided = int(efficiency.get("repeated_memory_tokens_avoided_estimate") or 0)
        events = int(efficiency.get("context_events") or 0)
        print(f"- Repeated memory context left out: ~{avoided} tokens across {events} tasks")
    if result.get("next_step"):
        print(result["next_step"])


def product_status(
    *, data_path: str | Path | None = None, home: Path | None = None
) -> dict[str, Any]:
    """Return the small ordinary-user view of Lians configuration and impact."""
    targets = client_targets(home)
    configured = [target for target in targets.values() if target.configured]
    detected = [target for target in targets.values() if target.detected]
    memory = MemoryStore(data_path or default_data_path()).stats()
    return {
        "status": "optimized" if configured else "not_configured",
        "headline": (
            f"Lians is active in {len(configured)} AI app"
            f"{'s' if len(configured) != 1 else ''}."
            if configured
            else "Lians is ready to optimize your AI apps."
        ),
        "clients": [
            {
                **target.public(),
                "status": (
                    "connected"
                    if target.configured
                    else "found"
                    if target.detected
                    else "not found"
                ),
            }
            for target in targets.values()
            if target.detected or target.configured
        ],
        "configured_clients": len(configured),
        "detected_clients": len(detected),
        "efficiency": memory["efficiency"],
        "privacy": {
            "local": True,
            "encrypted": memory["encrypted"],
            "ai_account_credentials_required": False,
        },
        "next_step": (
            "Keep using your connected apps normally. Lians adds only relevant saved context."
            if configured
            else "Open Lians for guided setup, or run `lians optimize --clients detected --yes`."
        ),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="lians", description="Use less repeated context in the AI tools you already use"
    )
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

    status = commands.add_parser("status", help="Show connected apps and measured context reuse")
    status.add_argument("--data", type=Path)
    status.add_argument("--json", action="store_true")

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

    for name in ("optimize", "install", "uninstall"):
        help_text = (
            "Connect supported AI apps to Lians"
            if name == "optimize"
            else f"{name.title()} supported AI client settings"
        )
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--clients",
            default="detected",
            help=(
                "Comma-separated antigravity,claude,cursor,windsurf,gemini,codex,"
                "cline,opencode; "
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

    experiment = commands.add_parser(
        "experiment", help="Measure a bounded-context product hypothesis"
    )
    experiments = experiment.add_subparsers(dest="experiment_name", required=True)
    claude_experiment = experiments.add_parser(
        "claude", help="Compare full replay with Lians context in Claude Code"
    )
    claude_experiment.add_argument(
        "--run",
        action="store_true",
        help="Send the two isolated prompts after subscription-auth checks pass",
    )
    claude_experiment.add_argument("--model", default="sonnet")
    claude_experiment.add_argument("--repetitions", type=int, default=1)
    claude_experiment.add_argument(
        "--scenario",
        choices=("baseline", "market-research"),
        default="baseline",
    )
    claude_experiment.add_argument("--max-context-tokens", type=int)
    claude_experiment.add_argument("--output", type=Path)
    claude_experiment.add_argument("--overwrite", action="store_true")
    claude_experiment.add_argument("--json", action="store_true")

    backup = commands.add_parser("backup", help="Move encrypted memory safely between devices")
    backup_commands = backup.add_subparsers(dest="backup_action", required=True)
    backup_export = backup_commands.add_parser(
        "export", help="Create a passphrase-encrypted .liansbackup file"
    )
    backup_export.add_argument("--output", type=Path, required=True)
    backup_export.add_argument("--data", type=Path)
    backup_export.add_argument(
        "--passphrase-file", type=Path, help="Read the secret from a protected file"
    )
    backup_export.add_argument("--overwrite", action="store_true")
    backup_export.add_argument("--json", action="store_true")
    backup_verify = backup_commands.add_parser(
        "verify", help="Check a backup without changing local memory"
    )
    backup_verify.add_argument("--input", type=Path, required=True)
    backup_verify.add_argument(
        "--passphrase-file", type=Path, help="Read the secret from a protected file"
    )
    backup_verify.add_argument("--json", action="store_true")
    backup_import = backup_commands.add_parser(
        "import", help="Merge a verified backup and re-encrypt it for this device"
    )
    backup_import.add_argument("--input", type=Path, required=True)
    backup_import.add_argument("--data", type=Path)
    backup_import.add_argument(
        "--passphrase-file", type=Path, help="Read the secret from a protected file"
    )
    backup_import.add_argument("--yes", action="store_true", help="Confirm the memory import")
    backup_import.add_argument("--json", action="store_true")
    return result


def _read_backup_passphrase(*, confirm: bool, passphrase_file: Path | None) -> str:
    if passphrase_file is not None:
        if passphrase_file.stat().st_size > 4096:
            raise SystemExit("Backup passphrase file is unexpectedly large")
        if sys.platform != "win32" and passphrase_file.stat().st_mode & 0o077:
            raise SystemExit("Backup passphrase file must be readable only by its owner")
        passphrase = passphrase_file.read_text(encoding="utf-8").rstrip("\r\n")
        if not passphrase:
            raise SystemExit("Backup passphrase file is empty")
        return passphrase
    passphrase = getpass.getpass("Backup passphrase: ")
    if confirm:
        repeated = getpass.getpass("Confirm backup passphrase: ")
        if passphrase != repeated:
            raise SystemExit("Backup passphrases did not match")
    return passphrase


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
    if args.command == "status":
        _show(product_status(data_path=args.data), as_json=args.json)
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
    if args.command == "experiment":
        if args.output is not None and args.output.exists() and not args.overwrite:
            raise SystemExit("Output already exists; use --overwrite to replace it")
        try:
            result = (
                run_claude_experiment(
                    model=args.model,
                    repetitions=args.repetitions,
                    max_context_tokens=args.max_context_tokens,
                    scenario=args.scenario,
                )
                if args.run
                else build_experiment_plan(
                    max_context_tokens=args.max_context_tokens,
                    scenario=args.scenario,
                ).report
            )
        except (ClaudeExperimentError, ValueError) as error:
            raise SystemExit(str(error)) from error
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif result["status"] == "planned":
            full = result["variants"]["full_replay"]
            bounded = result["variants"]["lians_bounded"]
            print("Claude comparison is ready; no Claude request was sent.")
            print(f"- Full replay: ~{full['prompt_token_estimate']} prompt tokens")
            print(f"- Lians context: ~{bounded['prompt_token_estimate']} prompt tokens")
            print(f"- Planned reduction: {result['planned_prompt_reduction_percent']}%")
            print(result["next_step"])
        else:
            comparison = result["comparison"]
            print("Claude comparison complete.")
            print(
                "- Both answers correct: "
                f"{'yes' if comparison['both_variants_answered_correctly'] else 'no'}"
            )
            print(
                "- Provider-reported input-token reduction: "
                f"{comparison['provider_reported_input_token_reduction_percent']}%"
            )
            print(f"- 50% evidence gate: {'passed' if result['evidence_gate']['met'] else 'not met'}")
            if args.output is not None:
                print(f"- Report: {args.output}")
            print(result["next_step"])
        return
    if args.command == "backup":
        if args.backup_action == "export":
            result = export_backup(
                MemoryStore(args.data or default_data_path()),
                args.output,
                _read_backup_passphrase(
                    confirm=True,
                    passphrase_file=args.passphrase_file,
                ),
                overwrite=args.overwrite,
            )
        elif args.backup_action == "verify":
            result = verify_backup(
                args.input,
                _read_backup_passphrase(
                    confirm=False,
                    passphrase_file=args.passphrase_file,
                ),
            )
        else:
            if not args.yes:
                raise SystemExit(
                    "Review the backup, then rerun import with --yes. Existing IDs are never overwritten."
                )
            result = import_backup(
                MemoryStore(args.data or default_data_path()),
                args.input,
                _read_backup_passphrase(
                    confirm=False,
                    passphrase_file=args.passphrase_file,
                ),
            )
        _show(result, as_json=args.json)
        return
    if args.command in {"optimize", "install", "uninstall"}:
        keys = _keys(args.clients)
        if not keys:
            raise SystemExit("No supported AI clients were detected. Use --clients to choose one.")
        if args.plan:
            action = "install" if args.command == "optimize" else args.command
            _show(plan(keys, action=action), as_json=args.json)
            return
        if not args.yes:
            raise SystemExit("Review the selected clients, then rerun with --yes (or use the GUI).")
        operation = install if args.command in {"optimize", "install"} else uninstall
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
