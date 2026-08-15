from __future__ import annotations

import json

import pytest
from lians_easy import cli
from lians_easy.store import MemoryStore


def test_backup_cli_exports_verifies_and_imports_without_secret_arguments(
    tmp_path, monkeypatch, capsys
) -> None:
    source_path = tmp_path / "source" / "memory.sqlite3"
    source = MemoryStore(source_path)
    memory = source.remember(
        "Use concise headings in every report.",
        source="CLI backup test",
        kind="preference",
        scope="global",
    )
    backup_path = tmp_path / "memory.liansbackup"
    target_path = tmp_path / "target" / "memory.sqlite3"
    passphrases = iter(
        [
            "a strong portable passphrase",
            "a strong portable passphrase",
            "a strong portable passphrase",
            "a strong portable passphrase",
        ]
    )
    monkeypatch.setattr(cli, "listen_for_windows_installer_shutdown", lambda: None)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(passphrases))

    cli.main(
        [
            "backup",
            "export",
            "--output",
            str(backup_path),
            "--data",
            str(source_path),
            "--json",
        ]
    )
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "exported"

    cli.main(["backup", "verify", "--input", str(backup_path), "--json"])
    verified = json.loads(capsys.readouterr().out)
    assert verified["status"] == "verified"

    with pytest.raises(SystemExit, match="rerun import with --yes"):
        cli.main(
            [
                "backup",
                "import",
                "--input",
                str(backup_path),
                "--data",
                str(target_path),
            ]
        )
    cli.main(
        [
            "backup",
            "import",
            "--input",
            str(backup_path),
            "--data",
            str(target_path),
            "--yes",
            "--json",
        ]
    )
    imported = json.loads(capsys.readouterr().out)
    assert imported["status"] == "imported"
    recalled = MemoryStore(target_path).list(state="current")
    assert recalled[0]["id"] == memory["id"]
    assert recalled[0]["content"] == memory["content"]

    with pytest.raises(SystemExit) as help_exit:
        cli.parser().parse_args(["backup", "export", "--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--passphrase-file" in help_text
    assert "--passphrase PASSPHRASE" not in help_text
