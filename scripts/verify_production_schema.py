"""Verify the production schema through Fly Machine exec."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections.abc import Callable

APP_NAME = "agentmem-lotus"
EXPECTED_REVISION = "0032_device_enrollment_exchange"
MACHINE_ID_PATTERN = re.compile(r"^[0-9a-f]{14}$")
REVISION_PATTERN = re.compile(rf"(?m)^{EXPECTED_REVISION}(?: \(head\))?$")
ATTEMPTS = 3
EXEC_TIMEOUT_SECONDS = 120
RETRY_DELAY_SECONDS = 5

# The protected workflow locally attenuates its app-scoped deploy token to this
# exact command and a 10-minute validity window before invoking this script.
# Keep the remote command fixed and reviewable.
SCHEMA_COMMAND = (
    "/bin/sh -c 'cd /app/agentmem && "
    "/opt/venv/bin/alembic -c alembic.ini current'"
)

Runner = Callable[..., subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]


class SchemaVerificationError(RuntimeError):
    """Raised when production schema verification cannot prove the expected head."""


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _write_capture(label: str, value: str) -> None:
    print(f"--- {label} ---")
    if value:
        print(value, end="" if value.endswith("\n") else "\n")
    else:
        print("<empty>")


def verify_schema(
    machine_id: str,
    *,
    runner: Runner = subprocess.run,
    sleeper: Sleeper = time.sleep,
) -> str:
    """Return the expected revision after a bounded, fully logged retry loop."""
    if not MACHINE_ID_PATTERN.fullmatch(machine_id):
        raise ValueError("production Machine ID must be 14 lowercase hexadecimal characters")

    command = [
        "flyctl",
        "machine",
        "exec",
        machine_id,
        SCHEMA_COMMAND,
        "--app",
        APP_NAME,
        "--timeout",
        str(EXEC_TIMEOUT_SECONDS),
    ]

    for attempt in range(1, ATTEMPTS + 1):
        try:
            completed = runner(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=EXEC_TIMEOUT_SECONDS + 15,
            )
            status = completed.returncode
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            status = "local-timeout"
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr)

        print(f"Schema verification attempt {attempt}/{ATTEMPTS}; status={status}")
        _write_capture("stdout", stdout)
        _write_capture("stderr", stderr)

        if status == 0:
            output = f"{stdout}\n{stderr}"
            if REVISION_PATTERN.search(output):
                print(f"Production schema revision: {EXPECTED_REVISION}")
                return EXPECTED_REVISION
            raise SchemaVerificationError(
                "Machine exec succeeded but did not report the expected schema head"
            )

        if attempt < ATTEMPTS:
            print(f"Retrying restricted Machine exec in {RETRY_DELAY_SECONDS} seconds.")
            sleeper(RETRY_DELAY_SECONDS)

    raise SchemaVerificationError(
        f"Machine exec failed after {ATTEMPTS} attempts"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        verify_schema(args.machine_id)
    except (SchemaVerificationError, ValueError) as exc:
        print(f"Production schema verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
