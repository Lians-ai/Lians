#!/usr/bin/env python3
"""Model-free latency gate for the persistent Codex recall hook runtime.

The benchmark reports two different measurements:

* true daemon cold start, including local embedding-model load and a probe;
* fresh hook-process latency while that exact authenticated daemon is warm.

Only hashes and timing/status metrics are printed. Prompt and recalled context
remain in subprocess pipes and temporary files that are removed on exit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
DEFAULT_HOOK = HERE / "user_prompt_submit_recall.py"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _logical_live_facts_hash(database: Path, namespace: str, agent_id: str) -> tuple[int, str]:
    """Hash retrieval-bearing rows independently of mutable SQLite internals."""

    connection = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT id, namespace, agent_id, memory_id, predicate_key, subject_id,
                   barrier_group, event_time, importance, metadata,
                   content_encrypted, embedding
            FROM live_facts
            WHERE namespace = ? AND agent_id = ?
            ORDER BY id ASC
            """,
            (namespace, agent_id),
        ).fetchall()
    finally:
        connection.close()
    digest = hashlib.sha256()
    for row in rows:
        canonical = [
            *row[:9],
            json.loads(row[9]) if isinstance(row[9], str) else row[9],
            hashlib.sha256(bytes(row[10] or b"")).hexdigest(),
            hashlib.sha256(str(row[11] or "").encode("utf-8")).hexdigest(),
        ]
        digest.update(
            json.dumps(
                canonical,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return len(rows), digest.hexdigest()


def _environment(args: argparse.Namespace, runtime_dir: Path, receipt: Path) -> dict[str, str]:
    values = dict(os.environ)
    values.update(
        {
            "LIANS_URL": "",
            "LIANS_LOCAL_DB": str(args.db.resolve()),
            "LIANS_NAMESPACE": args.namespace,
            "LIANS_AGENT_ID": args.agent_id,
            "LIANS_MCP_PROJECT_ROOT": "",
            "LIANS_CODEX_HOOK_K": str(args.k),
            "LIANS_CODEX_HOOK_MAX_TOKENS": str(args.max_tokens),
            "LIANS_CODEX_HOOK_MIN_SCORE": str(args.min_score),
            "LIANS_CODEX_HOOK_DAEMON": "client",
            "LIANS_CODEX_HOOK_DAEMON_RUNTIME_DIR": str(runtime_dir),
            "LIANS_CODEX_HOOK_DAEMON_IDLE_SECONDS": "600",
            "LIANS_CODEX_HOOK_DAEMON_REQUEST_TIMEOUT_MS": str(args.timeout_ms),
            "LIANS_CODEX_HOOK_DAEMON_START_TIMEOUT_MS": "60000",
            "LIANS_CODEX_HOOK_RECEIPT": str(receipt),
            "HF_HUB_OFFLINE": "1",
        }
    )
    if args.embedding_provider == "bge-onnx":
        values.update(
            {
                "EMBEDDING_PROVIDER": "bge-onnx",
                "BGE_ONNX_ARTIFACT_DIR": str(args.bge_onnx_artifact_dir.resolve()),
                "BGE_ONNX_INTRA_OP_THREADS": str(args.bge_onnx_intra_op_threads),
            }
        )
        values.pop("SENTENCE_TRANSFORMER_MODEL", None)
    else:
        values.update(
            {
                "EMBEDDING_PROVIDER": "sentence-transformers",
                "SENTENCE_TRANSFORMER_MODEL": args.embedding_model,
            }
        )
        values.pop("BGE_ONNX_ARTIFACT_DIR", None)
        values.pop("BGE_ONNX_INTRA_OP_THREADS", None)
    return values


def _command(
    hook: Path,
    argument: str,
    env: dict[str, str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(hook), argument],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=timeout,
    )


def run(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    if not args.db.is_file():
        raise FileNotFoundError(args.db)
    logical_rows, logical_sha256 = _logical_live_facts_hash(args.db, args.namespace, args.agent_id)
    hook = args.hook.resolve()
    event = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": f"<lians-query>{args.query}</lians-query>",
            "cwd": str(Path.cwd()),
        },
        separators=(",", ":"),
    )
    with tempfile.TemporaryDirectory(prefix="lians-codex-hook-bench-") as raw_dir:
        root = Path(raw_dir)
        receipt = root / "receipt.jsonl"
        env = _environment(args, root / "runtime", receipt)
        cold_started = time.perf_counter()
        prewarm = _command(hook, "--prewarm", env, timeout=90)
        cold_ms = (time.perf_counter() - cold_started) * 1_000
        if prewarm.returncode != 0:
            raise RuntimeError("recall daemon prewarm failed")

        wall_times: list[float] = []
        outputs: list[subprocess.CompletedProcess[str]] = []
        try:
            for _index in range(args.repeats):
                started = time.perf_counter()
                process = subprocess.run(
                    [sys.executable, str(hook)],
                    input=event,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                    timeout=max(5.0, args.timeout_ms / 1_000 + 2.0),
                )
                wall_times.append((time.perf_counter() - started) * 1_000)
                outputs.append(process)
        finally:
            _command(hook, "--stop", env, timeout=5)

        receipts = [
            json.loads(line)
            for line in receipt.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        valid_outputs = all(
            item.returncode == 0
            and not item.stderr
            and bool(item.stdout)
            and len(item.stdout.encode("utf-8")) <= 768 * 4 + 512
            for item in outputs
        )
        valid_receipts = len(receipts) == args.repeats and all(
            item.get("status") == "injected"
            and item.get("retrieval_transport") == "daemon"
            and item.get("retrieval_degraded") is False
            for item in receipts
        )
        p50 = statistics.median(wall_times)
        p95 = _nearest_rank(wall_times, 0.95)
        receipt_times = [float(item["elapsed_ms"]) for item in receipts]
        passed = valid_outputs and valid_receipts and p95 < args.target_p95_ms
        report = {
            "schema_version": "lians.codex-hook-daemon-latency.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_calls": 0,
            "database_file_sha256_after_run": hashlib.sha256(args.db.read_bytes()).hexdigest(),
            "logical_live_facts": {
                "rows": logical_rows,
                "sha256": logical_sha256,
                "canonicalization": (
                    "live_facts ordered by id as newline-delimited compact JSON; "
                    "metadata parsed as JSON; encrypted content and stored embedding "
                    "represented by their SHA-256"
                ),
            },
            "query_sha256": _sha256(args.query),
            "embedding_provider": args.embedding_provider,
            "embedding_model": (
                args.embedding_model
                if args.embedding_provider == "sentence-transformers"
                else "BAAI/bge-large-en-v1.5@d4aa6901d3a41ba39fb536a557fa166f842b0e09"
            ),
            "bge_onnx_artifact_sha256": (
                {
                    name: _sha256_file(args.bge_onnx_artifact_dir / name)
                    for name in ("model.onnx", "tokenizer.json", "manifest.json")
                }
                if args.embedding_provider == "bge-onnx"
                else None
            ),
            "memory_identity": {
                "namespace": args.namespace,
                "agent_id": args.agent_id,
                "k": args.k,
                "max_tokens": args.max_tokens,
                "min_score": args.min_score,
            },
            "true_daemon_cold_start_ms": round(cold_ms, 3),
            "measurement_boundaries": {
                "true_daemon_cold_start": (
                    "fresh daemon process through SDK/database initialization, exact "
                    "artifact hash validation, ONNX session creation, and probe recall"
                ),
                "warm_daemon_fresh_hook_process": (
                    "fresh Python hook process through authenticated loopback recall, "
                    "context rendering, receipt append, and protocol output; daemon is "
                    "already validated and prewarmed"
                ),
                "filesystem_cache": ("operating-system filesystem page cache was not flushed"),
            },
            "warm_daemon_fresh_hook_process": {
                "repeats": args.repeats,
                "wall_time_ms": [round(value, 3) for value in wall_times],
                "p50_ms": round(p50, 3),
                "p95_ms": round(p95, 3),
                "maximum_ms": round(max(wall_times), 3),
                "all_injected": valid_receipts,
                "all_protocol_outputs_valid": valid_outputs,
                "top_scores": [item.get("top_score") for item in receipts],
                "receipt_elapsed_ms": receipt_times,
                "receipt_p50_ms": round(statistics.median(receipt_times), 3),
                "receipt_p95_ms": round(_nearest_rank(receipt_times, 0.95), 3),
                "receipt_maximum_ms": round(max(receipt_times), 3),
            },
            "gate": {
                "target_p95_ms": args.target_p95_ms,
                "passed": passed,
                "scope": "fresh hook process to an already-running authenticated loopback daemon",
            },
        }
        return report, passed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--embedding-model", default="BAAI/bge-large-en-v1.5")
    parser.add_argument(
        "--embedding-provider",
        choices=("sentence-transformers", "bge-onnx"),
        default="sentence-transformers",
    )
    parser.add_argument("--bge-onnx-artifact-dir", type=Path)
    parser.add_argument("--bge-onnx-intra-op-threads", type=int, default=8)
    parser.add_argument("--hook", type=Path, default=DEFAULT_HOOK)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--min-score", type=float, default=0.45)
    parser.add_argument("--timeout-ms", type=int, default=3_000)
    parser.add_argument("--target-p95-ms", type=float, default=3_500.0)
    args = parser.parse_args()
    if not 5 <= args.repeats <= 100:
        parser.error("--repeats must be between 5 and 100")
    if args.embedding_provider == "bge-onnx":
        if args.bge_onnx_artifact_dir is None:
            parser.error("--bge-onnx-artifact-dir is required for bge-onnx")
        required = tuple(
            args.bge_onnx_artifact_dir / name
            for name in ("model.onnx", "tokenizer.json", "manifest.json")
        )
        if not all(path.is_file() for path in required):
            parser.error("--bge-onnx-artifact-dir is incomplete")
    if not 1 <= args.bge_onnx_intra_op_threads <= 256:
        parser.error("--bge-onnx-intra-op-threads must be between 1 and 256")
    return args


def main() -> int:
    args = parse_args()
    report, passed = run(args)
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
