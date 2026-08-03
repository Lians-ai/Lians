"""Regression coverage for the published, zero-network benchmark command."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_published_benchmark_entrypoint_passes_from_repository_root():
    root = Path(__file__).resolve().parents[2]
    script = root / "agentmem" / "scripts" / "run_benchmark.py"
    env = os.environ.copy()
    env.update({
        "AGENTMEM_ALLOW_UNENCRYPTED": "true",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "EMBEDDING_PROVIDER": "local",
        "KMS_PROVIDER": "env",
        "MASTER_ENCRYPTION_KEY": "",
        "RLS_BARRIERS_ENABLED": "false",
        "TEST_DATABASE_URL": "disabled://",
    })

    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "Accuracy: 22/22 (100%)" in output
    assert "Correct: 4/4" in output
    assert "All benchmarks passed" in output
