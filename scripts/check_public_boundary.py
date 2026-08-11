#!/usr/bin/env python3
"""Fail when private company material crosses into the public repository."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    "LICENSE",
    "NOTICE",
    "OPEN_CORE.md",
    "COMMERCIAL.md",
    "TRADEMARKS.md",
    ".github/CODEOWNERS",
    ".github/pull_request_template.md",
}

FORBIDDEN_PREFIXES = (
    ".private/",
    "apps/",
    "artifacts/",
    "customer-delivery/",
    "deployments/customers/",
    "docs/gtm/",
    "docs/internal/",
    "enterprise/",
    "outreach_research/",
    "outputs/",
    "platform/",
)

FORBIDDEN_PATHS = {
    "lians-gtm-plan.md",
    "docs/openai-universal-plugin-production.md",
    "docs/website-layout.md",
}

ALLOWED_LEGACY_TMP = {
    "tmp/pdfs/regulated-memory-eval/page-1.png",
    "tmp/pdfs/regulated-memory-eval/page-2.png",
    "tmp/pdfs/regulated-memory-eval/page-3.png",
    "tmp/pdfs/regulated-memory-eval/page-4.png",
}

SENSITIVE_SUFFIXES = (".pem", ".p12", ".pfx", ".tfstate")

CLAIM_FILES = (
    "README.md",
    "docs/pricing-tiers.md",
    "docs/billing.md",
    "docs/alternatives-mem0-self-hosted.md",
)

RETIRED_CLAIMS = (
    "the entire feature set is in the open",
    "the entire feature set, including every compliance primitive",
    "product capabilities are not withheld",
    "self-hosted product is apache 2.0 and complete",
    "lians (self-hosted, all open)",
    "apache 2.0, everything",
)


def tracked_files() -> set[str]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT
    ).decode("utf-8")
    return {name for name in output.split("\0") if name}


def main() -> int:
    tracked = tracked_files()
    errors: list[str] = []

    missing = REQUIRED_FILES - tracked
    errors.extend(f"required public-boundary file is missing: {path}" for path in sorted(missing))

    for path in sorted(tracked):
        normalized = path.replace("\\", "/")
        lower = normalized.lower()
        if normalized in FORBIDDEN_PATHS:
            errors.append(f"internal file is tracked publicly: {normalized}")
        if normalized.startswith(FORBIDDEN_PREFIXES):
            errors.append(f"private path is tracked publicly: {normalized}")
        if normalized.startswith("tmp/") and normalized not in ALLOWED_LEGACY_TMP:
            errors.append(f"new scratch artifact is tracked publicly: {normalized}")
        if lower.endswith(SENSITIVE_SUFFIXES) or lower.endswith(".tfstate.backup"):
            errors.append(f"sensitive deployment file is tracked publicly: {normalized}")

    for relative in CLAIM_FILES:
        path = ROOT / relative
        if not path.exists():
            errors.append(f"commercial claim file is missing: {relative}")
            continue
        text = path.read_text(encoding="utf-8").lower()
        for claim in RETIRED_CLAIMS:
            if claim in text:
                errors.append(f"retired all-open claim remains in {relative}: {claim!r}")

    if errors:
        print("Public repository boundary check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        f"Public repository boundary check passed: {len(tracked)} tracked files, "
        "required notices present, no private paths or retired all-open claims found."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
