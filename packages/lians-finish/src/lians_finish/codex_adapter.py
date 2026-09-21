"""Opt-in Codex CLI adapter for Lians Finish."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .policy import StagePlan
from .runtime import RunResult


def _usage_from_events(stdout: str) -> dict[str, int]:
    """Return the largest observed token counters without double-counting event snapshots."""

    observed: dict[str, int] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = key.casefold()
                if normalized in {
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                } and isinstance(child, int):
                    observed[normalized] = max(observed.get(normalized, 0), child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for line in stdout.splitlines():
        try:
            visit(json.loads(line))
        except json.JSONDecodeError:
            continue
    return observed


class CodexRunner:
    """Run a planned stage through the locally authenticated Codex CLI."""

    def __init__(self, executable: str = "codex", timeout_seconds: int = 1_800) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def run(self, stage: StagePlan, prompt: str, repo: Path) -> RunResult:
        if not stage.model or not stage.reasoning_effort or not stage.sandbox:
            raise ValueError(f"stage {stage.id!r} is not a model stage")
        scratch = repo / ".lians" / "tmp"
        scratch.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment["TEMP"] = str(scratch)
        environment["TMP"] = str(scratch)
        with tempfile.TemporaryDirectory(prefix="lians-finish-") as directory:
            message_path = Path(directory) / "last-message.txt"
            command = [
                self.executable,
                "exec",
                "--json",
                "--color",
                "never",
                "--model",
                stage.model,
                "--config",
                f'model_reasoning_effort="{stage.reasoning_effort}"',
                "--sandbox",
                stage.sandbox,
                "--cd",
                str(repo),
                "--output-last-message",
                str(message_path),
            ]
            command.append(prompt)
            completed = subprocess.run(
                command,
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
            message = (
                message_path.read_text(encoding="utf-8", errors="replace")
                if message_path.exists()
                else ""
            )
            return RunResult(
                stage_id=stage.id,
                model=stage.model,
                reasoning_effort=stage.reasoning_effort,
                premium=stage.premium,
                success=completed.returncode == 0,
                message=message.strip(),
                usage=_usage_from_events(completed.stdout),
                returncode=completed.returncode,
                stderr=completed.stderr[-4_000:],
            )
