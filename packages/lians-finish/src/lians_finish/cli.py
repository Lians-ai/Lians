"""CLI for the Lians Finish v0 product slice."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .codex_app_server import make_codex_runner
from .policy import (
    MODE_PREMIUM_BUDGETS,
    MODES,
    RouteError,
    build_route,
    load_policy_catalog,
)
from .runtime import (
    FinishRuntime,
    SubprocessVerifier,
    parse_verifier_command,
    planned_receipt,
    verify_receipt,
    write_artifacts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lians-finish",
        description="Assign each coding stage to the least expensive capable Codex model.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-receipt", help="Check a receipt hash offline")
    verify.add_argument("path", metavar="PATH", help="Receipt JSON file to read")
    subparsers.add_parser(
        "modes",
        help="List modes and premium budgets as JSON (no arguments)",
        add_help=False,
    )
    run = subparsers.add_parser("run", help="Plan or execute a usage-aware coding task")
    run.add_argument("task", help="Outcome the agent must complete")
    run.add_argument("--mode", choices=sorted(MODES), default="protect")
    run.add_argument(
        "--premium-budget",
        type=int,
        default=None,
        help="Cap premium attempts from 0 to the mode limit (protect=1, balanced=2, maximum=4)",
    )
    run.add_argument("--repo", default=".", help="Repository working directory")
    run.add_argument(
        "--policy-file",
        metavar="FILE",
        help="JSON overrides for Protect implementation model and reasoning effort",
    )
    run.add_argument("--out", default=".lians/latest", help="Receipt directory")
    run.add_argument("--execute", action="store_true", help="Actually invoke Codex models")
    run.add_argument("--verify", help="Required verification command when --execute is used")
    run.add_argument(
        "--allow-verifier",
        metavar="PATH",
        action="append",
        default=[],
        help="Allow an absolute verifier executable by resolved path (repeatable)",
    )
    run.add_argument("--codex-bin", default="codex", help="Codex CLI executable")
    run.add_argument(
        "--codex-bridge",
        choices=("auto", "app-server", "exec"),
        default="auto",
        help="Use one persistent app-server thread when available, or one-shot exec",
    )
    run.add_argument("--started-at", help="Fixed timestamp for deterministic tests")
    run.add_argument(
        "--json",
        action="store_true",
        help="Print the final receipt as JSON instead of the human summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "verify-receipt":
        try:
            valid, digest = verify_receipt(Path(args.path))
        except (OSError, TypeError, ValueError, RecursionError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"VALID {digest}" if valid else "TAMPERED")
        return 0 if valid else 2
    if args.command == "modes":
        payload = {
            "modes": [
                {"name": name, "premium_budget": MODE_PREMIUM_BUDGETS[name]}
                for name in sorted(MODE_PREMIUM_BUDGETS)
            ]
        }
        print(json.dumps(payload, separators=(",", ":")))
        return 0
    try:
        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise RouteError(f"repository does not exist: {repo}")
        catalog = load_policy_catalog(args.policy_file) if args.policy_file else None
        route = build_route(
            args.task,
            mode=args.mode,
            premium_budget=args.premium_budget,
            policy_catalog=catalog,
        )
        output = Path(args.out).resolve()
        if not args.execute:
            receipt = planned_receipt(route, created_at=args.started_at)
            write_artifacts(output, route, receipt)
            if args.json:
                print(json.dumps(receipt, indent=2, ensure_ascii=False))
                return 0
            print(
                f"PLANNED risk={route.risk} mode={route.mode} "
                f"premium_budget={route.max_premium_calls} out={output}"
            )
            return 0
        if not args.verify:
            raise RouteError(
                "--verify is required with --execute; Lians will not claim done unchecked"
            )
        verify_command = parse_verifier_command(args.verify)
        verifier = SubprocessVerifier(verify_command, allowed_paths=args.allow_verifier)
        runner = make_codex_runner(args.codex_bin, args.codex_bridge)
        try:
            runtime = FinishRuntime(runner)
            receipt = runtime.execute(
                route,
                repo,
                verifier,
                started_at=args.started_at,
                allowed_paths=args.allow_verifier,
            )
        finally:
            close = getattr(runner, "close", None)
            if callable(close):
                close()
        write_artifacts(output, route, receipt)
        if args.json:
            print(json.dumps(receipt, indent=2, ensure_ascii=False))
            return 0 if receipt["status"] == "PASS" else 2
        print(
            f"{receipt['status']} model_calls={receipt['model_calls']} "
            f"premium_calls={receipt['premium_calls']} "
            f"premium_budget={route.max_premium_calls} out={output}"
        )
        return 0 if receipt["status"] == "PASS" else 2
    except (OSError, RouteError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
