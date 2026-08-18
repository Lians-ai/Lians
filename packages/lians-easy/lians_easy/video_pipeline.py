"""Encrypted, resumable ingestion for large video-analysis corpora.

Lians does not decode or transcribe video in this module. It accepts structured
outputs from any vision/transcription provider, keeps the evidence encrypted on
device, and promotes only a bounded consolidation into normal agent memory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .store import MemoryStore, _reject_sensitive, _token_estimate

MAX_RECORD_BYTES = 128 * 1024
MAX_BATCH_SIZE = 10_000
DEFAULT_BATCH_SIZE = 2_000
MAX_FINDINGS = 100
MAX_TAGS = 64

_WORD = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
_TERM_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "analysis",
    "because",
    "before",
    "from",
    "have",
    "into",
    "more",
    "that",
    "their",
    "this",
    "video",
    "with",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _bounded_text(value: Any, field: str, *, maximum: int, required: bool = False) -> str:
    if value is None:
        rendered = ""
    elif isinstance(value, str):
        rendered = value.strip()
    else:
        raise TypeError(f"{field} must be a string")
    if required and not rendered:
        raise ValueError(f"{field} is required")
    if len(rendered) > maximum:
        raise ValueError(f"{field} must be {maximum:,} characters or fewer")
    return rendered


def _bounded_strings(value: Any, field: str, *, maximum: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise TypeError(f"{field} must be a string or array of strings")
    if len(values) > maximum:
        raise ValueError(f"{field} must contain {maximum} items or fewer")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        rendered = _bounded_text(item, f"{field} item", maximum=2_000, required=True)
        fingerprint = rendered.casefold()
        if fingerprint not in seen:
            result.append(rendered)
            seen.add(fingerprint)
    return result


def _normalized_timestamp(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    rendered = _bounded_text(value, "occurred_at", maximum=64, required=True).replace(
        "Z", "+00:00"
    )
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise ValueError("occurred_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()  # noqa: UP017


def normalize_video_analysis(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one provider-neutral video-analysis result."""

    external_id = _bounded_text(
        value.get("external_id", value.get("id")),
        "external_id",
        maximum=512,
        required=True,
    )
    summary = _bounded_text(
        value.get("summary", value.get("transcript_summary", value.get("analysis"))),
        "summary",
        maximum=40_000,
    )
    findings = _bounded_strings(value.get("findings"), "findings", maximum=MAX_FINDINGS)
    if not summary and not findings:
        raise ValueError("summary or findings is required")
    title = _bounded_text(value.get("title"), "title", maximum=1_000)
    source_uri = _bounded_text(value.get("source_uri"), "source_uri", maximum=4_096)
    provider = _bounded_text(value.get("provider"), "provider", maximum=128)
    model = _bounded_text(value.get("model"), "model", maximum=256)
    tags = _bounded_strings(value.get("tags"), "tags", maximum=MAX_TAGS)
    metadata = value.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise TypeError("metadata must be an object")
    metadata_bytes = _canonical(metadata)
    if len(metadata_bytes) > 32 * 1024:
        raise ValueError("metadata must be 32 KiB or smaller")

    normalized = {
        "external_id": external_id,
        "title": title,
        "source_uri": source_uri,
        "summary": summary,
        "findings": findings,
        "tags": tags,
        "provider": provider,
        "model": model,
        "occurred_at": _normalized_timestamp(value.get("occurred_at")),
        "metadata": metadata,
    }
    encoded = _canonical(normalized)
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValueError(f"normalized record must be {MAX_RECORD_BYTES // 1024} KiB or smaller")
    _reject_sensitive("\n".join((title, summary, *findings)))
    return normalized


@dataclass(frozen=True)
class PreparedVideoAnalysis:
    record_id: str
    external_id_hash: str
    content_sha256: str
    ciphertext: bytes
    nonce: bytes
    token_estimate: int
    occurred_at: str | None
    term_hashes: tuple[bytes, ...]


class VideoAnalysisPipeline:
    """Large-corpus storage that stays outside latency-sensitive agent memory."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._id_key = store.cipher.derive_key(info=b"lians-video-external-id-v1")
        self._initialize()

    def _initialize(self) -> None:
        with self.store._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS video_analysis_runs (
                    id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checkpoint INTEGER NOT NULL DEFAULT 0,
                    inserted_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    batch_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY (profile, id)
                );
                CREATE TABLE IF NOT EXISTS video_analysis_records (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    external_id_hash TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    payload_cipher BLOB NOT NULL,
                    payload_nonce BLOB NOT NULL,
                    search_terms BLOB NOT NULL DEFAULT X'',
                    token_estimate INTEGER NOT NULL,
                    occurred_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (profile, project_id, external_id_hash, content_sha256)
                );
                CREATE INDEX IF NOT EXISTS idx_video_analysis_project
                    ON video_analysis_records(profile, project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_video_analysis_external
                    ON video_analysis_records(profile, project_id, external_id_hash, created_at DESC);
                CREATE TABLE IF NOT EXISTS video_analysis_terms (
                    record_id TEXT NOT NULL REFERENCES video_analysis_records(id) ON DELETE CASCADE,
                    profile TEXT NOT NULL,
                    term_hash BLOB NOT NULL,
                    PRIMARY KEY (record_id, term_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_video_analysis_terms
                    ON video_analysis_terms(profile, term_hash, record_id);
                """
            )
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(video_analysis_records)")
            }
            if "search_terms" not in columns:
                db.execute(
                    "ALTER TABLE video_analysis_records "
                    "ADD COLUMN search_terms BLOB NOT NULL DEFAULT X''"
                )

    def _associated_data(self, record_id: str) -> bytes:
        return f"lians-video-analysis-v1\0{self.store.profile}\0{record_id}".encode()

    def _external_id_hash(self, external_id: str) -> str:
        return hmac.new(
            self._id_key,
            external_id.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _prepare(self, value: Mapping[str, Any]) -> PreparedVideoAnalysis:
        normalized = normalize_video_analysis(value)
        encoded = _canonical(normalized)
        record_id = str(uuid.uuid4())
        ciphertext, nonce = self.store.cipher.seal_bytes(
            encoded,
            associated_data=self._associated_data(record_id),
        )
        searchable = " ".join(
            (
                normalized["title"],
                normalized["summary"],
                " ".join(normalized["findings"]),
                " ".join(normalized["tags"]),
            )
        )
        return PreparedVideoAnalysis(
            record_id=record_id,
            external_id_hash=self._external_id_hash(normalized["external_id"]),
            content_sha256=hashlib.sha256(encoded).hexdigest(),
            ciphertext=ciphertext,
            nonce=nonce,
            token_estimate=_token_estimate(searchable),
            occurred_at=normalized["occurred_at"],
            term_hashes=tuple(self.store._term_hash_bytes(searchable)),
        )

    def _open(self, row: sqlite3.Row) -> dict[str, Any]:
        plaintext = self.store.cipher.open_bytes(
            row["payload_cipher"],
            row["payload_nonce"],
            associated_data=self._associated_data(row["id"]),
        )
        payload = json.loads(plaintext)
        return {
            **payload,
            "record_id": row["id"],
            "project_id": row["project_id"],
            "run_id": row["run_id"],
            "content_sha256": row["content_sha256"],
            "token_estimate": row["token_estimate"],
            "created_at": row["created_at"],
        }

    def _ensure_run(
        self,
        *,
        run_id: str,
        project_id: str,
        input_sha256: str,
    ) -> dict[str, Any]:
        run_id = _bounded_text(run_id, "run_id", maximum=128, required=True)
        project_id = _bounded_text(project_id, "project_id", maximum=256, required=True)
        if not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
            raise ValueError("input_sha256 must be a lowercase SHA-256 digest")
        timestamp = _now()
        with self.store._connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO video_analysis_runs
                   (id, profile, project_id, input_sha256, status, started_at, updated_at)
                   VALUES (?, ?, ?, ?, 'running', ?, ?)""",
                (run_id, self.store.profile, project_id, input_sha256, timestamp, timestamp),
            )
            row = db.execute(
                "SELECT * FROM video_analysis_runs WHERE profile = ? AND id = ?",
                (self.store.profile, run_id),
            ).fetchone()
        if row["project_id"] != project_id or row["input_sha256"] != input_sha256:
            raise ValueError("run_id already belongs to a different project or input file")
        return self._run_public(row)

    @staticmethod
    def _run_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["id"],
            "project_id": row["project_id"],
            "input_sha256": row["input_sha256"],
            "status": row["status"],
            "checkpoint": row["checkpoint"],
            "inserted": row["inserted_count"],
            "duplicates": row["duplicate_count"],
            "batches": row["batch_count"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "completed_at": row["completed_at"],
        }

    def status(self, run_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM video_analysis_runs WHERE profile = ? AND id = ?",
                (self.store.profile, run_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Video analysis run not found: {run_id}")
        return self._run_public(row)

    def ingest_batch(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        run_id: str,
        project_id: str,
        input_sha256: str,
        start_offset: int,
    ) -> dict[str, Any]:
        values = list(records)
        if not values:
            raise ValueError("batch cannot be empty")
        if len(values) > MAX_BATCH_SIZE:
            raise ValueError(f"batch cannot exceed {MAX_BATCH_SIZE:,} records")
        prepared = [self._prepare(value) for value in values]
        timestamp = _now()
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT * FROM video_analysis_runs WHERE profile = ? AND id = ?",
                (self.store.profile, run_id),
            ).fetchone()
            if run is None:
                raise KeyError(f"Video analysis run not found: {run_id}")
            if run["project_id"] != project_id or run["input_sha256"] != input_sha256:
                raise ValueError("run does not match this project and input")
            if run["status"] == "complete":
                return self._run_public(run)
            if run["checkpoint"] != start_offset:
                raise RuntimeError(
                    f"stale batch offset {start_offset}; resume from {run['checkpoint']}"
                )
            db.execute(
                """CREATE TEMP TABLE video_analysis_records_stage (
                       id TEXT, profile TEXT, project_id TEXT, run_id TEXT,
                       external_id_hash TEXT, content_sha256 TEXT, payload_cipher BLOB,
                       payload_nonce BLOB, search_terms BLOB, token_estimate INTEGER,
                       occurred_at TEXT, created_at TEXT
                   )"""
            )
            db.executemany(
                """INSERT INTO video_analysis_records_stage
                   (id, profile, project_id, run_id, external_id_hash, content_sha256,
                    payload_cipher, payload_nonce, search_terms, token_estimate,
                    occurred_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        item.record_id,
                        self.store.profile,
                        project_id,
                        run_id,
                        item.external_id_hash,
                        item.content_sha256,
                        item.ciphertext,
                        item.nonce,
                        b"".join(item.term_hashes),
                        item.token_estimate,
                        item.occurred_at,
                        timestamp,
                    )
                    for item in prepared
                ),
            )
            db.execute(
                """INSERT OR IGNORE INTO video_analysis_records
                   (id, profile, project_id, run_id, external_id_hash, content_sha256,
                    payload_cipher, payload_nonce, search_terms, token_estimate,
                    occurred_at, created_at)
                   SELECT id, profile, project_id, run_id, external_id_hash, content_sha256,
                          payload_cipher, payload_nonce, search_terms, token_estimate,
                          occurred_at, created_at
                   FROM video_analysis_records_stage"""
            )
            inserted = int(db.execute("SELECT changes()").fetchone()[0])
            duplicates = len(prepared) - inserted
            checkpoint = start_offset + len(values)
            db.execute(
                """UPDATE video_analysis_runs
                   SET checkpoint = ?, inserted_count = inserted_count + ?,
                       duplicate_count = duplicate_count + ?, batch_count = batch_count + 1,
                       updated_at = ?
                   WHERE profile = ? AND id = ?""",
                (
                    checkpoint,
                    inserted,
                    duplicates,
                    timestamp,
                    self.store.profile,
                    run_id,
                ),
            )
            self.store._activity(
                db,
                "video_analysis_batch_ingested",
                project_id=project_id,
                client="video-pipeline",
                details={
                    "run_id": run_id,
                    "records": len(values),
                    "inserted": inserted,
                    "duplicates": duplicates,
                    "checkpoint": checkpoint,
                },
            )
            row = db.execute(
                "SELECT * FROM video_analysis_runs WHERE profile = ? AND id = ?",
                (self.store.profile, run_id),
            ).fetchone()
        return self._run_public(row)

    def complete(self, run_id: str, *, expected_checkpoint: int) -> dict[str, Any]:
        timestamp = _now()
        with self.store._connect() as db:
            row = db.execute(
                "SELECT * FROM video_analysis_runs WHERE profile = ? AND id = ?",
                (self.store.profile, run_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"Video analysis run not found: {run_id}")
            if row["checkpoint"] != expected_checkpoint:
                raise RuntimeError(
                    f"run checkpoint is {row['checkpoint']}; expected {expected_checkpoint}"
                )
            db.execute(
                """UPDATE video_analysis_runs
                   SET status = 'complete', completed_at = COALESCE(completed_at, ?), updated_at = ?
                   WHERE profile = ? AND id = ?""",
                (timestamp, timestamp, self.store.profile, run_id),
            )
            row = db.execute(
                "SELECT * FROM video_analysis_runs WHERE profile = ? AND id = ?",
                (self.store.profile, run_id),
            ).fetchone()
        return self._run_public(row)

    def ingest_jsonl(
        self,
        path: str | Path,
        *,
        run_id: str,
        project_id: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_batches: int | None = None,
    ) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.stat().st_size > 2 * 1024 * 1024 * 1024:
            raise ValueError("input file cannot exceed 2 GiB")
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE:,}")
        digest = hashlib.sha256()
        with source.open("rb") as binary:
            for chunk in iter(lambda: binary.read(1024 * 1024), b""):
                digest.update(chunk)
        input_sha256 = digest.hexdigest()
        state = self._ensure_run(
            run_id=run_id,
            project_id=project_id,
            input_sha256=input_sha256,
        )
        if state["status"] == "complete":
            return {**state, "resumed_from": state["checkpoint"], "input": str(source)}

        resume_at = int(state["checkpoint"])
        batch: list[Mapping[str, Any]] = []
        processed = 0
        batches_this_call = 0
        with source.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number <= resume_at:
                    processed = line_number
                    continue
                if not line.strip():
                    raise ValueError(f"line {line_number} is blank")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"line {line_number} is not valid JSON: {exc.msg}") from exc
                if not isinstance(value, dict):
                    raise TypeError(f"line {line_number} must contain a JSON object")
                batch.append(value)
                processed = line_number
                if len(batch) == batch_size:
                    state = self.ingest_batch(
                        batch,
                        run_id=run_id,
                        project_id=project_id,
                        input_sha256=input_sha256,
                        start_offset=processed - len(batch),
                    )
                    batch = []
                    batches_this_call += 1
                    if max_batches is not None and batches_this_call >= max_batches:
                        return {
                            **state,
                            "resumed_from": resume_at,
                            "input": str(source),
                        }
            if batch:
                state = self.ingest_batch(
                    batch,
                    run_id=run_id,
                    project_id=project_id,
                    input_sha256=input_sha256,
                    start_offset=processed - len(batch),
                )
        if processed < resume_at:
            raise ValueError("input file is shorter than the saved checkpoint")
        state = self.complete(run_id, expected_checkpoint=processed)
        return {**state, "resumed_from": resume_at, "input": str(source)}

    def search(self, query: str, *, project_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rendered = _bounded_text(query, "query", maximum=2_000, required=True)
        term_hashes = self.store._term_hash_bytes(rendered)
        if not term_hashes:
            return []
        bounded_limit = max(1, min(limit, 100))
        placeholders = ",".join("?" for _ in term_hashes)
        with self.store._connect() as db:
            score = " + ".join(
                "CASE WHEN instr(search_terms, ?) > 0 THEN 1 ELSE 0 END"
                for _ in term_hashes
            )
            rows = db.execute(
                f"""WITH scored AS (
                        SELECT *, ({score}) AS match_score
                        FROM video_analysis_records
                        WHERE profile = ? AND project_id = ? AND length(search_terms) > 0
                    )
                    SELECT * FROM scored WHERE match_score > 0
                    ORDER BY match_score DESC, created_at DESC LIMIT ?""",  # nosec B608
                (*term_hashes, self.store.profile, project_id, bounded_limit),
            ).fetchall()

            remaining = bounded_limit - len(rows)
            if remaining > 0:
                # Databases created before the compact private index retain the
                # legacy keyed-term table and remain searchable after upgrade.
                legacy = db.execute(
                    f"""SELECT records.*
                        FROM video_analysis_records AS records
                        JOIN video_analysis_terms AS terms ON terms.record_id = records.id
                        WHERE records.profile = ? AND records.project_id = ?
                          AND length(records.search_terms) = 0
                          AND terms.profile = ? AND terms.term_hash IN ({placeholders})
                        GROUP BY records.id
                        ORDER BY COUNT(*) DESC, records.created_at DESC
                        LIMIT ?""",  # nosec B608
                    (
                        self.store.profile,
                        project_id,
                        self.store.profile,
                        *term_hashes,
                        remaining,
                    ),
                ).fetchall()
                rows.extend(legacy)
        return [self._open(row) for row in rows]

    def get(self, external_id: str, *, project_id: str) -> list[dict[str, Any]]:
        rendered = _bounded_text(external_id, "external_id", maximum=512, required=True)
        with self.store._connect() as db:
            rows = db.execute(
                """SELECT * FROM video_analysis_records
                   WHERE profile = ? AND project_id = ? AND external_id_hash = ?
                   ORDER BY created_at DESC""",
                (self.store.profile, project_id, self._external_id_hash(rendered)),
            ).fetchall()
        return [self._open(row) for row in rows]

    def iter_project(self, project_id: str) -> Iterator[dict[str, Any]]:
        with self.store._connect() as db:
            cursor = db.execute(
                """SELECT * FROM video_analysis_records
                   WHERE profile = ? AND project_id = ? ORDER BY created_at, id""",
                (self.store.profile, project_id),
            )
            while True:
                rows = cursor.fetchmany(500)
                if not rows:
                    break
                for row in rows:
                    yield self._open(row)

    def consolidate(
        self,
        *,
        project_id: str,
        top_n: int = 20,
        remember: bool = False,
    ) -> dict[str, Any]:
        bounded_top = max(5, min(top_n, 50))
        tags: Counter[str] = Counter()
        providers: Counter[str] = Counter()
        terms: Counter[str] = Counter()
        findings: Counter[str] = Counter()
        count = 0
        tokens = 0
        for record in self.iter_project(project_id):
            count += 1
            tokens += int(record["token_estimate"])
            tags.update(tag.casefold() for tag in record["tags"])
            if record["provider"]:
                providers[record["provider"]] += 1
            findings.update(item for item in record["findings"] if len(item) <= 500)
            text = " ".join((record["title"], record["summary"], *record["findings"]))
            terms.update(
                token
                for token in _WORD.findall(text.casefold())
                if token not in _TERM_STOPWORDS and not token.isdigit()
            )
        if not count:
            raise ValueError("project has no imported video analyses")
        result: dict[str, Any] = {
            "project_id": project_id,
            "record_count": count,
            "analysis_tokens": tokens,
            "top_tags": [{"value": key, "count": value} for key, value in tags.most_common(bounded_top)],
            "top_terms": [{"value": key, "count": value} for key, value in terms.most_common(bounded_top)],
            "providers": [{"value": key, "count": value} for key, value in providers.most_common()],
            "recurring_findings": [
                {"value": key, "count": value}
                for key, value in findings.most_common(10)
                if value > 1
            ],
            "method": "deterministic aggregate of encrypted provider outputs; no model inference",
        }
        if remember:
            top_tags = ", ".join(item["value"] for item in result["top_tags"][:10]) or "none"
            top_terms = ", ".join(item["value"] for item in result["top_terms"][:15])
            recurring = "; ".join(
                f"{item['value']} ({item['count']} videos)"
                for item in result["recurring_findings"][:5]
            ) or "No identical recurring findings; query the encrypted corpus for evidence."
            content = (
                f"Video analysis corpus contains {count:,} encrypted records and about "
                f"{tokens:,} analyzed-text tokens. Top tags: {top_tags}. "
                f"Frequent terms: {top_terms}. Recurring findings: {recurring}"
            )
            key_hash = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
            memory = self.store.set_current(
                f"video-analysis/{key_hash}/corpus",
                content,
                source="lians video analysis consolidation",
                topic="video analysis corpus",
                metadata={
                    "record_count": count,
                    "analysis_tokens": tokens,
                    "method": result["method"],
                },
                kind="research",
                scope="project",
                project_id=project_id,
                source_client="video-pipeline",
                reason="new encrypted video-analysis consolidation",
            )
            result["memory"] = memory
        return result

    def project_stats(self, project_id: str) -> dict[str, Any]:
        with self.store._connect() as db:
            row = db.execute(
                """SELECT COUNT(*), COALESCE(SUM(token_estimate), 0),
                          COUNT(DISTINCT external_id_hash)
                   FROM video_analysis_records WHERE profile = ? AND project_id = ?""",
                (self.store.profile, project_id),
            ).fetchone()
            run_count = db.execute(
                """SELECT COUNT(*) FROM video_analysis_runs
                   WHERE profile = ? AND project_id = ?""",
                (self.store.profile, project_id),
            ).fetchone()[0]
        return {
            "project_id": project_id,
            "records": row[0] or 0,
            "analysis_tokens": row[1] or 0,
            "videos": row[2] or 0,
            "runs": run_count or 0,
            "encrypted": True,
        }
