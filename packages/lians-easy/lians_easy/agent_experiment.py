"""Matched Lians context experiments for Claude, Codex, and Cursor."""

from __future__ import annotations

import copy
import json
import os
import shutil
import statistics
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .claude_experiment import (
    ClaudeExperimentError,
    CommandRunner,
    _json_object,
    _score,
    build_experiment_plan,
    claude_preflight,
    run_claude_experiment,
)

PROVIDERS = ("claude", "codex", "cursor")
PROVIDER_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
    "cursor": "Cursor",
}
REPORT_SCHEMA = "https://lians.ai/schemas/ai-context-experiment/v0.2"
CLAIM_BOUNDARY = (
    "This compares CLI-reported input usage for isolated calls on one synthetic workload. "
    "It does not prove that Lians enlarges a context window, extends a subscription quota, "
    "or produces the same savings on every task."
)
_CODEX_API_ENVIRONMENT_VARIABLES = ("OPENAI_API_KEY",)
_CURSOR_API_ENVIRONMENT_VARIABLES = ("CURSOR_API_KEY",)


class AgentExperimentError(ClaudeExperimentError):
    """Raised when a provider comparison cannot safely satisfy its contract."""


def _provider_key(provider: str) -> str:
    key = provider.strip().lower()
    if key not in PROVIDERS:
        raise ValueError("provider must be claude, codex, or cursor")
    return key


def _run_status(
    command: list[str],
    *,
    provider_name: str,
    run_command: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    try:
        return run_command(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AgentExperimentError(
            f"{provider_name} CLI could not be opened. Install the standalone CLI and try again."
        ) from error


def codex_preflight(
    *,
    environment: Mapping[str, str] | None = None,
    executable: str | None = None,
    run_command: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Require ChatGPT sign-in and refuse API-key billing before a Codex call."""
    active_environment = os.environ if environment is None else environment
    conflicting = [
        name for name in _CODEX_API_ENVIRONMENT_VARIABLES if active_environment.get(name)
    ]
    if conflicting:
        raise AgentExperimentError(
            "Live test stopped: OPENAI_API_KEY is set, so Codex may use API billing. "
            "Remove it from this shell and run `codex login` with the intended ChatGPT account."
        )

    resolved = executable or shutil.which("codex")
    if not resolved:
        raise AgentExperimentError(
            "Codex CLI was not found on PATH. Install the standalone Codex CLI, then run "
            "`codex login`."
        )
    completed = _run_status(
        [resolved, "login", "status"],
        provider_name="Codex",
        run_command=run_command,
    )
    status = f"{completed.stdout}\n{completed.stderr}".strip().lower()
    if completed.returncode != 0 or "logged in" not in status:
        raise AgentExperimentError(
            "Codex CLI is not signed in. Run `codex login` and choose ChatGPT sign-in."
        )
    if "api key" in status or "api_key" in status:
        raise AgentExperimentError(
            "Live test stopped: Codex CLI is authenticated by API key, not ChatGPT. "
            "Run `codex logout`, then `codex login` and choose ChatGPT sign-in."
        )
    if "chatgpt" not in status:
        raise AgentExperimentError(
            "Live test stopped: Codex authentication could not be verified as ChatGPT sign-in. "
            "Check `codex login status` before retrying."
        )
    return {
        "logged_in": True,
        "auth_method": "chatgpt",
        "provider": "openai",
        "executable": resolved,
    }


def cursor_preflight(
    *,
    environment: Mapping[str, str] | None = None,
    executable: str | None = None,
    run_command: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Require Cursor account login and refuse API-key billing before a call."""
    active_environment = os.environ if environment is None else environment
    if any(active_environment.get(name) for name in _CURSOR_API_ENVIRONMENT_VARIABLES):
        raise AgentExperimentError(
            "Live test stopped: CURSOR_API_KEY is set, so Cursor may use API billing. "
            "Remove it from this shell and run `cursor-agent login` with the intended account."
        )

    resolved = executable or shutil.which("cursor-agent")
    if not resolved:
        raise AgentExperimentError(
            "Cursor Agent was not found on PATH. Install Cursor CLI, then run "
            "`cursor-agent login`."
        )
    completed = _run_status(
        [resolved, "status"],
        provider_name="Cursor",
        run_command=run_command,
    )
    status = f"{completed.stdout}\n{completed.stderr}".strip().lower()
    if completed.returncode != 0 or "logged in" not in status or "not logged in" in status:
        raise AgentExperimentError(
            "Cursor Agent is not signed in. Run `cursor-agent login` and finish browser sign-in."
        )
    return {
        "logged_in": True,
        "auth_method": "account",
        "provider": "cursor",
        "executable": resolved,
    }


def provider_preflight(
    provider: str,
    *,
    environment: Mapping[str, str] | None = None,
    executable: str | None = None,
    run_command: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Check one provider without returning account identifiers or credentials."""
    key = _provider_key(provider)
    if key == "claude":
        try:
            return claude_preflight(
                environment=environment,
                executable=executable,
                run_command=run_command,
            )
        except ClaudeExperimentError as error:
            raise AgentExperimentError(str(error)) from error
    if key == "codex":
        return codex_preflight(
            environment=environment,
            executable=executable,
            run_command=run_command,
        )
    return cursor_preflight(
        environment=environment,
        executable=executable,
        run_command=run_command,
    )


def _usage_number(usage: Mapping[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return 0


def _codex_payload(value: str) -> tuple[str, dict[str, int]]:
    answer = ""
    raw_usage: Mapping[str, Any] = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise AgentExperimentError("Codex returned invalid JSONL output") from error
        if not isinstance(event, Mapping):
            continue
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") in {
                "agent_message",
                "message",
            }:
                text = item.get("text", item.get("content"))
                if isinstance(text, str):
                    answer = text
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), Mapping
        ):
            raw_usage = event["usage"]
    if not answer:
        raise AgentExperimentError("Codex result did not contain a text answer")
    input_tokens = _usage_number(raw_usage, "input_tokens", "inputTokens")
    if input_tokens <= 0:
        raise AgentExperimentError("Codex result did not contain input-token usage")
    return answer, {
        "input_tokens": input_tokens,
        "cached_input_tokens": _usage_number(
            raw_usage, "cached_input_tokens", "cachedInputTokens"
        ),
        "provider_reported_total_input_tokens": input_tokens,
        "output_tokens": _usage_number(raw_usage, "output_tokens", "outputTokens"),
    }


def _cursor_payload(value: str) -> tuple[str, dict[str, int]]:
    try:
        payload = _json_object(value)
    except ClaudeExperimentError as error:
        raise AgentExperimentError("Cursor returned invalid JSON output") from error
    answer = payload.get("result")
    if not isinstance(answer, str):
        raise AgentExperimentError("Cursor result did not contain a text answer")
    raw_usage = payload.get("usage")
    usage = raw_usage if isinstance(raw_usage, Mapping) else {}
    input_tokens = _usage_number(usage, "inputTokens", "input_tokens")
    if input_tokens <= 0:
        raise AgentExperimentError(
            "This Cursor CLI version did not report input-token usage. Update Cursor CLI and retry."
        )
    return answer, {
        "input_tokens": input_tokens,
        "cache_read_tokens": _usage_number(usage, "cacheReadTokens", "cache_read_tokens"),
        "cache_write_tokens": _usage_number(
            usage, "cacheWriteTokens", "cache_write_tokens"
        ),
        "provider_reported_total_input_tokens": input_tokens,
        "output_tokens": _usage_number(usage, "outputTokens", "output_tokens"),
    }


def _run_codex_prompt(
    prompt: str,
    *,
    executable: str,
    working_directory: str,
    expected: Mapping[str, Any],
    run_command: CommandRunner,
) -> dict[str, Any]:
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "-",
    ]
    started = time.perf_counter()
    try:
        completed = run_command(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            cwd=working_directory,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AgentExperimentError("Codex comparison call could not start") from error
    if completed.returncode != 0:
        raise AgentExperimentError(
            f"Codex comparison call failed with exit code {completed.returncode}; no result saved"
        )
    answer, usage = _codex_payload(completed.stdout)
    return {
        "answer": answer,
        "quality": _score(answer, expected=expected),
        "usage": usage,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def _run_cursor_prompt(
    prompt: str,
    *,
    executable: str,
    working_directory: str,
    expected: Mapping[str, Any],
    run_command: CommandRunner,
) -> dict[str, Any]:
    prompt_path = Path(working_directory, "PROMPT.txt")
    prompt_path.write_text(prompt, encoding="utf-8")
    instruction = (
        "Read PROMPT.txt in this isolated workspace and treat it as the entire task. "
        "Do not access any other file, run commands, or modify anything. Return only the "
        "answer requested by that file."
    )
    command = [
        executable,
        "-p",
        "--mode",
        "ask",
        "--output-format",
        "json",
        "--trust",
        "--workspace",
        working_directory,
        instruction,
    ]
    started = time.perf_counter()
    try:
        completed = run_command(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            cwd=working_directory,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise AgentExperimentError("Cursor comparison call could not start") from error
    if completed.returncode != 0:
        raise AgentExperimentError(
            f"Cursor comparison call failed with exit code {completed.returncode}; no result saved"
        )
    answer, usage = _cursor_payload(completed.stdout)
    return {
        "answer": answer,
        "quality": _score(answer, expected=expected),
        "usage": usage,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def _aggregate(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    totals = [
        int(run["usage"]["provider_reported_total_input_tokens"])  # type: ignore[index]
        for run in runs
    ]
    outputs = [int(run["usage"]["output_tokens"]) for run in runs]  # type: ignore[index]
    return {
        "runs": list(runs),
        "all_answers_correct": all(
            bool(run["quality"]["passed"]) for run in runs  # type: ignore[index]
        ),
        "average_provider_reported_total_input_tokens": round(statistics.mean(totals), 1),
        "average_output_tokens": round(statistics.mean(outputs), 1),
    }


def _provider_report(provider: str, *, scenario: str) -> tuple[Any, dict[str, Any]]:
    plan = build_experiment_plan(scenario=scenario)
    report = copy.deepcopy(plan.report)
    report.update(
        {
            "schema": REPORT_SCHEMA,
            "experiment": f"{provider}-{scenario}-full-replay-vs-lians-bounded-context",
            "lane": f"{provider}-cli-non-interactive",
            "provider": provider,
            "measurement": {
                "source": "provider_cli",
                "metric": "input_tokens",
                "label": f"{PROVIDER_NAMES[provider]} CLI reported input tokens",
            },
            "claim_boundary": CLAIM_BOUNDARY,
        }
    )
    report["execution_isolation"] = {
        "temporary_working_directory": True,
        "read_only": True,
        "session_persistence": False if provider == "codex" else None,
        "provider_specific": (
            "Codex ignores user config and rules and runs with a read-only sandbox."
            if provider == "codex"
            else "Cursor runs in read-only ask mode and reads one synthetic prompt file."
        ),
    }
    return plan, report


def _run_non_claude_experiment(
    provider: str,
    *,
    repetitions: int,
    scenario: str,
    environment: Mapping[str, str] | None,
    executable: str | None,
    run_command: CommandRunner,
) -> dict[str, Any]:
    if not 1 <= repetitions <= 5:
        raise ValueError("repetitions must be between 1 and 5")
    auth = provider_preflight(
        provider,
        environment=environment,
        executable=executable,
        run_command=run_command,
    )
    plan, report = _provider_report(provider, scenario=scenario)
    expected = report["fixture"]["expected_answer"]
    prompts = {"full_replay": plan.full_prompt, "lians_bounded": plan.lians_prompt}
    results: dict[str, list[dict[str, Any]]] = {"full_replay": [], "lians_bounded": []}
    with TemporaryDirectory(prefix=f"lians-{provider}-calls-") as root:
        for repetition in range(repetitions):
            order = (
                ("full_replay", "lians_bounded")
                if repetition % 2 == 0
                else ("lians_bounded", "full_replay")
            )
            for variant in order:
                working_directory = Path(root, f"{repetition + 1}-{variant}")
                working_directory.mkdir()
                if provider == "codex":
                    run = _run_codex_prompt(
                        prompts[variant],
                        executable=str(auth["executable"]),
                        working_directory=str(working_directory),
                        expected=expected,
                        run_command=run_command,
                    )
                else:
                    run = _run_cursor_prompt(
                        prompts[variant],
                        executable=str(auth["executable"]),
                        working_directory=str(working_directory),
                        expected=expected,
                        run_command=run_command,
                    )
                run["repetition"] = repetition + 1
                results[variant].append(run)

    full = _aggregate(results["full_replay"])
    bounded = _aggregate(results["lians_bounded"])
    full_tokens = float(full["average_provider_reported_total_input_tokens"])
    bounded_tokens = float(bounded["average_provider_reported_total_input_tokens"])
    delta = full_tokens - bounded_tokens
    reduction = round((delta / full_tokens) * 100, 1) if full_tokens else None
    both_correct = full["all_answers_correct"] and bounded["all_answers_correct"]
    gate_met = bool(both_correct and reduction is not None and reduction >= 50.0)
    return {
        **report,
        "status": "completed",
        "repetitions": repetitions,
        "auth": {
            "logged_in": auth["logged_in"],
            "auth_method": auth["auth_method"],
            "provider": auth["provider"],
        },
        "results": {"full_replay": full, "lians_bounded": bounded},
        "comparison": {
            "both_variants_answered_correctly": both_correct,
            "average_provider_reported_input_token_delta": round(delta, 1),
            "provider_reported_input_token_reduction_percent": reduction,
        },
        "evidence_gate": {**report["evidence_gate"], "met": gate_met},
        "next_step": (
            "The 50% synthetic evidence gate passed. Validate with consenting real users before "
            "making a broader product claim."
            if gate_met
            else "The 50% evidence gate did not pass; inspect quality and CLI usage before "
            "changing the product claim."
        ),
    }


def run_provider_experiment(
    provider: str,
    *,
    repetitions: int = 1,
    scenario: str = "market-research",
    environment: Mapping[str, str] | None = None,
    executable: str | None = None,
    run_command: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Run the matched comparison with the selected subscription-backed CLI."""
    key = _provider_key(provider)
    if key == "claude":
        try:
            report = run_claude_experiment(
                repetitions=repetitions,
                scenario=scenario,
                environment=environment,
                executable=executable,
                run_command=run_command,
            )
        except ClaudeExperimentError as error:
            raise AgentExperimentError(str(error)) from error
        report["provider"] = "claude"
        report["measurement"] = {
            "source": "provider_cli",
            "metric": "input_tokens",
            "label": "Claude CLI reported input tokens",
        }
        return report
    return _run_non_claude_experiment(
        key,
        repetitions=repetitions,
        scenario=scenario,
        environment=environment,
        executable=executable,
        run_command=run_command,
    )
