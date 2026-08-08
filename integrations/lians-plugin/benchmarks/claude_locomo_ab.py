"""Run a bounded Claude LOCOMO A/B without mutating Lians or Claude state.

The baseline gives Claude the complete recorded conversation. The reduced path
gives the same model and question only the top-k memories from an existing Lians
retrieval artifact. This isolates answer-context size while Claude Code's JSON
result supplies provider-observed usage and cost fields. Tools are disabled and
no MCP call runs, so the result is a context-isolation upper bound rather than
an end-to-end plugin measurement.

Use ``--dry-run`` to validate the context target without making model calls.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DATASET = REPO_ROOT / "agentmem" / "benchmarks" / "data" / "locomo10.json"
DEFAULT_PRED = (
    REPO_ROOT
    / "memory-benchmarks"
    / "results"
    / "locomo"
    / "predicted_lians_arctic"
)
SYSTEM_PROMPT = (
    "Answer only from the supplied context. If the answer is absent, say "
    "UNKNOWN. Return only the concise answer, with no explanation."
)


def _full_conversation(conversation: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, session in conversation.items():
        if not key.startswith("session_") or key.endswith("_date_time"):
            continue
        parts.append(f"SESSION ({conversation.get(f'{key}_date_time', '')}):")
        for turn in session:
            parts.append(f"{turn.get('speaker', '')}: {turn.get('text', '')}")
    return "\n".join(parts)


def _retrieved_context(question: dict[str, Any], top_k: int) -> str:
    memories = question["retrieval"]["search_results"][:top_k]
    return "\n".join(
        f"[{memory.get('created_at', '')}] {memory.get('memory', '')}"
        for memory in memories
    )


def _tokens(text: str) -> int:
    try:
        import tiktoken
    except ImportError as exc:  # pragma: no cover - exercised by operators
        raise SystemExit("Install tiktoken to run exact context accounting") from exc
    return len(tiktoken.get_encoding("o200k_base").encode(text))


def _prompt(question: str, reference_date: str, context: str) -> str:
    return (
        f"Reference date: {reference_date}\n"
        f"Question: {question}\n\n"
        f"CONTEXT\n{context}\nEND CONTEXT"
    )


def _claude_path() -> str:
    executable = shutil.which("claude")
    if executable is None:
        raise SystemExit("Claude Code CLI is not installed or is not on PATH")
    return executable


def _run_claude(prompt: str, *, model: str, max_budget_usd: float) -> dict[str, Any]:
    command = [
        _claude_path(),
        "--bare",
        "--print",
        "--output-format",
        "json",
        "--model",
        model,
        "--effort",
        "low",
        "--tools",
        "",
        "--no-session-persistence",
        "--max-budget-usd",
        str(max_budget_usd),
        "--system-prompt",
        SYSTEM_PROMPT,
    ]
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(f"Claude exited {completed.returncode}: {stderr[-1000:]}")
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Claude did not return JSON output") from exc
    usage = raw.get("usage", {})
    return {
        "answer": raw.get("result", ""),
        "duration_ms": raw.get("duration_ms"),
        "duration_api_ms": raw.get("duration_api_ms"),
        "num_turns": raw.get("num_turns"),
        "total_cost_usd": raw.get("total_cost_usd"),
        "usage": usage,
        "total_input_tokens": sum(
            int(usage.get(name, 0) or 0)
            for name in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        ),
    }


def build_report(
    question_file: Path,
    *,
    top_k: int,
    target_reduction: float,
    model: str,
    max_budget_usd: float,
    dry_run: bool,
) -> dict[str, Any]:
    question = json.loads(question_file.read_text(encoding="utf-8"))
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    conversation = dataset[question["conversation_idx"]]["conversation"]
    full_context = _full_conversation(conversation)
    reduced_context = _retrieved_context(question, top_k)
    full_tokens = _tokens(full_context)
    reduced_tokens = _tokens(reduced_context)
    semantic_reduction = 1.0 - (reduced_tokens / full_tokens)

    report: dict[str, Any] = {
        "question_id": question["question_id"],
        "question": question["question"],
        "ground_truth_answer": question.get("ground_truth_answer"),
        "model": model,
        "top_k": top_k,
        "benchmark_scope": "manual_context_isolation_no_tools_or_mcp",
        "mcp_tools_enabled": False,
        "tokenizer": "o200k_base (cross-model context accounting)",
        "semantic_context": {
            "full_tokens": full_tokens,
            "lians_tokens": reduced_tokens,
            "reduction": round(semantic_reduction, 6),
            "target": target_reduction,
            "target_met": semantic_reduction >= target_reduction,
        },
        "dry_run": dry_run,
    }
    recorded = question.get("cutoff_results", {}).get(f"top_{top_k}")
    if recorded:
        report["recorded_lians_cutoff_result"] = recorded

    if not dry_run:
        common = {
            "model": model,
            "max_budget_usd": max_budget_usd,
        }
        baseline = _run_claude(
            _prompt(question["question"], question["reference_date"], full_context),
            **common,
        )
        lians = _run_claude(
            _prompt(question["question"], question["reference_date"], reduced_context),
            **common,
        )
        provider_full = baseline["total_input_tokens"]
        provider_lians = lians["total_input_tokens"]
        provider_reduction = (
            1.0 - (provider_lians / provider_full) if provider_full else None
        )
        baseline_cost = baseline.get("total_cost_usd")
        lians_cost = lians.get("total_cost_usd")
        cost_reduction = (
            1.0 - (lians_cost / baseline_cost)
            if baseline_cost and lians_cost is not None
            else None
        )
        report["claude"] = {
            "full_conversation": baseline,
            "lians_top_k": lians,
            "total_input_reduction": (
                round(provider_reduction, 6)
                if provider_reduction is not None
                else None
            ),
            "total_cost_reduction": (
                round(cost_reduction, 6) if cost_reduction is not None else None
            ),
            "answers_require_review": True,
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", type=Path, default=DEFAULT_PRED)
    parser.add_argument("--question-id", default="conv0_q0")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--target-reduction", type=float, default=0.85)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--max-budget-usd", type=float, default=0.25)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("--top-k must be positive")
    if not 0 < args.target_reduction < 1:
        parser.error("--target-reduction must be between 0 and 1")

    question_file = args.pred / f"{args.question_id}.json"
    if not question_file.is_file():
        raise SystemExit(f"Missing LOCOMO retrieval artifact: {question_file}")
    report = build_report(
        question_file,
        top_k=args.top_k,
        target_reduction=args.target_reduction,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        dry_run=args.dry_run,
    )
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    if not report["semantic_context"]["target_met"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
