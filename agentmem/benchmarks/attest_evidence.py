"""Hash a passed benchmark artifact into release-claim evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.release_claims import valid_external_evidence


def attest(
    name: str,
    artifact: Path,
    *,
    methodology: str,
    independent_party: str | None = None,
    source_url: str | None = None,
) -> dict:
    raw = artifact.read_bytes()
    payload = json.loads(raw)
    passed = payload.get("passed")
    if passed is not True:
        raise ValueError("artifact must contain passed=true")
    record = {
        "schema": "lians.evidence.v1",
        "passed": True,
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "methodology": methodology.strip(),
        "artifact": artifact.name,
    }
    if independent_party:
        record["independent_party"] = independent_party.strip()
    if source_url:
        record["source_url"] = source_url.strip()
    if not valid_external_evidence(name, record):
        raise ValueError(
            "evidence is incomplete for this gate; competitive gates require "
            "an independent party and HTTPS source URL"
        )
    return {name: record}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--methodology", required=True)
    parser.add_argument("--independent-party")
    parser.add_argument("--source-url")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = attest(
        args.name,
        args.artifact,
        methodology=args.methodology,
        independent_party=args.independent_party,
        source_url=args.source_url,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Attested {args.name}: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
