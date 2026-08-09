"""Run a fail-closed Codex Sol baseline-versus-hook prompt matrix.

The matrix is deliberately manifest driven.  A report can only make a claim
about the exact model profiles and prompt artifacts named before execution.
Live execution requires an explicit estimated-credit cap, writes provider
stdout as byte-for-byte JSONL, and checkpoints after every accepted run so a
later invocation can resume without paying for completed cells again.

This harness estimates credits from measured token telemetry and manifest
rates.  It never represents those estimates as provider-reported billing.
Use ``--dry-run`` to validate and price the complete plan without launching
Codex.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from .codex_sol_ultra_ab import (
        Invocation,
        BenchmarkError,
        _answer,
        _complete_aggregate_accounting,
        _config_arg,
        _delegation_evidence,
        _full_conversation,
        _parse_events,
        _stderr_tail,
        _tool_calls,
        _usage,
    )
except ImportError:  # pragma: no cover - direct script execution
    from codex_sol_ultra_ab import (
        Invocation,
        BenchmarkError,
        _answer,
        _complete_aggregate_accounting,
        _config_arg,
        _delegation_evidence,
        _full_conversation,
        _parse_events,
        _stderr_tail,
        _tool_calls,
        _usage,
    )


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AGENTS = REPO_ROOT / "integrations" / "codex" / "AGENTS.md"
DEFAULT_HOOK = REPO_ROOT / "integrations" / "codex" / "user_prompt_submit_recall.py"
MANIFEST_SCHEMA = "lians.codex-sol-prompt-matrix-manifest.v1"
REPORT_SCHEMA = "lians.codex-sol-prompt-matrix-report.v1"
STATE_SCHEMA = "lians.codex-sol-prompt-matrix-state.v1"
ABBA = (("baseline", 1), ("candidate", 1), ("candidate", 2), ("baseline", 2))
BAAB = (("candidate", 1), ("baseline", 1), ("baseline", 2), ("candidate", 2))
RATE_FIELDS = (
    "uncached_input_credits_per_million",
    "cached_input_credits_per_million",
    "cache_write_input_credits_per_million",
    "output_credits_per_million",
)
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
BGE_ONNX_ARTIFACT_FILES = ("model.onnx", "tokenizer.json", "manifest.json")


class MatrixError(RuntimeError):
    """Raised when the matrix cannot produce trustworthy comparable evidence."""


@dataclass(frozen=True)
class Profile:
    profile_id: str
    model: str
    reasoning_effort: str
    service_tier: str
    rates: Mapping[str, float]
    maximum_estimated_credits_per_run: float


@dataclass(frozen=True)
class PromptCase:
    prompt_id: str
    category: str
    question: str
    accepted_answers: tuple[str, ...]
    denied_answers: tuple[str, ...]
    answer_instruction: str
    full_context: str
    context_id: str
    question_artifact: Path
    question_sha256: str
    dataset_artifact: Path
    dataset_sha256: str

    @property
    def gold(self) -> str:
        """Primary display answer; quality accepts any declared exact alias."""

        return self.accepted_answers[0]


@dataclass(frozen=True)
class MatrixManifest:
    suite_id: str
    target_usage_extension_percent: float
    execution_order: str
    profiles: tuple[Profile, ...]
    prompts: tuple[PromptCase, ...]
    hook_recall_k: int
    hook_max_context_tokens: int
    hook_min_score: float
    hook_receipt_elapsed_target_ms: float
    require_complete_retrieval: bool
    hook_embedding_backend: str
    hook_reranker_backend: str
    hook_reranker_prefetch: int
    hook_reranker_primary_lexical: bool
    rate_source_url: str
    rate_as_of: str
    manifest_sha256: str

    @property
    def target_multiplier(self) -> float:
        return 1.0 + self.target_usage_extension_percent / 100.0

    @property
    def target_cost_ratio(self) -> float:
        return 1.0 / self.target_multiplier


@dataclass(frozen=True)
class MatrixConfig:
    manifest_path: Path
    source_db: Path
    codex_exe: Path
    hook_python: Path
    agents_file: Path = DEFAULT_AGENTS
    hook_script: Path = DEFAULT_HOOK
    namespace: str = "local"
    agent_id: str = "locomo-conv-26"
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    bge_onnx_artifact_dir: Path | None = None
    reranker_onnx_model: Path | None = None
    reranker_onnx_tokenizer: Path | None = None
    timeout_seconds: float = 300.0
    raw_dir: Path | None = None
    state_path: Path | None = None
    estimated_credit_cap: float | None = None
    resume: bool = False
    prewarm_daemon: bool = False


@dataclass(frozen=True)
class MatrixRunSpec:
    sequence: int
    run_id: str
    order_variant: str
    profile: Profile
    prompt_case: PromptCase
    mode: str
    repetition: int
    cwd: Path
    prompt: str
    command: tuple[str, ...]
    database_path: Path | None
    hook_receipt_path: Path | None
    environment_overrides: tuple[tuple[str, str], ...]
    timeout_seconds: float
    required_retrieval_transport: str


@dataclass(frozen=True)
class DaemonCommandSpec:
    action: str
    command: tuple[str, ...]
    cwd: Path
    environment_overrides: tuple[tuple[str, str], ...]
    timeout_seconds: float


Runner = Callable[[MatrixRunSpec], Invocation]
DaemonRunner = Callable[[DaemonCommandSpec], Invocation]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MatrixError(f"{label} must be a JSON object: {path}")
    return value


def _id(value: Any, label: str) -> str:
    if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
        raise MatrixError(f"{label} must match {ID_PATTERN.pattern}")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MatrixError(f"{label} must be a non-empty string")
    return value.strip()


def _number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MatrixError(f"{label} must be a number")
    parsed = float(value)
    if not parsed >= minimum or not parsed < float("inf"):
        raise MatrixError(f"{label} must be finite and at least {minimum:g}")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MatrixError(f"{label} must be a positive integer")
    return value


def _artifact_path(value: Any, label: str) -> Path:
    raw = _text(value, label)
    path = Path(raw)
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    if not resolved.is_file():
        raise MatrixError(f"missing {label}: {resolved}")
    return resolved


def _question_paths(root: Mapping[str, Any]) -> list[tuple[str, Path, str, str]]:
    """Expand explicit prompt entries and bounded artifact series."""

    expanded: list[tuple[str, Path, str, str]] = []
    entries = root.get("prompts", [])
    if not isinstance(entries, list):
        raise MatrixError("prompts must be an array")
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise MatrixError(f"prompts[{index}] must be an object")
        path = _artifact_path(entry.get("question_artifact"), f"prompts[{index}].question_artifact")
        context_id = _id(entry.get("context_id"), f"prompts[{index}].context_id")
        instruction = _text(
            entry.get("answer_instruction", root.get("answer_instruction")),
            f"prompts[{index}].answer_instruction",
        )
        declared_id = str(entry.get("id", "")).strip()
        expanded.append((declared_id, path, context_id, instruction))

    series = root.get("question_series", [])
    if not isinstance(series, list):
        raise MatrixError("question_series must be an array")
    for series_index, entry in enumerate(series):
        if not isinstance(entry, Mapping):
            raise MatrixError(f"question_series[{series_index}] must be an object")
        template = _text(
            entry.get("question_artifact_template"),
            f"question_series[{series_index}].question_artifact_template",
        )
        if template.count("{index}") != 1:
            raise MatrixError("question_artifact_template must contain {index} exactly once")
        indices = entry.get("indices")
        if not isinstance(indices, list) or not indices:
            raise MatrixError(f"question_series[{series_index}].indices must be non-empty")
        if len(set(map(str, indices))) != len(indices):
            raise MatrixError(f"question_series[{series_index}].indices contains duplicates")
        context_id = _id(entry.get("context_id"), f"question_series[{series_index}].context_id")
        instruction = _text(
            entry.get("answer_instruction", root.get("answer_instruction")),
            f"question_series[{series_index}].answer_instruction",
        )
        for item in indices:
            if isinstance(item, bool) or not isinstance(item, (int, str)):
                raise MatrixError("question series indices must be integers or identifier strings")
            rendered = template.replace("{index}", str(item))
            path = _artifact_path(rendered, "question series artifact")
            expanded.append(("", path, context_id, instruction))
    return expanded


def load_manifest(path: Path) -> MatrixManifest:
    root = _read_object(path, "matrix manifest")
    if root.get("schema_version") != MANIFEST_SCHEMA:
        raise MatrixError(f"manifest schema_version must be {MANIFEST_SCHEMA}")
    suite_id = _id(root.get("suite_id"), "suite_id")
    target = _number(
        root.get("target_usage_extension_percent"),
        "target_usage_extension_percent",
        minimum=0.0,
    )
    execution_order = _text(root.get("execution_order"), "execution_order").casefold()
    if execution_order not in {"abba", "balanced"}:
        raise MatrixError("execution_order must be 'abba' or 'balanced'")

    hook = root.get("hook")
    if not isinstance(hook, Mapping):
        raise MatrixError("hook must be an object")
    recall_k = _positive_int(hook.get("recall_k"), "hook.recall_k")
    max_context = _positive_int(hook.get("max_context_tokens"), "hook.max_context_tokens")
    min_score = _number(hook.get("minimum_score"), "hook.minimum_score", minimum=0.0)
    if min_score > 1.0:
        raise MatrixError("hook.minimum_score cannot exceed 1")
    latency_target = _number(
        hook.get("hook_receipt_elapsed_target_ms"),
        "hook.hook_receipt_elapsed_target_ms",
        minimum=0.001,
    )
    require_complete = hook.get("require_complete_retrieval", True)
    if not isinstance(require_complete, bool):
        raise MatrixError("hook.require_complete_retrieval must be boolean")
    embedding_backend = (
        str(hook.get("embedding_backend", "sentence_transformers")).strip().casefold()
    )
    if embedding_backend not in {"sentence_transformers", "bge_onnx"}:
        raise MatrixError("hook.embedding_backend must be 'sentence_transformers' or 'bge_onnx'")
    reranker_backend = str(hook.get("reranker_backend", "off")).strip().casefold()
    if reranker_backend not in {"off", "onnx_cross_encoder"}:
        raise MatrixError("hook.reranker_backend must be 'off' or 'onnx_cross_encoder'")
    reranker_prefetch = _positive_int(hook.get("reranker_prefetch", 30), "hook.reranker_prefetch")
    reranker_primary_lexical = hook.get("reranker_primary_lexical", False)
    if not isinstance(reranker_primary_lexical, bool):
        raise MatrixError("hook.reranker_primary_lexical must be boolean")
    if reranker_primary_lexical and reranker_backend == "off":
        raise MatrixError("lexical-primary hook profile requires a reranker backend")

    accounting = root.get("estimated_credit_accounting")
    if not isinstance(accounting, Mapping):
        raise MatrixError("estimated_credit_accounting must be an object")
    rate_source = _text(accounting.get("source_url"), "estimated_credit_accounting.source_url")
    rate_as_of = _text(accounting.get("as_of"), "estimated_credit_accounting.as_of")

    raw_profiles = root.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise MatrixError("profiles must be a non-empty array")
    profiles: list[Profile] = []
    profile_ids: set[str] = set()
    exact_profiles: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_profiles):
        if not isinstance(raw, Mapping):
            raise MatrixError(f"profiles[{index}] must be an object")
        profile_id = _id(raw.get("id"), f"profiles[{index}].id")
        model = _text(raw.get("model"), f"profiles[{index}].model")
        if "sol" not in model.casefold():
            raise MatrixError(f"profiles[{index}].model is not a Sol model")
        effort = _text(raw.get("reasoning_effort"), f"profiles[{index}].reasoning_effort")
        tier = _text(raw.get("service_tier"), f"profiles[{index}].service_tier")
        if profile_id in profile_ids:
            raise MatrixError(f"duplicate profile id: {profile_id}")
        exact = (model, effort, tier)
        if exact in exact_profiles:
            raise MatrixError(f"duplicate model/effort/tier profile: {exact}")
        raw_rates = raw.get("rates_per_million_tokens")
        if not isinstance(raw_rates, Mapping) or set(raw_rates) != set(RATE_FIELDS):
            raise MatrixError(f"profiles[{index}] rates must contain exactly {RATE_FIELDS}")
        rates = {
            name: _number(raw_rates.get(name), f"profiles[{index}].rates.{name}")
            for name in RATE_FIELDS
        }
        maximum = _number(
            raw.get("maximum_estimated_credits_per_run"),
            f"profiles[{index}].maximum_estimated_credits_per_run",
            minimum=0.000000001,
        )
        profiles.append(Profile(profile_id, model, effort, tier, rates, maximum))
        profile_ids.add(profile_id)
        exact_profiles.add(exact)

    raw_contexts = root.get("contexts")
    if not isinstance(raw_contexts, list) or not raw_contexts:
        raise MatrixError("contexts must be a non-empty array")
    contexts: dict[str, tuple[Path, str, int, Sequence[Any]]] = {}
    for index, raw in enumerate(raw_contexts):
        if not isinstance(raw, Mapping):
            raise MatrixError(f"contexts[{index}] must be an object")
        context_id = _id(raw.get("id"), f"contexts[{index}].id")
        if context_id in contexts:
            raise MatrixError(f"duplicate context id: {context_id}")
        dataset_path = _artifact_path(raw.get("dataset_artifact"), "dataset artifact")
        conversation_index = raw.get("conversation_index")
        if (
            isinstance(conversation_index, bool)
            or not isinstance(conversation_index, int)
            or conversation_index < 0
        ):
            raise MatrixError(f"contexts[{index}].conversation_index must be non-negative")
        try:
            dataset_value = json.loads(dataset_path.read_text(encoding="utf-8"))
            conversation = dataset_value[conversation_index]["conversation"]
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            IndexError,
            KeyError,
            TypeError,
        ) as exc:
            raise MatrixError(f"context {context_id} cannot resolve its conversation") from exc
        if not isinstance(conversation, Mapping):
            raise MatrixError(f"context {context_id} conversation must be an object")
        if not isinstance(dataset_value, list):
            raise MatrixError(f"context {context_id} dataset must be an array")
        contexts[context_id] = (
            dataset_path,
            _full_conversation(conversation),
            conversation_index,
            dataset_value,
        )

    prompts: list[PromptCase] = []
    prompt_ids: set[str] = set()
    for declared_id, question_path, context_id, instruction in _question_paths(root):
        if context_id not in contexts:
            raise MatrixError(f"prompt references unknown context: {context_id}")
        question = _read_object(question_path, "question artifact")
        prompt_id = _id(question.get("question_id"), "question.question_id")
        if declared_id and declared_id != prompt_id:
            raise MatrixError(f"declared prompt id {declared_id} != artifact id {prompt_id}")
        if prompt_id in prompt_ids:
            raise MatrixError(f"duplicate prompt id: {prompt_id}")
        question_text = _text(question.get("question"), f"{prompt_id}.question")
        gold = _text(question.get("ground_truth_answer"), f"{prompt_id}.ground_truth_answer")
        category = str(question.get("category", "unspecified")).strip() or "unspecified"
        dataset_path, full_context, conversation_index, _ = contexts[context_id]
        artifact_index = question.get("conversation_idx")
        if artifact_index != conversation_index:
            raise MatrixError(
                f"{prompt_id}.conversation_idx {artifact_index!r} does not match context index "
                f"{conversation_index}"
            )
        prompts.append(
            PromptCase(
                prompt_id=prompt_id,
                category=category,
                question=question_text,
                accepted_answers=(gold,),
                denied_answers=(),
                answer_instruction=instruction,
                full_context=full_context,
                context_id=context_id,
                question_artifact=question_path,
                question_sha256=_sha256_bytes(question_text.encode("utf-8")),
                dataset_artifact=dataset_path,
                dataset_sha256=_sha256_file(dataset_path),
            )
        )
        prompt_ids.add(prompt_id)

    dataset_prompts = root.get("dataset_prompts", [])
    if not isinstance(dataset_prompts, list):
        raise MatrixError("dataset_prompts must be an array")
    for index, raw in enumerate(dataset_prompts):
        if not isinstance(raw, Mapping):
            raise MatrixError(f"dataset_prompts[{index}] must be an object")
        prompt_id = _id(raw.get("id"), f"dataset_prompts[{index}].id")
        if prompt_id in prompt_ids:
            raise MatrixError(f"duplicate prompt id: {prompt_id}")
        context_id = _id(raw.get("context_id"), f"dataset_prompts[{index}].context_id")
        if context_id not in contexts:
            raise MatrixError(f"prompt references unknown context: {context_id}")
        qa_index = raw.get("qa_index")
        if isinstance(qa_index, bool) or not isinstance(qa_index, int) or qa_index < 0:
            raise MatrixError(f"dataset_prompts[{index}].qa_index must be non-negative")
        dataset_path, full_context, conversation_index, dataset_value = contexts[context_id]
        try:
            qa = dataset_value[conversation_index]["qa"][qa_index]
        except (IndexError, KeyError, TypeError) as exc:
            raise MatrixError(f"{prompt_id} cannot resolve qa_index {qa_index}") from exc
        if not isinstance(qa, Mapping):
            raise MatrixError(f"{prompt_id} dataset QA must be an object")
        question_text = _text(qa.get("question"), f"{prompt_id}.question")
        category = str(qa.get("category", "unspecified")).strip() or "unspecified"
        declared_category = str(raw.get("category", category)).strip()
        if category != declared_category:
            raise MatrixError(
                f"{prompt_id} declared category {declared_category!r} != dataset {category!r}"
            )
        accepted_raw = raw.get("accepted_answers")
        if not isinstance(accepted_raw, list) or not accepted_raw:
            raise MatrixError(f"{prompt_id}.accepted_answers must be a non-empty array")
        accepted = tuple(_text(value, f"{prompt_id}.accepted_answers") for value in accepted_raw)
        if len(set(accepted)) != len(accepted):
            raise MatrixError(f"{prompt_id}.accepted_answers contains duplicates")
        denied_raw = raw.get("denied_answers", [])
        if not isinstance(denied_raw, list):
            raise MatrixError(f"{prompt_id}.denied_answers must be an array")
        denied = tuple(_text(value, f"{prompt_id}.denied_answers") for value in denied_raw)
        if {value.casefold() for value in accepted} & {value.casefold() for value in denied}:
            raise MatrixError(f"{prompt_id} cannot both accept and deny an answer")
        instruction = _text(
            raw.get("answer_instruction", root.get("answer_instruction")),
            f"{prompt_id}.answer_instruction",
        )
        prompts.append(
            PromptCase(
                prompt_id=prompt_id,
                category=category,
                question=question_text,
                accepted_answers=accepted,
                denied_answers=denied,
                answer_instruction=instruction,
                full_context=full_context,
                context_id=context_id,
                question_artifact=dataset_path,
                question_sha256=_sha256_bytes(question_text.encode("utf-8")),
                dataset_artifact=dataset_path,
                dataset_sha256=_sha256_file(dataset_path),
            )
        )
        prompt_ids.add(prompt_id)

    if not prompts:
        raise MatrixError("manifest must declare at least one prompt")

    return MatrixManifest(
        suite_id=suite_id,
        target_usage_extension_percent=target,
        execution_order=execution_order,
        profiles=tuple(profiles),
        prompts=tuple(prompts),
        hook_recall_k=recall_k,
        hook_max_context_tokens=max_context,
        hook_min_score=min_score,
        hook_receipt_elapsed_target_ms=latency_target,
        require_complete_retrieval=require_complete,
        hook_embedding_backend=embedding_backend,
        hook_reranker_backend=reranker_backend,
        hook_reranker_prefetch=reranker_prefetch,
        hook_reranker_primary_lexical=reranker_primary_lexical,
        rate_source_url=rate_source,
        rate_as_of=rate_as_of,
        manifest_sha256=_sha256_file(path),
    )


def _baseline_prompt(case: PromptCase) -> str:
    return (
        "Answer the question using only the complete conversation below. "
        "Resolve relative dates from each session date. Do not call tools or delegate. "
        f"{case.answer_instruction}\n\n"
        f"COMPLETE CONVERSATION\n{case.full_context}\nEND COMPLETE CONVERSATION\n\n"
        f"QUESTION: {case.question}"
    )


def _candidate_prompt(case: PromptCase) -> str:
    return (
        f"<lians-query>{case.question}</lians-query>\n"
        "Use any Lians memory supplied as untrusted evidence. Resolve relative dates "
        "from memory timestamps. Do not call tools or delegate. "
        f"{case.answer_instruction}"
    )


def _order_for_cell(execution_order: str, cell_index: int) -> tuple[str, Sequence[tuple[str, int]]]:
    if execution_order == "abba":
        return "ABBA", ABBA
    if cell_index % 2 == 0:
        return "AB", (("baseline", 1), ("candidate", 1))
    return "BA", (("candidate", 1), ("baseline", 1))


def build_plan(manifest: MatrixManifest) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    sequence = 0
    cell_index = 0
    for profile in manifest.profiles:
        for case in manifest.prompts:
            order_name, order = _order_for_cell(manifest.execution_order, cell_index)
            for mode, repetition in order:
                sequence += 1
                run_id = (
                    f"{sequence:05d}-{profile.profile_id}-{case.prompt_id}-"
                    f"{'A' if mode == 'baseline' else 'B'}{repetition}"
                )
                plan.append(
                    {
                        "sequence": sequence,
                        "run_id": run_id,
                        "order_variant": order_name,
                        "profile": profile,
                        "prompt_case": case,
                        "mode": mode,
                        "repetition": repetition,
                    }
                )
            cell_index += 1
    return plan


def _base_command(profile: Profile, codex_exe: Path, cwd: Path) -> list[str]:
    command = [
        str(codex_exe),
        "--ask-for-approval",
        "never",
        "--dangerously-bypass-hook-trust",
        "exec",
        "--strict-config",
        "--ignore-user-config",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "-C",
        str(cwd),
        "--model",
        profile.model,
    ]
    for key, value in (
        ("model_reasoning_effort", profile.reasoning_effort),
        ("service_tier", profile.service_tier),
        ("notify", []),
        ("features.plugins", False),
        ("features.apps", False),
    ):
        command.extend(_config_arg(key, value))
    return command


def _candidate_base_environment(
    config: MatrixConfig,
    manifest: MatrixManifest,
    *,
    candidate_dir: Path,
    copied_db: Path,
    daemon_runtime_dir: Path | None,
) -> tuple[tuple[str, str], ...]:
    if config.prewarm_daemon and daemon_runtime_dir is None:
        raise MatrixError("prewarmed daemon profile requires an isolated runtime directory")
    sdk_root = REPO_ROOT / "agentmem" / "sdk" / "python"
    environment: tuple[tuple[str, str], ...] = (
        ("LIANS_URL", ""),
        ("LIANS_API_KEY", ""),
        ("LIANS_LOCAL_DB", str(copied_db)),
        ("LIANS_AGENT_ID", config.agent_id),
        ("LIANS_NAMESPACE", config.namespace),
        ("LIANS_MCP_PROJECT_ROOT", str(candidate_dir)),
        ("LIANS_CODEX_HOOK_K", str(manifest.hook_recall_k)),
        ("LIANS_CODEX_HOOK_MAX_TOKENS", str(manifest.hook_max_context_tokens)),
        ("LIANS_CODEX_HOOK_MIN_SCORE", str(manifest.hook_min_score)),
        ("LIANS_CODEX_HOOK_DAEMON", "client" if config.prewarm_daemon else "off"),
        (
            "LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR",
            str(daemon_runtime_dir) if daemon_runtime_dir is not None else "",
        ),
        ("LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS", "1800"),
        ("LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS", "10000"),
        ("LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS", "120000"),
        ("HF_HUB_OFFLINE", "1"),
        (
            "PYTHONPATH",
            os.pathsep.join((str(sdk_root), str(REPO_ROOT / "agentmem"))),
        ),
    )
    if manifest.hook_embedding_backend == "bge_onnx":
        if config.bge_onnx_artifact_dir is None:
            raise MatrixError("BGE ONNX profile requires an artifact directory")
        environment = (
            *environment,
            ("EMBEDDING_PROVIDER", "bge-onnx"),
            ("BGE_ONNX_ARTIFACT_DIR", str(config.bge_onnx_artifact_dir)),
            ("BGE_ONNX_INTRA_OP_THREADS", "8"),
        )
    else:
        environment = (
            *environment,
            ("EMBEDDING_PROVIDER", "sentence-transformers"),
            ("SENTENCE_TRANSFORMER_MODEL", config.embedding_model),
        )
    if manifest.hook_reranker_backend == "off":
        return environment
    if config.reranker_onnx_model is None or config.reranker_onnx_tokenizer is None:
        raise MatrixError("ONNX reranker profile requires model and tokenizer artifacts")
    return (
        *environment,
        ("RECALL_RERANKER_ONNX_MODEL", str(config.reranker_onnx_model)),
        ("RECALL_RERANKER_ONNX_TOKENIZER", str(config.reranker_onnx_tokenizer)),
        ("RECALL_RERANKER_PREFETCH", str(manifest.hook_reranker_prefetch)),
        ("RECALL_RERANKER_BATCH_SIZE", "64"),
        ("RECALL_RERANKER_MAX_LENGTH", "256"),
        ("RECALL_RERANKER_ORT_THREADS", "4"),
        (
            "RECALL_RERANKER_PRIMARY_LEXICAL",
            "true" if manifest.hook_reranker_primary_lexical else "false",
        ),
    )


def _make_run_spec(
    config: MatrixConfig,
    manifest: MatrixManifest,
    planned: Mapping[str, Any],
    *,
    baseline_dir: Path,
    candidate_dir: Path,
    copied_db: Path,
    candidate_environment: tuple[tuple[str, str], ...],
) -> MatrixRunSpec:
    profile = planned["profile"]
    case = planned["prompt_case"]
    mode = str(planned["mode"])
    candidate = mode == "candidate"
    cwd = candidate_dir if candidate else baseline_dir
    command = _base_command(profile, config.codex_exe, cwd)
    receipt_path: Path | None = None
    environment: tuple[tuple[str, str], ...] = ()
    if candidate:
        hook_command = subprocess.list2cmdline([str(config.hook_python), str(config.hook_script)])
        command.extend(
            _config_arg(
                "hooks.UserPromptSubmit",
                [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": hook_command,
                                "commandWindows": hook_command,
                                "timeout": min(120, max(1, int(config.timeout_seconds))),
                                "statusMessage": "Recalling Lians memory",
                                "additionalContextLimit": manifest.hook_max_context_tokens,
                            }
                        ]
                    }
                ],
            )
        )
        receipt_path = candidate_dir / "hook-receipts" / f"{planned['run_id']}.jsonl"
        environment = (
            *candidate_environment,
            ("LIANS_CODEX_HOOK_RECEIPT", str(receipt_path)),
        )
    command.append("-")
    return MatrixRunSpec(
        sequence=int(planned["sequence"]),
        run_id=str(planned["run_id"]),
        order_variant=str(planned["order_variant"]),
        profile=profile,
        prompt_case=case,
        mode=mode,
        repetition=int(planned["repetition"]),
        cwd=cwd,
        prompt=_candidate_prompt(case) if candidate else _baseline_prompt(case),
        command=tuple(command),
        database_path=copied_db if candidate else None,
        hook_receipt_path=receipt_path,
        environment_overrides=environment,
        timeout_seconds=config.timeout_seconds,
        required_retrieval_transport="daemon" if config.prewarm_daemon else "direct",
    )


def _runtime_environment(overrides: Sequence[tuple[str, str]]) -> dict[str, str]:
    isolated_retrieval_keys = {
        "EMBEDDING_PROVIDER",
        "SENTENCE_TRANSFORMER_MODEL",
        "HF_HUB_OFFLINE",
        "BGE_ONNX_ARTIFACT_DIR",
        "BGE_ONNX_INTRA_OP_THREADS",
        "RECALL_RERANKER_MODEL",
        "RECALL_RERANKER_ONNX_MODEL",
        "RECALL_RERANKER_ONNX_TOKENIZER",
        "RECALL_RERANKER_PREFETCH",
        "RECALL_RERANKER_BATCH_SIZE",
        "RECALL_RERANKER_MAX_LENGTH",
        "RECALL_RERANKER_ORT_THREADS",
        "RECALL_RERANKER_PRIMARY_LEXICAL",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LIANS_") and key not in isolated_retrieval_keys
    }
    environment.update(dict(overrides))
    return environment


def run_codex(spec: MatrixRunSpec) -> Invocation:
    environment = _runtime_environment(spec.environment_overrides)
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(spec.command),
            input=spec.prompt.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=spec.timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise MatrixError(f"{spec.run_id} exceeded {spec.timeout_seconds:g} seconds") from exc
    return Invocation(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        wall_time_ms=(time.perf_counter() - started) * 1000.0,
    )


def run_daemon_command(spec: DaemonCommandSpec) -> Invocation:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(spec.command),
            input=b"",
            capture_output=True,
            check=False,
            timeout=spec.timeout_seconds,
            env=_runtime_environment(spec.environment_overrides),
            cwd=spec.cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise MatrixError(
            f"hook daemon {spec.action} exceeded {spec.timeout_seconds:g} seconds"
        ) from exc
    return Invocation(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
        wall_time_ms=(time.perf_counter() - started) * 1000.0,
    )


def _daemon_command_spec(
    config: MatrixConfig,
    action: str,
    *,
    candidate_dir: Path,
    candidate_environment: tuple[tuple[str, str], ...],
) -> DaemonCommandSpec:
    return DaemonCommandSpec(
        action=action,
        command=(str(config.hook_python), str(config.hook_script), action),
        cwd=candidate_dir,
        environment_overrides=candidate_environment,
        timeout_seconds=config.timeout_seconds if action == "--prewarm" else 10.0,
    )


def _parse_daemon_result(
    spec: DaemonCommandSpec,
    invocation: Invocation,
    *,
    expected_statuses: set[str],
) -> dict[str, Any]:
    if invocation.returncode != 0:
        raise MatrixError(
            f"hook daemon {spec.action} exited {invocation.returncode}: "
            f"{_stderr_tail(invocation.stderr)[-1000:]}"
        )
    try:
        value = json.loads(invocation.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixError(f"hook daemon {spec.action} returned invalid JSON") from exc
    if not isinstance(value, dict) or value.get("status") not in expected_statuses:
        raise MatrixError(f"hook daemon {spec.action} did not report {sorted(expected_statuses)}")
    return value


def _daemon_evidence(
    spec: DaemonCommandSpec, invocation: Invocation, result: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "action": spec.action,
        "status": result.get("status"),
        "wall_time_ms": round(invocation.wall_time_ms, 3),
        "stdout_sha256": _sha256_bytes(invocation.stdout),
        "stderr_sha256": _sha256_bytes(invocation.stderr),
        "stderr_tail_redacted": _stderr_tail(invocation.stderr),
    }


@contextmanager
def _prewarmed_daemon_session(
    config: MatrixConfig,
    *,
    candidate_dir: Path,
    daemon_runtime_dir: Path,
    candidate_environment: tuple[tuple[str, str], ...],
    daemon_runner: DaemonRunner,
    daemon_sessions: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
):
    if daemon_runtime_dir.exists():
        raise MatrixError("daemon runtime directory must not exist before cold prewarm")
    lifecycle: dict[str, Any] = {
        "enabled": True,
        "receipt_transport_required": "daemon",
        "runtime_directory_isolated": True,
        "runtime_directory": str(daemon_runtime_dir),
        "cold_start": None,
        "health": None,
        "profile_sha256": None,
        "stop": None,
    }
    ready = False
    try:
        prewarm_spec = _daemon_command_spec(
            config,
            "--prewarm",
            candidate_dir=candidate_dir,
            candidate_environment=candidate_environment,
        )
        prewarm_invocation = daemon_runner(prewarm_spec)
        prewarm_result = _parse_daemon_result(
            prewarm_spec, prewarm_invocation, expected_statuses={"ready"}
        )
        fingerprint = prewarm_result.get("fingerprint")
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise MatrixError("hook daemon prewarm omitted its profile fingerprint")
        ready_pid = prewarm_result.get("pid")
        if isinstance(ready_pid, bool) or not isinstance(ready_pid, int) or ready_pid < 1:
            raise MatrixError("hook daemon prewarm omitted its process identity")
        lifecycle["cold_start"] = _daemon_evidence(prewarm_spec, prewarm_invocation, prewarm_result)
        lifecycle["cold_start"]["ready_pid"] = ready_pid
        lifecycle["profile_sha256"] = fingerprint

        health_spec = _daemon_command_spec(
            config,
            "--health",
            candidate_dir=candidate_dir,
            candidate_environment=candidate_environment,
        )
        health_invocation = daemon_runner(health_spec)
        health_result = _parse_daemon_result(
            health_spec, health_invocation, expected_statuses={"ready"}
        )
        if health_result.get("fingerprint") != fingerprint:
            raise MatrixError("hook daemon health fingerprint did not match prewarm")
        if health_result.get("pid") != ready_pid:
            raise MatrixError("hook daemon health process did not match prewarm")
        lifecycle["health"] = _daemon_evidence(health_spec, health_invocation, health_result)
        lifecycle["health"]["ready_pid"] = health_result.get("pid")
        ready = True
        yield lifecycle
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            stop_spec = _daemon_command_spec(
                config,
                "--stop",
                candidate_dir=candidate_dir,
                candidate_environment=candidate_environment,
            )
            stop_invocation = daemon_runner(stop_spec)
            stop_result = _parse_daemon_result(
                stop_spec,
                stop_invocation,
                expected_statuses={"stopping", "not_running"},
            )
            lifecycle["stop"] = _daemon_evidence(stop_spec, stop_invocation, stop_result)
            if ready and stop_result.get("status") != "stopping":
                raise MatrixError("ready hook daemon was not running during required stop")
        except Exception as exc:
            lifecycle["stop"] = {"status": "failed", "error": str(exc)}
            if not active_error:
                raise
        finally:
            daemon_sessions.append(lifecycle)
            state["daemon_sessions"] = daemon_sessions
            _atomic_json(state_path, state)


def _estimate(usage: Mapping[str, int], rates: Mapping[str, float]) -> float:
    value = (
        usage["uncached_input_tokens"] * rates["uncached_input_credits_per_million"]
        + usage["cached_input_tokens"] * rates["cached_input_credits_per_million"]
        + usage["cache_write_input_tokens"] * rates["cache_write_input_credits_per_million"]
        + usage["output_tokens"] * rates["output_credits_per_million"]
    ) / 1_000_000
    return round(value, 9)


def _estimate_uncached(usage: Mapping[str, int], rates: Mapping[str, float]) -> float:
    value = (
        usage["input_tokens"] * rates["uncached_input_credits_per_million"]
        + usage["output_tokens"] * rates["output_credits_per_million"]
    ) / 1_000_000
    return round(value, 9)


def _read_receipt(
    spec: MatrixRunSpec, manifest: MatrixManifest
) -> tuple[dict[str, Any] | None, list[str]]:
    if spec.mode == "baseline":
        return None, []
    if spec.hook_receipt_path is None:
        return None, ["candidate hook receipt path was not configured"]
    try:
        lines = [
            line
            for line in spec.hook_receipt_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if len(lines) != 1:
            return None, [f"hook emitted {len(lines)} receipts, expected 1"]
        receipt = json.loads(lines[0])
        if not isinstance(receipt, dict):
            return None, ["hook receipt was not an object"]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, ["hook receipt was missing or invalid"]

    violations: list[str] = []
    if receipt.get("retrieval_transport") != spec.required_retrieval_transport:
        violations.append(
            "hook receipt retrieval transport did not match the required "
            f"{spec.required_retrieval_transport} profile"
        )
    if receipt.get("status") != "injected" or receipt.get("injected") is not True:
        violations.append("hook did not inject memory context")
    if receipt.get("prompt_sha256") != _sha256_bytes(spec.prompt.encode("utf-8")):
        violations.append("hook receipt prompt hash did not match")
    if receipt.get("query_source") != "explicit_tag":
        violations.append("hook did not use the explicit retrieval query tag")
    memory_count = receipt.get("memory_count")
    if isinstance(memory_count, bool) or not isinstance(memory_count, int) or memory_count < 1:
        violations.append("hook receipt contained no memories")
    tokens = receipt.get("token_estimate")
    if (
        isinstance(tokens, bool)
        or not isinstance(tokens, int)
        or tokens < 1
        or tokens > manifest.hook_max_context_tokens
    ):
        violations.append("hook context exceeded or omitted its token budget")
    score = receipt.get("top_score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or float(score) < manifest.hook_min_score
    ):
        violations.append("hook receipt did not clear the relevance threshold")
    elapsed = receipt.get("elapsed_ms")
    if (
        isinstance(elapsed, bool)
        or not isinstance(elapsed, (int, float))
        or not math.isfinite(float(elapsed))
        or elapsed < 0
    ):
        violations.append("hook receipt omitted retrieval elapsed_ms")
    if receipt.get("retrieval_degraded") is True:
        violations.append("hook injected degraded retrieval")
    if manifest.require_complete_retrieval:
        if receipt.get("candidate_window_complete") is not True:
            violations.append("hook candidate window was incomplete")
        if receipt.get("graph_search_complete") is not True:
            violations.append("hook graph search was incomplete")
    return receipt, violations


def _write_raw(path: Path, stdout: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(stdout)
    except FileExistsError as exc:
        raise MatrixError(f"refusing to overwrite raw artifact: {path}") from exc


def _parse_run(
    spec: MatrixRunSpec,
    invocation: Invocation,
    manifest: MatrixManifest,
    raw_path: Path,
) -> dict[str, Any]:
    _write_raw(raw_path, invocation.stdout)
    if invocation.returncode != 0:
        raise MatrixError(
            f"{spec.run_id} exited {invocation.returncode}: {_stderr_tail(invocation.stderr)[-1000:]}"
        )
    events = _parse_events(invocation.stdout, spec.run_id)
    usage = _usage(events, spec.run_id)
    answer = _answer(events, spec.run_id)
    tools = _tool_calls(events)
    delegation = _delegation_evidence(events)
    aggregate_complete = _complete_aggregate_accounting(events)
    receipt, violations = _read_receipt(spec, manifest)
    if tools:
        violations.append(f"{spec.mode} used a model-facing tool")
    if delegation and not aggregate_complete:
        violations.append("delegation lacked explicit complete thread-tree usage accounting")
    accepted = answer in spec.prompt_case.accepted_answers
    denied = answer.casefold() in {value.casefold() for value in spec.prompt_case.denied_answers}
    estimated = _estimate(usage, spec.profile.rates)
    uncached = _estimate_uncached(usage, spec.profile.rates)
    receipt_elapsed = receipt.get("elapsed_ms") if receipt is not None else None
    valid_receipt_elapsed = (
        isinstance(receipt_elapsed, (int, float))
        and not isinstance(receipt_elapsed, bool)
        and math.isfinite(float(receipt_elapsed))
        and float(receipt_elapsed) >= 0
    )
    if estimated > spec.profile.maximum_estimated_credits_per_run + 1e-12:
        violations.append("observed estimate exceeded the manifest per-run cost bound")
    return {
        "sequence": spec.sequence,
        "run_id": spec.run_id,
        "order_variant": spec.order_variant,
        "profile_id": spec.profile.profile_id,
        "model": spec.profile.model,
        "reasoning_effort": spec.profile.reasoning_effort,
        "service_tier": spec.profile.service_tier,
        "prompt_id": spec.prompt_case.prompt_id,
        "prompt_category": spec.prompt_case.category,
        "mode": spec.mode,
        "repetition": spec.repetition,
        "answer": answer,
        "gold_answer": spec.prompt_case.gold,
        "accepted_answers": list(spec.prompt_case.accepted_answers),
        "denied_answers": list(spec.prompt_case.denied_answers),
        "accepted_answer_match": accepted,
        "denied_answer_emitted": denied,
        "protected_quality_passed": accepted and not denied,
        "exact_answer_match": answer == spec.prompt_case.gold,
        "usage_accounting_complete": True,
        "usage": usage,
        "estimated_sol_credits": estimated,
        "estimated_sol_credits_all_input_uncached": uncached,
        "estimated_not_provider_reported": True,
        "wall_time_ms": round(invocation.wall_time_ms, 3),
        "hook_retrieval_elapsed_ms": (
            round(float(receipt_elapsed), 3) if valid_receipt_elapsed else None
        ),
        "hook_receipt": receipt,
        "required_retrieval_transport": spec.required_retrieval_transport,
        "model_facing_tool_calls": tools,
        "delegation_evidence": delegation,
        "complete_aggregate_accounting": aggregate_complete,
        "violations": violations,
        "contract_valid": not violations,
        "raw_stdout_artifact": str(raw_path.resolve()),
        "raw_stdout_sha256": _sha256_bytes(invocation.stdout),
        "stderr_sha256": _sha256_bytes(invocation.stderr),
        "stderr_tail_redacted": _stderr_tail(invocation.stderr),
    }


def _bge_onnx_artifact_hashes(artifact_dir: Path | None) -> dict[str, str] | None:
    if artifact_dir is None:
        return None
    return {name: _sha256_file(artifact_dir / name) for name in BGE_ONNX_ARTIFACT_FILES}


def _fingerprint(config: MatrixConfig, manifest: MatrixManifest) -> dict[str, Any]:
    questions = {case.prompt_id: case.question_sha256 for case in manifest.prompts}
    datasets = {str(case.dataset_artifact): case.dataset_sha256 for case in manifest.prompts}
    return {
        "manifest_sha256": manifest.manifest_sha256,
        "source_db_sha256": _sha256_file(config.source_db),
        "agents_sha256": _sha256_file(config.agents_file),
        "hook_sha256": _sha256_file(config.hook_script),
        "hook_python_sha256": _sha256_file(config.hook_python),
        "question_sha256": questions,
        "dataset_sha256": datasets,
        "namespace": config.namespace,
        "agent_id": config.agent_id,
        "embedding_model": config.embedding_model,
        "embedding_backend": manifest.hook_embedding_backend,
        "bge_onnx_artifact_sha256": _bge_onnx_artifact_hashes(config.bge_onnx_artifact_dir),
        "reranker_backend": manifest.hook_reranker_backend,
        "reranker_prefetch": manifest.hook_reranker_prefetch,
        "reranker_primary_lexical": manifest.hook_reranker_primary_lexical,
        "reranker_onnx_model_sha256": (
            _sha256_file(config.reranker_onnx_model)
            if config.reranker_onnx_model is not None
            else None
        ),
        "reranker_onnx_tokenizer_sha256": (
            _sha256_file(config.reranker_onnx_tokenizer)
            if config.reranker_onnx_tokenizer is not None
            else None
        ),
        "prewarm_daemon": config.prewarm_daemon,
    }


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    # Windows readers and antivirus scanners can briefly hold the destination
    # without granting delete/replace sharing. The temporary file is already
    # durable enough for a bounded retry; never discard or regenerate a paid
    # run merely because the checkpoint was locked for a few milliseconds.
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.025 * (attempt + 1))


@contextmanager
def _temporary_directory(prefix: str):
    """Create and reliably remove one benchmark-owned temporary directory."""

    temporary_root = Path(tempfile.gettempdir()).resolve()
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=temporary_root)).resolve()
    if temporary_root not in path.parents:
        raise MatrixError("temporary benchmark directory escaped the system temp root")
    try:
        yield path
    finally:
        for attempt in range(20):
            try:
                shutil.rmtree(path)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05 * (attempt + 1))


def _load_state(
    config: MatrixConfig,
    fingerprint: Mapping[str, Any],
    plan: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if config.state_path is None:
        raise MatrixError("live execution requires --state")
    if config.resume:
        if not config.state_path.is_file():
            raise MatrixError("--resume requires an existing state file")
        state = _read_object(config.state_path, "resume state")
        if state.get("schema_version") != STATE_SCHEMA:
            raise MatrixError("resume state schema is unsupported")
        if state.get("fingerprint") != fingerprint:
            raise MatrixError("resume fingerprint does not match current inputs")
    else:
        if config.state_path.exists():
            raise MatrixError("state already exists; use --resume or choose a new path")
        state = {
            "schema_version": STATE_SCHEMA,
            "fingerprint": dict(fingerprint),
            "completed_runs": [],
            "failed_attempts": [],
            "daemon_sessions": [],
        }
    completed = state.get("completed_runs")
    if not isinstance(completed, list):
        raise MatrixError("state.completed_runs must be an array")
    if len(completed) > len(plan):
        raise MatrixError("state contains more runs than the plan")
    for index, run in enumerate(completed):
        if not isinstance(run, Mapping) or run.get("run_id") != plan[index]["run_id"]:
            raise MatrixError("completed state runs must be an exact plan prefix")
        raw_path = Path(str(run.get("raw_stdout_artifact", "")))
        if not raw_path.is_file() or _sha256_file(raw_path) != run.get("raw_stdout_sha256"):
            raise MatrixError(f"resume raw artifact is missing or changed: {raw_path}")
    failed_attempts = state.get("failed_attempts", [])
    if not isinstance(failed_attempts, list):
        raise MatrixError("state.failed_attempts must be an array")
    for attempt in failed_attempts:
        if not isinstance(attempt, Mapping):
            raise MatrixError("state failed attempts must be objects")
        artifact = attempt.get("raw_stdout_artifact")
        if artifact is not None:
            raw_path = Path(str(artifact))
            if not raw_path.is_file() or _sha256_file(raw_path) != attempt.get("raw_stdout_sha256"):
                raise MatrixError(f"failed-attempt raw artifact is missing or changed: {raw_path}")
    daemon_sessions = state.get("daemon_sessions", [])
    if not isinstance(daemon_sessions, list) or not all(
        isinstance(session, Mapping) for session in daemon_sessions
    ):
        raise MatrixError("state.daemon_sessions must be an array of objects")
    return state


def _ratio(candidate: float, baseline: float, label: str) -> float:
    if candidate <= 0 or baseline <= 0:
        raise MatrixError(f"{label} costs must be greater than zero")
    return candidate / baseline


def _cell_report(
    manifest: MatrixManifest,
    profile: Profile,
    case: PromptCase,
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    cell = [
        run
        for run in runs
        if run["profile_id"] == profile.profile_id and run["prompt_id"] == case.prompt_id
    ]
    expected_cell_runs = 4 if manifest.execution_order == "abba" else 2
    if len(cell) != expected_cell_runs:
        return None
    baseline = [run for run in cell if run["mode"] == "baseline"]
    candidate = [run for run in cell if run["mode"] == "candidate"]
    if len(baseline) != len(candidate) or len(baseline) not in {1, 2}:
        raise MatrixError(f"cell {profile.profile_id}/{case.prompt_id} is unbalanced")
    selected_repetition = max(int(run["repetition"]) for run in cell)
    selected_a = next(run for run in baseline if run["repetition"] == selected_repetition)
    selected_b = next(run for run in candidate if run["repetition"] == selected_repetition)
    selected_ratio = _ratio(
        float(selected_b["estimated_sol_credits"]),
        float(selected_a["estimated_sol_credits"]),
        "selected",
    )
    pooled_ratio = _ratio(
        sum(float(run["estimated_sol_credits"]) for run in candidate),
        sum(float(run["estimated_sol_credits"]) for run in baseline),
        "pooled",
    )
    worst_ratio = _ratio(
        max(float(run["estimated_sol_credits"]) for run in candidate),
        min(float(run["estimated_sol_credits"]) for run in baseline),
        "worst repeat",
    )
    neutral_ratio = _ratio(
        max(float(run["estimated_sol_credits_all_input_uncached"]) for run in candidate),
        min(float(run["estimated_sol_credits_all_input_uncached"]) for run in baseline),
        "cache-neutral worst repeat",
    )
    target = manifest.target_cost_ratio
    economic = all(
        value <= target + 1e-12
        for value in (selected_ratio, pooled_ratio, worst_ratio, neutral_ratio)
    )
    quality = all(bool(run["protected_quality_passed"]) for run in cell)
    contracts = all(bool(run["contract_valid"]) for run in cell)
    retrieval = [
        float(value) for run in candidate if (value := run["hook_retrieval_elapsed_ms"]) is not None
    ]
    latency = len(retrieval) == len(candidate) and (
        max(retrieval) <= manifest.hook_receipt_elapsed_target_ms
    )
    qualified = quality and contracts and economic and latency
    return {
        "profile_id": profile.profile_id,
        "prompt_id": case.prompt_id,
        "prompt_category": case.category,
        "order_variant": cell[0]["order_variant"],
        "run_ids": [run["run_id"] for run in sorted(cell, key=lambda item: item["sequence"])],
        "quality_gate": {
            "rule": "trim outer whitespace, then exact primary/alias allowlist; denylist wins",
            "accepted_answers": list(case.accepted_answers),
            "denied_answers": list(case.denied_answers),
            "all_runs_passed": quality,
            "passed": quality,
        },
        "contract_gate": {
            "complete_usage_all_runs": all(bool(run["usage_accounting_complete"]) for run in cell),
            "all_tool_delegation_retrieval_and_receipt_contracts_passed": contracts,
            "passed": contracts,
        },
        "economics": {
            "repetitions_per_arm": len(baseline),
            "selected_repeat_candidate_cost_ratio": round(selected_ratio, 9),
            "pooled_repeats_candidate_cost_ratio": round(pooled_ratio, 9),
            "worst_repeat_candidate_cost_ratio": round(worst_ratio, 9),
            "cache_neutral_worst_repeat_cost_ratio": round(neutral_ratio, 9),
            "same_budget_usage_multiplier_selected": round(1 / selected_ratio, 9),
            "same_budget_usage_extension_percent_selected": round(
                (1 / selected_ratio - 1) * 100, 9
            ),
            "selected_passed": selected_ratio <= target + 1e-12,
            "pooled_passed": pooled_ratio <= target + 1e-12,
            "every_repeat_passed": worst_ratio <= target + 1e-12,
            "cache_neutral_passed": neutral_ratio <= target + 1e-12,
            "passed": economic,
        },
        "latency": {
            "measurement_profile": (
                "fresh_hook_process_to_prewarmed_daemon"
                if candidate[0]["required_retrieval_transport"] == "daemon"
                else "fresh_hook_process_direct_retrieval"
            ),
            "candidate_hook_receipt_elapsed_ms": retrieval,
            "maximum_hook_receipt_elapsed_ms": (round(max(retrieval), 3) if retrieval else None),
            "mean_hook_receipt_elapsed_ms": (
                round(statistics.fmean(retrieval), 3) if retrieval else None
            ),
            "target_ms": manifest.hook_receipt_elapsed_target_ms,
            "selected_baseline_wall_time_ms": selected_a["wall_time_ms"],
            "selected_candidate_wall_time_ms": selected_b["wall_time_ms"],
            "selected_wall_time_delta_ms": round(
                float(selected_b["wall_time_ms"]) - float(selected_a["wall_time_ms"]), 3
            ),
            "passed": latency,
        },
        "qualified": qualified,
    }


def _summaries(
    manifest: MatrixManifest, runs: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    cells = [
        cell
        for profile in manifest.profiles
        for case in manifest.prompts
        if (cell := _cell_report(manifest, profile, case, runs)) is not None
    ]
    prompt_quality: list[dict[str, Any]] = []
    for case in manifest.prompts:
        relevant = [cell for cell in cells if cell["prompt_id"] == case.prompt_id]
        prompt_quality.append(
            {
                "prompt_id": case.prompt_id,
                "category": case.category,
                "profiles_completed": len(relevant),
                "all_completed_profiles_passed_quality": bool(relevant)
                and all(cell["quality_gate"]["passed"] for cell in relevant),
                "all_declared_profiles_completed": len(relevant) == len(manifest.profiles),
            }
        )
    if len(cells) != len(manifest.profiles) * len(manifest.prompts):
        return cells, prompt_quality, None
    baseline = [run for run in runs if run["mode"] == "baseline"]
    candidate = [run for run in runs if run["mode"] == "candidate"]
    pooled = _ratio(
        sum(float(run["estimated_sol_credits"]) for run in candidate),
        sum(float(run["estimated_sol_credits"]) for run in baseline),
        "matrix pooled",
    )
    neutral = _ratio(
        sum(float(run["estimated_sol_credits_all_input_uncached"]) for run in candidate),
        sum(float(run["estimated_sol_credits_all_input_uncached"]) for run in baseline),
        "matrix cache-neutral pooled",
    )
    target = manifest.target_cost_ratio
    every_cell = all(bool(cell["qualified"]) for cell in cells)
    cell_latencies = [
        float(value)
        for cell in cells
        if (value := cell["latency"]["maximum_hook_receipt_elapsed_ms"]) is not None
    ]
    summary = {
        "declared_profile_count": len(manifest.profiles),
        "declared_prompt_count": len(manifest.prompts),
        "completed_cell_count": len(cells),
        "all_declared_cells_completed": True,
        "every_prompt_quality_passed_across_every_profile": all(
            item["all_completed_profiles_passed_quality"] for item in prompt_quality
        ),
        "every_cell_qualified": every_cell,
        "pooled_candidate_cost_ratio": round(pooled, 9),
        "cache_neutral_pooled_candidate_cost_ratio": round(neutral, 9),
        "worst_cell_candidate_cost_ratio": max(
            float(cell["economics"]["worst_repeat_candidate_cost_ratio"]) for cell in cells
        ),
        "maximum_hook_receipt_elapsed_ms": (
            max(cell_latencies) if len(cell_latencies) == len(cells) else None
        ),
        "pooled_target_met": pooled <= target + 1e-12,
        "cache_neutral_pooled_target_met": neutral <= target + 1e-12,
        "qualified": every_cell and pooled <= target + 1e-12 and neutral <= target + 1e-12,
    }
    return cells, prompt_quality, summary


def _report(
    config: MatrixConfig,
    manifest: MatrixManifest,
    plan: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    *,
    dry_run: bool,
    status: str,
    resumed_runs: int,
    failed_attempts: Sequence[Mapping[str, Any]] = (),
    daemon_sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    daemon_sessions = daemon_sessions if daemon_sessions is not None else []
    daemon_lifecycle_passed = (
        None
        if dry_run
        else (
            not config.prewarm_daemon
            or bool(daemon_sessions)
            and all(
                isinstance(session.get("cold_start"), Mapping)
                and session["cold_start"].get("status") == "ready"
                and isinstance(session.get("health"), Mapping)
                and session["health"].get("status") == "ready"
                and isinstance(session.get("profile_sha256"), str)
                and isinstance(session.get("stop"), Mapping)
                and session["stop"].get("status") == "stopping"
                for session in daemon_sessions
            )
        )
    )
    observed_spent = round(sum(float(run["estimated_sol_credits"]) for run in runs), 9)
    failed_reserve = round(
        sum(float(attempt["reserved_estimated_credits"]) for attempt in failed_attempts), 9
    )
    cap_consumed = round(observed_spent + failed_reserve, 9)
    planned_upper = round(
        sum(float(item["profile"].maximum_estimated_credits_per_run) for item in plan),
        9,
    )
    cells, prompt_quality, summary = _summaries(manifest, runs)
    complete = len(runs) == len(plan)
    if dry_run:
        verdict = {
            "status": "dry_run_only",
            "qualified": None,
            "statement": "No model call, measured result, or universal prompt claim was made.",
        }
    elif not complete:
        verdict = {
            "status": status,
            "qualified": None,
            "statement": "The matrix is incomplete; no cross-profile or every-prompt claim is valid.",
        }
    else:
        qualified = bool(
            summary and summary["qualified"] and not failed_attempts and daemon_lifecycle_passed
        )
        if failed_attempts:
            final_status = "completed_with_unaccounted_failed_attempts"
        elif qualified:
            final_status = "qualified_for_declared_matrix"
        else:
            final_status = "declared_matrix_not_qualified"
        verdict = {
            "status": final_status,
            "qualified": qualified,
            "statement": (
                "The verdict applies only to the exact manifest profiles, prompt IDs, artifacts, "
                "rates, and repetitions. It is not a universal prompt guarantee."
            ),
        }
    return {
        "schema_version": REPORT_SCHEMA,
        "suite_id": manifest.suite_id,
        "manifest_sha256": manifest.manifest_sha256,
        "dry_run": dry_run,
        "complete": complete,
        "target": {
            "usage_extension_percent": manifest.target_usage_extension_percent,
            "same_budget_usage_multiplier": round(manifest.target_multiplier, 9),
            "maximum_candidate_cost_ratio": round(manifest.target_cost_ratio, 9),
            "hook_receipt_elapsed_target_ms": manifest.hook_receipt_elapsed_target_ms,
        },
        "coverage": {
            "profiles": [
                {
                    "id": profile.profile_id,
                    "model": profile.model,
                    "reasoning_effort": profile.reasoning_effort,
                    "service_tier": profile.service_tier,
                    "maximum_estimated_credits_per_run": (
                        profile.maximum_estimated_credits_per_run
                    ),
                }
                for profile in manifest.profiles
            ],
            "prompts": [
                {
                    "id": case.prompt_id,
                    "category": case.category,
                    "question_sha256": case.question_sha256,
                    "dataset_sha256": case.dataset_sha256,
                }
                for case in manifest.prompts
            ],
        },
        "execution": {
            "order_policy": manifest.execution_order,
            "planned_run_count": len(plan),
            "completed_run_count": len(runs),
            "resumed_run_count": resumed_runs,
            "failed_attempt_count": len(failed_attempts),
            "all_attempts_have_complete_usage_accounting": not failed_attempts,
            "remaining_run_count": len(plan) - len(runs),
            "raw_jsonl_per_run": True,
            "quality_evaluated_before_economics": True,
        },
        "hook_execution_profile": {
            "receipt_transport_required": "daemon" if config.prewarm_daemon else "direct",
            "prewarm_daemon_enabled": config.prewarm_daemon,
            "recall_k": manifest.hook_recall_k,
            "minimum_score": manifest.hook_min_score,
            "maximum_context_tokens_estimate": manifest.hook_max_context_tokens,
            "embedding_backend": manifest.hook_embedding_backend,
            "bge_onnx_artifact_sha256": _bge_onnx_artifact_hashes(config.bge_onnx_artifact_dir),
            "reranker_backend": manifest.hook_reranker_backend,
            "reranker_prefetch": manifest.hook_reranker_prefetch,
            "reranker_primary_lexical": manifest.hook_reranker_primary_lexical,
            "reranker_onnx_model_sha256": (
                _sha256_file(config.reranker_onnx_model)
                if config.reranker_onnx_model is not None
                else None
            ),
            "reranker_onnx_tokenizer_sha256": (
                _sha256_file(config.reranker_onnx_tokenizer)
                if config.reranker_onnx_tokenizer is not None
                else None
            ),
            "daemon_lifecycle_passed": daemon_lifecycle_passed,
            "daemon_sessions": daemon_sessions,
        },
        "estimated_credit_budget": {
            "cap": config.estimated_credit_cap,
            "observed_from_completed_runs": observed_spent,
            "reserved_for_failed_attempts": failed_reserve,
            "cap_consumed_observed_plus_reserves": cap_consumed,
            "manifest_planned_upper_bound": planned_upper,
            "provider_reported": False,
            "rate_source_url": manifest.rate_source_url,
            "rate_as_of": manifest.rate_as_of,
        },
        "planned_runs": (
            [
                {
                    "sequence": item["sequence"],
                    "run_id": item["run_id"],
                    "order_variant": item["order_variant"],
                    "profile_id": item["profile"].profile_id,
                    "prompt_id": item["prompt_case"].prompt_id,
                    "mode": item["mode"],
                    "repetition": item["repetition"],
                }
                for item in plan
            ]
            if dry_run
            else None
        ),
        "runs": list(runs),
        "failed_attempts": list(failed_attempts),
        "cells": cells,
        "per_prompt_quality": prompt_quality,
        "matrix_summary": summary,
        "verdict": verdict,
        "limitations": [
            "Estimated credits are derived from token telemetry and manifest rates, not billing debits.",
            "A finite declared corpus cannot establish performance across literally every possible prompt.",
            "Hook receipt elapsed_ms measures a fresh hook process; with daemon mode it calls a "
            "separately prewarmed runtime and is not a cold-start measurement.",
            "An incomplete, resumed, or cost-capped matrix supports no aggregate claim.",
        ],
    }


def _validate_config(config: MatrixConfig, *, dry_run: bool) -> None:
    if config.timeout_seconds <= 0:
        raise MatrixError("timeout_seconds must be positive")
    if not config.namespace.strip() or not config.agent_id.strip():
        raise MatrixError("namespace and agent_id must be non-empty")
    required = (
        (config.manifest_path, "manifest"),
        (config.source_db, "source database"),
        (config.agents_file, "candidate AGENTS.md"),
        (config.hook_script, "hook script"),
        (config.hook_python, "hook Python"),
    )
    if not dry_run:
        required += ((config.codex_exe, "Codex executable"),)
    for path, label in required:
        if not path.is_file():
            raise MatrixError(f"missing {label}: {path}")
    if not dry_run:
        if config.raw_dir is None or config.state_path is None:
            raise MatrixError("live execution requires --raw-dir and --state")
        if config.estimated_credit_cap is None or config.estimated_credit_cap <= 0:
            raise MatrixError("live execution requires a positive --estimated-credit-cap")


def _validate_retrieval_config(config: MatrixConfig, manifest: MatrixManifest) -> None:
    if manifest.hook_embedding_backend == "bge_onnx":
        if config.bge_onnx_artifact_dir is None:
            raise MatrixError("BGE ONNX profile requires an artifact directory")
        if not config.bge_onnx_artifact_dir.is_dir():
            raise MatrixError(
                f"missing BGE ONNX artifact directory: {config.bge_onnx_artifact_dir}"
            )
        for name in BGE_ONNX_ARTIFACT_FILES:
            artifact = config.bge_onnx_artifact_dir / name
            if not artifact.is_file():
                raise MatrixError(f"missing BGE ONNX artifact: {artifact}")
    elif config.bge_onnx_artifact_dir is not None:
        raise MatrixError(
            "BGE ONNX artifact directory was supplied for a sentence-transformers profile"
        )

    artifacts = (config.reranker_onnx_model, config.reranker_onnx_tokenizer)
    if manifest.hook_reranker_backend == "off":
        if any(path is not None for path in artifacts):
            raise MatrixError("reranker artifacts were supplied for an off manifest profile")
        return
    if any(path is None for path in artifacts):
        raise MatrixError("ONNX reranker profile requires model and tokenizer artifacts")
    for path, label in zip(artifacts, ("ONNX reranker model", "ONNX tokenizer")):
        assert path is not None
        if not path.is_file():
            raise MatrixError(f"missing {label}: {path}")


def run_matrix(
    config: MatrixConfig,
    *,
    dry_run: bool,
    runner: Runner = run_codex,
    daemon_runner: DaemonRunner = run_daemon_command,
) -> dict[str, Any]:
    _validate_config(config, dry_run=dry_run)
    manifest = load_manifest(config.manifest_path)
    _validate_retrieval_config(config, manifest)
    plan = build_plan(manifest)
    if dry_run:
        return _report(
            config,
            manifest,
            plan,
            [],
            dry_run=True,
            status="dry_run_only",
            resumed_runs=0,
            daemon_sessions=[],
        )

    assert config.raw_dir is not None
    fingerprint = _fingerprint(config, manifest)
    state = _load_state(config, fingerprint, plan)
    completed: list[dict[str, Any]] = list(state["completed_runs"])
    failed_attempts: list[dict[str, Any]] = list(state.get("failed_attempts", []))
    daemon_sessions: list[dict[str, Any]] = list(state.get("daemon_sessions", []))
    state["daemon_sessions"] = daemon_sessions
    resumed_runs = len(completed)
    spent = sum(float(run["estimated_sol_credits"]) for run in completed) + sum(
        float(attempt["reserved_estimated_credits"]) for attempt in failed_attempts
    )
    assert config.state_path is not None
    _atomic_json(config.state_path, state)
    if len(completed) == len(plan):
        return _report(
            config,
            manifest,
            plan,
            completed,
            dry_run=False,
            status="complete",
            resumed_runs=resumed_runs,
            failed_attempts=failed_attempts,
            daemon_sessions=daemon_sessions,
        )
    status = "complete"

    with _temporary_directory("lians-sol-matrix-baseline-") as baseline_dir:
        with _temporary_directory("lians-sol-matrix-candidate-") as candidate_dir:
            if (baseline_dir / "AGENTS.md").exists():  # pragma: no cover - defensive
                raise MatrixError("baseline workspace unexpectedly contains AGENTS.md")
            shutil.copy2(config.agents_file, candidate_dir / "AGENTS.md")
            copied_db = candidate_dir / "matrix.sqlite"
            shutil.copy2(config.source_db, copied_db)
            daemon_runtime_dir = candidate_dir / "hook-daemon-runtime"
            candidate_environment = _candidate_base_environment(
                config,
                manifest,
                candidate_dir=candidate_dir,
                copied_db=copied_db,
                daemon_runtime_dir=(daemon_runtime_dir if config.prewarm_daemon else None),
            )
            daemon_context = (
                _prewarmed_daemon_session(
                    config,
                    candidate_dir=candidate_dir,
                    daemon_runtime_dir=daemon_runtime_dir,
                    candidate_environment=candidate_environment,
                    daemon_runner=daemon_runner,
                    daemon_sessions=daemon_sessions,
                    state=state,
                    state_path=config.state_path,
                )
                if config.prewarm_daemon
                else nullcontext()
            )

            with daemon_context:
                for planned in plan[len(completed) :]:
                    profile: Profile = planned["profile"]
                    if (
                        spent + profile.maximum_estimated_credits_per_run
                        > float(config.estimated_credit_cap) + 1e-12
                    ):
                        status = "estimated_credit_cap_reached"
                        break
                    spec = _make_run_spec(
                        config,
                        manifest,
                        planned,
                        baseline_dir=baseline_dir,
                        candidate_dir=candidate_dir,
                        copied_db=copied_db,
                        candidate_environment=candidate_environment,
                    )
                    attempt_number = 1 + sum(
                        1 for attempt in failed_attempts if attempt.get("run_id") == spec.run_id
                    )
                    raw_path = config.raw_dir / (
                        f"{spec.run_id}.attempt-{attempt_number:03d}.stdout.jsonl"
                    )
                    try:
                        invocation = runner(spec)
                        parsed = _parse_run(spec, invocation, manifest, raw_path)
                    except (MatrixError, BenchmarkError) as exc:
                        failed = {
                            "run_id": spec.run_id,
                            "sequence": spec.sequence,
                            "attempt": attempt_number,
                            "profile_id": profile.profile_id,
                            "prompt_id": spec.prompt_case.prompt_id,
                            "mode": spec.mode,
                            "repetition": spec.repetition,
                            "error": str(exc),
                            "reserved_estimated_credits": (
                                profile.maximum_estimated_credits_per_run
                            ),
                            "raw_stdout_artifact": (
                                str(raw_path.resolve()) if raw_path.is_file() else None
                            ),
                            "raw_stdout_sha256": (
                                _sha256_file(raw_path) if raw_path.is_file() else None
                            ),
                        }
                        failed_attempts.append(failed)
                        state["failed_attempts"] = failed_attempts
                        _atomic_json(config.state_path, state)
                        status = "run_failed_resumable"
                        break
                    completed.append(parsed)
                    spent += float(parsed["estimated_sol_credits"])
                    state["completed_runs"] = completed
                    state["failed_attempts"] = failed_attempts
                    _atomic_json(config.state_path, state)
                    if any("cost bound" in item for item in parsed["violations"]):
                        status = "manifest_run_cost_bound_exceeded"
                        break

    return _report(
        config,
        manifest,
        plan,
        completed,
        dry_run=False,
        status=status,
        resumed_runs=resumed_runs,
        failed_attempts=failed_attempts,
        daemon_sessions=daemon_sessions,
    )


def _discover_codex() -> Path:
    configured = os.environ.get("CODEX_CLI_PATH")
    if configured:
        return Path(configured)
    found = shutil.which("codex")
    if found:
        return Path(found)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates = list((Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        if candidates:
            return max(candidates, key=lambda item: item.stat().st_mtime)
    return Path("codex")


def _discover_hook_python() -> Path:
    candidates = (
        REPO_ROOT / "agentmem" / "sdk" / "python" / ".venv" / "Scripts" / "python.exe",
        REPO_ROOT / "agentmem" / "sdk" / "python" / ".venv" / "bin" / "python",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--codex-exe", type=Path, default=_discover_codex())
    parser.add_argument("--hook-python", type=Path, default=_discover_hook_python())
    parser.add_argument("--agents", dest="agents_file", type=Path, default=DEFAULT_AGENTS)
    parser.add_argument("--hook-script", type=Path, default=DEFAULT_HOOK)
    parser.add_argument("--namespace", default="local")
    parser.add_argument("--agent-id", default="locomo-conv-26")
    parser.add_argument("--embedding-model", default="BAAI/bge-large-en-v1.5")
    parser.add_argument("--bge-onnx-artifact-dir", type=Path)
    parser.add_argument("--reranker-onnx-model", type=Path)
    parser.add_argument("--reranker-onnx-tokenizer", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--state", dest="state_path", type=Path)
    parser.add_argument("--estimated-credit-cap", type=float)
    parser.add_argument(
        "--prewarm-daemon",
        action="store_true",
        help="prewarm one isolated local recall daemon and require daemon receipts",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.resume and args.dry_run:
        parser.error("--resume cannot be combined with --dry-run")
    config = MatrixConfig(
        manifest_path=args.manifest.resolve(),
        source_db=args.db.resolve(),
        codex_exe=args.codex_exe.resolve(),
        hook_python=args.hook_python.resolve(),
        agents_file=args.agents_file.resolve(),
        hook_script=args.hook_script.resolve(),
        namespace=args.namespace.strip(),
        agent_id=args.agent_id.strip(),
        embedding_model=args.embedding_model.strip(),
        bge_onnx_artifact_dir=(
            args.bge_onnx_artifact_dir.resolve() if args.bge_onnx_artifact_dir is not None else None
        ),
        reranker_onnx_model=(
            args.reranker_onnx_model.resolve() if args.reranker_onnx_model is not None else None
        ),
        reranker_onnx_tokenizer=(
            args.reranker_onnx_tokenizer.resolve()
            if args.reranker_onnx_tokenizer is not None
            else None
        ),
        timeout_seconds=args.timeout_seconds,
        raw_dir=args.raw_dir.resolve() if args.raw_dir else None,
        state_path=args.state_path.resolve() if args.state_path else None,
        estimated_credit_cap=args.estimated_credit_cap,
        resume=args.resume,
        prewarm_daemon=args.prewarm_daemon,
    )
    try:
        report = run_matrix(config, dry_run=args.dry_run)
    except MatrixError as exc:
        print(f"matrix benchmark error: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    if report["verdict"]["qualified"] is False:
        return 2
    if not report["complete"] and not args.dry_run:
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
