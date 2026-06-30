"""
Memory admission control — govern what is *allowed into* memory, not just what is
stored. No other agent-memory layer does this; it is the core of the "regulated
memory control plane" posture: before a fact is written, classify it (PII / PHI /
MNPI), score the source, and quarantine prompt-injection attempts, then admit,
reject, or hold it for human review.

Deterministic and dependency-free (regex + heuristics) so decisions are
reproducible and auditable. Swap in Presidio / a classifier later behind the same
``evaluate`` interface.

Modes (config ``admission_mode``):
  off      — no evaluation
  monitor  — evaluate, tag the memory + audit, but always admit (observe first)
  enforce  — reject prompt-injection / blocked-source writes; hold high-risk
             (PII/PHI/MNPI) writes for review
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Detectors ───────────────────────────────────────────────────────────────────

# Reasonably precise PII/PHI patterns (favor precision over recall to limit noise).
_DETECTORS: list[tuple[str, re.Pattern]] = [
    ("pii:ssn",   re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("pii:email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phi:mrn",   re.compile(r"\bMRN[-:\s]?\d{4,}\b", re.I)),
    ("phi:npi",   re.compile(r"\bNPI[-:\s]?\d{10}\b", re.I)),
]

_CREDIT_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")

# Material non-public information (finance) keyword heuristics.
_MNPI = re.compile(
    r"\b(material non[- ]?public|insider information|MNPI|embargoed earnings|"
    r"unannounced (?:merger|acquisition|deal)|pre[- ]?announcement)\b", re.I)

# Prompt-injection / instruction-override attempts.
_INJECTION = re.compile(
    r"(ignore (?:all )?(?:previous|prior|above) instructions|"
    r"disregard (?:the )?(?:system|previous) (?:prompt|instructions)|"
    r"reveal your (?:system )?prompt|"
    r"you are now (?:a|an|in)|override your (?:instructions|guardrails))", re.I)


def _luhn_ok(digits: str) -> bool:
    s = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(s) <= 16:
        return False
    total, parity = 0, len(s) % 2
    for i, d in enumerate(s):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_risk_tags(content: str) -> list[str]:
    """Return risk tags found in ``content`` (e.g. ['pii:ssn', 'mnpi', 'injection'])."""
    tags: list[str] = []
    for tag, rx in _DETECTORS:
        if rx.search(content):
            tags.append(tag)
    for m in _CREDIT_CARD.finditer(content):
        if _luhn_ok(m.group(0)):
            tags.append("pii:credit_card")
            break
    if _MNPI.search(content):
        tags.append("mnpi")
    if _INJECTION.search(content):
        tags.append("injection")
    return tags


# ── Decision ────────────────────────────────────────────────────────────────────


@dataclass
class AdmissionDecision:
    action: str                       # "admit" | "reject" | "review"
    risk_tags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def evaluate(
    content: str,
    source: str | None,
    *,
    mode: str = "monitor",
    blocked_sources: set[str] | None = None,
) -> AdmissionDecision:
    """
    Classify a candidate memory and decide whether to admit it.

    - **injection** or a **blocked source** → reject in enforce mode (never safe to
      admit silently).
    - **PII / PHI / MNPI** present → hold for review in enforce mode.
    - Otherwise → admit. In monitor mode everything is admitted but tagged.
    """
    blocked_sources = blocked_sources or set()
    tags = detect_risk_tags(content)
    reasons: list[str] = []

    src = (source or "").strip().lower()
    if src and src in blocked_sources:
        tags.append("source:blocked")

    if mode == "off":
        return AdmissionDecision("admit", tags, reasons)

    unsafe = "injection" in tags or "source:blocked" in tags
    high_risk = any(t.startswith(("pii:", "phi:")) or t == "mnpi" for t in tags)

    if mode == "enforce":
        if unsafe:
            if "injection" in tags:
                reasons.append("prompt-injection / instruction-override detected")
            if "source:blocked" in tags:
                reasons.append(f"source '{src}' is on the block list")
            return AdmissionDecision("reject", tags, reasons)
        if high_risk:
            reasons.append("sensitive data (PII/PHI/MNPI) requires review before admission")
            return AdmissionDecision("review", tags, reasons)

    # monitor mode, or enforce with no blocking/high-risk findings
    return AdmissionDecision("admit", tags, reasons)
