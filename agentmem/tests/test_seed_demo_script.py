from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Self

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "seed_demo.py"
SPEC = importlib.util.spec_from_file_location("seed_demo", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_read_key_is_never_rendered_in_instructions() -> None:
    output = MODULE._demo_instructions(
        api="https://example.test",
        namespace="demo",
        read_key_path=None,
    )

    assert "<read-key>" in output
    assert "--read-key-output" in output
    assert "was provisioned" in output


def test_read_key_output_is_new_owner_only_file(tmp_path: Path) -> None:
    key = "lians_read_bearer_key_for_owner_only_file"
    output_path = tmp_path / "read-key"

    MODULE._write_read_key(output_path, key)

    assert output_path.read_text(encoding="utf-8") == f"{key}\n"
    if os.name != "nt":
        assert output_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        MODULE._write_read_key(output_path, "replacement")
    assert output_path.read_text(encoding="utf-8") == f"{key}\n"


def test_read_key_output_requires_explicit_path(tmp_path: Path) -> None:
    parser = MODULE._build_parser()

    assert parser.parse_args([]).read_key_output is None
    assert parser.parse_args(["--read-key-output", str(tmp_path / "key")]).read_key_output == (
        tmp_path / "key"
    )


def test_main_never_logs_provisioned_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    read_key = "read_bearer_value_that_must_not_reach_stdout"
    write_key = "write_bearer_value_that_must_not_reach_stdout"
    output_path = tmp_path / "read-key"

    class FakeClient:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(MODULE.httpx, "Client", FakeClient)
    monkeypatch.setattr(MODULE, "_wait_for_api", lambda *args, **kwargs: None)
    monkeypatch.setattr(MODULE, "_ingest", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        MODULE,
        "_provision_key",
        lambda *args, **kwargs: read_key if kwargs["label"] == "demo-readonly" else write_key,
    )

    MODULE.main(["--read-key-output", str(output_path)])

    stdout = capsys.readouterr().out
    assert read_key not in stdout
    assert write_key not in stdout
    assert output_path.read_text(encoding="utf-8") == f"{read_key}\n"
