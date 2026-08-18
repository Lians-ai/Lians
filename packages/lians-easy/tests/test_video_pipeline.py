from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from lians_easy.store import MemoryStore
from lians_easy.video_pipeline import VideoAnalysisPipeline, normalize_video_analysis


def _record(index: int, *, theme: str = "retention") -> dict[str, object]:
    return {
        "external_id": f"video-{index:05d}",
        "title": f"Creator interview {index}",
        "summary": (
            f"The participant discusses {theme} and onboarding friction "
            f"needle{index:05d}."
        ),
        "findings": ["Clear setup guidance improves first-session completion."],
        "tags": [theme, "onboarding"],
        "provider": "local-test",
        "occurred_at": "2026-08-17T12:00:00Z",
        "metadata": {"duration_seconds": 60 + index},
    }


def _write_jsonl(path, count: int) -> None:
    path.write_text(
        "".join(json.dumps(_record(index)) + "\n" for index in range(count)),
        encoding="utf-8",
    )


def test_normalize_video_analysis_accepts_provider_neutral_aliases() -> None:
    normalized = normalize_video_analysis(
        {
            "id": "source-1",
            "transcript_summary": "A bounded summary.",
            "findings": "One finding",
        }
    )

    assert normalized["external_id"] == "source-1"
    assert normalized["summary"] == "A bounded summary."
    assert normalized["findings"] == ["One finding"]


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"external_id": "video-1"},
        {"external_id": "video-1", "summary": "x", "metadata": []},
        {"external_id": "video-1", "summary": "api_key=sk-secret-value-12345"},
    ],
)
def test_normalize_video_analysis_rejects_invalid_or_sensitive_records(record) -> None:
    with pytest.raises((TypeError, ValueError)):
        normalize_video_analysis(record)


def test_jsonl_ingestion_resumes_and_exact_replay_is_idempotent(tmp_path) -> None:
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 12)
    pipeline = VideoAnalysisPipeline(MemoryStore(tmp_path / "memory.sqlite3"))

    partial = pipeline.ingest_jsonl(
        source,
        run_id="research-wave",
        project_id="research",
        batch_size=4,
        max_batches=2,
    )
    assert partial["status"] == "running"
    assert partial["checkpoint"] == 8
    assert partial["inserted"] == 8

    complete = pipeline.ingest_jsonl(
        source,
        run_id="research-wave",
        project_id="research",
        batch_size=4,
    )
    assert complete["status"] == "complete"
    assert complete["checkpoint"] == 12
    assert complete["inserted"] == 12
    assert complete["resumed_from"] == 8

    replay = pipeline.ingest_jsonl(
        source,
        run_id="research-wave",
        project_id="research",
        batch_size=4,
    )
    assert replay["status"] == "complete"
    assert replay["inserted"] == 12
    assert pipeline.project_stats("research")["records"] == 12


def test_new_run_deduplicates_existing_exact_outputs(tmp_path) -> None:
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 6)
    pipeline = VideoAnalysisPipeline(MemoryStore(tmp_path / "memory.sqlite3"))
    pipeline.ingest_jsonl(source, run_id="first", project_id="research", batch_size=3)

    second = pipeline.ingest_jsonl(
        source,
        run_id="second",
        project_id="research",
        batch_size=3,
    )

    assert second["inserted"] == 0
    assert second["duplicates"] == 6
    assert pipeline.project_stats("research")["records"] == 6


def test_run_rejects_changed_input_or_stale_batch(tmp_path) -> None:
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 3)
    pipeline = VideoAnalysisPipeline(MemoryStore(tmp_path / "memory.sqlite3"))
    pipeline.ingest_jsonl(
        source,
        run_id="fixed-run",
        project_id="research",
        batch_size=1,
        max_batches=1,
    )
    source.write_text(json.dumps(_record(99)) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="different project or input"):
        pipeline.ingest_jsonl(
            source,
            run_id="fixed-run",
            project_id="research",
            batch_size=1,
        )


def test_private_search_and_exact_lookup_do_not_store_plaintext(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 5)
    pipeline = VideoAnalysisPipeline(MemoryStore(database))
    pipeline.ingest_jsonl(source, run_id="search", project_id="research")

    result = pipeline.search("needle00004 onboarding", project_id="research")
    exact = pipeline.get("video-00004", project_id="research")

    assert result[0]["external_id"] == "video-00004"
    assert exact[0]["summary"].endswith("needle00004.")
    with sqlite3.connect(database) as db:
        record = db.execute(
            """SELECT external_id_hash, payload_cipher, search_terms
               FROM video_analysis_records LIMIT 1"""
        ).fetchone()
        terms = b"".join(
            row[0] for row in db.execute("SELECT term_hash FROM video_analysis_terms")
        )
    assert "video-00000" not in record[0]
    assert b"Creator interview" not in record[1]
    assert b"onboarding" not in record[2]
    assert b"onboarding" not in terms


def test_existing_video_database_gets_compact_private_index_column(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    store = MemoryStore(database)
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE video_analysis_records (
                   id TEXT PRIMARY KEY, profile TEXT NOT NULL, project_id TEXT NOT NULL,
                   run_id TEXT NOT NULL, external_id_hash TEXT NOT NULL,
                   content_sha256 TEXT NOT NULL, payload_cipher BLOB NOT NULL,
                   payload_nonce BLOB NOT NULL, token_estimate INTEGER NOT NULL,
                   occurred_at TEXT, created_at TEXT NOT NULL,
                   UNIQUE (profile, project_id, external_id_hash, content_sha256)
               )"""
        )

    VideoAnalysisPipeline(store)

    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(video_analysis_records)")}
    assert "search_terms" in columns


def test_consolidation_is_bounded_and_can_promote_one_memory(tmp_path) -> None:
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 10)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = VideoAnalysisPipeline(store)
    pipeline.ingest_jsonl(source, run_id="summary", project_id="research")

    summary = pipeline.consolidate(project_id="research", top_n=5, remember=True)

    assert summary["record_count"] == 10
    assert summary["top_tags"][0] == {"value": "retention", "count": 10}
    assert summary["memory"]["kind"] == "research"
    assert len(store.list(kind="research")) == 1


def test_profile_erasure_includes_large_video_corpus(tmp_path) -> None:
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 4)
    store = MemoryStore(tmp_path / "memory.sqlite3")
    pipeline = VideoAnalysisPipeline(store)
    pipeline.ingest_jsonl(source, run_id="erase", project_id="research")

    result = store.erase_profile(
        confirmed=True,
        confirmation="ERASE ALL LIANS MEMORY",
    )

    assert result["video_analysis_records_erased"] == 4
    assert result["video_analysis_runs_erased"] == 1
    assert pipeline.project_stats("research")["records"] == 0


def test_input_digest_is_stable_for_resume(tmp_path) -> None:
    source = tmp_path / "analysis.jsonl"
    _write_jsonl(source, 2)
    pipeline = VideoAnalysisPipeline(MemoryStore(tmp_path / "memory.sqlite3"))

    result = pipeline.ingest_jsonl(source, run_id="digest", project_id="research")

    assert result["input_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
