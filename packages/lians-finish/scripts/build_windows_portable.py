"""Build the self-contained Windows Lians download."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from lians_finish import __version__


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_provenance(executable_hash: str) -> dict[str, object]:
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(ROOT), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

    head = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    source_commit = head.stdout.strip().lower() if head.returncode == 0 else None
    return {
        "schema": "lians.finish.build-provenance.v1",
        "product": "Lians Mission Control",
        "version": __version__,
        "source_repository": "https://github.com/Lians-ai/Lians",
        "source_commit": source_commit,
        "source_dirty": status.returncode != 0 or bool(status.stdout.strip()),
        "built_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "executable": "Lians.exe",
        "executable_sha256": executable_hash,
    }


def main() -> int:
    output = ROOT / "output"
    executable_directory = output / "windows-exe"
    distribution = ROOT / "dist"
    output.mkdir(exist_ok=True)
    executable_directory.mkdir(exist_ok=True)
    distribution.mkdir(exist_ok=True)
    web_assets = SRC / "lians_finish" / "web"
    entrypoint = ROOT / "scripts" / "windows_entrypoint.py"

    with tempfile.TemporaryDirectory(prefix="lians-pyinstaller-", dir=output) as temporary:
        temporary_root = Path(temporary)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--windowed",
            "--name",
            "Lians",
            "--paths",
            str(SRC),
            "--hidden-import",
            "lians_finish.web",
            "--add-data",
            f"{web_assets}{os.pathsep}lians_finish/web",
            "--distpath",
            str(executable_directory),
            "--workpath",
            str(temporary_root / "work"),
            "--specpath",
            str(temporary_root / "spec"),
            "--log-level",
            "WARN",
            str(entrypoint),
        ]
        subprocess.run(command, cwd=ROOT, check=True)

    executable = executable_directory / "Lians.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller did not produce Lians.exe")
    executable_hash = _sha256(executable)
    provenance = _source_provenance(executable_hash)
    archive = distribution / f"Lians-{__version__}-Windows-x64.zip"
    readme = f"""Lians Mission Control {__version__}

START
1. Extract this ZIP before opening it.
2. Double-click Lians.exe. Keep it open while using Lians.
3. Lians opens in your browser at http://127.0.0.1:4318/.

FIRST MISSION
1. Open Context & proof with the + button and choose a project folder.
2. Describe one finished outcome, then choose Protect, Balanced, or Maximum.
3. Review the detected proof command. Use Preview to inspect the route without a model call.
4. Choose Start only when the mission, proof, and premium ceiling are correct.

REQUIREMENT
Execution currently requires the Codex CLI to be installed and signed in on this computer.
Without Codex, route previews and portable-agent import/export still work.

PRIVACY AND RECORDS
Lians binds only to this computer and stores records under %LOCALAPPDATA%\\Lians Finish.
Portable .lians-agent files contain mission, policy, and proof configuration, but no provider
credentials or full workspace path. Importing one never starts a run.

WINDOWS NOTICE
This engineering release is unsigned, so Windows may show an unknown-publisher warning.
Do not bypass an organizational security policy.

BETA REPORT
After matched Protect and Maximum runs, open System and export the beta proof report.
Review the JSON before sharing it. It excludes task text, repository paths, prompts, and output.
"""
    beta_guide = (ROOT / "BETA-PROOF-GATE.md").read_text(encoding="utf-8")
    codex_bridge_guide = (ROOT / "CODEX-BRIDGE.md").read_text(encoding="utf-8")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.write(executable, "Lians.exe")
        bundle.writestr("README-WINDOWS.txt", readme)
        bundle.writestr("BETA-PROOF-GATE.md", beta_guide)
        bundle.writestr("CODEX-BRIDGE.md", codex_bridge_guide)
        bundle.writestr(
            "BUILD-PROVENANCE.json",
            json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        )
        bundle.writestr("SHA256SUMS.txt", f"{executable_hash.upper()}  Lians.exe\n")
    print(
        json.dumps(
            {
                "version": __version__,
                "executable": str(executable),
                "executable_sha256": executable_hash,
                "archive": str(archive),
                "archive_sha256": _sha256(archive),
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
