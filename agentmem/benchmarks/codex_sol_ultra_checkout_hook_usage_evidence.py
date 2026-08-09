"""Render sanitized usage evidence from the frozen Sol Ultra checkout-hook ABBA run.

This module never invokes Codex, a model, a hook, or the network. It preserves only the
four accepted observations needed to audit answer quality, token-priced estimates,
end-to-end wall time, and checkout-hook receipts.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

REPORT_DATE = "2026-08-08"
PUBLIC_QUESTION = "When did Caroline go to the LGBTQ support group?"
GOLD_ANSWER = "7 May 2023"

RATES_PER_MILLION = {
    "uncached_input_tokens": Decimal("125.0"),
    "cached_input_tokens": Decimal("12.5"),
    "output_tokens": Decimal("750.0"),
}

# Controlled execution order: baseline, candidate, candidate, baseline (ABBA).
ABBA_RUNS: tuple[dict[str, object], ...] = (
    {
        "sequence": 1,
        "arm": "baseline",
        "repetition": 1,
        "answer": "7 May 2023",
        "wall_time_ms": 3148.002,
        "usage": {
            "input_tokens": 25938,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "uncached_input_tokens": 25938,
            "output_tokens": 9,
            "reasoning_output_tokens": 0,
        },
        "hook_receipt": None,
    },
    {
        "sequence": 2,
        "arm": "candidate",
        "repetition": 1,
        "answer": "7 May 2023",
        "wall_time_ms": 23613.335,
        "usage": {
            "input_tokens": 12718,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "uncached_input_tokens": 12718,
            "output_tokens": 9,
            "reasoning_output_tokens": 0,
        },
        "hook_receipt": {
            "injected": True,
            "retrieval_degraded": False,
            "elapsed_ms": 18834,
        },
    },
    {
        "sequence": 3,
        "arm": "candidate",
        "repetition": 2,
        "answer": "7 May 2023",
        "wall_time_ms": 20458.282,
        "usage": {
            "input_tokens": 12718,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "uncached_input_tokens": 12718,
            "output_tokens": 9,
            "reasoning_output_tokens": 0,
        },
        "hook_receipt": {
            "injected": True,
            "retrieval_degraded": False,
            "elapsed_ms": 15474,
        },
    },
    {
        "sequence": 4,
        "arm": "baseline",
        "repetition": 2,
        "answer": "7 May 2023",
        "wall_time_ms": 3466.147,
        "usage": {
            "input_tokens": 25938,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "uncached_input_tokens": 25938,
            "output_tokens": 9,
            "reasoning_output_tokens": 0,
        },
        "hook_receipt": None,
    },
)

_PUBLICATION_DENYLIST = (
    re.compile(r"(?i)\b[a-z]:[\\/]"),
    re.compile(r"(?i)(?:/home/|/users/|%userprofile%)"),
    re.compile(r"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token|authorization)\s*[=:]"),
)


def _quantized(value: Decimal, places: str) -> float:
    return float(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def estimated_sol_credits(usage: Mapping[str, int]) -> Decimal:
    """Price one recorded turn using the report's fixed estimated Sol rates."""

    required = set(RATES_PER_MILLION) | {
        "cache_write_input_tokens",
        "input_tokens",
        "reasoning_output_tokens",
    }
    missing = required.difference(usage)
    if missing:
        raise ValueError(f"usage is missing fields: {sorted(missing)}")
    accounted_input = (
        usage["uncached_input_tokens"]
        + usage["cached_input_tokens"]
        + usage["cache_write_input_tokens"]
    )
    if accounted_input != usage["input_tokens"]:
        raise ValueError("input token accounting does not balance")
    if usage["cache_write_input_tokens"] != 0:
        raise ValueError("cache-write pricing is undocumented; expected zero tokens")
    if usage["reasoning_output_tokens"] > usage["output_tokens"]:
        raise ValueError("reasoning output is already included in output_tokens")
    weighted = sum(
        Decimal(usage[field]) * rate for field, rate in RATES_PER_MILLION.items()
    )
    return weighted / Decimal(1000000)


def calculate_economics(runs: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Recompute pooled estimated credits and same-budget extension from four runs."""

    if [run["arm"] for run in runs] != ["baseline", "candidate", "candidate", "baseline"]:
        raise ValueError("expected controlled ABBA execution order")
    credits = [estimated_sol_credits(run["usage"]) for run in runs]
    baseline_total = sum(
        (credit for run, credit in zip(runs, credits, strict=True) if run["arm"] == "baseline"),
        Decimal(),
    )
    candidate_total = sum(
        (
            credit
            for run, credit in zip(runs, credits, strict=True)
            if run["arm"] == "candidate"
        ),
        Decimal(),
    )
    multiplier = baseline_total / candidate_total
    extension = (multiplier - Decimal(1)) * Decimal(100)
    return {
        "baseline_pooled_estimated_sol_credits": _quantized(baseline_total, "0.0001"),
        "candidate_pooled_estimated_sol_credits": _quantized(candidate_total, "0.0001"),
        "same_budget_usage_multiplier": _quantized(multiplier, "0.000000001"),
        "same_budget_usage_extension_percent": _quantized(extension, "0.000000001"),
        "publication_display": (
            f"{multiplier.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP)}x / "
            f"+{extension.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%"
        ),
    }


def publication_safety_violations(payload: object) -> list[str]:
    """Return redaction-rule names triggered by a publication payload."""

    serialized = json.dumps(payload, ensure_ascii=False)
    return [pattern.pattern for pattern in _PUBLICATION_DENYLIST if pattern.search(serialized)]


def build_report() -> dict[str, object]:
    """Build the deterministic controlled-checkout evidence report."""

    published_runs = []
    for run in ABBA_RUNS:
        usage = dict(run["usage"])
        receipt = run["hook_receipt"]
        published_runs.append(
            {
                "sequence": run["sequence"],
                "arm": run["arm"],
                "repetition": run["repetition"],
                "answer": run["answer"],
                "exact_answer_match": str(run["answer"]).strip() == GOLD_ANSWER,
                "wall_time_ms": run["wall_time_ms"],
                "usage": usage,
                "estimated_sol_credits": _quantized(estimated_sol_credits(usage), "0.0001"),
                "hook_receipt": None if receipt is None else dict(receipt),
            }
        )

    baseline_second = next(
        run for run in published_runs if run["arm"] == "baseline" and run["repetition"] == 2
    )
    candidate_second = next(
        run for run in published_runs if run["arm"] == "candidate" and run["repetition"] == 2
    )
    wall_ratio = Decimal(str(candidate_second["wall_time_ms"])) / Decimal(
        str(baseline_second["wall_time_ms"])
    )
    report: dict[str, object] = {
        "schema": "lians.codex-sol-ultra-checkout-hook-usage-evidence.v1",
        "report_date": REPORT_DATE,
        "measurement": {
            "surface": "controlled checkout-hook ABBA",
            "provider": "OpenAI Codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
            "service_tier": "default",
            "normal_installed_plugin_loader_exercised": False,
            "model_facing_tools_enabled": False,
            "execution_order": ["baseline", "candidate", "candidate", "baseline"],
        },
        "public_locomo_item": {
            "question": PUBLIC_QUESTION,
            "gold_answer": GOLD_ANSWER,
            "quality_rule": "trim outer whitespace, then require exact gold string",
        },
        "estimated_credit_accounting": {
            "label": "estimated Sol credits; not a provider-reported per-turn debit",
            "token_source": "Codex turn.completed usage fields",
            "as_of": REPORT_DATE,
            "rate_source": {
                "title": "Codex pricing",
                "url": "https://learn.chatgpt.com/docs/pricing",
                "accessed": REPORT_DATE,
            },
            "rates_per_million_tokens": {
                field: float(rate) for field, rate in RATES_PER_MILLION.items()
            },
            "reasoning_output_treatment": (
                "reasoning_output_tokens is disclosed but already included in output_tokens"
            ),
        },
        "runs": published_runs,
        "quality_gate": {
            "exact_matches": sum(run["exact_answer_match"] for run in published_runs),
            "runs": len(published_runs),
            "passed": all(run["exact_answer_match"] for run in published_runs),
            "candidate_receipts_injected": all(
                run["hook_receipt"] is not None
                and run["hook_receipt"]["injected"]
                and not run["hook_receipt"]["retrieval_degraded"]
                for run in published_runs
                if run["arm"] == "candidate"
            ),
        },
        "economics": calculate_economics(ABBA_RUNS),
        "selected_second_repeat_wall": {
            "baseline_ms": baseline_second["wall_time_ms"],
            "candidate_ms": candidate_second["wall_time_ms"],
            "candidate_over_baseline_ratio": _quantized(wall_ratio, "0.000000001"),
            "candidate_end_to_end_wall_slower": True,
        },
        "current_installed_plugin_paid_ab": {
            "accepted": False,
            "status": "rejected before baseline",
            "reason": "the host failed to dispatch the trusted installed-plugin hook",
            "accepted_installed_plugin_economics_result_exists": False,
        },
        "claim_boundary": {
            "supported": (
                "The 2.035x same-budget result and +103.51% extension apply only to this "
                "four-run Sol Ultra LOCOMO checkout-hook workload using estimated credits."
            ),
            "estimated_credits_not_provider_debits": True,
            "installed_public_plugin_end_to_end_result": False,
            "universal_usage_extension_supported": False,
            "candidate_end_to_end_wall_was_slower": True,
            "other_prompts_models_machines_and_concurrency_not_measured": True,
        },
    }
    violations = publication_safety_violations(report)
    if violations:
        raise ValueError(f"publication-safety validation failed: {violations}")
    return report


def render_report() -> str:
    return json.dumps(build_report(), indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    rendered = render_report()
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
