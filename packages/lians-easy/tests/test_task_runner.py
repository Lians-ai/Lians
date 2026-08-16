from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from lians_easy.task_runner import run_bounded_task


def brief() -> dict[str, object]:
    return {
        "schema": "https://lians.ai/schemas/work-brief/v0.1",
        "kind": "research",
        "summary": {"records_received": 100, "topic_counts": {"context": 60}},
        "representative_evidence": [
            {"source_id": "post-1", "text": "Long sessions repeat context."}
        ],
        "guardrails": {"raw_records_stay_local": True},
        "receipt": {
            "raw_record_count": 100,
            "raw_token_estimate": 5000,
            "brief_token_estimate": 500,
        },
    }


@pytest.mark.parametrize("provider", ["claude", "codex", "cursor"])
def test_bounded_task_uses_read_only_one_turn_provider_commands(provider: str) -> None:
    seen: dict[str, object] = {}

    def preflight(selected: str) -> dict[str, str]:
        assert selected == provider
        return {"executable": f"{provider}.exe"}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        prompt = kwargs.get("input")
        if provider == "cursor":
            assert prompt is None
            prompt = Path(str(kwargs["cwd"]), "PROMPT.txt").read_text(encoding="utf-8")
            stdout = json.dumps(
                {
                    "result": "Use the context theme.",
                    "usage": {"inputTokens": 420, "outputTokens": 20},
                }
            )
        elif provider == "codex":
            stdout = "\n".join(
                [
                    json.dumps(
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": "Use the context theme."},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "turn.completed",
                            "usage": {"input_tokens": 420, "output_tokens": 20},
                        }
                    ),
                ]
            )
        else:
            stdout = json.dumps(
                {
                    "result": "Use the context theme.",
                    "usage": {"input_tokens": 420, "output_tokens": 20},
                }
            )
        assert isinstance(prompt, str)
        assert "Summarize the strongest theme" in prompt
        assert "Long sessions repeat context" in prompt
        assert "Do not use tools" in prompt
        assert "raw_record_count" in prompt
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    result = run_bounded_task(
        provider,
        brief(),
        "Summarize the strongest theme",
        preflight=preflight,
        run_command=run,
    )

    command = seen["command"]
    assert isinstance(command, list)
    assert result["answer"] == "Use the context theme."
    assert result["usage"]["provider_reported_total_input_tokens"] == 420
    if provider == "claude":
        assert "--tools" in command
        assert "--no-session-persistence" in command
        assert "--max-turns" in command
    elif provider == "codex":
        assert "--sandbox" in command
        assert "read-only" in command
        assert "--ephemeral" in command
    else:
        assert "--mode" in command
        assert "ask" in command


def test_bounded_task_refuses_credentials_in_the_user_task() -> None:
    with pytest.raises(ValueError, match="credential-like"):
        run_bounded_task(
            "claude",
            brief(),
            "Use Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
            preflight=lambda provider: {"executable": "claude"},
        )
