"""Exercise Lians' 10,000-video analysis ingestion and recovery boundary."""

from __future__ import annotations

import argparse
import ctypes
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from lians_easy.store import MemoryStore
from lians_easy.video_pipeline import VideoAnalysisPipeline


def _peak_rss_bytes() -> int | None:
    """Read process peak RSS without instrumenting the timed workload."""

    if sys.platform == "win32":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        succeeded = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.PeakWorkingSetSize) if succeeded else None
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    except (ImportError, OSError):
        return None


def _record(index: int) -> dict[str, object]:
    themes = ("retention", "pricing", "onboarding", "trust", "workflow")
    theme = themes[index % len(themes)]
    cohort = f"cohort-{index % 25:02d}"
    scenes = [
        {
            "scene": scene + 1,
            "start_seconds": scene * 45,
            "end_seconds": (scene + 1) * 45,
            "visual_summary": (
                f"Participant demonstrates the {theme} workflow at stage {scene + 1} "
                "while the interface, gestures, and visible outcome remain inspectable."
            ),
            "spoken_claims": [
                f"The participant reports {theme} friction at stage {scene + 1}.",
                "The participant asks for evidence before the agent takes an action.",
            ],
            "entities": [theme, cohort, "agent workflow"],
            "sentiment": ("frustrated", "uncertain", "positive")[scene % 3],
            "confidence": round(0.91 + ((index + scene) % 8) / 100, 2),
        }
        for scene in range(8)
    ]
    return {
        "external_id": f"video-{index:05d}",
        "title": f"Research video {index:05d}",
        "summary": (
            f"Participant {index:05d} discusses {theme}, {cohort}, and an agent workflow. "
            f"The unique evidence marker is needle{index:05d}."
        ),
        "findings": [
            f"{theme.title()} affects whether this participant finishes the workflow.",
            "Users want evidence they can inspect before an agent acts.",
            "The first failure occurs during setup rather than during final output review.",
            "Visible progress improves trust when a long-running task changes state.",
            "Repeated instructions indicate missing continuity between agent sessions.",
            "The participant prefers corrective questions over silent assumptions.",
            "A concise handoff is more useful than replaying the complete transcript.",
            "The strongest opportunity combines continuity, control, and inspectable evidence.",
        ],
        "tags": [theme, cohort, "agent-workflow"],
        "provider": "synthetic-provider-output",
        "model": "benchmark-fixture-v1",
        "occurred_at": "2026-08-17T12:00:00Z",
        "metadata": {
            "duration_seconds": 360,
            "language": "en",
            "synthetic": True,
            "analysis_dimensions": [
                "intent",
                "sentiment",
                "claims",
                "entities",
                "objections",
                "workflow",
                "recommendations",
                "timestamped evidence",
            ],
            "intent": f"Complete a {theme} workflow with less repeated context.",
            "primary_objection": "The agent may act without enough inspectable evidence.",
            "recommended_action": (
                "Return the smallest relevant handoff, expose its evidence, and ask before "
                "crossing a consequential boundary."
            ),
            "scenes": scenes,
        },
    }


def _write_input(path: Path, records: int) -> float:
    started = time.perf_counter()
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for index in range(records):
            handle.write(json.dumps(_record(index), separators=(",", ":")) + "\n")
    return time.perf_counter() - started


def run(*, records: int = 10_000, batch_size: int = 500) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="lians-video-10k-") as temporary:
        root = Path(temporary)
        source = root / "video-analysis.jsonl"
        database = root / "memory.sqlite3"
        generation_seconds = _write_input(source, records)
        pipeline = VideoAnalysisPipeline(MemoryStore(database))

        partial_started = time.perf_counter()
        partial_batch_size = min(batch_size, 2_000)
        partial = pipeline.ingest_jsonl(
            source,
            run_id="ten-thousand-video-analysis",
            project_id="video-research",
            batch_size=partial_batch_size,
            max_batches=2,
        )
        partial_seconds = time.perf_counter() - partial_started

        resume_started = time.perf_counter()
        complete = pipeline.ingest_jsonl(
            source,
            run_id="ten-thousand-video-analysis",
            project_id="video-research",
            batch_size=batch_size,
        )
        resume_seconds = time.perf_counter() - resume_started
        peak_bytes = _peak_rss_bytes()

        replay_started = time.perf_counter()
        replay = pipeline.ingest_jsonl(
            source,
            run_id="ten-thousand-video-analysis-replay",
            project_id="video-research",
            batch_size=batch_size,
        )
        replay_seconds = time.perf_counter() - replay_started

        search_started = time.perf_counter()
        search = pipeline.search(
            f"needle{records - 1:05d} agent workflow",
            project_id="video-research",
            limit=5,
        )
        search_ms = (time.perf_counter() - search_started) * 1_000

        consolidation_started = time.perf_counter()
        consolidation = pipeline.consolidate(
            project_id="video-research",
            top_n=20,
            remember=True,
        )
        consolidation_seconds = time.perf_counter() - consolidation_started
        stats = pipeline.project_stats("video-research")
        db = sqlite3.connect(database)
        try:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            plaintext_hits = db.execute(
                """SELECT COUNT(*) FROM video_analysis_records
                   WHERE CAST(payload_cipher AS TEXT) LIKE '%Research video%'"""
            ).fetchone()[0]
        finally:
            db.close()

        ingest_seconds = partial_seconds + resume_seconds
        gates = {
            "all_records_committed": complete["checkpoint"] == records,
            "interruption_recovered": partial["checkpoint"]
            == min(records, partial_batch_size * 2),
            "no_duplicate_rows": stats["records"] == records,
            "replay_was_idempotent": replay["inserted"] == 0
            and replay["duplicates"] == records,
            "oldest_or_latest_evidence_searchable": bool(search)
            and search[0]["external_id"] == f"video-{records - 1:05d}",
            "bounded_consolidation_created": consolidation["record_count"] == records
            and bool(consolidation.get("memory")),
            "sqlite_integrity_ok": integrity == "ok",
            "ciphertext_has_no_obvious_plaintext": plaintext_hits == 0,
            "single_digit_structured_ingest": records != 10_000 or ingest_seconds < 10.0,
            "single_digit_complete_hot_path": records != 10_000
            or ingest_seconds + (search_ms / 1_000) + consolidation_seconds < 10.0,
        }
        return {
            "status": "passed" if all(gates.values()) else "failed",
            "scope": {
                "records": records,
                "batch_size": batch_size,
                "input_bytes": source.stat().st_size,
                "provider_inference": "excluded; fixture represents completed provider outputs",
            },
            "timing": {
                "fixture_generation_seconds": round(generation_seconds, 3),
                "partial_ingest_seconds": round(partial_seconds, 3),
                "resume_seconds": round(resume_seconds, 3),
                "total_ingest_seconds": round(ingest_seconds, 3),
                "records_per_second": round(records / ingest_seconds, 2),
                "idempotent_replay_seconds": round(replay_seconds, 3),
                "search_ms": round(search_ms, 3),
                "consolidation_seconds": round(consolidation_seconds, 3),
            },
            "resources": {
                "database_bytes": database.stat().st_size,
                "process_peak_rss_bytes": peak_bytes,
            },
            "run": complete,
            "replay": replay,
            "project": stats,
            "top_tags": consolidation["top_tags"][:5],
            "gates": gates,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run(records=args.records, batch_size=args.batch_size)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
