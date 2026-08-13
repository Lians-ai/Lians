"""Small SQLite memory store for the no-dependencies Lians runtime."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    # datetime.UTC is unavailable on the package's supported Python 3.10.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _tokens(value: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"[a-z0-9]{2,}", value.lower())))


class MemoryStore:
    """Inspectable local memory with append-only corrections and confirmed erasure."""

    def __init__(self, path: str | Path, *, profile: str = "personal") -> None:
        self.path = Path(path).expanduser()
        parent_was_present = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32" and not parent_was_present:
            self.path.parent.chmod(0o700)
        self.profile = profile
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    content TEXT,
                    source TEXT NOT NULL,
                    topic TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    supersedes_id TEXT REFERENCES memories(id),
                    superseded_by_id TEXT REFERENCES memories(id),
                    forgotten_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_memories_profile_state
                    ON memories(profile, forgotten_at, superseded_by_id, created_at DESC);
                """
            )
        if sys.platform != "win32":
            self.path.chmod(0o600)

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "content": row["content"],
            "source": row["source"],
            "topic": row["topic"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "supersedes_id": row["supersedes_id"],
            "superseded_by_id": row["superseded_by_id"],
            "forgotten_at": row["forgotten_at"],
            "state": (
                "forgotten"
                if row["forgotten_at"]
                else "superseded"
                if row["superseded_by_id"]
                else "current"
            ),
        }

    def remember(
        self,
        content: str,
        *,
        source: str = "user",
        topic: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Memory content cannot be blank")
        memory_id = str(uuid.uuid4())
        timestamp = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO memories
                   (id, profile, content, source, topic, metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    self.profile,
                    content,
                    source.strip() or "user",
                    topic.strip() if topic else None,
                    json.dumps(metadata or {}, sort_keys=True),
                    timestamp,
                    timestamp,
                ),
            )
            row = db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._public(row)

    def recall(self, query: str, *, limit: int = 5, max_chars: int = 4000) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM memories
                   WHERE profile = ? AND forgotten_at IS NULL AND superseded_by_id IS NULL
                   ORDER BY created_at DESC LIMIT 500""",
                (self.profile,),
            ).fetchall()

        ranked: list[tuple[float, sqlite3.Row]] = []
        for recency, row in enumerate(rows):
            haystack = " ".join((row["content"] or "", row["topic"] or "")).lower()
            overlap = sum(1 for token in query_tokens if token in haystack)
            if query_tokens and not overlap:
                continue
            score = float(overlap) + max(0.0, 0.25 - recency / 2000)
            ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)

        result: list[dict[str, Any]] = []
        used = 0
        for score, row in ranked[: max(1, min(limit, 50))]:
            item = self._public(row)
            item["score"] = round(score, 4)
            item_size = len(item.get("content") or "")
            if result and used + item_size > max_chars:
                break
            result.append(item)
            used += item_size
        return result

    def list(self, *, state: str = "current", limit: int = 50) -> list[dict[str, Any]]:
        predicates = {
            "current": "forgotten_at IS NULL AND superseded_by_id IS NULL",
            "superseded": "forgotten_at IS NULL AND superseded_by_id IS NOT NULL",
            "forgotten": "forgotten_at IS NOT NULL",
            "all": "1 = 1",
        }
        if state not in predicates:
            raise ValueError("state must be current, superseded, forgotten, or all")
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT * FROM memories WHERE profile = ? AND {predicates[state]}
                    ORDER BY created_at DESC LIMIT ?""",
                (self.profile, max(1, min(limit, 200))),
            ).fetchall()
        return [self._public(row) for row in rows]

    def correct(self, memory_id: str, content: str) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Corrected memory content cannot be blank")
        replacement_id = str(uuid.uuid4())
        timestamp = _now()
        with self._connect() as db:
            original = db.execute(
                "SELECT * FROM memories WHERE id = ? AND profile = ?",
                (memory_id, self.profile),
            ).fetchone()
            if original is None or original["forgotten_at"]:
                raise LookupError("Memory not found")
            if original["superseded_by_id"]:
                raise ValueError("Memory was already corrected; inspect current memories first")
            metadata = json.loads(original["metadata_json"] or "{}")
            metadata["correction_of"] = memory_id
            db.execute(
                """INSERT INTO memories
                   (id, profile, content, source, topic, metadata_json, created_at, updated_at,
                    supersedes_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    replacement_id,
                    self.profile,
                    content,
                    original["source"],
                    original["topic"],
                    json.dumps(metadata, sort_keys=True),
                    timestamp,
                    timestamp,
                    memory_id,
                ),
            )
            db.execute(
                "UPDATE memories SET superseded_by_id = ?, updated_at = ? WHERE id = ?",
                (replacement_id, timestamp, memory_id),
            )
            row = db.execute("SELECT * FROM memories WHERE id = ?", (replacement_id,)).fetchone()
        return self._public(row)

    def forget(self, memory_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            raise ValueError("Permanent forgetting requires confirmed=true")
        timestamp = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM memories WHERE id = ? AND profile = ?",
                (memory_id, self.profile),
            ).fetchone()
            if row is None:
                raise LookupError("Memory not found")
            if row["forgotten_at"]:
                return {"id": memory_id, "status": "already_forgotten"}
            db.execute(
                """UPDATE memories SET content = NULL, metadata_json = '{}',
                   forgotten_at = ?, updated_at = ? WHERE id = ?""",
                (timestamp, timestamp, memory_id),
            )
        return {"id": memory_id, "status": "forgotten", "forgotten_at": timestamp}

    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute(
                """SELECT
                   SUM(CASE WHEN forgotten_at IS NULL AND superseded_by_id IS NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN forgotten_at IS NULL AND superseded_by_id IS NOT NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN forgotten_at IS NOT NULL THEN 1 ELSE 0 END)
                   FROM memories WHERE profile = ?""",
                (self.profile,),
            ).fetchone()
        return {
            "current": row[0] or 0,
            "superseded": row[1] or 0,
            "forgotten": row[2] or 0,
            "database": str(self.path),
            "profile": self.profile,
        }
