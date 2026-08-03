#!/usr/bin/env python3
"""Inventory or transactionally rewrap all Lians master-key-derived values."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "agentmem" / "src"
BACKUP_TOOLS_ROOT = REPOSITORY_ROOT / "ops" / "backup"
for path in (SOURCE_ROOT, BACKUP_TOOLS_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backup_lib import OperatorError, verify_bundle  # noqa: E402
from lians.config import get_settings  # noqa: E402
from lians.db import parse_db_url  # noqa: E402
from lians.key_rotation import (  # noqa: E402
    KeyRotationError,
    inspect_master_key_write_fence,
    prepare_master_key_write_fence,
    run_rotation,
)
from lians.kms import (  # noqa: E402
    MasterKeyConfigurationError,
    get_master_keyring,
    load_master_key,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run by default. Fence preparation and --apply require a recent "
            "verified backup and use one PostgreSQL transaction."
        )
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--apply",
        action="store_true",
        help="Commit verified rewrites and atomically narrow a prepared write fence",
    )
    action.add_argument(
        "--prepare-write-fence",
        action="store_true",
        help=(
            "Lock every protected table and activate the configured bounded "
            "current/previous write fence"
        ),
    )
    action.add_argument(
        "--write-fence-status",
        action="store_true",
        help="Inspect the persistent write fence and its configured-key relationship",
    )
    action.add_argument(
        "--assert-write-fence-prepared",
        action="store_true",
        help="Exit nonzero unless the fence exactly allows configured current and previous IDs",
    )
    action.add_argument(
        "--assert-write-fence-narrowed",
        action="store_true",
        help="Exit nonzero unless the fence allows only the configured current ID",
    )
    parser.add_argument(
        "--backup-bundle",
        type=Path,
        help=(
            "Backup bundle produced by ops/backup/create_logical_backup.py "
            "(required for fence preparation and --apply)"
        ),
    )
    parser.add_argument(
        "--database-id",
        default=os.getenv("LIANS_DATABASE_ID", "").strip(),
        help="Stable database identity; must match the verified backup (or LIANS_DATABASE_ID)",
    )
    parser.add_argument("--max-backup-age-hours", type=float, default=24.0)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--lock-timeout-seconds", type=int, default=30)
    parser.add_argument(
        "--assert-safe-to-remove-previous",
        action="store_true",
        help="Exit nonzero unless the dry inventory proves zero legacy/previous values",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        help="Write the JSON report to a new mode-0600 file as well as stdout",
    )
    return parser.parse_args()


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise OperatorError("Backup manifest completed_at is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OperatorError("Backup manifest completed_at is invalid") from exc
    if parsed.tzinfo is None:
        raise OperatorError("Backup manifest completed_at has no timezone")
    return parsed.astimezone(UTC)


def verified_backup(args: argparse.Namespace) -> dict[str, Any] | None:
    mutating = args.apply or args.prepare_write_fence
    if not mutating:
        if args.backup_bundle is not None:
            raise OperatorError(
                "--backup-bundle is accepted only with --apply or --prepare-write-fence"
            )
        return None
    if args.backup_bundle is None:
        raise OperatorError(
            "--backup-bundle is required with --apply or --prepare-write-fence"
        )
    if not args.database_id:
        raise OperatorError(
            "--database-id or LIANS_DATABASE_ID is required for write-fence mutation"
        )
    if not 0 < args.max_backup_age_hours <= 168:
        raise OperatorError("--max-backup-age-hours must be greater than 0 and at most 168")

    manifest, verification = verify_bundle(args.backup_bundle)
    completed_at = _parse_timestamp(manifest.get("completed_at"))
    now = datetime.now(UTC)
    if completed_at > now + timedelta(minutes=5):
        raise OperatorError("Backup completion time is in the future")
    if now - completed_at > timedelta(hours=args.max_backup_age_hours):
        raise OperatorError("Verified backup is older than the permitted rotation window")
    revisions = manifest.get("schema_inventory", {}).get("alembic_revisions")
    if not isinstance(revisions, list) or len(revisions) != 1 or not revisions[0]:
        raise OperatorError("Backup must contain exactly one Alembic head")
    source = manifest.get("source") or {}
    backup_database_id = source.get("database_id")
    if not isinstance(backup_database_id, str) or not backup_database_id:
        raise OperatorError("Backup has no stable source database identity")
    if backup_database_id != args.database_id:
        raise OperatorError("--database-id does not match the verified backup")
    system_identifier = source.get("system_identifier")
    if system_identifier is not None and not isinstance(system_identifier, str):
        raise OperatorError("Backup PostgreSQL system identifier is malformed")
    return {
        "manifest_sha256": verification["manifest_sha256"],
        "source_database": source.get("database_name"),
        "source_system_identifier": system_identifier,
        "source_endpoint_fingerprint": source.get("endpoint_fingerprint_sha256"),
        "database_id": backup_database_id,
        "alembic_revision": revisions[0],
        "backup_id_sha256": hashlib.sha256(
            str(manifest["backup_id"]).encode("utf-8")
        ).hexdigest(),
    }


def _write_report(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise OperatorError("Refusing to overwrite --report-file")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _database_url_fingerprint(database_url: str) -> str:
    parsed = urlparse(database_url)
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    database = unquote(parsed.path.lstrip("/")).strip()
    if not host or not database:
        raise OperatorError("DATABASE_URL must identify one PostgreSQL host and database")
    canonical = f"postgresql|{host}|{parsed.port or 5432}|{database}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    lifecycle_action = any(
        (
            args.prepare_write_fence,
            args.write_fence_status,
            args.assert_write_fence_prepared,
            args.assert_write_fence_narrowed,
        )
    )
    if args.assert_safe_to_remove_previous and lifecycle_action:
        raise OperatorError(
            "--assert-safe-to-remove-previous is used with the default dry inventory"
        )
    backup = verified_backup(args)
    await load_master_key()
    ring = get_master_keyring()
    if args.assert_safe_to_remove_previous and ring.previous is None:
        raise KeyRotationError(
            "--assert-safe-to-remove-previous requires MASTER_KEY_PREVIOUS_ID and material"
        )

    settings = get_settings()
    if backup is not None and _database_url_fingerprint(settings.database_url) != backup.get(
        "source_endpoint_fingerprint"
    ):
        raise OperatorError("DATABASE_URL endpoint does not match the verified backup")
    database_url, connect_args = parse_db_url(settings.database_url)
    engine = create_async_engine(database_url, connect_args=connect_args, pool_pre_ping=True)
    try:
        async with (
            engine.connect() as connection,
            connection.begin(),
            AsyncSession(bind=connection, expire_on_commit=False) as session,
        ):
            common_backup = {
                "backup_manifest_sha256": (backup or {}).get("manifest_sha256"),
                "backup_source_database": (backup or {}).get("source_database"),
                "backup_source_system_identifier": (backup or {}).get(
                    "source_system_identifier"
                ),
                "backup_alembic_revision": (backup or {}).get("alembic_revision"),
                "expected_database_id": args.database_id or None,
                "backup_database_id": (backup or {}).get("database_id"),
            }
            if args.prepare_write_fence:
                report = await prepare_master_key_write_fence(
                    session,
                    **common_backup,
                    batch_size=args.batch_size,
                    lock_timeout_seconds=args.lock_timeout_seconds,
                )
            elif args.write_fence_status:
                report = await inspect_master_key_write_fence(
                    session,
                    lock_timeout_seconds=args.lock_timeout_seconds,
                )
            elif args.assert_write_fence_prepared:
                report = await inspect_master_key_write_fence(
                    session,
                    assertion="prepared",
                    lock_timeout_seconds=args.lock_timeout_seconds,
                )
            elif args.assert_write_fence_narrowed:
                report = await inspect_master_key_write_fence(
                    session,
                    assertion="narrowed",
                    lock_timeout_seconds=args.lock_timeout_seconds,
                )
            else:
                report = await run_rotation(
                    session,
                    apply=args.apply,
                    **common_backup,
                    batch_size=args.batch_size,
                    lock_timeout_seconds=args.lock_timeout_seconds,
                )
    finally:
        await engine.dispose()

    if backup is not None:
        report["backup_id_sha256"] = backup["backup_id_sha256"]
    if args.assert_safe_to_remove_previous and not report["safe_to_remove_previous"]:
        raise KeyRotationError(
            "Previous key removal refused: legacy or previous-key values remain"
        )
    return report


def main() -> int:
    args = arguments()
    report = asyncio.run(execute(args))
    payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    if args.report_file is not None:
        _write_report(args.report_file, payload)
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ImportError, KeyRotationError, MasterKeyConfigurationError, OperatorError) as exc:
        error = {
            "schema": "urn:lians:ops:master-key-rotation-report:v1",
            "status": "failed",
            "error": type(exc).__name__,
            "message": str(exc)[:1000],
        }
        print(json.dumps(error, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001
        # Cloud SDK and database exceptions can include endpoints or provider
        # diagnostics.  Keep the default failure report deliberately opaque.
        print(
            json.dumps(
                {
                    "schema": "urn:lians:ops:master-key-rotation-report:v1",
                    "status": "failed",
                    "error": type(exc).__name__,
                    "message": "Unexpected rotation failure; inspect protected operator logs",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(2)
