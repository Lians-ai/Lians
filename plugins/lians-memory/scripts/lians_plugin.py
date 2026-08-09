#!/usr/bin/env python3
"""Stable cross-platform entrypoint for the Lians Memory Codex plugin."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Sequence

# Installed plugin snapshots should remain immutable after setup/doctor runs.
# Set this before importing the adjacent bootstrap module so Python does not
# create __pycache__ inside Codex's plugin cache or a shared partner checkout.
sys.dont_write_bytecode = True

# Safe-path mode deliberately omits the script directory from ``sys.path``.
# Add only this resolved, plugin-owned directory while importing the adjacent
# standard-library bootstrap, then remove it immediately.
_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
sys.path.insert(0, _SCRIPT_DIRECTORY)
try:
    from bootstrap import (  # noqa: E402
        DEFAULT_MANAGED_URL,
        BootstrapError,
        configure_runtime_environment,
        doctor,
        read_profile,
        resolve_data_home,
        runtime_python,
        setup,
        verify_profile_matches_bundle,
    )
finally:
    if sys.path and sys.path[0] == _SCRIPT_DIRECTORY:
        del sys.path[0]


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_RUNTIME = PLUGIN_ROOT / "runtime" / "user_prompt_submit_recall.py"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lians_plugin.py",
        description="Set up and run the portable Lians Memory Codex plugin.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    setup_parser = commands.add_parser("setup", help="create the isolated frozen runtime")
    setup_parser.add_argument("--mode", choices=("local", "managed"), required=True)
    setup_parser.add_argument("--managed-url")
    model = setup_parser.add_mutually_exclusive_group()
    model.add_argument(
        "--download-bge",
        action="store_true",
        help="download and hash-verify the pinned 1.34 GB BGE ONNX model",
    )
    model.add_argument(
        "--bge-source",
        type=Path,
        help="directory containing the pinned model.onnx and tokenizer.json",
    )
    setup_parser.add_argument("--json", action="store_true")

    doctor_parser = commands.add_parser("doctor", help="validate setup without reading memories")
    doctor_parser.add_argument("--json", action="store_true")

    commands.add_parser("mcp", help="run the compact Lians MCP server")
    commands.add_parser("hook", help="run the model-free UserPromptSubmit recall hook")
    commands.add_parser("prewarm", help="quietly prewarm the local recall daemon")
    daemon = commands.add_parser("daemon", help="operate the local recall daemon")
    daemon.add_argument("action", choices=("health", "start", "stop"))
    return parser


def _print_result(result: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    status = "ready" if result.get("ok") else "not ready"
    print(f"Lians Memory: {status}")
    if result.get("mode"):
        print(f"Mode: {result['mode']}")
    if result.get("data_home"):
        print(f"Data: {result['data_home']}")
    for message in result.get("messages", []):
        print(f"Warning: {message}")


def _setup(args: argparse.Namespace) -> int:
    if args.mode == "managed" and (args.download_bge or args.bge_source):
        raise BootstrapError("managed mode does not use a local embedding model")
    if args.mode == "managed" and not args.managed_url:
        raise BootstrapError(
            "managed mode requires --managed-url for a deployed HTTPS Lians service"
        )
    if args.mode == "local" and not (args.download_bge or args.bge_source):
        raise BootstrapError(
            "local mode needs --download-bge or --bge-source; the plugin ships no model binary"
        )
    data_home = resolve_data_home()
    result = setup(
        mode=args.mode,
        data_home=data_home,
        managed_url=args.managed_url or DEFAULT_MANAGED_URL,
        bge_source=args.bge_source,
        download_bge=args.download_bge,
        plugin_root=PLUGIN_ROOT,
    )
    _print_result(result, as_json=args.json)
    if args.mode == "managed" and not os.environ.get("LIANS_API_KEY", "").strip():
        print(
            "Managed setup is complete. Export LIANS_API_KEY before doctor or use; "
            "the key was not stored.",
            file=sys.stderr,
        )
    return 0


def _doctor(args: argparse.Namespace) -> int:
    data_home = resolve_data_home()
    result = doctor(data_home, os.environ, plugin_root=PLUGIN_ROOT)
    _print_result(result, as_json=args.json)
    return 0 if result["ok"] else 1


def _run_hook_runtime(
    command: str,
    hook_arg: str | None,
    child_env: dict[str, str],
) -> int:
    """Run the bundled model-free hook without a second Python process."""

    module_name = "lians_memory_plugin_hook_runtime"
    prior_module = sys.modules.get(module_name)
    prior_environ = dict(os.environ)
    prior_argv = list(sys.argv)
    try:
        os.environ.clear()
        os.environ.update(child_env)
        sys.argv = [str(HOOK_RUNTIME)]
        spec = importlib.util.spec_from_file_location(module_name, HOOK_RUNTIME)
        if spec is None or spec.loader is None:
            raise BootstrapError("bundled hook runtime cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        if command == "hook":
            return int(module.main())
        if hook_arg is None:
            raise BootstrapError("hook lifecycle command is missing")
        return int(module._daemon_command(hook_arg))
    finally:
        os.environ.clear()
        os.environ.update(prior_environ)
        sys.argv = prior_argv
        if prior_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = prior_module


def _runtime_command(command: str, daemon_action: str | None = None) -> int:
    data_home = resolve_data_home()
    project_root = Path.cwd().resolve()
    hook_like = command in {"hook", "prewarm", "daemon"}
    try:
        profile = read_profile(data_home)
        verify_profile_matches_bundle(profile, plugin_root=PLUGIN_ROOT)
        python = runtime_python(data_home)
        if not python.is_file():
            raise BootstrapError("the frozen plugin runtime is missing; run setup")
        require_key = command in {"mcp", "hook"}
        child_env = configure_runtime_environment(
            data_home,
            profile,
            os.environ,
            project_root=project_root,
            require_managed_key=require_key,
            repair_private_paths=command != "hook",
        )
        child_env["LIANS_MEMORY_HOME"] = str(data_home)
        os.chdir(child_env["LIANS_PLUGIN_RUNTIME_CWD"])
        if command == "mcp":
            argv = [str(python), "-m", "lians.mcp_server"]
            os.execve(str(python), argv, child_env)
        else:
            if not HOOK_RUNTIME.is_file():
                raise BootstrapError(f"bundled hook runtime is missing: {HOOK_RUNTIME}")
            hook_arg: str | None = None
            if command == "prewarm":
                hook_arg = "--prewarm-quiet"
            elif command == "daemon":
                hook_arg = {
                    "health": "--health",
                    "start": "--prewarm",
                    "stop": "--stop",
                }[str(daemon_action)]
            return _run_hook_runtime(command, hook_arg, child_env)
    except BootstrapError as exc:
        # Hooks are additive.  A new or temporarily broken plugin must never
        # block the user's prompt; MCP is optional until doctor passes.
        if hook_like:
            return 0
        print(f"Lians Memory is unavailable: {exc}", file=sys.stderr)
        return 2
    except Exception:
        if hook_like:
            return 0
        print("Lians Memory is unavailable: unexpected launcher failure", file=sys.stderr)
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "setup":
            return _setup(args)
        if args.command == "doctor":
            return _doctor(args)
        if args.command == "daemon":
            return _runtime_command("daemon", args.action)
        return _runtime_command(args.command)
    except BootstrapError as exc:
        print(f"Lians Memory: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Lians Memory: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
