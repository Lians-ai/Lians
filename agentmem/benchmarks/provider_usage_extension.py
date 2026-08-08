"""Offline, provider-neutral evaluation of same-budget AI usage extension.

This evaluator deliberately separates two quantities that are easy to confuse:

* ``85% usage extension`` means 1.85 times as many comparable tasks for the
  same budget. It requires candidate per-task cost to be no more than
  ``1 / 1.85`` (about 54.05%) of baseline cost.
* ``85% token reduction`` means the candidate uses 15% of the measured token
  quantity. Under equal token weights that would imply about 6.67 times as many
  tasks, not 1.85 times.

The input is a single JSON object. A minimal example is::

    {
      "provider": "example-provider",
      "workload": "memory-recall-v1",
      "target_usage_extension_percent": 85,
      "quality_gate": {
        "protected_checks": [
          {"name": "answer_correct", "passed": true}
        ]
      },
      "baseline": {"raw_input_tokens": 1000, "output_tokens": 100},
      "candidate": {"raw_input_tokens": 450, "output_tokens": 100},
      "token_weights": {"raw_input": 1, "output": 1}
    }

When both measurements provide ``reported_cost`` and the same
``reported_cost_unit``, that provider-reported value is the economic basis.
``reported_credits`` is accepted as a convenience and has the implicit unit
``credits``. Locally calculated amounts must instead use ``estimated_cost`` or
``estimated_credits`` plus root-level ``estimate_metadata`` that names the
method and source. Otherwise a token-only weighted proxy is used. Baseline and
candidate must expose the same token dimensions and input-accounting basis. Raw
input is never double-counted when both cached and uncached input counts are
supplied.

The result is evidence for the named measured workload only. It is not a
universal model, provider, latency, quality, token, credit, or cost guarantee.

Run from the ``agentmem`` directory::

    python -m benchmarks.provider_usage_extension case.json --out report.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "lians.provider-usage-extension.v1"
TOKEN_FIELDS = (
    "raw_input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
)
WEIGHT_FIELDS = ("raw_input", "cached_input", "uncached_input", "output")
DEFAULT_WEIGHTS = {name: 1.0 for name in WEIGHT_FIELDS}


class EvaluationError(ValueError):
    """Raised when an evaluation case is incomplete or internally inconsistent."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EvaluationError(f"{path} must be an object")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{path} must be a non-empty string")
    return value.strip()


def _number(value: Any, path: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{path} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise EvaluationError(f"{path} must be finite")
    if minimum is not None and result < minimum:
        raise EvaluationError(f"{path} must be at least {minimum:g}")
    return result


def _token_count(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvaluationError(f"{path} must be a non-negative integer")
    return value


def _round(value: float, places: int = 6) -> float:
    return round(value, places)


def _parse_quality_gate(value: Any) -> dict[str, Any]:
    gate = _mapping(value, "quality_gate")
    raw_checks = gate.get("protected_checks")
    if not isinstance(raw_checks, Sequence) or isinstance(raw_checks, (str, bytes)):
        raise EvaluationError("quality_gate.protected_checks must be a non-empty array")
    if not raw_checks:
        raise EvaluationError("quality_gate.protected_checks must not be empty")

    checks: list[dict[str, Any]] = []
    names: set[str] = set()
    failures: list[str] = []
    for index, raw_check in enumerate(raw_checks):
        check = _mapping(raw_check, f"quality_gate.protected_checks[{index}]")
        name = _text(check.get("name"), f"quality_gate.protected_checks[{index}].name")
        if name in names:
            raise EvaluationError(f"duplicate protected quality check: {name}")
        names.add(name)
        passed = check.get("passed")
        if not isinstance(passed, bool):
            raise EvaluationError(
                f"quality_gate.protected_checks[{index}].passed must be a boolean"
            )
        checks.append({"name": name, "passed": passed})
        if not passed:
            failures.append(f"protected check failed: {name}")

    score_names = (
        "baseline_score",
        "candidate_score",
        "minimum_candidate_score",
        "maximum_score_regression",
    )
    scores: dict[str, float] = {}
    for name in score_names:
        if name in gate:
            scores[name] = _number(gate[name], f"quality_gate.{name}")

    if "minimum_candidate_score" in scores and "candidate_score" not in scores:
        raise EvaluationError(
            "quality_gate.candidate_score is required with minimum_candidate_score"
        )
    if "maximum_score_regression" in scores:
        if "baseline_score" not in scores or "candidate_score" not in scores:
            raise EvaluationError(
                "baseline_score and candidate_score are required with maximum_score_regression"
            )
        if scores["maximum_score_regression"] < 0:
            raise EvaluationError("quality_gate.maximum_score_regression cannot be negative")

    candidate_score = scores.get("candidate_score")
    minimum_score = scores.get("minimum_candidate_score")
    if candidate_score is not None and minimum_score is not None:
        if candidate_score < minimum_score:
            failures.append(
                f"candidate score {candidate_score:g} is below minimum {minimum_score:g}"
            )

    baseline_score = scores.get("baseline_score")
    maximum_regression = scores.get("maximum_score_regression")
    if (
        baseline_score is not None
        and candidate_score is not None
        and maximum_regression is not None
        and baseline_score - candidate_score > maximum_regression + 1e-12
    ):
        observed = baseline_score - candidate_score
        failures.append(f"score regression {observed:g} exceeds maximum {maximum_regression:g}")

    return {
        "evaluated_first": True,
        "passed": not failures,
        "protected_checks": checks,
        **scores,
        "failures": failures,
    }


def _parse_measurement(value: Any, path: str) -> dict[str, Any]:
    raw = _mapping(value, path)
    token_counts: dict[str, int] = {}
    for name in TOKEN_FIELDS:
        if name in raw:
            token_counts[name] = _token_count(raw[name], f"{path}.{name}")

    cached_present = "cached_input_tokens" in token_counts
    uncached_present = "uncached_input_tokens" in token_counts
    if cached_present != uncached_present:
        raise EvaluationError(
            f"{path} must provide cached_input_tokens and uncached_input_tokens together"
        )
    if cached_present:
        token_input_basis = "cached_uncached_input"
        if "raw_input_tokens" in token_counts:
            component_total = (
                token_counts["cached_input_tokens"] + token_counts["uncached_input_tokens"]
            )
            if token_counts["raw_input_tokens"] != component_total:
                raise EvaluationError(
                    f"{path}.raw_input_tokens must equal cached_input_tokens + "
                    f"uncached_input_tokens exactly ({token_counts['raw_input_tokens']} != "
                    f"{component_total})"
                )
    elif "raw_input_tokens" in token_counts:
        token_input_basis = "raw_input"
    else:
        token_input_basis = "no_input_tokens"

    has_cost = "reported_cost" in raw
    has_credits = "reported_credits" in raw
    has_estimated_cost = "estimated_cost" in raw
    has_estimated_credits = "estimated_credits" in raw
    if has_cost and has_credits:
        raise EvaluationError(f"{path} cannot set both reported_cost and reported_credits")
    if has_estimated_cost and has_estimated_credits:
        raise EvaluationError(f"{path} cannot set both estimated_cost and estimated_credits")
    if (has_cost or has_credits) and (has_estimated_cost or has_estimated_credits):
        raise EvaluationError(
            f"{path} cannot mix provider-reported and locally estimated cost fields"
        )

    reported_cost: float | None = None
    reported_cost_unit: str | None = None
    if has_credits:
        reported_cost = _number(raw["reported_credits"], f"{path}.reported_credits", minimum=0)
        reported_cost_unit = "credits"
    elif has_cost:
        reported_cost = _number(raw["reported_cost"], f"{path}.reported_cost", minimum=0)
        if "reported_cost_unit" not in raw:
            raise EvaluationError(f"{path}.reported_cost_unit is required with reported_cost")
        reported_cost_unit = _text(
            raw["reported_cost_unit"], f"{path}.reported_cost_unit"
        ).casefold()
    elif "reported_cost_unit" in raw:
        raise EvaluationError(f"{path}.reported_cost_unit requires reported_cost")

    if reported_cost == 0:
        raise EvaluationError(f"{path} provider-reported cost must be greater than zero")

    estimated_cost: float | None = None
    estimated_cost_unit: str | None = None
    if has_estimated_credits:
        estimated_cost = _number(raw["estimated_credits"], f"{path}.estimated_credits", minimum=0)
        estimated_cost_unit = "credits"
    elif has_estimated_cost:
        estimated_cost = _number(raw["estimated_cost"], f"{path}.estimated_cost", minimum=0)
        if "estimated_cost_unit" not in raw:
            raise EvaluationError(f"{path}.estimated_cost_unit is required with estimated_cost")
        estimated_cost_unit = _text(
            raw["estimated_cost_unit"], f"{path}.estimated_cost_unit"
        ).casefold()
    elif "estimated_cost_unit" in raw:
        raise EvaluationError(f"{path}.estimated_cost_unit requires estimated_cost")

    if estimated_cost == 0:
        raise EvaluationError(f"{path} estimated cost must be greater than zero")

    return {
        "token_counts": token_counts,
        "token_input_basis": token_input_basis,
        "reported_cost": reported_cost,
        "reported_cost_unit": reported_cost_unit,
        "estimated_cost": estimated_cost,
        "estimated_cost_unit": estimated_cost_unit,
    }


def _parse_estimate_metadata(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "estimate_metadata")
    metadata: dict[str, Any] = {
        "method": _text(raw.get("method"), "estimate_metadata.method"),
        "source": _text(raw.get("source"), "estimate_metadata.source"),
    }
    for name in ("source_url", "as_of", "notes"):
        if name in raw:
            metadata[name] = _text(raw[name], f"estimate_metadata.{name}")

    if "rates" in raw:
        raw_rates = _mapping(raw["rates"], "estimate_metadata.rates")
        if not raw_rates:
            raise EvaluationError("estimate_metadata.rates must not be empty when provided")
        rates: dict[str, float] = {}
        for raw_name, raw_value in raw_rates.items():
            name = _text(raw_name, "estimate_metadata.rates key")
            rates[name] = _number(raw_value, f"estimate_metadata.rates.{name}", minimum=0)
        metadata["rates"] = rates
    return metadata


def _parse_weights(value: Any) -> dict[str, float]:
    if value is None:
        return dict(DEFAULT_WEIGHTS)
    raw = _mapping(value, "token_weights")
    unknown = sorted(set(raw) - set(WEIGHT_FIELDS))
    if unknown:
        raise EvaluationError(f"unknown token weight(s): {', '.join(unknown)}")
    weights = dict(DEFAULT_WEIGHTS)
    for name, item in raw.items():
        weights[name] = _number(item, f"token_weights.{name}", minimum=0)
    return weights


def _weighted_tokens(token_counts: Mapping[str, int], weights: Mapping[str, float]) -> float | None:
    cached_present = "cached_input_tokens" in token_counts
    uncached_present = "uncached_input_tokens" in token_counts

    total = 0.0
    accounted = False
    if cached_present and uncached_present:
        total += token_counts["cached_input_tokens"] * weights["cached_input"]
        total += token_counts["uncached_input_tokens"] * weights["uncached_input"]
        accounted = True
    elif "raw_input_tokens" in token_counts:
        # Components may still be displayed, but incomplete components are not
        # treated as the whole input and raw input is charged exactly once.
        total += token_counts["raw_input_tokens"] * weights["raw_input"]
        accounted = True

    if "output_tokens" in token_counts:
        total += token_counts["output_tokens"] * weights["output"]
        accounted = True
    return total if accounted else None


def _resolve_accounting(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    weights: Mapping[str, float],
    estimate_metadata: Any,
) -> dict[str, Any]:
    baseline_tokens = baseline["token_counts"]
    candidate_tokens = candidate["token_counts"]
    baseline_has_output = "output_tokens" in baseline_tokens
    candidate_has_output = "output_tokens" in candidate_tokens
    if baseline_has_output != candidate_has_output:
        raise EvaluationError(
            "baseline and candidate must either both provide output_tokens or both omit it"
        )

    baseline_input_basis = baseline["token_input_basis"]
    candidate_input_basis = candidate["token_input_basis"]
    if baseline_input_basis != candidate_input_basis:
        raise EvaluationError(
            "baseline and candidate token input accounting basis must match "
            f"({baseline_input_basis} != {candidate_input_basis})"
        )

    baseline_dimensions = set(baseline_tokens)
    candidate_dimensions = set(candidate_tokens)
    if baseline_dimensions != candidate_dimensions:
        baseline_only = sorted(baseline_dimensions - candidate_dimensions)
        candidate_only = sorted(candidate_dimensions - baseline_dimensions)
        raise EvaluationError(
            "baseline and candidate must provide the same token accounting dimensions "
            f"(baseline-only={baseline_only}, candidate-only={candidate_only})"
        )

    baseline_reported = baseline["reported_cost"]
    candidate_reported = candidate["reported_cost"]
    if (baseline_reported is None) != (candidate_reported is None):
        raise EvaluationError(
            "baseline and candidate must both provide provider-reported cost/credits, or neither"
        )

    baseline_estimated = baseline["estimated_cost"]
    candidate_estimated = candidate["estimated_cost"]
    if (baseline_estimated is None) != (candidate_estimated is None):
        raise EvaluationError(
            "baseline and candidate must both provide estimated cost/credits, or neither"
        )
    if baseline_reported is not None and baseline_estimated is not None:
        raise EvaluationError("provider-reported and estimated accounting bases cannot be mixed")

    parsed_estimate_metadata: dict[str, Any] | None = None
    if baseline_estimated is not None:
        if estimate_metadata is None:
            raise EvaluationError(
                "estimate_metadata with method and source is required for estimated cost/credits"
            )
        parsed_estimate_metadata = _parse_estimate_metadata(estimate_metadata)
    elif estimate_metadata is not None:
        raise EvaluationError("estimate_metadata requires estimated cost/credits on both sides")

    baseline_weighted = _weighted_tokens(baseline_tokens, weights)
    candidate_weighted = _weighted_tokens(candidate_tokens, weights)

    if baseline_reported is not None:
        baseline_unit = baseline["reported_cost_unit"]
        candidate_unit = candidate["reported_cost_unit"]
        if baseline_unit != candidate_unit:
            raise EvaluationError("baseline and candidate provider-reported cost units must match")
        basis = "provider_reported_cost"
        unit = baseline_unit
        baseline_cost = baseline_reported
        candidate_cost = candidate_reported
    elif baseline_estimated is not None:
        baseline_unit = baseline["estimated_cost_unit"]
        candidate_unit = candidate["estimated_cost_unit"]
        if baseline_unit != candidate_unit:
            raise EvaluationError("baseline and candidate estimated cost units must match")
        basis = "estimated_cost"
        unit = baseline_unit
        baseline_cost = baseline_estimated
        candidate_cost = candidate_estimated
    else:
        if baseline_weighted is None or candidate_weighted is None:
            raise EvaluationError(
                "token-only accounting requires token counts for both baseline and candidate"
            )
        if baseline_weighted <= 0 or candidate_weighted <= 0:
            raise EvaluationError("weighted token cost must be greater than zero on both sides")
        basis = "weighted_tokens"
        unit = "weighted_token_units"
        baseline_cost = baseline_weighted
        candidate_cost = candidate_weighted

    return {
        "basis": basis,
        "unit": unit,
        "token_input_basis": baseline_input_basis,
        "token_weights": dict(weights),
        "estimate_metadata": parsed_estimate_metadata,
        "baseline": {
            "token_counts": baseline_tokens,
            "weighted_token_cost": baseline_weighted,
            "reported_cost": baseline_reported,
            "reported_cost_unit": baseline["reported_cost_unit"],
            "estimated_cost": baseline_estimated,
            "estimated_cost_unit": baseline["estimated_cost_unit"],
            "effective_task_cost": baseline_cost,
        },
        "candidate": {
            "token_counts": candidate_tokens,
            "weighted_token_cost": candidate_weighted,
            "reported_cost": candidate_reported,
            "reported_cost_unit": candidate["reported_cost_unit"],
            "estimated_cost": candidate_estimated,
            "estimated_cost_unit": candidate["estimated_cost_unit"],
            "effective_task_cost": candidate_cost,
        },
    }


def _token_changes(
    baseline: Mapping[str, int], candidate: Mapping[str, int]
) -> dict[str, dict[str, float | int | None]]:
    changes: dict[str, dict[str, float | int | None]] = {}
    for name in TOKEN_FIELDS:
        if name not in baseline or name not in candidate:
            continue
        before = baseline[name]
        after = candidate[name]
        ratio = after / before if before else None
        reduction = (1.0 - ratio) * 100.0 if ratio is not None else None
        changes[name] = {
            "baseline": before,
            "candidate": after,
            "delta": after - before,
            "candidate_ratio": _round(ratio) if ratio is not None else None,
            "reduction_percent": _round(reduction) if reduction is not None else None,
        }
    return changes


def evaluate_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one measured baseline/candidate case without network access.

    Protected quality is evaluated before any economics. A candidate can meet
    the mathematical cost threshold yet remain unqualified when a protected
    quality check or score constraint fails.
    """

    root = _mapping(case, "case")
    provider = _text(root.get("provider"), "provider")
    workload = _text(root.get("workload"), "workload")

    # Keep this first: economic efficiency cannot compensate for failed quality.
    quality = _parse_quality_gate(root.get("quality_gate"))

    extension_percent = _number(
        root.get("target_usage_extension_percent", 85.0),
        "target_usage_extension_percent",
        minimum=0,
    )
    usage_multiplier = 1.0 + extension_percent / 100.0
    maximum_cost_ratio = 1.0 / usage_multiplier
    required_reduction_percent = (1.0 - maximum_cost_ratio) * 100.0

    baseline = _parse_measurement(root.get("baseline"), "baseline")
    candidate = _parse_measurement(root.get("candidate"), "candidate")
    weights = _parse_weights(root.get("token_weights"))
    accounting = _resolve_accounting(baseline, candidate, weights, root.get("estimate_metadata"))

    baseline_cost = accounting["baseline"]["effective_task_cost"]
    candidate_cost = accounting["candidate"]["effective_task_cost"]
    cost_ratio = candidate_cost / baseline_cost
    observed_multiplier = baseline_cost / candidate_cost
    observed_extension_percent = (observed_multiplier - 1.0) * 100.0
    observed_reduction_percent = (1.0 - cost_ratio) * 100.0
    economic_target_met = cost_ratio <= maximum_cost_ratio + 1e-12
    qualified = quality["passed"] and economic_target_met

    if not quality["passed"]:
        status = "quality_gate_failed"
        statement = (
            "This measured case did not qualify because protected quality failed; "
            "cost observations are diagnostic only. No universal savings conclusion "
            "is supported."
        )
    elif not economic_target_met:
        status = "usage_extension_target_missed"
        statement = (
            f"This measured {workload} case for {provider} preserved protected quality "
            f"but did not reach the requested {usage_multiplier:.2f}x same-budget usage "
            "target. No universal savings conclusion is supported."
        )
    else:
        status = "qualified_for_measured_workload"
        statement = (
            f"In this measured {workload} case for {provider}, the candidate preserved "
            f"protected quality and reached {observed_multiplier:.2f}x same-budget usage "
            f"using {accounting['basis']}. This result is workload-specific, not a "
            "universal savings guarantee."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "scope": {"provider": provider, "workload": workload},
        "evaluation_order": ["protected_quality", "same_budget_economics"],
        "target": {
            "requested_usage_extension_percent": _round(extension_percent),
            "same_budget_usage_multiplier": _round(usage_multiplier),
            "maximum_candidate_cost_ratio": _round(maximum_cost_ratio),
            "minimum_task_cost_reduction_percent": _round(required_reduction_percent),
            "interpretation": (
                f"A {extension_percent:g}% usage extension means {usage_multiplier:g}x tasks "
                f"for the same budget, so candidate per-task cost must be at most "
                f"{maximum_cost_ratio * 100:.4f}% of baseline. It does not mean an "
                f"{extension_percent:g}% token reduction."
            ),
        },
        "quality_gate": quality,
        "accounting": accounting,
        "observed": {
            "candidate_cost_ratio": _round(cost_ratio),
            "task_cost_reduction_percent": _round(observed_reduction_percent),
            "same_budget_usage_multiplier": _round(observed_multiplier),
            "same_budget_usage_extension_percent": _round(observed_extension_percent),
            "token_changes": _token_changes(baseline["token_counts"], candidate["token_counts"]),
        },
        "verdict": {
            "economic_target_met": economic_target_met,
            "protected_quality_passed": quality["passed"],
            "qualified_target_met": qualified,
            "status": status,
            "statement": statement,
        },
        "limitations": [
            "The result applies only to the named provider, workload, measurements, and weights.",
            "Weighted tokens are a proxy unless both sides provide comparable provider cost or credits.",
            "No unmeasured claim about quality, recall, latency, tokens, cost, or credits is implied.",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a measured same-budget usage-extension case offline."
    )
    parser.add_argument("case", type=Path, help="input JSON case")
    parser.add_argument("--out", type=Path, help="optional report JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.case.read_text(encoding="utf-8"))
        report = evaluate_case(payload)
    except (OSError, json.JSONDecodeError, EvaluationError) as exc:
        parser.error(str(exc))

    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
