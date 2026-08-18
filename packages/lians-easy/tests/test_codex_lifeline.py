from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lians_easy.codex_lifeline import codex_lifeline_snapshot


def _project(home: Path, name: str, *, memories: int, receipts: list[dict]) -> Path:
    project = home / "projects" / name
    project.mkdir(parents=True)
    database = sqlite3.connect(project / "memory.sqlite3")
    try:
        database.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, erased_at TEXT, system_valid_to TEXT)"
        )
        database.executemany(
            "INSERT INTO memories (erased_at, system_valid_to) VALUES (NULL, NULL)",
            [() for _ in range(memories)],
        )
        database.execute("INSERT INTO memories (erased_at, system_valid_to) VALUES ('now', NULL)")
        database.commit()
    finally:
        database.close()
    (project / "hook-receipts.jsonl").write_text(
        "\n".join(json.dumps(item) for item in receipts) + "\n",
        encoding="utf-8",
    )
    return project


def test_codex_lifeline_reads_latest_project_receipts_without_claiming_savings(
    tmp_path: Path,
) -> None:
    _project(
        tmp_path,
        "lians-demo-123456789abc",
        memories=3,
        receipts=[
            {
                "status": "injected",
                "injected": True,
                "memory_count": 5,
                "token_estimate": 420,
            },
            {"status": "no_match", "injected": False, "memory_count": 0},
            {
                "status": "injected",
                "injected": True,
                "memory_count": 2,
                "token_estimate": 160,
            },
        ],
    )

    result = codex_lifeline_snapshot(home=tmp_path, limit=2)

    assert result is not None
    assert result["saved_memories"] == 3
    assert result["context_events"] == 2
    assert result["memories_reused"] == 7
    assert result["context_tokens_sent_estimate"] == 580
    assert result["repeated_tokens_avoided_estimate"] == 0
    assert result["token_metric"] == {
        "label": "Context reused",
        "value": 580,
        "detail": "Tokens delivered from memory",
        "approximate": True,
    }
    assert result["activity"][0]["title"] == "Codex · Lians Demo"
    assert "2 memories reused" in result["activity"][0]["detail"]


def test_codex_lifeline_fails_closed_on_missing_or_invalid_receipts(
    tmp_path: Path,
) -> None:
    project = tmp_path / "projects" / "broken-123456789abc"
    project.mkdir(parents=True)
    (project / "hook-receipts.jsonl").write_text("not json\n", encoding="utf-8")

    assert codex_lifeline_snapshot(home=tmp_path) is None
