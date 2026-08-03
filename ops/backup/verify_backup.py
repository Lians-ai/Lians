#!/usr/bin/env python3
"""Verify a Lians backup bundle without connecting to a database."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from backup_lib import (
    OperatorError,
    database_archive,
    require_program,
    verify_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--pg-restore", default="pg_restore")
    args = parser.parse_args()

    manifest, verification = verify_bundle(args.bundle)
    pg_restore = require_program(args.pg_restore)
    archive = database_archive(args.bundle.resolve(), manifest)
    result = subprocess.run(
        [pg_restore, "--list", str(archive)],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise OperatorError(f"pg_restore rejected the archive: {result.stderr.strip()[-2000:]}")
    toc_entries = sum(1 for line in result.stdout.splitlines() if line and not line.startswith(";"))
    if toc_entries != manifest.get("archive", {}).get("toc_entries"):
        raise OperatorError(
            "Archive table-of-contents count differs from the sealed manifest"
        )
    print(
        json.dumps(
            {
                "status": "verified",
                "backup_id": manifest["backup_id"],
                "toc_entries": toc_entries,
                **verification,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
