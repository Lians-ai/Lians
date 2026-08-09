"""Secondary, posthoc semantic audit for a completed Codex Sol matrix.

This module deliberately does not alter or replace the matrix's predeclared
exact-match quality verdict.  The workflow has two separate phases:

1. ``freeze-rubric`` builds and hashes a rubric using only the selected manifest
   questions, reference answers, adversarial answers, and categories.
2. ``audit`` verifies that frozen rubric, verifies every report/raw-JSONL answer,
   blinds arm/profile metadata, deduplicates and deterministically shuffles the
   question/answer units, applies narrow deterministic alias rules, and sends
   only unresolved units to an optional pinned, tool-free Claude CLI judge.

The resulting report is explicitly secondary/posthoc and cannot qualify a matrix
that failed its primary predeclared criterion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


RUBRIC_SCHEMA = "lians.codex-sol-semantic-rubric.v1"
RUBRIC_PAYLOAD_SCHEMA = "lians.codex-sol-semantic-rubric-payload.v1"
JUDGE_ARTIFACT_SCHEMA = "lians.codex-sol-semantic-judge-artifact.v1"
AUDIT_REPORT_SCHEMA = "lians.codex-sol-semantic-audit-report.v1"
MATRIX_REPORT_SCHEMA = "lians.codex-sol-prompt-matrix-report.v1"
MATRIX_MANIFEST_SCHEMA = "lians.codex-sol-prompt-matrix-manifest.v1"

AUDIT_LABEL = "secondary_posthoc_semantic_audit"
MAX_CLAUDE_BUDGET_USD = Decimal("0.10")
DEFAULT_CLAUDE_BUDGET_USD = Decimal("0.05")
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5-20251001"

GLOBAL_CRITERIA = (
    "Pass only when the answer gives the correct substantive answer to the question, "
    "allowing ordinary wording, grammatical, date-format, and obvious lexical aliases.",
    "Every additional factual claim must be both true in the supplied raw evidence and "
    "relevant to the question; an unsupported, false, transferred, or irrelevant extra "
    "claim makes the whole answer fail.",
    "Do not reward partial overlap when the answer omits a substantive part of the "
    "reference answer or changes its certainty.",
    "For category 5, pass only the exact string UNKNOWN. Any other spelling, framing, "
    "explanation, or transfer of the adversarial answer fails.",
)

FORBIDDEN_BLIND_KEYS = {
    "mode",
    "profile_id",
    "reasoning_effort",
    "service_tier",
    "run_id",
    "order_variant",
    "repetition",
    "sequence",
}

UNCERTAINTY_PREFIXES = (
    "unclear",
    "it is unclear",
    "it's unclear",
    "it’s unclear",
    "unknown",
    "cannot tell",
    "can't tell",
    "can’t tell",
)

DECISION_REASONS = {
    "correct",
    "incorrect",
    "unsupported_extra_claim",
    "irrelevant_extra_claim",
    "incomplete",
    "contradicts_reference",
    "adversarial_transfer",
    "other",
}


class SemanticAuditError(RuntimeError):
    """Raised when an audit input or judge artifact violates the contract."""


@dataclass(frozen=True)
class Observation:
    run_id: str
    prompt_id: str
    answer: str
    mode: str
    profile_id: str
    repetition: int
    exact_match: bool
    raw_name: str
    raw_sha256: str


@dataclass(frozen=True)
class JudgmentUnit:
    blind_case_id: str
    rubric_case_id: str
    prompt_id: str
    answer_id: str
    answer: str
    question: str
    category: int
    ground_truth: str
    accepted_answers: tuple[str, ...]
    denied_answers: tuple[str, ...]
    evidence: tuple[dict[str, str], ...]


ClaudeRunner = Callable[[Sequence[str], str, float], subprocess.CompletedProcess[str]]


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticAuditError(f"could not read {label} {path}: {exc}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SemanticAuditError(f"{label} must be a JSON object")
    return value


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticAuditError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "an array" if allow_empty else "a non-empty array"
        raise SemanticAuditError(f"{label} must be {qualifier} of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_nonempty_text(item, f"{label}[{index}]"))
    return result


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _dataset_record(dataset: Any, conversation_index: int) -> Mapping[str, Any]:
    if not isinstance(dataset, list):
        raise SemanticAuditError("LOCOMO dataset must be a JSON array")
    if conversation_index < 0 or conversation_index >= len(dataset):
        raise SemanticAuditError(f"conversation_index {conversation_index} is out of range")
    record = dataset[conversation_index]
    if not isinstance(record, Mapping):
        raise SemanticAuditError("selected LOCOMO conversation must be an object")
    if not isinstance(record.get("qa"), list):
        raise SemanticAuditError("selected LOCOMO conversation has no qa array")
    return record


def _reference_text(value: Any, label: str) -> str:
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float)):
        raise SemanticAuditError(f"{label} must be a scalar reference answer")
    rendered = str(value).strip()
    if not rendered:
        raise SemanticAuditError(f"{label} must not be empty")
    return rendered


def build_rubric(manifest_path: Path, dataset_path: Path) -> dict[str, Any]:
    """Build the frozen rubric without reading matrix outputs or answer strings."""

    manifest = _object(_load_json(manifest_path, "manifest"), "manifest")
    if manifest.get("schema_version") != MATRIX_MANIFEST_SCHEMA:
        raise SemanticAuditError(f"manifest schema_version must be {MATRIX_MANIFEST_SCHEMA}")
    dataset = _load_json(dataset_path, "LOCOMO dataset")

    contexts_raw = manifest.get("contexts")
    if not isinstance(contexts_raw, list) or not contexts_raw:
        raise SemanticAuditError("manifest contexts must be a non-empty array")
    contexts: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for index, context_raw in enumerate(contexts_raw):
        if not isinstance(context_raw, Mapping):
            raise SemanticAuditError(f"contexts[{index}] must be an object")
        context_id = _nonempty_text(context_raw.get("id"), f"contexts[{index}].id")
        conversation_index = context_raw.get("conversation_index")
        if isinstance(conversation_index, bool) or not isinstance(conversation_index, int):
            raise SemanticAuditError(f"contexts[{index}].conversation_index must be an integer")
        contexts[context_id] = (
            conversation_index,
            _dataset_record(dataset, conversation_index),
        )

    prompt_rows = manifest.get("dataset_prompts")
    if not isinstance(prompt_rows, list) or not prompt_rows:
        raise SemanticAuditError("manifest dataset_prompts must be a non-empty array")

    cases: list[dict[str, Any]] = []
    seen_prompt_ids: set[str] = set()
    for index, prompt_raw in enumerate(prompt_rows):
        if not isinstance(prompt_raw, Mapping):
            raise SemanticAuditError(f"dataset_prompts[{index}] must be an object")
        prompt_id = _nonempty_text(prompt_raw.get("id"), f"dataset_prompts[{index}].id")
        if prompt_id in seen_prompt_ids:
            raise SemanticAuditError(f"duplicate prompt id: {prompt_id}")
        seen_prompt_ids.add(prompt_id)
        context_id = _nonempty_text(
            prompt_raw.get("context_id"), f"dataset_prompts[{index}].context_id"
        )
        if context_id not in contexts:
            raise SemanticAuditError(f"unknown context id for {prompt_id}: {context_id}")
        qa_index = prompt_raw.get("qa_index")
        if isinstance(qa_index, bool) or not isinstance(qa_index, int) or qa_index < 0:
            raise SemanticAuditError(f"qa_index for {prompt_id} must be non-negative")
        conversation_index, record = contexts[context_id]
        qa_rows = record["qa"]
        if qa_index >= len(qa_rows) or not isinstance(qa_rows[qa_index], Mapping):
            raise SemanticAuditError(f"qa_index for {prompt_id} is missing from the dataset")
        qa = qa_rows[qa_index]
        question = _nonempty_text(qa.get("question"), f"question for {prompt_id}")
        category = prompt_raw.get("category")
        if (
            isinstance(category, bool)
            or not isinstance(category, int)
            or category not in range(1, 6)
        ):
            raise SemanticAuditError(f"category for {prompt_id} must be an integer from 1 to 5")
        if qa.get("category") != category:
            raise SemanticAuditError(f"manifest/dataset category mismatch for {prompt_id}")
        accepted = _string_list(
            prompt_raw.get("accepted_answers"), f"accepted_answers for {prompt_id}"
        )
        denied = _string_list(
            prompt_raw.get("denied_answers", []),
            f"denied_answers for {prompt_id}",
            allow_empty=True,
        )
        dataset_answer = qa.get("answer")
        adversarial_answer = qa.get("adversarial_answer")
        if category == 5:
            if accepted != ["UNKNOWN"]:
                raise SemanticAuditError(f"category-5 {prompt_id} must accept only UNKNOWN")
            ground_truth = "UNKNOWN"
            if dataset_answer is not None:
                raise SemanticAuditError(f"category-5 {prompt_id} must not have a dataset answer")
            if adversarial_answer is None:
                raise SemanticAuditError(f"category-5 {prompt_id} needs an adversarial answer")
            adversarial_text = _reference_text(
                adversarial_answer, f"adversarial answer for {prompt_id}"
            )
            if adversarial_text not in denied:
                raise SemanticAuditError(
                    f"category-5 {prompt_id} must deny its dataset adversarial answer"
                )
        else:
            ground_truth = _reference_text(dataset_answer, f"dataset answer for {prompt_id}")
            adversarial_text = None
            if ground_truth.casefold() not in {item.casefold() for item in accepted}:
                raise SemanticAuditError(
                    f"accepted answers for {prompt_id} omit the dataset ground truth"
                )

        case_source = {
            "prompt_id": prompt_id,
            "question": question,
            "category": category,
            "ground_truth": ground_truth,
            "accepted_answers": accepted,
            "denied_answers": denied,
            "adversarial_answer": adversarial_text,
        }
        cases.append(
            {
                "rubric_case_id": "rubric-" + _sha256_bytes(_canonical_bytes(case_source))[:20],
                "context_id": context_id,
                "conversation_index": conversation_index,
                "qa_index": qa_index,
                **case_source,
            }
        )

    payload = {
        "schema_version": RUBRIC_PAYLOAD_SCHEMA,
        "audit_label": AUDIT_LABEL,
        "qualification_role": "secondary_only_not_original_qualification",
        "source_policy": {
            "allowed_fields": [
                "manifest prompt identifiers",
                "questions",
                "ground-truth/reference answers",
                "adversarial/denied answers",
                "categories",
                "dataset indices needed to bind those fields",
            ],
            "matrix_outputs_or_answer_strings_used": False,
        },
        "global_criteria": list(GLOBAL_CRITERIA),
        "cases": cases,
    }
    return {
        "schema_version": RUBRIC_SCHEMA,
        "freeze_status": "frozen_before_semantic_judging",
        "manifest_sha256": _sha256_file(manifest_path),
        "dataset_sha256": _sha256_file(dataset_path),
        "rubric_sha256": _sha256_bytes(_canonical_bytes(payload)),
        "rubric": payload,
    }


def freeze_rubric(manifest_path: Path, dataset_path: Path, output_path: Path) -> dict[str, Any]:
    artifact = build_rubric(manifest_path, dataset_path)
    _atomic_json(output_path, artifact)
    return artifact


def load_and_verify_rubric(
    rubric_path: Path,
    manifest_path: Path,
    dataset_path: Path,
) -> dict[str, Any]:
    frozen = _object(_load_json(rubric_path, "frozen rubric"), "frozen rubric")
    expected = build_rubric(manifest_path, dataset_path)
    if frozen != expected:
        raise SemanticAuditError(
            "frozen rubric does not exactly match a fresh manifest/dataset-only reconstruction"
        )
    rubric = frozen.get("rubric")
    if not isinstance(rubric, dict):
        raise SemanticAuditError("frozen rubric payload is missing")
    observed_hash = _sha256_bytes(_canonical_bytes(rubric))
    if frozen.get("rubric_sha256") != observed_hash:
        raise SemanticAuditError("frozen rubric SHA-256 is invalid")
    return frozen


def _extract_raw_answer(path: Path) -> str:
    answers: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SemanticAuditError(f"could not read raw JSONL {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SemanticAuditError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(event, Mapping) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if isinstance(item, Mapping) and item.get("type") == "agent_message":
            text = item.get("text")
            if not isinstance(text, str):
                raise SemanticAuditError(f"agent_message text is not a string in {path}")
            answers.append(text)
    if len(answers) != 1:
        raise SemanticAuditError(
            f"expected exactly one completed agent answer in {path}, found {len(answers)}"
        )
    return answers[0]


def collect_observations(
    report_path: Path,
    raw_dir: Path,
) -> tuple[dict[str, Any], list[Observation], dict[str, Any]]:
    report = _object(_load_json(report_path, "matrix report"), "matrix report")
    if report.get("schema_version") != MATRIX_REPORT_SCHEMA:
        raise SemanticAuditError(f"matrix report schema_version must be {MATRIX_REPORT_SCHEMA}")
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise SemanticAuditError("matrix report must contain completed runs")
    raw_root = raw_dir.resolve()
    if not raw_root.is_dir():
        raise SemanticAuditError(f"raw JSONL directory is missing: {raw_root}")

    observations: list[Observation] = []
    referenced_names: set[str] = set()
    file_fingerprints: list[dict[str, str]] = []
    run_ids: set[str] = set()
    for index, run_raw in enumerate(runs):
        if not isinstance(run_raw, Mapping):
            raise SemanticAuditError(f"runs[{index}] must be an object")
        run_id = _nonempty_text(run_raw.get("run_id"), f"runs[{index}].run_id")
        if run_id in run_ids:
            raise SemanticAuditError(f"duplicate run_id: {run_id}")
        run_ids.add(run_id)
        declared_path = Path(
            _nonempty_text(
                run_raw.get("raw_stdout_artifact"),
                f"raw_stdout_artifact for {run_id}",
            )
        )
        raw_path = (raw_root / declared_path.name).resolve()
        try:
            raw_path.relative_to(raw_root)
        except ValueError as exc:  # pragma: no cover - defensive on resolved basename
            raise SemanticAuditError(f"raw artifact escapes raw directory: {raw_path}") from exc
        if not raw_path.is_file():
            raise SemanticAuditError(f"missing raw JSONL for {run_id}: {raw_path}")
        if raw_path.name in referenced_names:
            raise SemanticAuditError(f"raw JSONL reused by multiple runs: {raw_path.name}")
        referenced_names.add(raw_path.name)
        actual_hash = _sha256_file(raw_path)
        declared_hash = _nonempty_text(
            run_raw.get("raw_stdout_sha256"), f"raw_stdout_sha256 for {run_id}"
        )
        if actual_hash != declared_hash:
            raise SemanticAuditError(f"raw JSONL hash mismatch for {run_id}")
        raw_answer = _extract_raw_answer(raw_path)
        report_answer = run_raw.get("answer")
        if not isinstance(report_answer, str) or raw_answer != report_answer:
            raise SemanticAuditError(f"raw/report answer mismatch for {run_id}")
        repetition = run_raw.get("repetition")
        if isinstance(repetition, bool) or not isinstance(repetition, int):
            raise SemanticAuditError(f"invalid repetition for {run_id}")
        observations.append(
            Observation(
                run_id=run_id,
                prompt_id=_nonempty_text(run_raw.get("prompt_id"), f"prompt_id for {run_id}"),
                answer=raw_answer,
                mode=_nonempty_text(run_raw.get("mode"), f"mode for {run_id}"),
                profile_id=_nonempty_text(run_raw.get("profile_id"), f"profile_id for {run_id}"),
                repetition=repetition,
                exact_match=bool(run_raw.get("protected_quality_passed", False)),
                raw_name=raw_path.name,
                raw_sha256=actual_hash,
            )
        )
        file_fingerprints.append({"name": raw_path.name, "sha256": actual_hash})

    actual_names = {path.name for path in raw_root.glob("*.stdout.jsonl")}
    if actual_names != referenced_names:
        missing_from_report = sorted(actual_names - referenced_names)
        missing_from_dir = sorted(referenced_names - actual_names)
        raise SemanticAuditError(
            "raw/report artifact set mismatch: "
            f"unreferenced={missing_from_report}, missing={missing_from_dir}"
        )
    file_fingerprints.sort(key=lambda item: item["name"])
    integrity = {
        "report_sha256": _sha256_file(report_path),
        "raw_jsonl_file_count": len(file_fingerprints),
        "all_declared_raw_sha256_verified": True,
        "all_raw_answers_equal_report_answers": True,
        "raw_set_sha256": _sha256_bytes(_canonical_bytes(file_fingerprints)),
    }
    return report, observations, integrity


def _evidence_index(record: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    conversation = record.get("conversation")
    if not isinstance(conversation, Mapping):
        raise SemanticAuditError("selected LOCOMO record has no conversation object")
    result: dict[str, dict[str, str]] = {}
    for session_name, messages_raw in conversation.items():
        if not isinstance(messages_raw, list):
            continue
        date_value = conversation.get(f"{session_name}_date_time")
        date = str(date_value).strip() if date_value is not None else ""
        for message_raw in messages_raw:
            if not isinstance(message_raw, Mapping):
                continue
            dia_id = message_raw.get("dia_id")
            speaker = message_raw.get("speaker")
            text = message_raw.get("text")
            if not all(isinstance(item, str) and item.strip() for item in (dia_id, speaker, text)):
                continue
            result[dia_id] = {
                "evidence_id": dia_id,
                "session": str(session_name),
                "session_date": date,
                "speaker": speaker,
                "text": text,
            }
    return result


def _evidence_by_prompt(
    manifest_path: Path,
    dataset_path: Path,
) -> dict[str, tuple[dict[str, str], ...]]:
    manifest = _object(_load_json(manifest_path, "manifest"), "manifest")
    dataset = _load_json(dataset_path, "LOCOMO dataset")
    contexts_raw = manifest.get("contexts")
    prompts_raw = manifest.get("dataset_prompts")
    if not isinstance(contexts_raw, list) or not isinstance(prompts_raw, list):
        raise SemanticAuditError("manifest contexts/dataset_prompts are invalid")
    context_records: dict[str, Mapping[str, Any]] = {}
    indexes: dict[str, dict[str, dict[str, str]]] = {}
    for context_raw in contexts_raw:
        if not isinstance(context_raw, Mapping):
            continue
        context_id = str(context_raw.get("id", ""))
        conversation_index = context_raw.get("conversation_index")
        if isinstance(conversation_index, int) and not isinstance(conversation_index, bool):
            record = _dataset_record(dataset, conversation_index)
            context_records[context_id] = record
            indexes[context_id] = _evidence_index(record)
    result: dict[str, tuple[dict[str, str], ...]] = {}
    for prompt_raw in prompts_raw:
        if not isinstance(prompt_raw, Mapping):
            continue
        prompt_id = str(prompt_raw.get("id", ""))
        context_id = str(prompt_raw.get("context_id", ""))
        qa_index = prompt_raw.get("qa_index")
        if context_id not in context_records or not isinstance(qa_index, int):
            raise SemanticAuditError(f"could not resolve evidence for {prompt_id}")
        qa_rows = context_records[context_id]["qa"]
        qa = qa_rows[qa_index]
        refs = qa.get("evidence", []) if isinstance(qa, Mapping) else []
        if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
            raise SemanticAuditError(f"invalid evidence references for {prompt_id}")
        resolved: list[dict[str, str]] = []
        for ref in refs:
            item = indexes[context_id].get(ref)
            if item is None:
                raise SemanticAuditError(f"unresolved evidence reference {ref} for {prompt_id}")
            resolved.append(item)
        result[prompt_id] = tuple(resolved)
    return result


def _answer_id(answer: str) -> str:
    return "answer-" + _sha256_text("answer\0" + answer)[:24]


def _blind_case_id(rubric_sha256: str, rubric_case_id: str, answer_id: str) -> str:
    source = f"{rubric_sha256}\0{rubric_case_id}\0{answer_id}"
    return "blind-" + _sha256_text(source)[:24]


def prepare_judgment_units(
    frozen_rubric: Mapping[str, Any],
    observations: Sequence[Observation],
    evidence_by_prompt: Mapping[str, tuple[dict[str, str], ...]],
) -> tuple[list[JudgmentUnit], dict[str, str], str]:
    rubric = frozen_rubric["rubric"]
    rubric_cases = rubric["cases"]
    by_prompt = {case["prompt_id"]: case for case in rubric_cases}
    unknown_prompts = sorted({item.prompt_id for item in observations} - set(by_prompt))
    if unknown_prompts:
        raise SemanticAuditError(f"report uses prompts absent from rubric: {unknown_prompts}")
    missing_prompts = sorted(set(by_prompt) - {item.prompt_id for item in observations})
    if missing_prompts:
        raise SemanticAuditError(f"rubric prompts absent from report: {missing_prompts}")

    answer_bank = {_answer_id(item.answer): item.answer for item in observations}
    if len(answer_bank) != len(set(answer_bank.values())):
        raise SemanticAuditError("answer-id collision")
    unique_pairs = {(item.prompt_id, item.answer) for item in observations}
    units: list[JudgmentUnit] = []
    rubric_sha256 = str(frozen_rubric["rubric_sha256"])
    for prompt_id, answer in sorted(unique_pairs):
        rubric_case = by_prompt[prompt_id]
        answer_id = _answer_id(answer)
        units.append(
            JudgmentUnit(
                blind_case_id=_blind_case_id(
                    rubric_sha256, rubric_case["rubric_case_id"], answer_id
                ),
                rubric_case_id=rubric_case["rubric_case_id"],
                prompt_id=prompt_id,
                answer_id=answer_id,
                answer=answer,
                question=rubric_case["question"],
                category=int(rubric_case["category"]),
                ground_truth=rubric_case["ground_truth"],
                accepted_answers=tuple(rubric_case["accepted_answers"]),
                denied_answers=tuple(rubric_case["denied_answers"]),
                evidence=evidence_by_prompt.get(prompt_id, ()),
            )
        )
    seed_material = {
        "protocol": "rubric-answer-units-shuffle-v1",
        "rubric_sha256": rubric_sha256,
        "answer_bank": sorted(answer_bank.items()),
        "unit_ids": sorted(item.blind_case_id for item in units),
    }
    shuffle_seed_sha256 = _sha256_bytes(_canonical_bytes(seed_material))
    generator = random.Random(int(shuffle_seed_sha256, 16))
    generator.shuffle(units)
    return units, dict(sorted(answer_bank.items())), shuffle_seed_sha256


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.replace("’", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^\w\s'+-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?\t\r\n")
    return text


def _surface_forms(value: str, question: str) -> set[str]:
    base = _normalize(value)
    forms = {base}
    pending = [base]
    subject_match = re.match(r"what is ([a-z][a-z -]+)'s identity\??$", _normalize(question))
    subject = subject_match.group(1) if subject_match else None
    while pending:
        current = pending.pop()
        variants: list[str] = []
        for prefix in ("in ", "that "):
            if current.startswith(prefix):
                variants.append(current[len(prefix) :])
        if subject and current.startswith(f"{subject} is "):
            variants.append(current[len(subject) + 4 :])
        if current.startswith("a "):
            variants.append(current[2:])
        variants.append(re.sub(r"\breally\s+", "", current))
        variants.append(re.sub(r"\btrans woman\b", "transgender woman", current))
        for variant in variants:
            variant = re.sub(r"\s+", " ", variant).strip()
            if variant and variant not in forms:
                forms.add(variant)
                pending.append(variant)
    return forms


def deterministic_decision(unit: JudgmentUnit) -> dict[str, Any] | None:
    """Return only narrow, answer-independent aliases and explicit contradictions."""

    if unit.category == 5:
        passed = unit.answer == "UNKNOWN"
        return {
            "blind_case_id": unit.blind_case_id,
            "passed": passed,
            "reason": "correct" if passed else "adversarial_transfer",
            "rationale": (
                "Category 5 requires the exact string UNKNOWN."
                if passed
                else "Category 5 permits only the exact string UNKNOWN."
            ),
            "decision_source": "deterministic_category5_exact_v1",
        }

    answer_forms = _surface_forms(unit.answer, unit.question)
    reference_forms: set[str] = set()
    for accepted in unit.accepted_answers:
        reference_forms.update(_surface_forms(accepted, unit.question))
    if answer_forms & reference_forms:
        return {
            "blind_case_id": unit.blind_case_id,
            "passed": True,
            "reason": "correct",
            "rationale": "The answer is an exact reference answer or an obvious surface alias.",
            "decision_source": "deterministic_surface_alias_v1",
        }

    normalized = _normalize(unit.answer)
    if any(normalized.startswith(prefix) for prefix in UNCERTAINTY_PREFIXES):
        if not any(
            _normalize(item).startswith(UNCERTAINTY_PREFIXES) for item in unit.accepted_answers
        ):
            return {
                "blind_case_id": unit.blind_case_id,
                "passed": False,
                "reason": "contradicts_reference",
                "rationale": "The answer changes the benchmark reference from a conclusion to uncertainty.",
                "decision_source": "deterministic_explicit_uncertainty_v1",
            }
    return None


def _unit_for_judge(unit: JudgmentUnit) -> dict[str, Any]:
    return {
        "blind_case_id": unit.blind_case_id,
        "question": unit.question,
        "category": unit.category,
        "ground_truth": unit.ground_truth,
        "accepted_reference_answers": list(unit.accepted_answers),
        "denied_adversarial_answers": list(unit.denied_answers),
        "candidate_answer": unit.answer,
        "raw_evidence": list(unit.evidence),
    }


def _assert_blind_payload(value: Any) -> None:
    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            forbidden = FORBIDDEN_BLIND_KEYS & {str(key) for key in item}
            if forbidden:
                raise SemanticAuditError(
                    f"blind judge payload contains forbidden metadata keys: {sorted(forbidden)}"
                )
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)


def build_claude_request(unresolved: Sequence[JudgmentUnit], rubric_sha256: str) -> dict[str, Any]:
    request = {
        "audit_label": AUDIT_LABEL,
        "rubric_sha256": rubric_sha256,
        "arm_and_profile_metadata_hidden": True,
        "criteria": list(GLOBAL_CRITERIA),
        "instructions": [
            "Judge each case independently against the frozen benchmark reference and raw evidence.",
            "Treat the benchmark ground truth as the target substantive answer.",
            "Pass only if the candidate conveys that answer and every extra factual claim is "
            "both directly supported by the supplied evidence and relevant to the question.",
            "Return exactly one decision for every blind_case_id and do not invent IDs.",
        ],
        "cases": [_unit_for_judge(item) for item in unresolved],
    }
    _assert_blind_payload(request)
    return request


def _claude_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "blind_case_id": {"type": "string"},
                        "passed": {"type": "boolean"},
                        "reason": {"type": "string", "enum": sorted(DECISION_REASONS)},
                        "rationale": {"type": "string"},
                    },
                    "required": ["blind_case_id", "passed", "reason", "rationale"],
                },
            }
        },
        "required": ["decisions"],
    }


def _default_claude_runner(
    command: Sequence[str], prompt: str, timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
    )


def _claude_auth_status(claude_exe: str) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [claude_exe, "auth", "status"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SemanticAuditError(f"could not inspect Claude CLI authentication: {exc}") from exc
    if completed.returncode != 0:
        raise SemanticAuditError(
            f"Claude CLI authentication check failed: {completed.stderr.strip()}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SemanticAuditError("Claude CLI auth status did not return JSON") from exc
    if not isinstance(value, Mapping) or value.get("loggedIn") is not True:
        raise SemanticAuditError("Claude CLI is not signed in")
    return {
        "logged_in": True,
        "auth_method": value.get("authMethod"),
        "api_provider": value.get("apiProvider"),
    }


def _claude_version(claude_exe: str) -> str:
    try:
        completed = subprocess.run(
            [claude_exe, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SemanticAuditError(f"could not inspect Claude CLI version: {exc}") from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise SemanticAuditError("Claude CLI version check failed")
    return completed.stdout.strip()


def _parse_claude_envelope(stdout: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SemanticAuditError("Claude CLI stdout was not a JSON envelope") from exc
    if not isinstance(envelope, dict):
        raise SemanticAuditError("Claude CLI stdout envelope must be an object")
    structured = envelope.get("structured_output")
    if structured is None:
        result = envelope.get("result")
        if isinstance(result, str):
            try:
                structured = json.loads(result)
            except json.JSONDecodeError as exc:
                raise SemanticAuditError("Claude CLI result was not structured JSON") from exc
    if not isinstance(structured, dict):
        raise SemanticAuditError("Claude CLI envelope has no structured decision output")
    return envelope, structured


def call_claude_judge(
    request: Mapping[str, Any],
    *,
    claude_exe: str,
    model: str,
    max_budget_usd: Decimal,
    timeout_seconds: float,
    runner: ClaudeRunner = _default_claude_runner,
    auth_status: Mapping[str, Any] | None = None,
    cli_version: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if max_budget_usd <= 0 or max_budget_usd > MAX_CLAUDE_BUDGET_USD:
        raise SemanticAuditError(
            f"Claude budget must be positive and no more than ${MAX_CLAUDE_BUDGET_USD}"
        )
    if re.fullmatch(r"claude-[a-z0-9-]+", model) is None or not re.search(
        r"(?:\d{8}|-\d+-\d+)$", model
    ):
        raise SemanticAuditError("Claude model must be a pinned version, not a moving alias")
    _assert_blind_payload(request)
    verified_auth = dict(auth_status or _claude_auth_status(claude_exe))
    version = cli_version or _claude_version(claude_exe)
    prompt = json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True)
    schema = json.dumps(_claude_schema(), separators=(",", ":"), sort_keys=True)
    system_prompt = (
        "You are an independent semantic benchmark judge. Use only the supplied frozen "
        "rubric fields and raw evidence. Do not use tools or outside knowledge. Return the "
        "requested structured decisions only. You are not qualifying the original matrix."
    )
    command = [
        claude_exe,
        "--print",
        "--output-format",
        "json",
        "--json-schema",
        schema,
        "--model",
        model,
        "--effort",
        "low",
        "--max-budget-usd",
        format(max_budget_usd, "f"),
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--disable-slash-commands",
        "--safe-mode",
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--system-prompt",
        system_prompt,
    ]
    try:
        completed = runner(command, prompt, timeout_seconds)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SemanticAuditError(f"Claude CLI judge failed to execute: {exc}") from exc
    raw = {
        "cli_version": version,
        "authentication": verified_auth,
        "requested_model": model,
        "model_pinned": True,
        "effort": "low",
        "tools": [],
        "mcp_servers": [],
        "safe_mode": True,
        "session_persistence": False,
        "max_budget_usd": format(max_budget_usd, "f"),
        "timeout_seconds": timeout_seconds,
        "prompt_sha256": _sha256_text(prompt),
        "prompt": prompt,
        "argv_without_prompt": command,
        "returncode": completed.returncode,
        "stdout_sha256": _sha256_text(completed.stdout),
        "stdout": completed.stdout,
        "stderr_sha256": _sha256_text(completed.stderr),
        "stderr": completed.stderr,
    }
    if completed.returncode != 0:
        stdout_tail = completed.stdout[-2000:].strip()
        stderr_tail = completed.stderr[-2000:].strip()
        raise SemanticAuditError(
            "Claude CLI judge returned nonzero status "
            f"{completed.returncode}: stdout={stdout_tail!r}, stderr={stderr_tail!r}"
        )
    envelope, structured = _parse_claude_envelope(completed.stdout)
    cost = envelope.get("total_cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float, str)):
        raise SemanticAuditError("Claude CLI did not report total_cost_usd")
    try:
        cost_decimal = Decimal(str(cost))
    except InvalidOperation as exc:
        raise SemanticAuditError("Claude CLI total_cost_usd is invalid") from exc
    if cost_decimal < 0 or cost_decimal > max_budget_usd:
        raise SemanticAuditError("Claude CLI reported cost outside the hard budget cap")

    decisions_raw = structured.get("decisions")
    if not isinstance(decisions_raw, list):
        raise SemanticAuditError("Claude structured output has no decisions array")
    expected_ids = {str(item["blind_case_id"]) for item in request["cases"]}
    parsed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, decision_raw in enumerate(decisions_raw):
        if not isinstance(decision_raw, Mapping):
            raise SemanticAuditError(f"Claude decisions[{index}] is not an object")
        case_id = decision_raw.get("blind_case_id")
        passed = decision_raw.get("passed")
        reason = decision_raw.get("reason")
        rationale = decision_raw.get("rationale")
        if not isinstance(case_id, str) or case_id not in expected_ids or case_id in seen:
            raise SemanticAuditError(
                f"Claude returned an invalid/duplicate blind case id: {case_id}"
            )
        if not isinstance(passed, bool):
            raise SemanticAuditError(f"Claude decision {case_id} has invalid passed")
        if reason not in DECISION_REASONS:
            raise SemanticAuditError(f"Claude decision {case_id} has invalid reason")
        if not isinstance(rationale, str) or not rationale.strip():
            raise SemanticAuditError(f"Claude decision {case_id} has no rationale")
        seen.add(case_id)
        parsed.append(
            {
                "blind_case_id": case_id,
                "passed": passed,
                "reason": reason,
                "rationale": rationale.strip(),
                "decision_source": "claude_cli_independent_semantic_judge",
            }
        )
    if seen != expected_ids:
        raise SemanticAuditError(f"Claude omitted blind case ids: {sorted(expected_ids - seen)}")
    raw["provider_envelope"] = envelope
    raw["provider_reported_total_cost_usd"] = str(cost)
    raw["provider_reported_usage"] = envelope.get("usage")
    raw["provider_reported_model_usage"] = envelope.get("modelUsage")
    return parsed, raw


def _count_summary(values: Sequence[bool]) -> dict[str, Any]:
    total = len(values)
    passed = sum(values)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 9) if total else None,
    }


def _partial_summary(values: Sequence[bool | None]) -> dict[str, Any]:
    total = len(values)
    resolved_values = [item for item in values if item is not None]
    resolved = len(resolved_values)
    passed = sum(resolved_values)
    failed = resolved - passed
    unresolved = total - resolved
    return {
        "status": "complete" if unresolved == 0 else "incomplete",
        "total": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "passed": passed,
        "failed": failed,
        "pass_rate": round(passed / total, 9) if total and not unresolved else None,
        "resolved_pass_rate": round(passed / resolved, 9) if resolved else None,
        "minimum_possible_pass_rate": round(passed / total, 9) if total else None,
        "maximum_possible_pass_rate": (round((passed + unresolved) / total, 9) if total else None),
    }


def _decision_public(decision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "blind_case_id": decision["blind_case_id"],
        "passed": decision["passed"],
        "reason": decision["reason"],
        "rationale": decision["rationale"],
        "decision_source": decision["decision_source"],
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["secondary_semantic_summary"]
    exact = report["primary_exact_match_snapshot"]
    cost = report["cost_accounting"]["external_claude_judge"]
    semantic_outcome = (
        f"{summary['passed']}/{summary['total']} ({summary['pass_rate']:.3%})"
        if summary["status"] == "complete"
        else (
            f"incomplete: {summary['resolved']}/{summary['total']} runs resolved; "
            f"{summary['passed']} pass, {summary['failed']} fail, "
            f"{summary['unresolved']} unresolved"
        )
    )
    lines = [
        "# Codex Sol matrix secondary semantic audit",
        "",
        "> **Secondary/posthoc only.** This audit does not replace, revise, or qualify the "
        "primary predeclared exact-match verdict, which remains failed.",
        "",
        "## Outcome",
        "",
        f"- Primary verdict: `{report['primary_predeclared_verdict']['status']}` "
        f"(`qualified: {str(report['primary_predeclared_verdict']['qualified']).lower()}`).",
        f"- Primary exact-match snapshot: {exact['passed']}/{exact['total']} "
        f"({exact['pass_rate']:.3%}).",
        f"- Secondary semantic audit: {semantic_outcome}.",
        f"- All semantic answers passed: `{str(summary['all_runs_passed']).lower()}`.",
        f"- Frozen rubric SHA-256: `{report['rubric']['rubric_sha256']}`.",
        f"- Raw judge artifact SHA-256: `{report['judge']['artifact_sha256']}`.",
        "",
        "No overall secondary rate is reported while cases remain unresolved. "
        "`semantic_qualification` is deliberately `not_applicable`; the original matrix "
        "remains not qualified.",
        "",
        "## Per-prompt results",
        "",
        "| Prompt | Cat. | Exact | Semantic | Semantic rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report["per_prompt"]:
        semantic = item["semantic"]
        semantic_cell = (
            f"{semantic['passed']}/{semantic['total']}"
            if semantic["status"] == "complete"
            else (
                f"{semantic['passed']} pass, {semantic['failed']} fail, "
                f"{semantic['unresolved']} unresolved"
            )
        )
        rate_cell = (
            f"{semantic['pass_rate']:.3%}" if semantic["pass_rate"] is not None else "incomplete"
        )
        lines.append(
            f"| `{item['prompt_id']}` | {item['category']} | "
            f"{item['exact']['passed']}/{item['exact']['total']} | "
            f"{semantic_cell} | {rate_cell} |"
        )
    lines.extend(
        [
            "",
            "## Blinding and reproducibility",
            "",
            f"- {report['judge']['source_run_count']} run answers were reduced to "
            f"{report['judge']['unique_answer_string_count']} unique answer strings and "
            f"{report['judge']['unique_question_answer_unit_count']} unique question/answer units.",
            f"- {report['judge']['deterministic_unit_count']} units used frozen deterministic "
            f"rules; {report['judge']['unresolved_unit_count']} units remain unresolved.",
            "- Arm, profile, reasoning effort, repetition, run ID, and sequence metadata are "
            "absent from the prepared blind judge packet.",
            f"- Deterministic shuffle seed SHA-256: `{report['judge']['shuffle_seed_sha256']}`.",
            "- Every raw JSONL SHA-256 and extracted answer matched the primary report.",
            "",
            "## Judge cost",
            "",
            "- Deterministic rules: `$0`.",
            f"- Accepted audit external-judge status: `{cost['status']}`.",
            f"- Accepted audit Claude CLI reported cost: "
            f"`${cost['provider_reported_total_cost_usd']}`.",
            f"- Prepared judge configuration: pinned `{cost['requested_model']}`, with tools "
            "and MCP servers disabled; it was not invoked for the accepted audit.",
            "",
            "The accepted deterministic audit has exact external model cost of $0. Earlier "
            "discarded/failed judge attempts are disclosed separately and prevent exact "
            "aggregate experiment-cost reporting. Original Sol credits remain estimates from "
            "the primary report and are not recast as billed cost here.",
        ]
    )
    disclosure = report["cost_accounting"].get("discarded_or_failed_attempts")
    if disclosure:
        lines.extend(
            [
                "",
                "### Discarded-attempt disclosure",
                "",
                f"Discarded/failed attempts: {disclosure['attempt_count']}; exact aggregate "
                "provider telemetry was not retained, so the only defensible numeric bound is "
                f"$0 to ${disclosure['hard_cap_sum_upper_bound_usd']} from the sum of hard caps.",
            ]
        )
    lines.extend(["", "## Limitations", ""])
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def run_audit(
    *,
    rubric_path: Path,
    manifest_path: Path,
    dataset_path: Path,
    matrix_report_path: Path,
    raw_dir: Path,
    output_json_path: Path,
    output_markdown_path: Path,
    raw_judge_path: Path,
    claude_exe: str = "claude",
    claude_model: str = DEFAULT_CLAUDE_MODEL,
    max_budget_usd: Decimal = DEFAULT_CLAUDE_BUDGET_USD,
    timeout_seconds: float = 180.0,
    claude_runner: ClaudeRunner = _default_claude_runner,
    claude_auth_status: Mapping[str, Any] | None = None,
    claude_cli_version: str | None = None,
    external_judge_enabled: bool = True,
    external_attempt_disclosure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    frozen = load_and_verify_rubric(rubric_path, manifest_path, dataset_path)
    input_report_hash_before = _sha256_file(matrix_report_path)
    matrix_report, observations, integrity = collect_observations(matrix_report_path, raw_dir)
    evidence = _evidence_by_prompt(manifest_path, dataset_path)
    units, answer_bank, shuffle_seed = prepare_judgment_units(frozen, observations, evidence)

    deterministic: list[dict[str, Any]] = []
    unresolved: list[JudgmentUnit] = []
    for unit in units:
        decision = deterministic_decision(unit)
        if decision is None:
            unresolved.append(unit)
        else:
            deterministic.append(decision)

    claude_request = build_claude_request(unresolved, frozen["rubric_sha256"])
    if unresolved and external_judge_enabled:
        external, external_raw = call_claude_judge(
            claude_request,
            claude_exe=claude_exe,
            model=claude_model,
            max_budget_usd=max_budget_usd,
            timeout_seconds=timeout_seconds,
            runner=claude_runner,
            auth_status=claude_auth_status,
            cli_version=claude_cli_version,
        )
    else:
        external = []
        external_raw = {
            "invoked": False,
            "status": ("not_needed" if not unresolved else "disabled_after_failed_attempts"),
            "requested_model": claude_model,
            "model_pinned": True,
            "tools": [],
            "mcp_servers": [],
            "max_budget_usd": "0" if unresolved else format(max_budget_usd, "f"),
            "provider_reported_total_cost_usd": "0",
            "provider_reported_usage": None,
            "provider_reported_model_usage": None,
        }
    decisions = {item["blind_case_id"]: item for item in [*deterministic, *external]}
    if external_judge_enabled and set(decisions) != {item.blind_case_id for item in units}:
        raise SemanticAuditError("judgments do not cover every unique question/answer unit")

    raw_judge = {
        "schema_version": JUDGE_ARTIFACT_SCHEMA,
        "audit_label": AUDIT_LABEL,
        "qualification_role": "secondary_only_not_original_qualification",
        "rubric_sha256": frozen["rubric_sha256"],
        "blind_protocol": {
            "arm_profile_and_run_metadata_hidden": True,
            "forbidden_metadata_keys": sorted(FORBIDDEN_BLIND_KEYS),
            "answer_strings_deduplicated_globally": True,
            "question_answer_units_deduplicated": True,
            "deterministic_shuffle": True,
            "shuffle_seed_sha256": shuffle_seed,
        },
        "answer_bank": [
            {"answer_id": answer_id, "answer": answer} for answer_id, answer in answer_bank.items()
        ],
        "ordered_blind_units": [
            {
                **_unit_for_judge(unit),
                "answer_id": unit.answer_id,
                "rubric_case_id": unit.rubric_case_id,
                "decision": (
                    _decision_public(decisions[unit.blind_case_id])
                    if unit.blind_case_id in decisions
                    else None
                ),
                "resolution_status": (
                    "resolved" if unit.blind_case_id in decisions else "unresolved"
                ),
            }
            for unit in units
        ],
        "external_judge_request": claude_request,
        "external_judge_raw": external_raw,
        "external_attempt_disclosure": dict(external_attempt_disclosure or {}),
    }
    # Provider telemetry can legitimately use names such as ``service_tier``.
    # Blinding applies to what the judge saw, so validate only the request and
    # benchmark-unit projection, not the provider's response envelope.
    _assert_blind_payload(raw_judge["external_judge_request"])
    _assert_blind_payload(
        [
            {
                "blind_case_id": item["blind_case_id"],
                "question": item["question"],
                "category": item["category"],
                "ground_truth": item["ground_truth"],
                "accepted_reference_answers": item["accepted_reference_answers"],
                "denied_adversarial_answers": item["denied_adversarial_answers"],
                "candidate_answer": item["candidate_answer"],
                "raw_evidence": item["raw_evidence"],
            }
            for item in raw_judge["ordered_blind_units"]
        ]
    )
    _atomic_json(raw_judge_path, raw_judge)
    raw_judge_hash = _sha256_file(raw_judge_path)

    by_prompt_case = {case["prompt_id"]: case for case in frozen["rubric"]["cases"]}
    unit_by_pair = {(unit.prompt_id, unit.answer): unit for unit in units}
    run_rows: list[dict[str, Any]] = []
    for observation in observations:
        unit = unit_by_pair[(observation.prompt_id, observation.answer)]
        decision = decisions.get(unit.blind_case_id)
        run_rows.append(
            {
                "run_id": observation.run_id,
                "prompt_id": observation.prompt_id,
                "mode": observation.mode,
                "profile_id": observation.profile_id,
                "repetition": observation.repetition,
                "answer_id": unit.answer_id,
                "blind_case_id": unit.blind_case_id,
                "primary_exact_match_passed": observation.exact_match,
                "secondary_semantic_passed": (decision["passed"] if decision is not None else None),
                "semantic_reason": decision["reason"] if decision is not None else None,
                "semantic_decision_source": (
                    decision["decision_source"] if decision is not None else None
                ),
                "semantic_resolution_status": (
                    "resolved" if decision is not None else "unresolved"
                ),
                "raw_stdout_artifact": observation.raw_name,
                "raw_stdout_sha256": observation.raw_sha256,
            }
        )

    per_prompt: list[dict[str, Any]] = []
    for prompt_id in by_prompt_case:
        rows = [item for item in run_rows if item["prompt_id"] == prompt_id]
        case = by_prompt_case[prompt_id]
        per_prompt.append(
            {
                "prompt_id": prompt_id,
                "category": case["category"],
                "exact": _count_summary(
                    [bool(item["primary_exact_match_passed"]) for item in rows]
                ),
                "semantic": _partial_summary([item["secondary_semantic_passed"] for item in rows]),
            }
        )

    per_mode: list[dict[str, Any]] = []
    for mode in sorted({item.mode for item in observations}):
        rows = [item for item in run_rows if item["mode"] == mode]
        per_mode.append(
            {
                "mode": mode,
                "exact": _count_summary(
                    [bool(item["primary_exact_match_passed"]) for item in rows]
                ),
                "semantic": _partial_summary([item["secondary_semantic_passed"] for item in rows]),
            }
        )

    per_profile: list[dict[str, Any]] = []
    for profile_id in sorted({item.profile_id for item in observations}):
        rows = [item for item in run_rows if item["profile_id"] == profile_id]
        per_profile.append(
            {
                "profile_id": profile_id,
                "exact": _count_summary(
                    [bool(item["primary_exact_match_passed"]) for item in rows]
                ),
                "semantic": _partial_summary([item["secondary_semantic_passed"] for item in rows]),
            }
        )

    exact_summary = _count_summary([bool(item["primary_exact_match_passed"]) for item in run_rows])
    semantic_summary = _partial_summary([item["secondary_semantic_passed"] for item in run_rows])
    semantic_summary["all_runs_passed"] = (
        semantic_summary["status"] == "complete" and semantic_summary["failed"] == 0
    )
    primary_verdict = matrix_report.get("verdict")
    if not isinstance(primary_verdict, dict):
        raise SemanticAuditError("matrix report has no primary verdict object")
    if primary_verdict.get("qualified") is not False:
        raise SemanticAuditError(
            "this audit is scoped to preserving a failed primary matrix verdict"
        )
    input_report_hash_after = _sha256_file(matrix_report_path)
    if input_report_hash_after != input_report_hash_before:
        raise SemanticAuditError("primary report changed while the posthoc audit was running")

    provider_cost = external_raw["provider_reported_total_cost_usd"]
    unresolved_unit_count = len(units) - len(decisions)
    accepted_cost_status = (
        "cli_reported" if external_raw.get("invoked", True) else "exact_zero_no_call"
    )
    disclosure = dict(external_attempt_disclosure or {})
    report = {
        "schema_version": AUDIT_REPORT_SCHEMA,
        "audit_label": AUDIT_LABEL,
        "posthoc": True,
        "secondary_only": True,
        "original_qualification_use_prohibited": True,
        "semantic_qualification": "not_applicable",
        "primary_report": {
            "path": str(matrix_report_path),
            "sha256_before": input_report_hash_before,
            "sha256_after": input_report_hash_after,
            "unchanged": True,
        },
        "primary_predeclared_verdict": dict(primary_verdict),
        "primary_predeclared_verdict_sha256": _sha256_bytes(_canonical_bytes(primary_verdict)),
        "primary_failure_preserved": True,
        "rubric": {
            "path": str(rubric_path),
            "rubric_sha256": frozen["rubric_sha256"],
            "manifest_sha256": frozen["manifest_sha256"],
            "dataset_sha256": frozen["dataset_sha256"],
            "fresh_reconstruction_matched": True,
            "matrix_outputs_or_answer_strings_used_to_build": False,
        },
        "input_integrity": integrity,
        "judge": {
            "artifact_path": str(raw_judge_path),
            "artifact_sha256": raw_judge_hash,
            "source_run_count": len(observations),
            "unique_answer_string_count": len(answer_bank),
            "unique_question_answer_unit_count": len(units),
            "deterministic_unit_count": len(deterministic),
            "external_unit_count": len(external),
            "unresolved_unit_count": unresolved_unit_count,
            "audit_complete": unresolved_unit_count == 0,
            "shuffle_seed_sha256": shuffle_seed,
            "arm_profile_and_run_metadata_hidden": True,
            "no_tools": True,
            "model_pinned": True,
        },
        "cost_accounting": {
            "deterministic_rules_usd": "0",
            "external_claude_judge": {
                "status": accepted_cost_status,
                "invoked": bool(external_raw.get("invoked", True)),
                "requested_model": claude_model,
                "cli_version": external_raw.get("cli_version"),
                "max_budget_usd": external_raw.get("max_budget_usd", format(max_budget_usd, "f")),
                "provider_reported_total_cost_usd": provider_cost,
                "provider_reported_usage": external_raw.get("provider_reported_usage"),
                "provider_reported_model_usage": external_raw.get("provider_reported_model_usage"),
                "invoice_reconciled": False,
            },
            "total_posthoc_provider_reported_usd": provider_cost,
            "accepted_audit_cost_is_exact": True,
            "discarded_or_failed_attempts": disclosure,
            "aggregate_experiment_cost_is_exact": not bool(disclosure),
            "original_matrix_credit_accounting": matrix_report.get("estimated_credit_budget"),
            "original_matrix_cost_is_estimated_not_provider_billed": True,
        },
        "primary_exact_match_snapshot": exact_summary,
        "secondary_semantic_summary": semantic_summary,
        "per_prompt": per_prompt,
        "per_mode": per_mode,
        "per_profile": per_profile,
        "runs": run_rows,
        "limitations": [
            "This is a secondary, posthoc semantic audit and was not predeclared as the "
            "matrix qualification criterion.",
            "The primary exact-match failure remains authoritative and unchanged; the "
            "semantic result must not be substituted into the original qualification.",
            "Deterministic rules intentionally cover only narrow surface aliases and explicit "
            "category-5/uncertainty cases; unresolved cases are not coerced to pass or fail.",
            "The independent CLI judge result was not accepted because its raw telemetry was "
            "not retained after a local validator error; later attempts failed or were stopped, "
            "so this final artifact is intentionally incomplete.",
            "The accepted rubric-only audit made no external model call and therefore has exact "
            "$0 external-model cost. Aggregate cost across discarded/failed attempts is unknown "
            "and only bounded by the disclosed hard caps.",
            "The benchmark ground truth is treated as authoritative even when a counterfactual "
            "could reasonably be described as uncertain outside the benchmark.",
            "No independent-model decision is part of the accepted audit artifact.",
            "A finite 10-question, 120-run matrix does not establish universal semantic quality.",
        ],
    }
    _atomic_json(output_json_path, report)
    _atomic_text(output_markdown_path, _render_markdown(report))
    return report


def _decimal_argument(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal dollar amount") from exc
    if parsed <= 0 or parsed > MAX_CLAUDE_BUDGET_USD:
        raise argparse.ArgumentTypeError(
            f"must be positive and no more than {MAX_CLAUDE_BUDGET_USD}"
        )
    return parsed


def _nonnegative_decimal_argument(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a decimal dollar amount") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    freeze = commands.add_parser(
        "freeze-rubric", help="freeze the manifest/dataset-only semantic rubric"
    )
    freeze.add_argument("--manifest", type=Path, required=True)
    freeze.add_argument("--dataset", type=Path, required=True)
    freeze.add_argument("--output", type=Path, required=True)

    audit = commands.add_parser(
        "audit", help="run the blinded secondary audit from a frozen rubric"
    )
    audit.add_argument("--rubric", type=Path, required=True)
    audit.add_argument("--manifest", type=Path, required=True)
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--matrix-report", type=Path, required=True)
    audit.add_argument("--raw-dir", type=Path, required=True)
    audit.add_argument("--output-json", type=Path, required=True)
    audit.add_argument("--output-markdown", type=Path, required=True)
    audit.add_argument("--raw-judge-output", type=Path, required=True)
    audit.add_argument("--claude-exe", default="claude")
    audit.add_argument("--claude-model", default=DEFAULT_CLAUDE_MODEL)
    audit.add_argument(
        "--max-budget-usd",
        type=_decimal_argument,
        default=DEFAULT_CLAUDE_BUDGET_USD,
    )
    audit.add_argument("--timeout-seconds", type=float, default=180.0)
    audit.add_argument(
        "--no-external-judge",
        action="store_true",
        help="leave non-deterministic units unresolved and make no model call",
    )
    audit.add_argument("--discarded-judge-attempt-count", type=int, default=0)
    audit.add_argument(
        "--discarded-judge-attempt-upper-bound-usd",
        type=_nonnegative_decimal_argument,
        default=Decimal("0"),
    )
    audit.add_argument(
        "--discarded-judge-attempt-note",
        action="append",
        default=[],
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "freeze-rubric":
            artifact = freeze_rubric(args.manifest, args.dataset, args.output)
            print(
                json.dumps(
                    {
                        "status": "frozen",
                        "output": str(args.output),
                        "rubric_sha256": artifact["rubric_sha256"],
                    },
                    sort_keys=True,
                )
            )
        else:
            attempt_disclosure = None
            if args.discarded_judge_attempt_count:
                attempt_disclosure = {
                    "attempt_count": args.discarded_judge_attempt_count,
                    "exact_provider_reported_total_cost_usd": None,
                    "aggregate_cost_known": False,
                    "hard_cap_sum_upper_bound_usd": format(
                        args.discarded_judge_attempt_upper_bound_usd, "f"
                    ),
                    "notes": args.discarded_judge_attempt_note,
                }
            report = run_audit(
                rubric_path=args.rubric,
                manifest_path=args.manifest,
                dataset_path=args.dataset,
                matrix_report_path=args.matrix_report,
                raw_dir=args.raw_dir,
                output_json_path=args.output_json,
                output_markdown_path=args.output_markdown,
                raw_judge_path=args.raw_judge_output,
                claude_exe=args.claude_exe,
                claude_model=args.claude_model,
                max_budget_usd=args.max_budget_usd,
                timeout_seconds=args.timeout_seconds,
                external_judge_enabled=not args.no_external_judge,
                external_attempt_disclosure=attempt_disclosure,
            )
            print(
                json.dumps(
                    {
                        "status": report["secondary_semantic_summary"]["status"],
                        "output": str(args.output_json),
                        "primary_failure_preserved": report["primary_failure_preserved"],
                        "semantic": report["secondary_semantic_summary"],
                    },
                    sort_keys=True,
                )
            )
    except SemanticAuditError as exc:
        print(f"semantic audit failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
