from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "verify_production_schema.py"
SPEC = importlib.util.spec_from_file_location("verify_production_schema", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

MACHINE_ID = "78451d0fe5d158"


def result(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_retries_machine_exec_and_preserves_exact_command(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    responses = iter(
        [
            result(1, stderr="temporary Fly SSH error\n"),
            result(0, stdout="0033_sync_device_key_rotation (head)\n"),
        ]
    )
    delays: list[float] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return next(responses)

    assert MODULE.verify_schema(MACHINE_ID, runner=runner, sleeper=delays.append) == MODULE.EXPECTED_REVISION

    assert len(calls) == 2
    assert calls[0][0] == calls[1][0]
    assert calls[0][0] == [
        "flyctl",
        "machine",
        "exec",
        MACHINE_ID,
        MODULE.SCHEMA_COMMAND,
        "--app",
        MODULE.APP_NAME,
        "--timeout",
        "120",
    ]
    assert calls[0][1] == {
        "capture_output": True,
        "check": False,
        "text": True,
        "timeout": 135,
    }
    assert delays == [5]

    captured = capsys.readouterr()
    assert "attempt 1/3; status=1" in captured.out
    assert "temporary Fly SSH error" in captured.out
    assert "attempt 2/3; status=0" in captured.out
    assert "Production schema revision: 0033_sync_device_key_rotation" in captured.out


def test_reports_every_failed_attempt_before_exiting(capsys: pytest.CaptureFixture[str]) -> None:
    attempts = 0

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        return result(1, stdout=f"attempt-{attempts}-stdout\n", stderr=f"attempt-{attempts}-stderr\n")

    with pytest.raises(MODULE.SchemaVerificationError, match="after 3 attempts"):
        MODULE.verify_schema(MACHINE_ID, runner=runner, sleeper=lambda _: None)

    captured = capsys.readouterr()
    for attempt in range(1, 4):
        assert f"attempt {attempt}/3; status=1" in captured.out
        assert f"attempt-{attempt}-stdout" in captured.out
        assert f"attempt-{attempt}-stderr" in captured.out


def test_reports_local_timeout_capture_and_retries(capsys: pytest.CaptureFixture[str]) -> None:
    attempts = 0

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise subprocess.TimeoutExpired(
                command,
                timeout=135,
                output=b"partial stdout\n",
                stderr=b"partial stderr\n",
            )
        return result(0, stdout="0033_sync_device_key_rotation (head)\n")

    assert MODULE.verify_schema(MACHINE_ID, runner=runner, sleeper=lambda _: None) == MODULE.EXPECTED_REVISION

    captured = capsys.readouterr()
    assert "attempt 1/3; status=local-timeout" in captured.out
    assert "partial stdout" in captured.out
    assert "partial stderr" in captured.out


def test_success_without_expected_revision_fails_closed() -> None:
    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return result(0, stdout="0027_previous_revision\n")

    with pytest.raises(MODULE.SchemaVerificationError, match="expected schema head"):
        MODULE.verify_schema(MACHINE_ID, runner=runner, sleeper=lambda _: None)


@pytest.mark.parametrize("machine_id", ["", "master", "A" * 14, "a" * 13, "a" * 15])
def test_rejects_untrusted_machine_ids(machine_id: str) -> None:
    with pytest.raises(ValueError, match="Machine ID"):
        MODULE.verify_schema(machine_id)
