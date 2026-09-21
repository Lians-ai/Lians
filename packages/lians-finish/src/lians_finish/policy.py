"""Deterministic v0 routing policy for Lians Finish."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROUTE_SCHEMA = "lians.finish.route.v1"
ROUTER_VERSION = "rules-0.1.0"
MODE_PREMIUM_BUDGETS = {"protect": 1, "balanced": 2, "maximum": 4}
MODES = set(MODE_PREMIUM_BUDGETS)
IMPLEMENTATION_MODELS = frozenset({"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"})
IMPLEMENTATION_EFFORTS = frozenset({"low", "medium", "high"})

_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s,;]{12,}",
        re.IGNORECASE,
    ),
)


def contains_secret(value: str) -> bool:
    """Return whether text resembles a credential that should not be persisted."""

    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


HIGH_RISK_SIGNALS = {
    "authentication": ("auth", "authentication", "login", "password", "oauth", "session"),
    "authorization": ("permission", "authorization", "rbac", "access control"),
    "money": ("payment", "billing", "stripe", "payout", "invoice"),
    "security": ("security", "vulnerability", "encryption", "secret", "credential"),
    "production_data": ("migration", "production database", "delete data", "customer data"),
    "deployment": ("deploy", "release", "production", "infrastructure"),
}

COMPLEX_SIGNALS = {
    "architecture": ("architecture", "redesign", "rewrite", "system design"),
    "concurrency": ("race condition", "deadlock", "concurrency", "async"),
    "difficult_debugging": ("intermittent", "flaky", "memory leak", "root cause"),
    "large_change": ("refactor", "across the codebase", "multiple services", "end-to-end"),
    "performance": ("performance", "latency", "throughput", "optimize"),
}

SIMPLE_SIGNALS = {
    "documentation": ("readme", "documentation", "docs", "typo", "copy"),
    "small_test": ("add a test", "write a test", "test case"),
    "formatting": ("format", "lint", "rename"),
}


class RouteError(ValueError):
    """Raised when a task cannot be routed safely."""


@dataclass(frozen=True)
class PolicyCatalog:
    """Validated, immutable routing overrides for Protect implementation only."""

    implementation_model: str | None = None
    implementation_effort: str | None = None

    def __post_init__(self) -> None:
        if self.implementation_model is not None and (
            not isinstance(self.implementation_model, str)
            or self.implementation_model not in IMPLEMENTATION_MODELS
        ):
            raise RouteError(
                f"protect.implementation_model must be one of {sorted(IMPLEMENTATION_MODELS)}"
            )
        if self.implementation_effort is not None and (
            not isinstance(self.implementation_effort, str)
            or self.implementation_effort not in IMPLEMENTATION_EFFORTS
        ):
            raise RouteError(
                f"protect.implementation_effort must be one of {sorted(IMPLEMENTATION_EFFORTS)}"
            )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RouteError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_policy_catalog(path: Path | str) -> PolicyCatalog:
    """Load the deliberately narrow JSON policy-file format."""

    policy_path = Path(path)
    try:
        raw = policy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RouteError(f"cannot read policy file {policy_path}: {exc}") from exc
    try:
        document = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise RouteError(f"invalid policy JSON: {exc.msg}") from exc
    if not isinstance(document, dict):
        raise RouteError("policy file root must be an object containing protect")
    if set(document) != {"protect"}:
        raise RouteError("policy file must contain only the top-level protect object")
    protect = document["protect"]
    if not isinstance(protect, dict):
        raise RouteError("policy protect value must be an object")
    allowed = {"implementation_model", "implementation_effort"}
    unknown = set(protect) - allowed
    if unknown:
        raise RouteError(f"unknown protect policy field(s): {sorted(unknown)}")
    for field in allowed:
        if field in protect and not isinstance(protect[field], str):
            raise RouteError(f"protect.{field} must be a string")
    return PolicyCatalog(
        implementation_model=protect.get("implementation_model"),
        implementation_effort=protect.get("implementation_effort"),
    )


@dataclass(frozen=True)
class StagePlan:
    id: str
    purpose: str
    model: str | None
    reasoning_effort: str | None
    sandbox: str | None
    run_when: str
    premium: bool
    reason: str


@dataclass(frozen=True)
class RoutePlan:
    schema: str
    router_version: str
    task: str
    mode: str
    risk: str
    signals: tuple[str, ...]
    max_premium_calls: int
    stages: tuple[StagePlan, ...]
    claim_boundary: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["signals"] = list(self.signals)
        value["stages"] = [asdict(stage) for stage in self.stages]
        value["route_sha256"] = _sha256(value)
        return value


def _sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_task(task: str) -> str:
    if not isinstance(task, str) or not task.strip():
        raise RouteError("task must be non-empty text")
    task = task.strip()
    if len(task) > 8_000:
        raise RouteError("task exceeds 8,000 characters")
    if contains_secret(task):
        raise RouteError("task appears to contain a secret; remove it before routing")
    return task


def _matches(text: str, groups: dict[str, tuple[str, ...]]) -> list[str]:
    return [name for name, terms in groups.items() if any(term in text for term in terms)]


def classify_task(task: str) -> tuple[str, tuple[str, ...]]:
    normalized = task.casefold()
    high = _matches(normalized, HIGH_RISK_SIGNALS)
    complex_matches = _matches(normalized, COMPLEX_SIGNALS)
    simple = _matches(normalized, SIMPLE_SIGNALS)
    signals = tuple(dict.fromkeys([*high, *complex_matches, *simple]))
    if high:
        return "high", signals
    if complex_matches or len(task) > 280:
        return "complex", signals
    if simple:
        return "simple", signals
    return "normal", signals


def _implementation_stage(mode: str, risk: str, catalog: PolicyCatalog) -> StagePlan:
    if mode == "maximum":
        return StagePlan(
            id="implement",
            purpose="Implement the requested change",
            model="gpt-6-astra",
            reasoning_effort="high",
            sandbox="workspace-write",
            run_when="always",
            premium=True,
            reason="Maximum mode prioritizes capability over usage conservation.",
        )
    if mode == "balanced":
        return StagePlan(
            id="implement",
            purpose="Implement the requested change",
            model="gpt-5.6-sol",
            reasoning_effort="medium" if risk != "high" else "high",
            sandbox="workspace-write",
            run_when="always",
            premium=False,
            reason="Sol is the reliable default; high-risk tasks receive more reasoning.",
        )
    model = catalog.implementation_model or (
        "gpt-5.6-luna" if risk == "simple" else "gpt-5.6-terra"
    )
    effort = catalog.implementation_effort or ("low" if risk in {"simple", "normal"} else "medium")
    return StagePlan(
        id="implement",
        purpose="Implement the requested change",
        model=model,
        reasoning_effort=effort,
        sandbox="workspace-write",
        run_when="always",
        premium=False,
        reason="Protect mode starts with the least expensive plausible implementation model.",
    )


def build_route(
    task: str,
    *,
    mode: str = "protect",
    premium_budget: int | None = None,
    policy_catalog: PolicyCatalog | None = None,
) -> RoutePlan:
    """Build a deterministic stage plan without invoking a model."""

    task = _validate_task(task)
    if policy_catalog is not None and not isinstance(policy_catalog, PolicyCatalog):
        raise RouteError("policy_catalog must be a PolicyCatalog")
    catalog = policy_catalog or PolicyCatalog()
    mode = mode.strip().lower()
    if mode not in MODES:
        raise RouteError(f"mode must be one of {sorted(MODES)}")
    risk, signals = classify_task(task)
    max_premium = MODE_PREMIUM_BUDGETS[mode]
    if premium_budget is not None:
        if type(premium_budget) is not int or not 0 <= premium_budget <= max_premium:
            raise RouteError(
                f"premium budget must be an integer between 0 and {max_premium} for mode {mode}"
            )
        max_premium = premium_budget
    review_when = "always" if risk == "high" else "verifier_failure"
    stages = (
        StagePlan(
            id="discover",
            purpose="Inspect the repository and produce a bounded implementation plan",
            model="gpt-5.6-luna" if mode != "maximum" else "gpt-6-astra",
            reasoning_effort="low" if mode != "maximum" else "high",
            sandbox="read-only",
            run_when="always",
            premium=mode == "maximum",
            reason=(
                "Maximum mode provides an Astra-only comparison path."
                if mode == "maximum"
                else "Repository discovery rarely warrants the strongest model."
            ),
        ),
        _implementation_stage(mode, risk, catalog),
        StagePlan(
            id="verify",
            purpose="Run the user-supplied deterministic verification command",
            model=None,
            reasoning_effort=None,
            sandbox=None,
            run_when="after_implementation",
            premium=False,
            reason="Tests and checks decide whether escalation is needed.",
        ),
        StagePlan(
            id="escalate",
            purpose="Repair a failed verification or review a high-risk change",
            model="gpt-6-astra",
            reasoning_effort="ultra" if mode == "maximum" else "high",
            sandbox="workspace-write",
            run_when=review_when,
            premium=True,
            reason=(
                "High-risk work receives a premium review."
                if review_when == "always" and mode != "maximum"
                else "Premium capability is reserved for verifier failure."
            ),
        ),
    )
    return RoutePlan(
        schema=ROUTE_SCHEMA,
        router_version=ROUTER_VERSION,
        task=task,
        mode=mode,
        risk=risk,
        signals=signals,
        max_premium_calls=max_premium,
        stages=stages,
        claim_boundary=(
            "This route is a deterministic policy decision, not evidence that the selected model "
            "will complete the task or reduce provider quota consumption."
        ),
    )
