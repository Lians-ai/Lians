#!/usr/bin/env python3
"""Authorize a publication only from the default branch or a protected tag."""

from __future__ import annotations

import os
import subprocess


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"required publication context {name} is missing")
    return value


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def main() -> None:
    event = _required("GITHUB_EVENT_NAME")
    ref = _required("GITHUB_REF")
    source_sha = _required("GITHUB_SHA")
    default_branch = _required("LIANS_DEFAULT_BRANCH")
    protected = os.environ.get("LIANS_REF_PROTECTED", "").strip().lower()

    # GITHUB_SHA can identify an annotated tag object. Compare the peeled commit
    # so annotated and lightweight protected tags receive the same policy.
    source_commit = _git("rev-parse", f"{source_sha}^{{commit}}")
    head = _git("rev-parse", "HEAD^{commit}")
    if head != source_commit:
        raise SystemExit(
            f"checked-out HEAD {head} does not match source commit {source_commit}"
        )

    if event == "workflow_dispatch":
        expected = f"refs/heads/{default_branch}"
        if ref != expected:
            raise SystemExit(
                f"manual publication requires the default-branch ref {expected}, got {ref}"
            )
    elif event == "push":
        if not ref.startswith("refs/tags/"):
            raise SystemExit("tag publication requires a refs/tags/* source")
        if protected != "true":
            raise SystemExit("publication tags must be protected by a repository ruleset")
        default_ref = f"refs/remotes/origin/{default_branch}"
        try:
            _git("show-ref", "--verify", default_ref)
            _git("merge-base", "--is-ancestor", source_commit, default_ref)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                f"tag commit {source_commit} is not reachable from {default_ref}; "
                "checkout must use fetch-depth: 0"
            ) from exc
    else:
        raise SystemExit(f"unsupported publication event: {event}")

    print(
        f"publication source authorized: event={event} ref={ref} "
        f"commit={source_commit}"
    )


if __name__ == "__main__":
    main()
