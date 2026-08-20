"""Build and exercise Lians Easy from a clean, temporary virtual environment."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
from pathlib import Path


def _run(*arguments: str, cwd: Path | None = None) -> None:
    subprocess.run(arguments, cwd=cwd, check=True, timeout=600)


def main() -> None:
    repository = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="lians-clean-install-") as raw_directory:
        environment = Path(raw_directory) / "environment"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        executable = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        _run(
            str(executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            str(repository / "packages" / "lians-easy"),
        )
        _run(str(executable), "-c", "import lians_easy")
        _run(str(executable), "-m", "lians_easy.cli", "--version")


if __name__ == "__main__":
    main()
