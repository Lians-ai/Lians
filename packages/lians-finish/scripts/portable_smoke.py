"""Smoke-test the already-running standalone Lians application."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path


def _get(url: str, token: str | None = None) -> tuple[bytes, dict[str, str]]:
    headers = {"X-Lians-Token": token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(), dict(response.headers.items())


def _post(url: str, token: str, value: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(value).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Lians-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    base = args.url.rstrip("/")

    health = json.loads(_get(f"{base}/api/health")[0])
    if health != {"status": "ok", "scope": "loopback"}:
        raise RuntimeError(f"unexpected health response: {health}")
    index, headers = _get(f"{base}/")
    if headers.get("X-Frame-Options") != "DENY":
        raise RuntimeError("standalone application did not supply security headers")
    match = re.search(rb'<meta name="lians-token" content="([^"]+)">', index)
    if not match:
        raise RuntimeError("standalone application did not embed its local token")
    token = match.group(1).decode("ascii")
    wordmark = _get(f"{base}/assets/wordmark.png")[0]
    wordmark_hash = hashlib.sha256(wordmark).hexdigest()
    expected_wordmark = "51495b5fc3e9dd339e5d2a5d4f4ae4c82f703c7d2ded21254d087c36b836cd4d"
    if wordmark_hash != expected_wordmark:
        raise RuntimeError("standalone application wordmark bytes changed")
    before = json.loads(_get(f"{base}/api/runs", token)[0])["runs"]
    if before:
        raise RuntimeError("standalone smoke data directory was not empty")
    exported = _post(
        f"{base}/api/agent/export",
        token,
        {
            "repository": str(args.repo.resolve()),
            "task": "Move this governed mission without running it",
            "constraints": "Do not store credentials",
            "definition_of_done": "The portable smoke test passes",
            "mode": "protect",
            "verification_command": "python -m unittest discover -s tests -v",
        },
    )
    serialized_bundle = json.dumps(exported["bundle"])
    if str(args.repo.resolve()) in serialized_bundle:
        raise RuntimeError("portable agent disclosed the full local workspace path")
    imported = _post(
        f"{base}/api/agent/import",
        token,
        {"bundle_text": serialized_bundle},
    )
    after = json.loads(_get(f"{base}/api/runs", token)[0])["runs"]
    beta = json.loads(_get(f"{base}/api/beta/report", token)[0])
    beta_report = beta.get("report", {})
    if (
        after
        or imported["task"] != "Move this governed mission without running it"
        or imported["repository"]
        or imported["workspace_name"] != args.repo.resolve().name
    ):
        raise RuntimeError("standalone portable-agent round trip changed execution state")
    if (
        not str(beta.get("filename", "")).startswith("lians-proof-gate-beta-")
        or beta_report.get("aggregate", {}).get("eligible_runs") != 0
        or beta_report.get("runs") != []
        or beta_report.get("participant_id") is None
    ):
        raise RuntimeError("standalone beta report did not preserve the empty-run contract")
    print(
        json.dumps(
            {
                "health": "passed",
                "security_headers": "passed",
                "wordmark_sha256": wordmark_hash,
                "portable_agent": "passed",
                "privacy_bounded_beta_report": "passed",
                "workspace_path_exported": False,
                "runs_before": len(before),
                "runs_after": len(after),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
