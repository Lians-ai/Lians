"""Zero-credit LOCOMO evaluation of the Codex production recall profile.

The evaluation applies the checked-out Codex ``UserPromptSubmit`` renderer at
``k=20``, a 768-token-estimate context cap, and ``min_score=0.45``.  It uses
the 1,540 archived category 1--4 prediction artifacts directly.  LOCOMO's 446
category-5 prompts were not archived by the upstream judged protocol, so they
are deterministically replayed from the original SQLite stores and cached
Snowflake Arctic query embeddings.  No language-model or network call occurs.

The report deliberately separates raw top-k, score-eligible pre-cap, and
actually rendered post-cap evidence/answer coverage.  Category-5 evidence
surfacing is retrieval telemetry only; refusal correctness requires a
generation/judge protocol and is not inferred here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import tiktoken


AGENTMEM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = AGENTMEM_ROOT.parent
if str(AGENTMEM_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTMEM_ROOT))
DEFAULT_DATASET = AGENTMEM_ROOT / "benchmarks" / "data" / "locomo10.json"
DEFAULT_PREDICTIONS = (
    WORKSPACE_ROOT / "memory-benchmarks" / "results" / "locomo" / "predicted_lians_arctic"
)
DEFAULT_DB_DIR = AGENTMEM_ROOT / "results" / "locomo_dbs_arctic"
DEFAULT_CACHE_DIR = AGENTMEM_ROOT / "results" / "replay"
DEFAULT_HOOK = WORKSPACE_ROOT / "integrations" / "codex" / "user_prompt_submit_recall.py"
DEFAULT_OUT = (
    WORKSPACE_ROOT / "docs" / "benchmarks" / "locomo-production-profile-corpus-2026-08-08.json"
)

PROFILE_K = 20
PROFILE_MAX_TOKENS = 768
PROFILE_MIN_SCORE = 0.45
ARCTIC_CACHE_STEM = "alt_Snowflake__snowflake-arctic-embed-l-v2.0_conv_{}.npz"
CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}
FROZEN_CONV0_QA_INDICES = (3, 4, 0, 1, 2, 14, 82, 83, 152, 153)
SWEEP_POLICIES: tuple[dict[str, Any], ...] = (
    {"id": "fixed-0.00", "kind": "fixed", "minimum_score": 0.00},
    {"id": "fixed-0.25", "kind": "fixed", "minimum_score": 0.25},
    {"id": "fixed-0.30", "kind": "fixed", "minimum_score": 0.30},
    {"id": "fixed-0.35", "kind": "fixed", "minimum_score": 0.35},
    {"id": "fixed-0.40", "kind": "fixed", "minimum_score": 0.40},
    {"id": "fixed-0.45", "kind": "fixed", "minimum_score": 0.45},
    {"id": "fixed-0.50", "kind": "fixed", "minimum_score": 0.50},
    {"id": "top-3", "kind": "top_n", "n": 3},
    {"id": "top-5", "kind": "top_n", "n": 5},
    {
        "id": "provider-calibrated-top3-gate-p25",
        "kind": "provider_quantile_top_n",
        "n": 3,
        "top_score_gate_quantile": 0.25,
    },
    {
        "id": "provider-calibrated-top3-gate-p50",
        "kind": "provider_quantile_top_n",
        "n": 3,
        "top_score_gate_quantile": 0.50,
    },
    {
        "id": "provider-calibrated-top5-gate-p25",
        "kind": "provider_quantile_top_n",
        "n": 5,
        "top_score_gate_quantile": 0.25,
    },
    {"id": "within-top-0.05", "kind": "top_margin", "margin": 0.05},
    {"id": "within-top-0.10", "kind": "top_margin", "margin": 0.10},
    {"id": "at-least-90pct-of-top", "kind": "top_ratio", "ratio": 0.90},
    {"id": "at-least-80pct-of-top", "kind": "top_ratio", "ratio": 0.80},
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nearest_rank(values: Sequence[int | float], quantile: float) -> int | float | None:
    """Return a deterministic nearest-rank percentile."""

    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _turn_content(turn: Mapping[str, Any]) -> str:
    text = str(turn.get("text") or "").strip()
    caption = str(turn.get("blip_caption") or "").strip()
    speaker = str(turn.get("speaker") or "")
    body = f"{speaker}: {text}" if text else f"{speaker}:"
    if caption:
        body += f" [shared a photo: {caption}]"
    return body


def content_to_dia_ids(conversation: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    mapped: dict[str, list[str]] = defaultdict(list)
    for key, turns in conversation.items():
        if not key.startswith("session_") or not isinstance(turns, list):
            continue
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            mapped[_turn_content(turn)].append(str(turn.get("dia_id") or ""))
    return {content: tuple(ids) for content, ids in mapped.items()}


def load_production_renderer(path: Path) -> ModuleType:
    name = "lians_locomo_production_renderer"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load production renderer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    for attribute in ("Settings", "render_context", "_sanitize"):
        if not hasattr(module, attribute):
            raise RuntimeError(f"production renderer lacks {attribute}")
    return module


def production_settings(renderer: ModuleType) -> Any:
    return renderer.Settings(
        url="",
        api_key="",
        local_db="",
        namespace="locomo-production-profile",
        agent_id="locomo-production-profile",
        k=PROFILE_K,
        max_tokens=PROFILE_MAX_TOKENS,
        min_score=PROFILE_MIN_SCORE,
        receipt_path="",
        backend="local",
    )


def policy_minimum_score(
    policy: Mapping[str, Any], ranked_items: Sequence[Mapping[str, Any]]
) -> float:
    """Resolve a fixed or per-query threshold without changing rank order."""

    scores = sorted((float(item.get("score") or 0.0) for item in ranked_items), reverse=True)
    if not scores:
        return 1.0
    kind = str(policy["kind"])
    if kind == "fixed":
        return float(policy["minimum_score"])
    if kind == "top_n":
        n = max(1, int(policy["n"]))
        return scores[min(n, len(scores)) - 1]
    if kind == "provider_quantile_top_n":
        gate = float(policy["calibrated_top_score_gate"])
        if scores[0] < gate:
            return math.nextafter(scores[0], math.inf)
        n = max(1, int(policy["n"]))
        return scores[min(n, len(scores)) - 1]
    if kind == "top_margin":
        return max(0.0, scores[0] - float(policy["margin"]))
    if kind == "top_ratio":
        return max(0.0, scores[0] * float(policy["ratio"]))
    raise ValueError(f"unknown threshold policy kind: {kind}")


def _answer_present(answer: Any, texts: Iterable[str]) -> bool | None:
    if answer is None:
        return None
    needle = str(answer).strip().lower()
    if not needle:
        return None
    return any(needle in text.lower() for text in texts)


def _evidence_flags(
    evidence: Sequence[str], items: Sequence[Mapping[str, Any]]
) -> tuple[bool, bool] | tuple[None, None]:
    if not evidence:
        return None, None
    got = {str(item.get("dia_id") or "") for item in items}
    return any(value in got for value in evidence), all(value in got for value in evidence)


def evaluate_prompt(
    *,
    renderer: ModuleType,
    settings: Any,
    encoder: Any,
    question_id: str,
    conversation_idx: int,
    qa_index: int,
    qa: Mapping[str, Any],
    source: str,
    ranked_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply the production renderer and preserve each filtering boundary."""

    raw = list(ranked_items[: settings.k])
    eligible = [
        item
        for item in raw
        if float(item.get("score") or 0.0) >= settings.min_score
        and renderer._sanitize(item.get("content"), (settings.api_key,))
    ]
    render_input = [
        {
            "content": item.get("content", ""),
            "event_time": item.get("event_time", ""),
            "score": item.get("score"),
        }
        for item in raw
    ]
    context, included, truncated, top_score, eligible_count = renderer.render_context(
        render_input, settings
    )
    after_cap = eligible[:included]
    if context:
        status = "injected"
    elif not raw:
        status = "no_match"
    elif not eligible_count:
        status = "below_threshold"
    else:
        status = "skipped_budget"

    evidence = [str(value) for value in qa.get("evidence") or []]
    raw_any, raw_all = _evidence_flags(evidence, raw)
    before_any, before_all = _evidence_flags(evidence, eligible)
    after_any, after_all = _evidence_flags(evidence, after_cap)
    raw_text = [str(item.get("content") or "") for item in raw]
    before_text = [str(item.get("content") or "") for item in eligible]
    after_text = [context] if context else []
    answer = qa.get("answer")
    adversarial_answer = qa.get("adversarial_answer")

    return {
        "question_id": question_id,
        "conversation_idx": conversation_idx,
        "qa_index": qa_index,
        "category": int(qa.get("category", 0)),
        "category_name": CATEGORY_NAMES.get(int(qa.get("category", 0)), "unknown"),
        "source": source,
        "minimum_score_applied": round(float(settings.min_score), 6),
        "status": status,
        "injected": bool(context),
        "truncated": bool(truncated),
        "top_score": round(float(top_score), 6) if top_score is not None else None,
        "raw_topk_count": len(raw),
        "eligible_before_char_cap_count": len(eligible),
        "rendered_after_char_cap_count": len(after_cap),
        "returned_chars": len(context),
        "returned_token_estimate_char_div_4": (len(context) + 3) // 4 if context else 0,
        "returned_tokens_o200k": len(encoder.encode(context)) if context else 0,
        "evidence_count": len(evidence),
        "evidence_any_raw_topk": raw_any,
        "evidence_all_raw_topk": raw_all,
        "evidence_any_before_char_cap": before_any,
        "evidence_all_before_char_cap": before_all,
        "evidence_any_after_char_cap": after_any,
        "evidence_all_after_char_cap": after_all,
        "answer_string_available": _answer_present(answer, [str(answer or "")]) is not None,
        "answer_string_raw_topk": _answer_present(answer, raw_text),
        "answer_string_before_char_cap": _answer_present(answer, before_text),
        "answer_string_after_char_cap": _answer_present(answer, after_text),
        "adversarial_answer_string_available": _answer_present(
            adversarial_answer, [str(adversarial_answer or "")]
        )
        is not None,
        "adversarial_answer_string_raw_topk": _answer_present(adversarial_answer, raw_text),
        "adversarial_answer_string_before_char_cap": _answer_present(
            adversarial_answer, before_text
        ),
        "adversarial_answer_string_after_char_cap": _answer_present(adversarial_answer, after_text),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row["status"]) for row in rows)

    def boolean_metric(name: str) -> dict[str, Any]:
        values = [bool(row[name]) for row in rows if row.get(name) is not None]
        passed = sum(values)
        return {"n": len(values), "count": passed, "rate": _rate(passed, len(values))}

    token_exact = [int(row["returned_tokens_o200k"]) for row in rows if row["injected"]]
    token_estimate = [
        int(row["returned_token_estimate_char_div_4"]) for row in rows if row["injected"]
    ]
    injected = statuses.get("injected", 0)
    truncated = sum(bool(row["truncated"]) for row in rows)
    return {
        "n": len(rows),
        "injection": {
            "injected": injected,
            "skipped": len(rows) - injected,
            "injection_rate": _rate(injected, len(rows)),
            "status_counts": dict(sorted(statuses.items())),
            "truncated": truncated,
            "truncation_rate": _rate(truncated, len(rows)),
        },
        "evidence": {
            name: boolean_metric(name)
            for name in (
                "evidence_any_raw_topk",
                "evidence_all_raw_topk",
                "evidence_any_before_char_cap",
                "evidence_all_before_char_cap",
                "evidence_any_after_char_cap",
                "evidence_all_after_char_cap",
            )
        },
        "answer_string_coverage": {
            name: boolean_metric(name)
            for name in (
                "answer_string_raw_topk",
                "answer_string_before_char_cap",
                "answer_string_after_char_cap",
            )
        },
        "adversarial_answer_string_coverage": {
            name: boolean_metric(name)
            for name in (
                "adversarial_answer_string_raw_topk",
                "adversarial_answer_string_before_char_cap",
                "adversarial_answer_string_after_char_cap",
            )
        },
        "returned_context_tokens_injected_only": {
            "tokenizer": "o200k_base",
            "n": len(token_exact),
            "p50": nearest_rank(token_exact, 0.50),
            "p95": nearest_rank(token_exact, 0.95),
            "p99": nearest_rank(token_exact, 0.99),
            "max": max(token_exact) if token_exact else None,
        },
        "hook_char_div_4_estimate_injected_only": {
            "n": len(token_estimate),
            "p50": nearest_rank(token_estimate, 0.50),
            "p95": nearest_rank(token_estimate, 0.95),
            "p99": nearest_rank(token_estimate, 0.99),
            "max": max(token_estimate) if token_estimate else None,
        },
    }


def evaluate_policy_sweep(
    *,
    renderer: ModuleType,
    base_settings: Any,
    encoder: Any,
    cases: Sequence[Mapping[str, Any]],
    policies: Sequence[Mapping[str, Any]] = SWEEP_POLICIES,
) -> list[dict[str, Any]]:
    """Evaluate score-scale alternatives over already-ranked, model-free cases."""

    output: list[dict[str, Any]] = []
    top_scores = [
        max(float(item.get("score") or 0.0) for item in case["ranked_items"])
        for case in cases
        if case["ranked_items"]
    ]
    for policy in policies:
        resolved_policy = dict(policy)
        if policy["kind"] == "provider_quantile_top_n":
            resolved_policy["calibrated_top_score_gate"] = round(
                float(nearest_rank(top_scores, float(policy["top_score_gate_quantile"]))),
                6,
            )
        rows: list[dict[str, Any]] = []
        effective_thresholds: list[float] = []
        for case in cases:
            threshold = policy_minimum_score(resolved_policy, case["ranked_items"])
            effective_thresholds.append(threshold)
            rows.append(
                evaluate_prompt(
                    renderer=renderer,
                    settings=replace(base_settings, min_score=threshold),
                    encoder=encoder,
                    question_id=str(case["question_id"]),
                    conversation_idx=int(case["conversation_idx"]),
                    qa_index=int(case["qa_index"]),
                    qa=case["qa"],
                    source=str(case["source"]),
                    ranked_items=case["ranked_items"],
                )
            )
        answerable = [row for row in rows if row["category"] in {1, 2, 3, 4}]
        adversarial = [row for row in rows if row["category"] == 5]
        policy_result = dict(resolved_policy)
        policy_result.update(
            effective_minimum_score={
                "p50": round(float(nearest_rank(effective_thresholds, 0.50)), 6),
                "p95": round(float(nearest_rank(effective_thresholds, 0.95)), 6),
                "min": round(min(effective_thresholds), 6),
                "max": round(max(effective_thresholds), 6),
            },
            answerable_categories_1_to_4=summarize(answerable),
            adversarial_category_5=summarize(adversarial),
        )
        output.append(policy_result)
    return output


class ArcticReplay:
    """Deterministic replica used only for the non-archived category-5 slice."""

    def __init__(
        self,
        *,
        conversation_idx: int,
        sample: Mapping[str, Any],
        db_path: Path,
        cache_path: Path,
    ) -> None:
        # Import checked-in benchmark primitives only after the caller has
        # chosen replay mode; importing them cannot perform network/model calls.
        from benchmarks.locomo_dump_mem0 import _bm25_scores, _load_docs
        from lians.ranking import _bm25_tokens, query_time_windows

        self._bm25_scores = _bm25_scores
        self._query_time_windows = query_time_windows
        db_ids, db_contents, db_embeddings, db_times, db_dia_ids = _load_docs(db_path, "live_facts")
        cache = np.load(cache_path)
        self.query_embeddings = cache["q_pre"].astype(np.float32)
        cached_docs = cache["doc_embs"].astype(np.float32)
        snapshot = json.loads(
            (cache_path.parent / f"conv_{conversation_idx}.meta.json").read_text(encoding="utf-8")
        )["docs"]
        if len(snapshot) != len(cached_docs):
            raise ValueError(
                f"cache/snapshot document shape mismatch for conversation {conversation_idx}"
            )
        current_by_identity = {
            (db_dia_ids[index], db_contents[index]): index for index in range(len(db_contents))
        }
        mapped = [
            current_by_identity.get((str(item["dia_id"]), str(item["content"])))
            for item in snapshot
        ]
        if any(index is None for index in mapped):
            raise ValueError(
                f"snapshot documents missing from SQLite conversation {conversation_idx}"
            )
        current_indexes = [int(index) for index in mapped if index is not None]
        self.ids = [db_ids[index] for index in current_indexes]
        self.contents = [str(item["content"]) for item in snapshot]
        self.doc_embs = cached_docs
        self.times = [db_times[index] for index in current_indexes]
        self.dia_ids = [str(item["dia_id"]) for item in snapshot]
        current_embeddings = db_embeddings[current_indexes]
        self.max_cached_document_delta = float(np.max(np.abs(cached_docs - current_embeddings)))
        self.current_db_rows_excluded_from_snapshot = len(db_contents) - len(snapshot)
        self.qa_with_evidence = [
            (index, qa) for index, qa in enumerate(sample["qa"]) if qa.get("evidence")
        ]
        if len(self.qa_with_evidence) != len(self.query_embeddings):
            raise ValueError(
                f"cache/dataset question shape mismatch for conversation {conversation_idx}"
            )
        self.query_row_by_qa_index = {
            qa_index: row for row, (qa_index, _qa) in enumerate(self.qa_with_evidence)
        }
        self.timestamps = np.array(
            [
                dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                if value
                else 0.0
                for value in self.times
            ],
            dtype=np.float64,
        )
        self.doc_tf: list[dict[str, int]] = []
        self.doc_len: list[int] = []
        for content in self.contents:
            tokens = _bm25_tokens(content)
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            self.doc_tf.append(counts)
            self.doc_len.append(len(tokens))

    def rank(self, *, qa_index: int, question: str, k: int) -> list[dict[str, Any]]:
        row = self.query_row_by_qa_index[qa_index]
        semantic = self.doc_embs @ self.query_embeddings[row]
        neighbor_best = np.zeros_like(semantic)
        for index in range(len(semantic)):
            for neighbor in (index - 1, index + 1):
                if (
                    0 <= neighbor < len(semantic)
                    and abs(self.timestamps[index] - self.timestamps[neighbor]) <= 3600
                    and semantic[neighbor] > neighbor_best[index]
                ):
                    neighbor_best[index] = semantic[neighbor]
        scores = 0.50 * (semantic + 0.30 * neighbor_best) + 0.05 * self._bm25_scores(
            question, self.doc_tf, self.doc_len
        )
        windows = self._query_time_windows(question)
        if windows:
            scores = scores + np.array(
                [
                    0.1 if any(lower <= value <= upper for lower, upper in windows) else 0.0
                    for value in self.timestamps
                ],
                dtype=np.float32,
            )
        order = np.argsort(-scores, kind="stable")[:k]
        return [
            {
                "content": self.contents[index],
                "score": round(float(scores[index]), 6),
                "event_time": self.times[index],
                "dia_id": self.dia_ids[index],
            }
            for index in order
        ]


def _artifact_items(
    document: Mapping[str, Any], content_map: Mapping[str, tuple[str, ...]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for result in document["retrieval"]["search_results"][:PROFILE_K]:
        content = str(result.get("memory") or "")
        matches = content_map.get(content, ())
        if len(matches) != 1:
            raise ValueError(
                f"artifact content must map to exactly one dialogue id; got {len(matches)}"
            )
        output.append(
            {
                "content": content,
                "score": float(result.get("score") or 0.0),
                "event_time": str(result.get("created_at") or ""),
                "dia_id": matches[0],
            }
        )
    return output


def _schema_preflight(db_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(db_path)
    try:
        memory_columns = {row[1] for row in connection.execute("pragma table_info(memories)")}
        policy_columns = {
            row[1] for row in connection.execute("pragma table_info(namespace_policies)")
        }
    finally:
        connection.close()
    required_memory = {"system_valid_from", "system_valid_to"}
    required_policy = {"governance_status"}
    missing = sorted((required_memory - memory_columns) | (required_policy - policy_columns))
    return {
        "current_sdk_live_recall_compatible": not missing,
        "missing_current_schema_columns": missing,
    }


def run_evaluation(
    *,
    dataset_path: Path,
    predictions_dir: Path,
    db_dir: Path,
    cache_dir: Path,
    hook_path: Path,
) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(dataset, list) or len(dataset) != 10:
        raise ValueError("expected the ten-conversation LOCOMO dataset")
    renderer = load_production_renderer(hook_path)
    settings = production_settings(renderer)
    encoder = tiktoken.get_encoding("o200k_base")
    rows: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    artifact_manifest = hashlib.sha256()
    artifact_count = 0
    replay_fidelity: list[dict[str, Any]] = []
    cache_deltas: list[float] = []
    db_rows_excluded_from_snapshot: list[int] = []

    for conversation_idx, sample in enumerate(dataset):
        content_map = content_to_dia_ids(sample["conversation"])
        replay = ArcticReplay(
            conversation_idx=conversation_idx,
            sample=sample,
            db_path=db_dir / f"conv_{conversation_idx}.sqlite",
            cache_path=cache_dir / ARCTIC_CACHE_STEM.format(conversation_idx),
        )
        cache_deltas.append(replay.max_cached_document_delta)
        db_rows_excluded_from_snapshot.append(replay.current_db_rows_excluded_from_snapshot)
        validated_categories: set[int] = set()

        for qa_index, qa in enumerate(sample["qa"]):
            category = int(qa.get("category", 0))
            question_id = f"conv{conversation_idx}_q{qa_index}"
            if category in {1, 2, 3, 4}:
                artifact_path = predictions_dir / f"{question_id}.json"
                raw = artifact_path.read_bytes()
                artifact_manifest.update(artifact_path.name.encode("utf-8"))
                artifact_manifest.update(hashlib.sha256(raw).digest())
                document = json.loads(raw)
                if document.get("question") != qa.get("question"):
                    raise ValueError(f"artifact/dataset question mismatch: {question_id}")
                ranked = _artifact_items(document, content_map)
                source = "archived_prediction_artifact"
                artifact_count += 1

                # One prompt per available answerable category and conversation
                # checks how closely the category-5 replay recipe matches the
                # archived ranking surface.  This is a limitation measurement,
                # not a substitution for the artifacts above.
                if qa.get("evidence") and category not in validated_categories:
                    replayed = replay.rank(
                        qa_index=qa_index, question=str(qa["question"]), k=PROFILE_K
                    )
                    positional = sum(
                        left["content"] == right["content"] for left, right in zip(ranked, replayed)
                    )
                    overlap = len(
                        {item["content"] for item in ranked}
                        & {item["content"] for item in replayed}
                    )
                    score_matches = sum(
                        left["score"] == right["score"] for left, right in zip(ranked, replayed)
                    )
                    replay_fidelity.append(
                        {
                            "question_id": question_id,
                            "category": category,
                            "top20_positional_content_matches": positional,
                            "top20_content_set_overlap": overlap,
                            "top20_positional_score_matches_at_6dp": score_matches,
                        }
                    )
                    validated_categories.add(category)
            elif category == 5:
                ranked = replay.rank(qa_index=qa_index, question=str(qa["question"]), k=PROFILE_K)
                source = "deterministic_cached_arctic_replay"
            else:
                raise ValueError(f"unknown LOCOMO category {category}: {question_id}")

            cases.append(
                {
                    "question_id": question_id,
                    "conversation_idx": conversation_idx,
                    "qa_index": qa_index,
                    "qa": qa,
                    "source": source,
                    "ranked_items": ranked,
                }
            )
            rows.append(
                evaluate_prompt(
                    renderer=renderer,
                    settings=settings,
                    encoder=encoder,
                    question_id=question_id,
                    conversation_idx=conversation_idx,
                    qa_index=qa_index,
                    qa=qa,
                    source=source,
                    ranked_items=ranked,
                )
            )

    if artifact_count != 1540:
        raise ValueError(f"expected 1,540 category 1-4 artifacts, found {artifact_count}")
    category5_count = sum(row["category"] == 5 for row in rows)
    if category5_count != 446:
        raise ValueError(f"expected 446 category-5 prompts, found {category5_count}")

    by_category = {
        f"{category}-{CATEGORY_NAMES[category]}": summarize(
            [row for row in rows if row["category"] == category]
        )
        for category in sorted(CATEGORY_NAMES)
    }
    answerable = [row for row in rows if row["category"] in {1, 2, 3, 4}]
    adversarial = [row for row in rows if row["category"] == 5]
    positional_values = [
        int(value["top20_positional_content_matches"]) for value in replay_fidelity
    ]
    overlap_values = [int(value["top20_content_set_overlap"]) for value in replay_fidelity]
    score_values = [
        int(value["top20_positional_score_matches_at_6dp"]) for value in replay_fidelity
    ]
    schema = _schema_preflight(db_dir / "conv_0.sqlite")
    policy_sweep = evaluate_policy_sweep(
        renderer=renderer,
        base_settings=settings,
        encoder=encoder,
        cases=cases,
    )
    policy_by_id = {str(policy["id"]): policy for policy in policy_sweep}
    top3 = policy_by_id["top-3"]
    fixed_045 = policy_by_id["fixed-0.45"]
    fixed_zero = policy_by_id["fixed-0.00"]
    calibrated_p25 = policy_by_id["provider-calibrated-top3-gate-p25"]
    cases_by_identity = {
        (int(case["conversation_idx"]), int(case["qa_index"])): case for case in cases
    }
    frozen_profiles: dict[str, dict[str, Any]] = {}
    for frozen_k in (3, 5, 20):
        frozen_rows = [
            evaluate_prompt(
                renderer=renderer,
                settings=replace(settings, k=frozen_k, min_score=0.0),
                encoder=encoder,
                question_id=str(cases_by_identity[(0, qa_index)]["question_id"]),
                conversation_idx=0,
                qa_index=qa_index,
                qa=cases_by_identity[(0, qa_index)]["qa"],
                source=str(cases_by_identity[(0, qa_index)]["source"]),
                ranked_items=cases_by_identity[(0, qa_index)]["ranked_items"],
            )
            for qa_index in FROZEN_CONV0_QA_INDICES
        ]
        frozen_answerable = [row for row in frozen_rows if row["category"] in {1, 2, 3, 4}]
        frozen_adversarial = [row for row in frozen_rows if row["category"] == 5]
        frozen_profiles[f"top{frozen_k}-min0"] = {
            "profile": {"k": frozen_k, "minimum_score": 0.0},
            "all_10": summarize(frozen_rows),
            "answerable_8": summarize(frozen_answerable),
            "adversarial_2": summarize(frozen_adversarial),
            "returned_tokens_o200k_range": {
                "min": min(row["returned_tokens_o200k"] for row in frozen_rows),
                "max": max(row["returned_tokens_o200k"] for row in frozen_rows),
            },
            "detail": frozen_rows,
        }

    return {
        "schema_version": "lians.locomo-production-profile-corpus.v1",
        "benchmark": "LOCOMO production-profile zero-model-credit corpus evaluation",
        "corpus": {
            "conversations": len(dataset),
            "all_prompts": len(rows),
            "category_1_to_4_archived_artifacts": artifact_count,
            "category_5_deterministic_replays": category5_count,
            "prompts_with_evidence": sum(bool(row["evidence_count"]) for row in rows),
        },
        "profile": {
            "k": settings.k,
            "max_token_estimate": settings.max_tokens,
            "char_budget": settings.max_tokens * 4,
            "minimum_score": settings.min_score,
            "renderer": str(hook_path.resolve()),
            "renderer_sha256": sha256_file(hook_path),
            "tokenizer": "o200k_base",
            "token_percentile_method": "nearest-rank over injected additionalContext only",
        },
        "provenance": {
            "dataset": str(dataset_path.resolve()),
            "dataset_sha256": sha256_file(dataset_path),
            "prediction_directory": str(predictions_dir.resolve()),
            "prediction_artifact_manifest_sha256": artifact_manifest.hexdigest(),
            "database_directory": str(db_dir.resolve()),
            "query_cache_directory": str(cache_dir.resolve()),
            "maximum_cached_vs_sqlite_document_embedding_absolute_delta": max(cache_deltas),
            "current_sqlite_rows_excluded_to_preserve_cached_snapshot": sum(
                db_rows_excluded_from_snapshot
            ),
            "external_model_calls": 0,
            "network_calls": 0,
        },
        "results": {
            "all_prompts": summarize(rows),
            "answerable_categories_1_to_4": summarize(answerable),
            "adversarial_category_5": summarize(adversarial),
            "by_category": by_category,
        },
        "replay_fidelity_validation": {
            "n": len(replay_fidelity),
            "selection": "first evidence-bearing prompt per available category per conversation",
            "top20_positional_content_match_rate": _rate(
                sum(positional_values), 20 * len(positional_values)
            ),
            "top20_content_set_overlap_rate": _rate(sum(overlap_values), 20 * len(overlap_values)),
            "top20_positional_score_match_rate_at_6dp": _rate(
                sum(score_values), 20 * len(score_values)
            ),
            "detail": replay_fidelity,
        },
        "threshold_policy_sweep": {
            "zero_credit": True,
            "rankings_held_constant": True,
            "fixed_policy_warning": (
                "Fixed thresholds are specific to the archived/replayed Arctic score scale. "
                "Top-N, top-margin, and top-ratio rows are offline policy alternatives that "
                "would require an explicit production implementation and validation."
            ),
            "policies": policy_sweep,
        },
        "production_recommendation_before_paid_validation": {
            "recommended_candidate": "top-3",
            "status": "advance_to_paid_validation_not_approved_for_universal_shipping",
            "policy": (
                "Use rank-only top-3 injection with no universal absolute score floor; keep the "
                "existing untrusted-evidence instruction and validate false-premise behavior in "
                "the paid Sol matrix."
            ),
            "measured_tradeoff": {
                "answerable_injection_rate": top3["answerable_categories_1_to_4"]["injection"][
                    "injection_rate"
                ],
                "answerable_evidence_any_after_cap": top3["answerable_categories_1_to_4"][
                    "evidence"
                ]["evidence_any_after_char_cap"]["rate"],
                "answerable_evidence_all_after_cap": top3["answerable_categories_1_to_4"][
                    "evidence"
                ]["evidence_all_after_char_cap"]["rate"],
                "answerable_returned_tokens_o200k_p95": top3["answerable_categories_1_to_4"][
                    "returned_context_tokens_injected_only"
                ]["p95"],
                "answerable_returned_tokens_o200k_p99": top3["answerable_categories_1_to_4"][
                    "returned_context_tokens_injected_only"
                ]["p99"],
                "answerable_returned_tokens_o200k_max": top3["answerable_categories_1_to_4"][
                    "returned_context_tokens_injected_only"
                ]["max"],
                "category5_adversarial_answer_string_exposure": top3["adversarial_category_5"][
                    "adversarial_answer_string_coverage"
                ]["adversarial_answer_string_after_char_cap"]["rate"],
            },
            "why": [
                (
                    "It restores answerable evidence-any by "
                    f"{round((top3['answerable_categories_1_to_4']['evidence']['evidence_any_after_char_cap']['rate'] - fixed_045['answerable_categories_1_to_4']['evidence']['evidence_any_after_char_cap']['rate']) * 100, 2)} "
                    "percentage points versus fixed 0.45."
                ),
                (
                    "It depends on rank rather than one backend's absolute score scale and had "
                    "no truncation in this corpus."
                ),
                (
                    "Its exact o200k p99 payload is materially below the nominal 768-token "
                    "budget, unlike low fixed thresholds that fill the character cap."
                ),
            ],
            "required_paid_gates": [
                "Do not accept a quality regression versus baseline on answerable prompts.",
                "Explicitly score category-5 false-premise/refusal behavior; retrieval exposure alone is not correctness.",
                "Require measured same-budget usage extension and latency gates for every Sol profile.",
                "Enforce an exact model-tokenizer hard cap before universal production rollout.",
            ],
            "not_recommended": {
                "fixed-0.45": "Artifact-scale recall is too low and the score is not portable.",
                "fixed-0.00": (
                    "It truncates nearly every answerable context and reaches "
                    f"{fixed_zero['answerable_categories_1_to_4']['returned_context_tokens_injected_only']['max']} "
                    "exact o200k tokens despite the nominal 768 estimate."
                ),
                "provider-calibrated-top3-gate-p25": (
                    "Useful as a conservative matrix comparator, but on this corpus it injects "
                    f"{round(calibrated_p25['answerable_categories_1_to_4']['injection']['injection_rate'] * 100, 2)}% "
                    "of answerable prompts versus "
                    f"{round(calibrated_p25['adversarial_category_5']['injection']['injection_rate'] * 100, 2)}% "
                    "of category 5, so top-score confidence does not separate false premises. "
                    "Its numeric gate is artifact/provider-specific and must not be copied globally."
                ),
            },
        },
        "frozen_conv0_paid_manifest_rank_depth_audit": {
            "qa_indices_in_manifest_order": list(FROZEN_CONV0_QA_INDICES),
            "profiles": frozen_profiles,
            "limiting_evidence_ranks": {
                "qa4": {"gold": ["D1:5"], "ranks": [88]},
                "qa1": {"gold": ["D1:12"], "ranks": [30]},
                "qa14": {"gold": ["D4:15", "D3:5"], "ranks": [1, 75]},
            },
            "assessment": (
                "Top-3, top-5, and capped top-20 all inject ten of ten and have identical "
                "coverage: evidence-any for six of eight answerable prompts, evidence-all for "
                "five of eight, and adversarial evidence for one of two category-5 prompts. "
                "Top-20 truncates every case and adds tokens without recovering QA4, QA1, or "
                "QA14's second evidence because those records rank 88, 30, and 75."
            ),
        },
        "limitations": {
            "category_5": (
                "The upstream judged run archived categories 1-4 only. Category 5 uses a "
                "deterministic replay from the original SQLite contents/embeddings and cached "
                "Arctic query embeddings. Replay fidelity is quantified above; category-5 "
                "numbers are not artifact-exact where that check is below 100%."
            ),
            "absolute_score_calibration": (
                "The 0.45 threshold is applied exactly to the archived/replayed Arctic scores, "
                "but those July scores are not proven to share the current production SDK's "
                "absolute score calibration. Injection and threshold-loss rates are therefore "
                "an artifact-profile audit, not a claim about current live-corpus injection."
            ),
            "adversarial_interpretation": (
                "Category-5 evidence and adversarial-answer substring coverage measure what "
                "retrieval exposed, not whether a model would correctly refuse the false premise."
            ),
            "renderer": (
                "The checked-out production render_context function was executed for every "
                "prompt, so threshold/cap/injection and returned-context measurements are exact "
                "for the recorded renderer SHA."
            ),
            "current_sdk_live_recall": {
                **schema,
                "status": (
                    "not_run_read_only_checkpoint_schema_preflight_failed"
                    if not schema["current_sdk_live_recall_compatible"]
                    else "schema_preflight_passed_but_not_required"
                ),
                "note": (
                    "The July Arctic checkpoints predate current system-validity/governance "
                    "columns. They were not mutated; direct current-SDK recall is therefore not "
                    "claimed. Raw SQLite rows are read-only replay inputs."
                ),
            },
        },
        "detail": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--hook", type=Path, default=DEFAULT_HOOK)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    report = run_evaluation(
        dataset_path=args.dataset,
        predictions_dir=args.predictions,
        db_dir=args.db_dir,
        cache_dir=args.cache_dir,
        hook_path=args.hook,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    all_results = report["results"]["all_prompts"]
    answerable = report["results"]["answerable_categories_1_to_4"]
    print(
        f"wrote {args.out} | prompts={report['corpus']['all_prompts']} | "
        f"injected={all_results['injection']['injected']} | "
        f"answerable evidence-any after cap="
        f"{answerable['evidence']['evidence_any_after_char_cap']['rate']}"
    )


if __name__ == "__main__":
    main()
