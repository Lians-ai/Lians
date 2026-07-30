"""Run the engine and Python SDK suites in isolated import environments."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SDK_TESTS = [
    "agentmem/tests/test_compaction_flush.py",
    "agentmem/tests/test_compare_regulated.py",
    "agentmem/tests/test_distill_ingest.py",
    "agentmem/tests/test_eval_harness.py",
    "agentmem/tests/test_graph.py",
    "agentmem/tests/test_harness.py",
    "agentmem/tests/test_langchain.py",
    "agentmem/tests/test_memory_feedback.py",
    "agentmem/tests/test_memory_intelligence_integrations.py",
    "agentmem/tests/test_mcp_local.py",
    "agentmem/tests/test_regulated_eval.py",
    "agentmem/tests/test_sdk.py",
    "agentmem/tests/test_sdk_extended.py",
    "agentmem/tests/test_sdk_retry.py",
]


def _run(args: list[str], pythonpath: str) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = pythonpath
    return subprocess.call(
        [sys.executable, "-m", "pytest", *args],
        cwd=ROOT,
        env=env,
    )


def main() -> int:
    sdk_set = set(SDK_TESTS)
    engine_tests = sorted(
        str(path.relative_to(ROOT)).replace("\\", "/")
        for path in (ROOT / "agentmem/tests").glob("test_*.py")
        if str(path.relative_to(ROOT)).replace("\\", "/") not in sdk_set
    )
    if _run(
        [*engine_tests, "-q"],
        os.pathsep.join(
            [
                str(ROOT / "agentmem/src"),
                str(ROOT / "agentmem"),
            ]
        ),
    ):
        return 1
    return _run(
        [*SDK_TESTS, "-q"],
        os.pathsep.join(
            [
                str(ROOT / "agentmem/sdk/python"),
                str(ROOT / "agentmem"),
            ]
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
