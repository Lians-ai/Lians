"""Force a frozen installer interruption and prove durable recovery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def _config_path(home: Path, roaming: Path, xdg_config: Path) -> Path:
    if sys.platform == "win32":
        return roaming / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    return xdg_config / "Claude" / "claude_desktop_config.json"


def _run(binary: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(binary),
            "install",
            "--clients",
            "claude",
            "--yes",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=60,
    )


def verify(binary: Path) -> dict[str, object]:
    binary = binary.resolve()
    if not binary.is_file():
        raise FileNotFoundError(binary)
    summary: dict[str, object]
    fixture: Path
    with TemporaryDirectory(prefix="lians-installer-crash-") as directory:
        fixture = Path(directory)
        home = fixture / "home"
        roaming = fixture / "roaming"
        local = fixture / "local"
        xdg_config = fixture / "config"
        config = _config_path(home, roaming, xdg_config)
        hook = home / ".claude" / "settings.json"
        config.parent.mkdir(parents=True)
        hook.parent.mkdir(parents=True)
        private_marker = "preserve-this-private-setting"
        original_config = json.dumps(
            {"theme": "light", "privateSetting": private_marker},
            separators=(",", ":"),
        ).encode()
        original_hook = b'{"hooks":{"Unrelated":[{"command":"keep-me"}]}}'
        config.write_bytes(original_config)
        hook.write_bytes(original_hook)

        environment = os.environ.copy()
        environment.update(
            {
                "USERPROFILE": str(home),
                "HOME": str(home),
                "APPDATA": str(roaming),
                "LOCALAPPDATA": str(local),
                "XDG_CONFIG_HOME": str(xdg_config),
                "XDG_DATA_HOME": str(local),
                "LIANS_EASY_HOME": str(local / "Lians"),
                "LIANS_EASY_TEST_MODE": "crash-recovery",
                "LIANS_EASY_TEST_CRASH_AFTER_WRITE": config.name,
            }
        )

        crashed = _run(binary, environment)
        assert crashed.returncode == 86, (
            crashed.returncode,
            crashed.stdout,
            crashed.stderr,
        )
        assert config.read_bytes() != original_config
        assert hook.read_bytes() == original_hook
        transaction_dir = local / "Lians" / "setup-transactions"
        journals = list(transaction_dir.glob("*.json"))
        assert len(journals) == 1
        assert private_marker not in journals[0].read_text(encoding="utf-8")
        interrupted_backups = set(config.parent.glob("*.lians-backup-*"))
        assert interrupted_backups

        environment.pop("LIANS_EASY_TEST_CRASH_AFTER_WRITE")
        environment.pop("LIANS_EASY_TEST_MODE")
        retried = _run(binary, environment)
        assert retried.returncode == 0, (retried.stdout, retried.stderr)
        result = json.loads(retried.stdout)
        assert result["status"] == "installed"
        assert result["recovered_clients"] == ["claude"]
        updated_config = json.loads(config.read_text(encoding="utf-8"))
        updated_hook = json.loads(hook.read_text(encoding="utf-8"))
        assert updated_config["privateSetting"] == private_marker
        assert "lians" in updated_config["mcpServers"]
        assert updated_hook["hooks"]["Unrelated"] == [{"command": "keep-me"}]
        assert updated_hook["hooks"]["UserPromptSubmit"]
        assert not list(transaction_dir.glob("*.json"))
        assert not any(path.exists() for path in interrupted_backups)
        summary = {
            "binary": str(binary),
            "forced_process_exit": crashed.returncode,
            "partial_primary_write_observed": True,
            "untouched_hook_preserved_after_crash": True,
            "journal_excluded_private_setting_content": True,
            "recovered_clients": result["recovered_clients"],
            "unrelated_settings_preserved": True,
            "interrupted_backups_removed": True,
        }
    assert not fixture.exists()
    summary["temporary_fixture_removed_on_exit"] = True
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(verify(arguments.binary), indent=2))


if __name__ == "__main__":
    main()
