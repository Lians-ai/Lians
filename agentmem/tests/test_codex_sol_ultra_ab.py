"""Mocked contracts for the paid-call-free Codex Sol Ultra A/B harness."""

from __future__ import annotations

import json
import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.codex_sol_ultra_ab import (  # noqa: E402
    ABBA_ORDER,
    MAX_CONTEXT_TOKENS,
    MODEL,
    REASONING_EFFORT,
    SERVICE_TIER,
    SOL_CREDIT_RATES,
    TOP_K,
    BenchmarkConfig,
    BenchmarkError,
    Invocation,
    RunSpec,
    _make_spec,
    _parse_events,
    _usage,
    estimate_sol_credits,
    estimate_sol_credits_all_input_uncached,
    run_benchmark,
)


def _inputs(tmp_path: Path) -> BenchmarkConfig:
    codex = tmp_path / "codex.exe"
    python = tmp_path / "python.exe"
    database = tmp_path / "locomo.sqlite"
    agents = tmp_path / "source-AGENTS.md"
    for path, content in (
        (codex, b"codex"),
        (python, b"python"),
        (database, b"sqlite fixture"),
        (agents, b"# Lians\nRecall before answering.\n"),
    ):
        path.write_bytes(content)
    dataset = tmp_path / "locomo10.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "conversation": {
                        "session_1": [
                            {
                                "speaker": "Caroline",
                                "text": "I went to the support group yesterday.",
                            }
                        ],
                        "session_1_date_time": "8 May 2023",
                    }
                }
            ]
        ),
        encoding="utf-8",
    )
    question = tmp_path / "conv0_q0.json"
    question.write_text(
        json.dumps(
            {
                "question_id": "conv0_q0",
                "conversation_idx": 0,
                "question": "When did Caroline go to the support group?",
                "ground_truth_answer": "7 May 2023",
            }
        ),
        encoding="utf-8",
    )
    return BenchmarkConfig(
        codex_exe=codex,
        mcp_python=python,
        source_db=database,
        question_file=question,
        dataset_file=dataset,
        agents_file=agents,
        raw_dir=tmp_path / "raw",
    )


def _event_bytes(
    *,
    answer: str = "7 May 2023",
    input_tokens: int,
    cached_tokens: int,
    cache_write_tokens: int = 0,
    output_tokens: int = 10,
    tool: bool,
    delegation: bool = False,
    aggregate: bool = False,
) -> bytes:
    events: list[dict] = [
        {"type": "thread.started", "thread_id": "thread-test"},
        {"type": "turn.started"},
    ]
    if delegation:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "id": "delegate-1",
                    "type": "collaboration_tool_call",
                    "tool": "spawn_agent",
                    "status": "completed",
                },
            }
        )
    if tool:
        events.append(
            {
                "type": "item.completed",
                "item": {
                    "id": "recall-1",
                    "type": "mcp_tool_call",
                    "server": "lians",
                    "tool": "recall",
                    "arguments": {
                        "query": "When did Caroline go to the support group?",
                    },
                    "status": "completed",
                    "result": {"content": [{"type": "text", "text": "fixture"}]},
                },
            }
        )
    events.append(
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        }
    )
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "cache_write_input_tokens": cache_write_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": max(0, output_tokens - 2),
    }
    if aggregate:
        usage["includes_subagent_usage"] = True
        usage["aggregate_usage_complete"] = True
    events.append({"type": "turn.completed", "usage": usage})
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def test_command_profiles_pin_sol_ultra_and_the_compact_two_tool_candidate(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path)
    baseline_dir = tmp_path / "baseline"
    candidate_dir = tmp_path / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    copied_db = candidate_dir / "copy.sqlite"
    copied_db.write_bytes(b"db")

    baseline = _make_spec(
        config,
        sequence=1,
        mode="baseline",
        repetition=1,
        cwd=baseline_dir,
        prompt="baseline",
        database_path=None,
    )
    candidate = _make_spec(
        config,
        sequence=2,
        mode="candidate",
        repetition=1,
        cwd=candidate_dir,
        prompt="candidate",
        database_path=copied_db,
    )
    baseline_command = "\n".join(baseline.command)
    candidate_command = "\n".join(candidate.command)

    for command in (baseline_command, candidate_command):
        assert f"--model\n{MODEL}" in command
        assert "--ignore-user-config" in command
        assert f'model_reasoning_effort="{REASONING_EFFORT}"' in command
        assert f'service_tier="{SERVICE_TIER}"' in command
        assert "features.plugins=false" in command
        assert "features.apps=false" in command
    assert "mcp_servers.lians" not in baseline_command
    assert 'mcp_servers.lians.enabled_tools=["remember","recall"]' in candidate_command
    assert 'LIANS_MCP_ENABLED_TOOLS="remember,recall"' in candidate_command
    assert 'LIANS_MCP_SCHEMA_PROFILE="compact"' in candidate_command
    assert f'LIANS_MCP_RECALL_K="{TOP_K}"' in candidate_command
    assert f'LIANS_MCP_CONTEXT_MAX_TOKENS="{MAX_CONTEXT_TOKENS}"' in candidate_command
    assert str(config.mcp_python).replace("\\", "\\\\") in candidate_command


def test_usage_parsing_and_credit_estimate_prices_cache_writes_separately() -> None:
    raw = _event_bytes(
        input_tokens=1000,
        cached_tokens=200,
        cache_write_tokens=100,
        output_tokens=10,
        tool=False,
    )
    usage = _usage(_parse_events(raw, "fixture"), "fixture")

    assert usage["uncached_input_tokens"] == 700
    expected = (
        700 * SOL_CREDIT_RATES["uncached_input_credits_per_million"]
        + 200 * SOL_CREDIT_RATES["cached_input_credits_per_million"]
        + 100 * SOL_CREDIT_RATES["cache_write_input_credits_per_million"]
        + 10 * SOL_CREDIT_RATES["output_credits_per_million"]
    ) / 1_000_000
    assert estimate_sol_credits(usage) == pytest.approx(expected)
    assert estimate_sol_credits_all_input_uncached(usage) == pytest.approx(
        (1000 * 125 + 10 * 750) / 1_000_000
    )
    # reasoning_output_tokens is disclosure-only and is not charged twice.
    assert usage["reasoning_output_tokens"] == 8


def test_usage_rejects_input_components_larger_than_total() -> None:
    raw = _event_bytes(
        input_tokens=100,
        cached_tokens=80,
        cache_write_tokens=30,
        tool=False,
    )
    with pytest.raises(BenchmarkError, match="exceed total input"):
        _usage(_parse_events(raw, "fixture"), "fixture")


def test_mocked_abba_selects_second_repeats_and_preserves_raw_jsonl(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path)
    seen: list[tuple[str, int]] = []
    raw_by_label: dict[str, bytes] = {}

    def runner(spec: RunSpec) -> Invocation:
        seen.append((spec.mode, spec.repetition))
        if spec.mode == "baseline":
            assert not (spec.cwd / "AGENTS.md").exists()
            assert spec.database_path is None
            stdout = _event_bytes(
                input_tokens=20_000 + spec.repetition,
                cached_tokens=0,
                output_tokens=10,
                tool=False,
            )
        else:
            assert (spec.cwd / "AGENTS.md").is_file()
            assert spec.database_path is not None
            assert spec.database_path.is_file()
            assert spec.database_path.resolve() != config.source_db.resolve()
            assert "exactly once" in spec.prompt
            stdout = _event_bytes(
                input_tokens=10_000 + spec.repetition,
                cached_tokens=9_000,
                output_tokens=50,
                tool=True,
            )
        raw_by_label[spec.label] = stdout
        return Invocation(stdout=stdout, stderr=b"warning only\n", returncode=0, wall_time_ms=5)

    report = run_benchmark(config, dry_run=False, runner=runner)

    assert seen == list(ABBA_ORDER)
    assert report["selected"] == {
        "baseline_label": "04-baseline-A2",
        "candidate_label": "03-candidate-B2",
        "rule": "second exact repeat for each arm",
    }
    assert report["quality_gate"]["passed"] is True
    assert report["verdict"]["qualified_target_met"] is True
    assert report["verdict"]["every_repeat_target_met"] is True
    assert report["verdict"]["cache_neutral_sensitivity_target_met"] is True
    assert report["target"]["usage_extension_percent"] == 80
    assert report["profile"]["reasoning_effort"] == "ultra"
    for run in report["runs"]:
        artifact = Path(run["raw_stdout_artifact"])
        assert artifact.read_bytes() == raw_by_label[run["label"]]
        assert "LIANS_API_KEY" not in artifact.read_text(encoding="utf-8")
        assert run["events"]


def test_cache_luck_alone_cannot_qualify_target(tmp_path: Path) -> None:
    config = _inputs(tmp_path)

    def runner(spec: RunSpec) -> Invocation:
        baseline = spec.mode == "baseline"
        return Invocation(
            stdout=_event_bytes(
                input_tokens=20_000 if baseline else 19_000,
                cached_tokens=0 if baseline else 18_000,
                output_tokens=10,
                tool=not baseline,
            ),
            stderr=b"",
            returncode=0,
            wall_time_ms=1,
        )

    report = run_benchmark(config, dry_run=False, runner=runner)

    assert report["verdict"]["selected_repeat_target_met"] is True
    assert report["verdict"]["cache_neutral_sensitivity_target_met"] is False
    assert report["verdict"]["qualified_target_met"] is False


def test_hook_profile_requires_real_receipt_and_uses_no_model_facing_tool(
    tmp_path: Path,
) -> None:
    config = replace(_inputs(tmp_path), retrieval_path="hook")

    def runner(spec: RunSpec) -> Invocation:
        baseline = spec.mode == "baseline"
        if not baseline:
            assert spec.hook_receipt_path is not None
            command = "\n".join(spec.command)
            assert "hooks.UserPromptSubmit=" in command
            assert str(config.hook_script).replace("\\", "\\\\") in command
            spec.hook_receipt_path.parent.mkdir(parents=True, exist_ok=True)
            spec.hook_receipt_path.write_text(
                json.dumps(
                    {
                        "status": "injected",
                        "injected": True,
                        "prompt_sha256": hashlib.sha256(spec.prompt.encode("utf-8")).hexdigest(),
                        "context_sha256": "a" * 64,
                        "memory_count": 2,
                        "token_estimate": 200,
                        "top_score": 0.58,
                        "retrieval_degraded": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return Invocation(
            stdout=_event_bytes(
                input_tokens=20_000 if baseline else 10_000,
                cached_tokens=0,
                output_tokens=10,
                tool=False,
            ),
            stderr=b"",
            returncode=0,
            wall_time_ms=1,
        )

    report = run_benchmark(config, dry_run=False, runner=runner)

    candidates = [run for run in report["runs"] if run["mode"] == "candidate"]
    assert all(run["hook_receipt"]["status"] == "injected" for run in candidates)
    assert all(run["tool_calls"] == [] for run in candidates)
    assert report["verdict"]["qualified_target_met"] is True


def test_delegation_without_explicit_complete_aggregate_accounting_fails_closed(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path)

    def runner(spec: RunSpec) -> Invocation:
        return Invocation(
            stdout=_event_bytes(
                input_tokens=20_000 if spec.mode == "baseline" else 10_000,
                cached_tokens=0 if spec.mode == "baseline" else 9_000,
                tool=spec.mode == "candidate",
                delegation=spec.sequence == 1,
            ),
            stderr=b"",
            returncode=0,
            wall_time_ms=1,
        )

    report = run_benchmark(config, dry_run=False, runner=runner)

    first = report["runs"][0]
    assert first["delegation_evidence"]
    assert first["complete_aggregate_accounting"] is False
    assert any("without explicit complete" in item for item in first["violations"])
    assert report["verdict"]["qualified_target_met"] is False


def test_explicit_complete_thread_tree_accounting_allows_delegation_marker(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path)

    def runner(spec: RunSpec) -> Invocation:
        return Invocation(
            stdout=_event_bytes(
                input_tokens=20_000 if spec.mode == "baseline" else 10_000,
                cached_tokens=0 if spec.mode == "baseline" else 9_000,
                tool=spec.mode == "candidate",
                delegation=spec.sequence == 1,
                aggregate=spec.sequence == 1,
            ),
            stderr=b"",
            returncode=0,
            wall_time_ms=1,
        )

    report = run_benchmark(config, dry_run=False, runner=runner)

    first = report["runs"][0]
    assert first["delegation_evidence"]
    assert first["complete_aggregate_accounting"] is True
    assert not first["violations"]


def test_dry_run_makes_no_model_call_and_records_exact_plan(tmp_path: Path) -> None:
    config = _inputs(tmp_path)

    def forbidden(_: RunSpec) -> Invocation:  # pragma: no cover - must never run
        raise AssertionError("dry-run launched Codex")

    report = run_benchmark(config, dry_run=True, runner=forbidden)

    assert report["dry_run"] is True
    assert report["profile"]["model"] == "gpt-5.6-sol"
    assert report["profile"]["service_tier"] == "default"
    assert report["profile"]["execution_order"] == [
        "baseline:1",
        "candidate:1",
        "candidate:2",
        "baseline:2",
    ]
    assert report["verdict"]["status"] == "dry_run_only"
