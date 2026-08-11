"""The local-first Lians developer command line."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lians", description="Lians developer tools")
    subcommands = parser.add_subparsers(dest="command", required=True)

    evaluate = subcommands.add_parser("eval", help="Evaluate memory quality and latency")
    evaluate.add_argument("dataset", nargs="?", help="LoCoMo-style JSON dataset")
    evaluate.add_argument("--k", type=int, default=5)
    evaluate.add_argument("--mode", choices=("fast", "deep", "reconstruct"), default="fast")
    evaluate.add_argument("--min-recall", type=float, default=0.8)
    evaluate.add_argument("--max-stale-leak-rate", type=float, default=0.0)
    evaluate.add_argument("--max-p95-latency-ms", type=float, default=500.0)
    evaluate.add_argument("--embedding-provider")
    evaluate.add_argument("--json", action="store_true", dest="json_stdout")
    evaluate.add_argument("--output", type=Path, help="Write the full JSON report")

    dev = subcommands.add_parser("dev", help="Run a persistent local Lians API")
    dev.add_argument("--db", type=Path, default=Path("~/.lians/local.db"))
    dev.add_argument("--host", default="127.0.0.1")
    dev.add_argument("--port", type=int, default=8000)
    dev.add_argument("--namespace", default="local-dev")
    dev.add_argument("--api-key", help="Use a stable development API key")
    dev.add_argument("--embedding-provider", default="local")

    subcommands.add_parser("doctor", help="Check the local Lians runtime")
    return parser


def _run_eval(args: argparse.Namespace) -> int:
    from .evaluation import load_dataset, render_summary, run_evaluation
    from .local_client import LocalLiansClient

    dataset = load_dataset(args.dataset)
    with LocalLiansClient(embedding_provider=args.embedding_provider) as client:
        report = run_evaluation(
            client,
            dataset,
            k=args.k,
            mode=args.mode,
            min_recall=args.min_recall,
            max_stale_leak_rate=args.max_stale_leak_rate,
            max_p95_latency_ms=args.max_p95_latency_ms,
        )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered if args.json_stdout else render_summary(report))
    return 0 if report["evaluation_passed"] else 1


async def _prepare_dev_database(database_url: str, namespace: str, raw_key: str) -> None:
    from src.lians.db import AsyncSessionLocal, Base, engine
    from src.lians.models import ApiKey
    from sqlalchemy import select

    pg_indexes = [
        index
        for table in Base.metadata.tables.values()
        for index in list(table.indexes)
        if index.dialect_kwargs.get("postgresql_using") is not None
    ]
    for index in pg_indexes:
        index.table.indexes.discard(index)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    hashed_key = hashlib.sha256(raw_key.encode()).hexdigest()
    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(select(ApiKey).where(ApiKey.hashed_key == hashed_key))
        ).scalar_one_or_none()
        if existing is None:
            session.add(ApiKey(
                hashed_key=hashed_key,
                namespace=namespace,
                scopes=["read", "write", "admin"],
                role="owner",
            ))
            await session.commit()


def _run_dev(args: argparse.Namespace) -> int:
    from .local_client import _ensure_src_importable

    _ensure_src_importable()
    db_path = args.db.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    raw_key = args.api_key or secrets.token_urlsafe(32)
    os.environ.update({
        "DATABASE_URL": f"sqlite+aiosqlite:///{db_path.as_posix()}",
        "EMBEDDING_PROVIDER": args.embedding_provider,
        "AGENTMEM_ALLOW_UNENCRYPTED": "true",
        "MASTER_ENCRYPTION_KEY": "",
        "RECALL_CACHE_ENABLED": "false",
        "RLS_BARRIERS_ENABLED": "false",
        "DEPLOYMENT_ENVIRONMENT": "development",
        "CORS_ORIGINS": "*",
        "API_SECRET_SEED": secrets.token_urlsafe(32),
    })
    asyncio.run(_prepare_dev_database(os.environ["DATABASE_URL"], args.namespace, raw_key))

    print("Lians local API is ready")
    print(f"  API URL  http://{args.host}:{args.port}")
    print(f"  API key  {raw_key}")
    print(f"  database {db_path}")
    print("The API key is shown once. Pass --api-key to keep it stable across restarts.")
    import uvicorn

    uvicorn.run("src.lians.main:app", host=args.host, port=args.port, reload=False)
    return 0


def _run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    for module, label in (
        ("sqlalchemy", "SQLite runtime"),
        ("aiosqlite", "async SQLite driver"),
        ("fastapi", "local API"),
        ("uvicorn", "development server"),
    ):
        try:
            __import__(module)
            checks.append((label, True, "available"))
        except ImportError:
            checks.append((label, False, f"missing {module}"))
    try:
        __import__("sentence_transformers")
        checks.append(("semantic embeddings", True, "sentence-transformers"))
    except ImportError:
        checks.append(("semantic embeddings", False, "using test-grade local embeddings"))

    print("Lians local runtime")
    for label, passed, detail in checks:
        print(f"  {'OK' if passed else '--':2} {label:22} {detail}")
    required_ok = all(passed for _, passed, _ in checks[:4])
    if not required_ok:
        print("Install the complete runtime with: pip install 'lians-sdk[local]'")
    return 0 if required_ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "eval":
        return _run_eval(args)
    if args.command == "dev":
        return _run_dev(args)
    return _run_doctor()


if __name__ == "__main__":
    raise SystemExit(main())
