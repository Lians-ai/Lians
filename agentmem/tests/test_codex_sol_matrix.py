"""Paid-call-free contracts for the manifest-driven Codex Sol matrix."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.codex_sol_matrix import (  # noqa: E402
    REPORT_SCHEMA,
    DaemonCommandSpec,
    Invocation,
    MatrixConfig,
    MatrixError,
    MatrixRunSpec,
    _atomic_json,
    build_plan,
    load_manifest,
    run_matrix,
)


def _manifest_payload(dataset: Path, *, profiles: int = 1, order: str = "balanced") -> dict:
    rates = {
        "uncached_input_credits_per_million": 125,
        "cached_input_credits_per_million": 12.5,
        "cache_write_input_credits_per_million": 156.25,
        "output_credits_per_million": 750,
    }
    profile_values = [
        {
            "id": f"sol-profile-{index}",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra" if index == 0 else "high",
            "service_tier": "default",
            "maximum_estimated_credits_per_run": 5,
            "rates_per_million_tokens": rates,
        }
        for index in range(profiles)
    ]
    return {
        "schema_version": "lians.codex-sol-prompt-matrix-manifest.v1",
        "suite_id": "fixture-suite",
        "target_usage_extension_percent": 80,
        "execution_order": order,
        "answer_instruction": "Return only the answer.",
        "hook": {
            "recall_k": 20,
            "max_context_tokens": 768,
            "minimum_score": 0.45,
            "hook_receipt_elapsed_target_ms": 3500,
            "require_complete_retrieval": True,
        },
        "estimated_credit_accounting": {
            "source_url": "https://example.test/pricing",
            "as_of": "2026-08-08",
        },
        "profiles": profile_values,
        "contexts": [
            {
                "id": "fixture-context",
                "dataset_artifact": str(dataset),
                "conversation_index": 0,
            }
        ],
        "dataset_prompts": [
            {
                "id": "fixture-prompt-1",
                "context_id": "fixture-context",
                "qa_index": 0,
                "category": 1,
                "accepted_answers": ["Alpha", "alpha"],
            },
            {
                "id": "fixture-prompt-2",
                "context_id": "fixture-context",
                "qa_index": 1,
                "category": 5,
                "accepted_answers": ["UNKNOWN"],
                "denied_answers": ["Beta"],
                "answer_instruction": "Return exactly UNKNOWN.",
            },
        ],
    }


def _inputs(
    tmp_path: Path,
    *,
    profiles: int = 1,
    prompts: int = 2,
    order: str = "balanced",
    cap: float = 100,
) -> MatrixConfig:
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "conversation": {
                        "session_1": [
                            {"speaker": "A", "text": "The answer is Alpha."},
                            {"speaker": "B", "text": "The unrelated answer is Beta."},
                        ],
                        "session_1_date_time": "8 May 2023",
                    },
                    "qa": [
                        {"question": "What is the answer?", "answer": "Alpha", "category": 1},
                        {
                            "question": "What answer belongs to C?",
                            "answer": None,
                            "adversarial_answer": "Beta",
                            "category": 5,
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    payload = _manifest_payload(dataset, profiles=profiles, order=order)
    payload["dataset_prompts"] = payload["dataset_prompts"][:prompts]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    files = {
        "source_db": tmp_path / "source.sqlite",
        "codex_exe": tmp_path / "codex.exe",
        "hook_python": tmp_path / "python.exe",
        "agents_file": tmp_path / "AGENTS.md",
        "hook_script": tmp_path / "hook.py",
    }
    for path in files.values():
        path.write_bytes(f"fixture:{path.name}".encode())
    return MatrixConfig(
        manifest_path=manifest,
        raw_dir=tmp_path / "raw",
        state_path=tmp_path / "state.json",
        estimated_credit_cap=cap,
        **files,
    )


def _events(
    answer: str,
    *,
    input_tokens: int,
    cached_tokens: int = 0,
    output_tokens: int = 1,
    complete: bool = True,
) -> bytes:
    values: list[dict] = [
        {"type": "thread.started", "thread_id": "fixture"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "answer", "type": "agent_message", "text": answer},
        },
    ]
    if complete:
        values.append(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_tokens,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 0,
                },
            }
        )
    return ("\n".join(json.dumps(value) for value in values) + "\n").encode()


def _write_receipt(
    spec: MatrixRunSpec,
    *,
    elapsed_ms: float = 100,
    transport: str | None = None,
) -> None:
    assert spec.hook_receipt_path is not None
    spec.hook_receipt_path.parent.mkdir(parents=True, exist_ok=True)
    spec.hook_receipt_path.write_text(
        json.dumps(
            {
                "status": "injected",
                "injected": True,
                "retrieval_transport": transport or spec.required_retrieval_transport,
                "prompt_sha256": hashlib.sha256(spec.prompt.encode()).hexdigest(),
                "query_source": "explicit_tag",
                "memory_count": 2,
                "token_estimate": 200,
                "top_score": 0.7,
                "elapsed_ms": elapsed_ms,
                "retrieval_degraded": False,
                "candidate_window_complete": True,
                "graph_search_complete": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _passing_runner(spec: MatrixRunSpec) -> Invocation:
    if spec.mode == "candidate":
        _write_receipt(spec)
        tokens = 8_000
        wall = 1_500
    else:
        tokens = 20_000
        wall = 3_000
    return Invocation(
        stdout=_events(spec.prompt_case.gold, input_tokens=tokens),
        stderr=b"fixture warning\n",
        returncode=0,
        wall_time_ms=wall,
    )


DAEMON_FINGERPRINT = "a" * 64


def _successful_daemon_runner(spec: DaemonCommandSpec) -> Invocation:
    if spec.action in {"--prewarm", "--health"}:
        value = {
            "status": "ready",
            "fingerprint": DAEMON_FINGERPRINT,
            "pid": 1234,
            "protocol": 1,
        }
    else:
        value = {"status": "stopping"}
    return Invocation(
        stdout=json.dumps(value).encode(),
        stderr=b"",
        returncode=0,
        wall_time_ms=1234 if spec.action == "--prewarm" else 2,
    )


def test_frozen_manifest_is_a_six_profile_ten_case_balanced_dry_run(tmp_path: Path) -> None:
    manifest_path = (
        ROOT.parent / "docs" / "benchmarks" / "manifests" / "codex-sol-locomo-10-case-v1.json"
    )
    source_db = tmp_path / "source.sqlite"
    source_db.write_bytes(b"fixture db")
    config = MatrixConfig(
        manifest_path=manifest_path,
        source_db=source_db,
        codex_exe=tmp_path / "unused-codex",
        hook_python=Path(sys.executable),
        agents_file=ROOT.parent / "integrations" / "codex" / "AGENTS.md",
        hook_script=ROOT.parent / "integrations" / "codex" / "user_prompt_submit_recall.py",
        estimated_credit_cap=50,
        prewarm_daemon=True,
    )

    def forbidden(_: MatrixRunSpec) -> Invocation:  # pragma: no cover - must not run
        raise AssertionError("dry run launched a paid model")

    report = run_matrix(config, dry_run=True, runner=forbidden)

    assert report["schema_version"] == REPORT_SCHEMA
    assert len(report["coverage"]["profiles"]) == 6
    assert [item["reasoning_effort"] for item in report["coverage"]["profiles"]] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert len(report["coverage"]["prompts"]) == 10
    question_hashes = {item["question_sha256"] for item in report["coverage"]["prompts"]}
    dataset_hashes = {item["dataset_sha256"] for item in report["coverage"]["prompts"]}
    assert len(question_hashes) == 10
    assert len(dataset_hashes) == 1
    assert question_hashes.isdisjoint(dataset_hashes)
    assert report["coverage"]["prompts"][0]["question_sha256"] == (
        "3d81469db48234b833ca03ce04cda98f08030c66b582d3d59aa803d6a153f8e0"
    )
    assert report["execution"]["planned_run_count"] == 120
    assert report["estimated_credit_budget"]["manifest_planned_upper_bound"] == 600
    assert report["planned_runs"][0]["order_variant"] == "AB"
    assert report["planned_runs"][2]["order_variant"] == "BA"
    assert report["verdict"]["status"] == "dry_run_only"
    assert report["hook_execution_profile"] == {
        "receipt_transport_required": "daemon",
        "prewarm_daemon_enabled": True,
        "recall_k": 20,
        "minimum_score": 0.45,
        "maximum_context_tokens_estimate": 768,
        "embedding_backend": "sentence_transformers",
        "bge_onnx_artifact_sha256": None,
        "reranker_backend": "off",
        "reranker_prefetch": 30,
        "reranker_primary_lexical": False,
        "reranker_onnx_model_sha256": None,
        "reranker_onnx_tokenizer_sha256": None,
        "daemon_lifecycle_passed": None,
        "daemon_sessions": [],
    }

    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_category: dict[int, list[int]] = {}
    for item in raw_manifest["dataset_prompts"]:
        by_category.setdefault(item["category"], []).append(item["qa_index"])
    assert by_category == {1: [3, 4], 2: [0, 1], 3: [2, 14], 4: [82, 83], 5: [152, 153]}
    category_five = raw_manifest["dataset_prompts"][-2:]
    assert all(item["accepted_answers"] == ["UNKNOWN"] for item in category_five)
    assert all(item["denied_answers"] for item in category_five)


def test_frozen_exact_bge_onnx_manifest_covers_all_six_efforts(tmp_path: Path) -> None:
    manifest_path = (
        ROOT.parent
        / "docs"
        / "benchmarks"
        / "manifests"
        / "codex-sol-locomo-10-case-bge-onnx-v2.json"
    )
    source_db = tmp_path / "source.sqlite"
    source_db.write_bytes(b"fixture db")
    artifact_dir = tmp_path / "bge-onnx"
    artifact_dir.mkdir()
    for name in ("model.onnx", "tokenizer.json", "manifest.json"):
        (artifact_dir / name).write_bytes(f"fixture:{name}".encode())
    config = MatrixConfig(
        manifest_path=manifest_path,
        source_db=source_db,
        codex_exe=tmp_path / "unused-codex",
        hook_python=Path(sys.executable),
        agents_file=ROOT.parent / "integrations" / "codex" / "AGENTS.md",
        hook_script=ROOT.parent / "integrations" / "codex" / "user_prompt_submit_recall.py",
        bge_onnx_artifact_dir=artifact_dir,
        prewarm_daemon=True,
    )

    report = run_matrix(config, dry_run=True)

    assert report["execution"]["planned_run_count"] == 120
    assert report["hook_execution_profile"]["embedding_backend"] == "bge_onnx"
    assert report["hook_execution_profile"]["reranker_backend"] == "off"
    assert report["hook_execution_profile"]["recall_k"] == 20
    assert report["hook_execution_profile"]["minimum_score"] == 0


def test_reranker_profile_is_fingerprinted_and_forwarded_to_candidate(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path, prompts=1)
    payload = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    payload["hook"].update(
        {
            "recall_k": 20,
            "minimum_score": 0,
            "reranker_backend": "onnx_cross_encoder",
            "reranker_prefetch": 100,
            "reranker_primary_lexical": True,
        }
    )
    config.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    model = tmp_path / "reranker.onnx"
    tokenizer = tmp_path / "tokenizer.json"
    model.write_bytes(b"fixture ONNX model")
    tokenizer.write_text("{}", encoding="utf-8")
    config = replace(
        config,
        reranker_onnx_model=model,
        reranker_onnx_tokenizer=tokenizer,
    )
    candidate_environment: dict[str, str] = {}

    def runner(spec: MatrixRunSpec) -> Invocation:
        if spec.mode == "candidate":
            candidate_environment.update(dict(spec.environment_overrides))
        return _passing_runner(spec)

    report = run_matrix(config, dry_run=False, runner=runner)

    profile = report["hook_execution_profile"]
    assert profile["recall_k"] == 20
    assert profile["minimum_score"] == 0
    assert profile["reranker_backend"] == "onnx_cross_encoder"
    assert profile["reranker_prefetch"] == 100
    assert profile["reranker_primary_lexical"] is True
    assert profile["reranker_onnx_model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()
    assert (
        profile["reranker_onnx_tokenizer_sha256"]
        == hashlib.sha256(tokenizer.read_bytes()).hexdigest()
    )
    assert candidate_environment["RECALL_RERANKER_ONNX_MODEL"] == str(model)
    assert candidate_environment["RECALL_RERANKER_ONNX_TOKENIZER"] == str(tokenizer)
    assert candidate_environment["RECALL_RERANKER_PREFETCH"] == "100"
    assert candidate_environment["RECALL_RERANKER_PRIMARY_LEXICAL"] == "true"


def test_reranker_profile_fails_closed_without_both_artifacts(tmp_path: Path) -> None:
    config = _inputs(tmp_path, prompts=1)
    payload = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    payload["hook"]["reranker_backend"] = "onnx_cross_encoder"
    config.manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MatrixError, match="requires model and tokenizer"):
        run_matrix(config, dry_run=True)


def test_bge_onnx_profile_is_hashed_and_isolated_from_sentence_transformers(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path, prompts=1)
    payload = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    payload["hook"]["embedding_backend"] = "bge_onnx"
    config.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    artifact_dir = tmp_path / "bge-onnx"
    artifact_dir.mkdir()
    for name, content in {
        "model.onnx": b"exact model",
        "tokenizer.json": b"{}",
        "manifest.json": b'{"schema":"fixture"}',
    }.items():
        (artifact_dir / name).write_bytes(content)
    config = replace(config, bge_onnx_artifact_dir=artifact_dir)
    candidate_environment: dict[str, str] = {}

    def runner(spec: MatrixRunSpec) -> Invocation:
        if spec.mode == "candidate":
            candidate_environment.update(dict(spec.environment_overrides))
        return _passing_runner(spec)

    report = run_matrix(config, dry_run=False, runner=runner)

    profile = report["hook_execution_profile"]
    assert profile["embedding_backend"] == "bge_onnx"
    assert profile["bge_onnx_artifact_sha256"] == {
        name: hashlib.sha256((artifact_dir / name).read_bytes()).hexdigest()
        for name in ("model.onnx", "tokenizer.json", "manifest.json")
    }
    assert candidate_environment["EMBEDDING_PROVIDER"] == "bge-onnx"
    assert candidate_environment["BGE_ONNX_ARTIFACT_DIR"] == str(artifact_dir)
    assert candidate_environment["BGE_ONNX_INTRA_OP_THREADS"] == "8"
    assert "SENTENCE_TRANSFORMER_MODEL" not in candidate_environment


def test_bge_onnx_profile_requires_the_complete_artifact_set(tmp_path: Path) -> None:
    config = _inputs(tmp_path, prompts=1)
    payload = json.loads(config.manifest_path.read_text(encoding="utf-8"))
    payload["hook"]["embedding_backend"] = "bge_onnx"
    config.manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    artifact_dir = tmp_path / "bge-onnx"
    artifact_dir.mkdir()
    (artifact_dir / "model.onnx").write_bytes(b"model")

    with pytest.raises(MatrixError, match="missing BGE ONNX artifact"):
        run_matrix(
            replace(config, bge_onnx_artifact_dir=artifact_dir),
            dry_run=True,
        )


def test_balanced_matrix_pins_every_profile_and_preserves_raw_jsonl(tmp_path: Path) -> None:
    config = _inputs(tmp_path, profiles=2)
    seen: list[tuple[str, str, str]] = []
    raw: dict[str, bytes] = {}

    def runner(spec: MatrixRunSpec) -> Invocation:
        command = "\n".join(spec.command)
        assert f"--model\n{spec.profile.model}" in command
        assert f'model_reasoning_effort="{spec.profile.reasoning_effort}"' in command
        assert f'service_tier="{spec.profile.service_tier}"' in command
        assert "--ignore-user-config" in command
        if spec.mode == "candidate":
            assert (spec.cwd / "AGENTS.md").is_file()
            assert "hooks.UserPromptSubmit=" in command
            assert spec.database_path is not None and spec.database_path.is_file()
            _write_receipt(spec)
        else:
            assert not (spec.cwd / "AGENTS.md").exists()
            assert "hooks.UserPromptSubmit=" not in command
            assert spec.database_path is None
        seen.append((spec.profile.profile_id, spec.prompt_case.prompt_id, spec.mode))
        stdout = _events(
            spec.prompt_case.gold,
            input_tokens=8_000 if spec.mode == "candidate" else 20_000,
        )
        raw[spec.run_id] = stdout
        return Invocation(stdout=stdout, stderr=b"", returncode=0, wall_time_ms=1000)

    report = run_matrix(config, dry_run=False, runner=runner)

    assert report["complete"] is True
    assert report["verdict"]["qualified"] is True
    assert report["matrix_summary"]["every_prompt_quality_passed_across_every_profile"] is True
    assert report["matrix_summary"]["every_cell_qualified"] is True
    assert len(seen) == 8
    assert [run["order_variant"] for run in report["runs"][:4]] == ["AB", "AB", "BA", "BA"]
    for run in report["runs"]:
        artifact = Path(run["raw_stdout_artifact"])
        assert artifact.read_bytes() == raw[run["run_id"]]
        assert artifact.name.endswith("attempt-001.stdout.jsonl")
        assert run["usage_accounting_complete"] is True


def test_alias_is_accepted_but_category_five_adversarial_answer_is_denied(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path)

    def runner(spec: MatrixRunSpec) -> Invocation:
        if spec.mode == "candidate":
            _write_receipt(spec)
        if spec.prompt_case.prompt_id == "fixture-prompt-1":
            answer = "alpha"
        elif spec.mode == "candidate":
            answer = "Beta"
        else:
            answer = "UNKNOWN"
        return Invocation(
            stdout=_events(answer, input_tokens=8_000 if spec.mode == "candidate" else 20_000),
            stderr=b"",
            returncode=0,
            wall_time_ms=100,
        )

    report = run_matrix(config, dry_run=False, runner=runner)

    assert report["complete"] is True
    assert report["verdict"]["qualified"] is False
    first = next(cell for cell in report["cells"] if cell["prompt_id"] == "fixture-prompt-1")
    second = next(cell for cell in report["cells"] if cell["prompt_id"] == "fixture-prompt-2")
    assert first["quality_gate"]["passed"] is True
    assert second["quality_gate"]["passed"] is False
    assert second["contract_gate"]["passed"] is True
    denied_run = next(
        run
        for run in report["runs"]
        if run["prompt_id"] == "fixture-prompt-2" and run["mode"] == "candidate"
    )
    assert denied_run["denied_answer_emitted"] is True


def test_cache_luck_cannot_pass_the_cache_neutral_gate(tmp_path: Path) -> None:
    config = _inputs(tmp_path, prompts=1)

    def runner(spec: MatrixRunSpec) -> Invocation:
        if spec.mode == "candidate":
            _write_receipt(spec)
        return Invocation(
            stdout=_events(
                spec.prompt_case.gold,
                input_tokens=19_000 if spec.mode == "candidate" else 20_000,
                cached_tokens=18_000 if spec.mode == "candidate" else 0,
            ),
            stderr=b"",
            returncode=0,
            wall_time_ms=100,
        )

    report = run_matrix(config, dry_run=False, runner=runner)
    economics = report["cells"][0]["economics"]

    assert economics["selected_passed"] is True
    assert economics["cache_neutral_passed"] is False
    assert report["verdict"]["qualified"] is False


def test_failed_attempt_reserves_cost_and_resume_retries_without_overwrite(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path, prompts=1)
    calls = 0

    def incomplete(spec: MatrixRunSpec) -> Invocation:
        nonlocal calls
        calls += 1
        return Invocation(
            stdout=_events(spec.prompt_case.gold, input_tokens=20_000, complete=False),
            stderr=b"",
            returncode=0,
            wall_time_ms=100,
        )

    first = run_matrix(config, dry_run=False, runner=incomplete)

    assert calls == 1
    assert first["complete"] is False
    assert first["verdict"]["status"] == "run_failed_resumable"
    assert first["estimated_credit_budget"]["reserved_for_failed_attempts"] == 5
    failed_raw = Path(first["failed_attempts"][0]["raw_stdout_artifact"])
    failed_bytes = failed_raw.read_bytes()

    resumed = run_matrix(replace(config, resume=True), dry_run=False, runner=_passing_runner)

    assert resumed["complete"] is True
    assert resumed["verdict"]["qualified"] is False
    assert resumed["verdict"]["status"] == "completed_with_unaccounted_failed_attempts"
    assert resumed["execution"]["failed_attempt_count"] == 1
    assert resumed["execution"]["all_attempts_have_complete_usage_accounting"] is False
    assert failed_raw.read_bytes() == failed_bytes
    assert Path(resumed["runs"][0]["raw_stdout_artifact"]).name.endswith("attempt-002.stdout.jsonl")


def test_estimated_credit_cap_pauses_between_calls_and_can_be_raised_on_resume(
    tmp_path: Path,
) -> None:
    config = _inputs(tmp_path, prompts=1, cap=5)
    initial_calls: list[str] = []

    def first_runner(spec: MatrixRunSpec) -> Invocation:
        initial_calls.append(spec.run_id)
        return _passing_runner(spec)

    first = run_matrix(config, dry_run=False, runner=first_runner)
    first_raw = Path(first["runs"][0]["raw_stdout_artifact"])
    first_hash = hashlib.sha256(first_raw.read_bytes()).hexdigest()

    assert len(initial_calls) == 1
    assert first["verdict"]["status"] == "estimated_credit_cap_reached"
    assert first["execution"]["completed_run_count"] == 1

    resume_calls: list[str] = []

    def resume_runner(spec: MatrixRunSpec) -> Invocation:
        resume_calls.append(spec.run_id)
        return _passing_runner(spec)

    resumed = run_matrix(
        replace(config, resume=True, estimated_credit_cap=20),
        dry_run=False,
        runner=resume_runner,
    )

    assert len(resume_calls) == 1
    assert resumed["complete"] is True
    assert resumed["execution"]["resumed_run_count"] == 1
    assert hashlib.sha256(first_raw.read_bytes()).hexdigest() == first_hash


def test_resume_fails_closed_when_a_fingerprinted_input_changes(tmp_path: Path) -> None:
    config = _inputs(tmp_path, prompts=1, cap=5)
    first = run_matrix(config, dry_run=False, runner=_passing_runner)
    assert first["complete"] is False
    config.agents_file.write_text("changed policy", encoding="utf-8")

    with pytest.raises(MatrixError, match="fingerprint"):
        run_matrix(
            replace(config, resume=True, estimated_credit_cap=20),
            dry_run=False,
            runner=_passing_runner,
        )


def test_abba_mode_keeps_two_repetitions_and_worst_repeat_gate(tmp_path: Path) -> None:
    config = _inputs(tmp_path, prompts=1, order="abba")
    manifest = load_manifest(config.manifest_path)
    assert [item["mode"] for item in build_plan(manifest)] == [
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]

    def runner(spec: MatrixRunSpec) -> Invocation:
        if spec.mode == "candidate":
            _write_receipt(spec)
        candidate_tokens = 8_000 if spec.repetition == 1 else 19_000
        tokens = candidate_tokens if spec.mode == "candidate" else 20_000
        return Invocation(
            stdout=_events(spec.prompt_case.gold, input_tokens=tokens),
            stderr=b"",
            returncode=0,
            wall_time_ms=100,
        )

    report = run_matrix(config, dry_run=False, runner=runner)
    economics = report["cells"][0]["economics"]

    assert economics["repetitions_per_arm"] == 2
    assert economics["worst_repeat_candidate_cost_ratio"] > 0.9
    assert economics["every_repeat_passed"] is False
    assert report["verdict"]["qualified"] is False


def test_prewarmed_daemon_lifecycle_uses_exact_candidate_environment_and_stops(
    tmp_path: Path,
) -> None:
    config = replace(_inputs(tmp_path, prompts=1), prewarm_daemon=True)
    timeline: list[str] = []
    lifecycle_environments: list[dict[str, str]] = []
    candidate_environment: dict[str, str] = {}

    def daemon_runner(spec: DaemonCommandSpec) -> Invocation:
        timeline.append(spec.action)
        lifecycle_environments.append(dict(spec.environment_overrides))
        assert spec.cwd.name.startswith("lians-sol-matrix-candidate-")
        assert spec.command == (
            str(config.hook_python),
            str(config.hook_script),
            spec.action,
        )
        return _successful_daemon_runner(spec)

    def model_runner(spec: MatrixRunSpec) -> Invocation:
        timeline.append(f"model:{spec.mode}")
        if spec.mode == "candidate":
            candidate_environment.update(dict(spec.environment_overrides))
        return _passing_runner(spec)

    report = run_matrix(
        config,
        dry_run=False,
        runner=model_runner,
        daemon_runner=daemon_runner,
    )

    assert timeline == ["--prewarm", "--health", "model:baseline", "model:candidate", "--stop"]
    assert report["complete"] is True
    assert report["verdict"]["qualified"] is True
    profile = report["hook_execution_profile"]
    assert profile["receipt_transport_required"] == "daemon"
    assert profile["daemon_lifecycle_passed"] is True
    assert len(profile["daemon_sessions"]) == 1
    session = profile["daemon_sessions"][0]
    assert session["profile_sha256"] == DAEMON_FINGERPRINT
    assert session["cold_start"]["wall_time_ms"] == 1234
    assert session["health"]["status"] == "ready"
    assert session["stop"]["status"] == "stopping"
    assert lifecycle_environments[0] == lifecycle_environments[1] == lifecycle_environments[2]
    lifecycle_environment = lifecycle_environments[0]
    assert lifecycle_environment["LIANS_CODEX_HOOK_DAEMON"] == "client"
    assert lifecycle_environment["LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR"]
    receipt_path = candidate_environment.pop("LIANS_CODEX_HOOK_RECEIPT")
    assert receipt_path.endswith(".jsonl")
    assert candidate_environment == lifecycle_environment
    candidate_run = next(run for run in report["runs"] if run["mode"] == "candidate")
    assert candidate_run["hook_receipt"]["retrieval_transport"] == "daemon"
    latency = report["cells"][0]["latency"]
    assert latency["measurement_profile"] == "fresh_hook_process_to_prewarmed_daemon"
    assert latency["candidate_hook_receipt_elapsed_ms"] == [100.0]
    assert latency["maximum_hook_receipt_elapsed_ms"] == 100.0


@pytest.mark.parametrize("failure_action", ["--prewarm", "--health"])
def test_daemon_prewarm_and_health_fail_closed_and_still_stop(
    tmp_path: Path,
    failure_action: str,
) -> None:
    config = replace(_inputs(tmp_path, prompts=1), prewarm_daemon=True)
    actions: list[str] = []
    model_calls = 0

    def daemon_runner(spec: DaemonCommandSpec) -> Invocation:
        actions.append(spec.action)
        if spec.action == failure_action:
            return Invocation(
                stdout=b'{"status":"not_ready"}',
                stderr=b"fixture lifecycle failure",
                returncode=1 if failure_action == "--prewarm" else 0,
                wall_time_ms=1,
            )
        if spec.action == "--stop":
            status = "not_running" if failure_action == "--prewarm" else "stopping"
            return Invocation(
                stdout=json.dumps({"status": status}).encode(),
                stderr=b"",
                returncode=0,
                wall_time_ms=1,
            )
        return _successful_daemon_runner(spec)

    def forbidden_model(_: MatrixRunSpec) -> Invocation:  # pragma: no cover - must not run
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("model launched after daemon lifecycle failure")

    with pytest.raises(MatrixError, match="hook daemon"):
        run_matrix(
            config,
            dry_run=False,
            runner=forbidden_model,
            daemon_runner=daemon_runner,
        )

    assert model_calls == 0
    assert actions[0] == "--prewarm"
    assert actions[-1] == "--stop"
    persisted = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert len(persisted["daemon_sessions"]) == 1


def test_daemon_profile_rejects_direct_receipt_without_conflating_quality(
    tmp_path: Path,
) -> None:
    config = replace(_inputs(tmp_path, prompts=1), prewarm_daemon=True)

    def direct_fallback_runner(spec: MatrixRunSpec) -> Invocation:
        if spec.mode == "candidate":
            _write_receipt(spec, transport="direct")
        return Invocation(
            stdout=_events(
                spec.prompt_case.gold,
                input_tokens=8_000 if spec.mode == "candidate" else 20_000,
            ),
            stderr=b"",
            returncode=0,
            wall_time_ms=100,
        )

    report = run_matrix(
        config,
        dry_run=False,
        runner=direct_fallback_runner,
        daemon_runner=_successful_daemon_runner,
    )

    cell = report["cells"][0]
    assert cell["quality_gate"]["passed"] is True
    assert cell["contract_gate"]["passed"] is False
    assert report["verdict"]["qualified"] is False
    candidate = next(run for run in report["runs"] if run["mode"] == "candidate")
    assert candidate["contract_valid"] is False
    assert any("required daemon profile" in item for item in candidate["violations"])


def test_live_run_requires_explicit_cap_raw_dir_and_state(tmp_path: Path) -> None:
    config = replace(
        _inputs(tmp_path),
        raw_dir=None,
        state_path=None,
        estimated_credit_cap=None,
    )

    with pytest.raises(MatrixError, match="raw-dir and --state"):
        run_matrix(config, dry_run=False, runner=_passing_runner)


def test_atomic_checkpoint_retries_transient_windows_share_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    real_replace = __import__("os").replace
    attempts = 0

    def transient(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("fixture share violation")
        real_replace(source, destination)

    monkeypatch.setattr("benchmarks.codex_sol_matrix.os.replace", transient)

    _atomic_json(target, {"completed": 97})

    assert attempts == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"completed": 97}


def test_completed_resume_generates_report_without_model_or_daemon_calls(
    tmp_path: Path,
) -> None:
    config = replace(_inputs(tmp_path, prompts=1), prewarm_daemon=True)
    first = run_matrix(
        config,
        dry_run=False,
        runner=_passing_runner,
        daemon_runner=_successful_daemon_runner,
    )
    assert first["complete"] is True

    def forbidden_model(_spec):  # pragma: no cover - must not run
        raise AssertionError("completed resume launched a model")

    def forbidden_daemon(_spec):  # pragma: no cover - must not run
        raise AssertionError("completed resume launched a daemon")

    report = run_matrix(
        replace(config, resume=True),
        dry_run=False,
        runner=forbidden_model,
        daemon_runner=forbidden_daemon,
    )

    assert report["complete"] is True
    assert report["execution"]["resumed_run_count"] == 2
    assert (
        report["hook_execution_profile"]["daemon_sessions"]
        == (first["hook_execution_profile"]["daemon_sessions"])
    )
