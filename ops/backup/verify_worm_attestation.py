#!/usr/bin/env python3
"""Revalidate a local WORM attestation pair and its exact provider-native anchor.

The CLI intentionally accepts no credential or provider-selection arguments.
Authentication is resolved exclusively through the provider SDK's default
workload-identity chain, with the expected ownership boundary supplied through
the same non-secret environment configuration used by the uploader.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backup_lib import OperatorError
from worm_attestation import verify_anchored_attestation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "attestation",
        type=Path,
        help="canonical core provider-attestation JSON",
    )
    args = parser.parse_args()
    print(json.dumps(verify_anchored_attestation(args.attestation), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OperatorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
