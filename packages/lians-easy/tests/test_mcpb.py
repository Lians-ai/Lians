from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from lians_easy import __version__

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_mcpb_manifest_matches_the_runtime():
    manifest = json.loads((PACKAGE_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == "0.4"
    assert manifest["version"] == __version__
    assert manifest["server"] == {
        "type": "uv",
        "entry_point": "mcp_entrypoint.py",
        "mcp_config": {
            "command": "uv",
            "args": ["run", "--directory", "${__dirname}", "mcp_entrypoint.py"],
        },
    }
    assert (PACKAGE_ROOT / manifest["icon"]).is_file()
    assert {tool["name"] for tool in manifest["tools"]} == {
        "remember",
        "set_current",
        "recall",
        "understand_request",
        "list_memories",
        "memory_health",
        "memory_history",
        "memory_at",
        "track_dependencies",
        "state_impact",
        "state_repair_brief",
        "resolve_state_impact",
        "start_task",
        "checkpoint_task",
        "task_status",
        "task_context",
        "continue_work",
        "configure_verification",
        "verify_work",
        "verification_status",
        "correct_memory",
        "forget_memory",
    }


def test_mcpb_entrypoint_serves_the_stdio_contract(tmp_path):
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            "",
        ]
    )
    environment = os.environ.copy()
    environment["LIANS_EASY_DB"] = str(tmp_path / "mcpb.sqlite3")

    result = subprocess.run(
        [sys.executable, str(PACKAGE_ROOT / "mcp_entrypoint.py")],
        cwd=PACKAGE_ROOT,
        env=environment,
        input=requests,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )

    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert responses[0]["result"]["serverInfo"] == {
        "name": "Lians Memory",
        "version": __version__,
    }
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
        "remember",
        "set_current",
        "recall",
        "understand_request",
        "list_memories",
        "memory_health",
        "memory_history",
        "memory_at",
        "track_dependencies",
        "state_impact",
        "state_repair_brief",
        "resolve_state_impact",
        "start_task",
        "checkpoint_task",
        "task_status",
        "task_context",
        "continue_work",
        "configure_verification",
        "verify_work",
        "verification_status",
        "correct_memory",
        "forget_memory",
    }
