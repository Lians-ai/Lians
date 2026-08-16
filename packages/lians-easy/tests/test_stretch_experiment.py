from __future__ import annotations

import json
import subprocess

import pytest
from lians_easy import stretch_experiment


def test_social_research_compiles_10000_posts_past_the_three_x_gate() -> None:
    plan = stretch_experiment.build_stretch_plan(workload="social-research")
    report = plan.report
    expected = plan.expected

    assert report["fixture"]["raw_record_count"] == 10_000
    assert expected == {
        "records_received": 10_000,
        "unique_posts": 9_000,
        "duplicate_posts": 1_000,
        "negative_posts": 7_200,
        "top_topic": "context continuity",
        "top_topic_posts": 3_600,
        "top_requested_integration": "Claude Code",
        "top_requested_integration_posts": 3_600,
    }
    assert report["projection"]["estimated_work_per_input_token_multiplier"] >= 3.0
    assert report["projection"]["estimated_usage_extension_percent"] >= 200.0
    assert report["evidence_gate"]["offline_met"] is True
    compiled = json.loads(plan.optimized_prompt.split("\n", 2)[1])["compiled_summary"]
    assert {key: compiled[key] for key in expected} == expected


def test_browser_marketing_collapses_history_and_enforces_guards() -> None:
    plan = stretch_experiment.build_stretch_plan(workload="browser-marketing")
    expected = plan.expected

    assert plan.report["fixture"]["raw_record_count"] == 2_400
    assert expected["surfaces_tracked"] == 200
    assert expected["history_events_collapsed"] == 2_200
    assert expected["published_surfaces"] == 60
    assert expected["blocked_surfaces"] == 30
    assert expected["waiting_surfaces"] == 30
    assert expected["candidate_surfaces"] == 80
    assert expected["next_eligible_surfaces"] == [
        "surface-0120",
        "surface-0121",
        "surface-0122",
        "surface-0123",
        "surface-0124",
    ]
    assert expected["never_repeat_published"] is True
    assert expected["never_contact_hard_excluded"] is True
    assert expected["approval_gates_respected"] is True
    assert plan.report["projection"]["estimated_work_per_input_token_multiplier"] >= 3.0


def test_full_scale_paired_run_is_refused_before_provider_preflight() -> None:
    calls: list[list[str]] = []

    def runner(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        raise AssertionError("provider must not be called")

    with pytest.raises(ValueError, match="too large"):
        stretch_experiment.run_stretch_experiment(
            "codex",
            workload="social-research",
            paired=True,
            environment={},
            executable="codex",
            run_command=runner,
        )
    assert calls == []


def test_codex_compiled_only_run_scores_exact_answer() -> None:
    plan = stretch_experiment.build_stretch_plan(
        workload="social-research",
        records=100,
    )
    calls: list[list[str]] = []

    def runner(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(command)
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        answer = json.dumps(plan.expected, separators=(",", ":"))
        output = "\n".join(
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": answer},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 700,
                            "cached_input_tokens": 0,
                            "output_tokens": 60,
                        },
                    }
                ),
            )
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    report = stretch_experiment.run_stretch_experiment(
        "codex",
        workload="social-research",
        records=100,
        environment={},
        executable="codex",
        run_command=runner,
    )

    assert report["comparison"]["mode"] == "compiled-only"
    assert report["comparison"]["compiled_answer_exact"] is True
    assert report["comparison"]["provider_reported_work_per_token_multiplier"] is None
    assert report["evidence_gate"]["live_met"] is True
    assert len(calls) == 2
