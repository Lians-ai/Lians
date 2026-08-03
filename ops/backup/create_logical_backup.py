#!/usr/bin/env python3
"""Create an atomic Lians PostgreSQL logical-backup bundle.

This is the independent logical copy in the recovery strategy.  It does not
claim to provide PostgreSQL point-in-time recovery; PITR requires provider base
backups plus a continuous WAL archive.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from backup_lib import (
    MANIFEST_SCHEMA,
    OperatorError,
    checksum_lines,
    command_version,
    endpoint_fingerprint,
    ensure_directory,
    fsync_directory,
    libpq_environment,
    psql_json,
    psql_scalar,
    require_program,
    sha256_file,
    utc_now,
    write_new_bytes,
    write_new_json,
)


BACKUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,79}$")
ENVIRONMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

SOURCE_SQL = r"""
SELECT jsonb_build_object(
  'database_name', current_database(),
  'server_version', current_setting('server_version'),
  'server_version_num', current_setting('server_version_num')::integer,
  'database_size_bytes', pg_database_size(current_database()),
  'in_recovery', pg_is_in_recovery(),
  'wal_lsn', CASE WHEN pg_is_in_recovery()
      THEN pg_last_wal_replay_lsn()::text
      ELSE pg_current_wal_lsn()::text END,
  'captured_at', clock_timestamp()
)::text;
"""

TABLES_SQL = r"""
SELECT COALESCE(jsonb_agg(
  jsonb_build_object(
    'schema', n.nspname,
    'name', c.relname,
    'rls_enabled', c.relrowsecurity,
    'rls_forced', c.relforcerowsecurity,
    'estimated_rows', GREATEST(c.reltuples::bigint, 0)
  ) ORDER BY n.nspname, c.relname
), '[]'::jsonb)::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\';
"""

EXTENSIONS_SQL = r"""
SELECT COALESCE(jsonb_agg(
  jsonb_build_object('name', extname, 'version', extversion)
  ORDER BY extname
), '[]'::jsonb)::text
FROM pg_extension;
"""

MIGRATIONS_SQL = r"""
SELECT CASE WHEN to_regclass('public.alembic_version') IS NULL
  THEN '[]'::jsonb
  ELSE (SELECT COALESCE(jsonb_agg(version_num ORDER BY version_num), '[]'::jsonb)
        FROM alembic_version)
END::text;
"""

INTEGRITY_SQL = r"""
SELECT jsonb_build_object(
  'invalid_indexes', (
    SELECT count(*) FROM pg_index WHERE NOT indisvalid OR NOT indisready
  ),
  'unvalidated_constraints', (
    SELECT count(*) FROM pg_constraint WHERE NOT convalidated
  )
)::text;
"""

AUDIT_SQL = r"""
SELECT CASE WHEN to_regclass('public.event_log') IS NULL THEN
  jsonb_build_object('present', false)
ELSE jsonb_build_object(
  'present', true,
  'event_count_lower_bound', (SELECT count(*) FROM event_log),
  'namespace_count_lower_bound', (SELECT count(DISTINCT namespace) FROM event_log),
  'fork_count', (
    SELECT count(*) FROM (
      SELECT namespace, prev_hash
      FROM event_log
      GROUP BY namespace, prev_hash
      HAVING count(*) > 1
    ) AS forks
  ),
  'orphan_count', (
    SELECT count(*)
    FROM event_log child
    WHERE child.prev_hash IS NOT NULL
      AND child.prev_hash <> repeat('0', 64)
      AND NOT EXISTS (
        SELECT 1 FROM event_log parent
        WHERE parent.namespace = child.namespace
          AND parent.row_hash = child.prev_hash
      )
  )
)
END::text;
"""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--environment", default=os.getenv("DEPLOYMENT_ENVIRONMENT", "").strip())
    parser.add_argument("--database-id", default=os.getenv("LIANS_DATABASE_ID", "").strip())
    parser.add_argument("--backup-id")
    parser.add_argument("--retention-until", help="RFC 3339 intent copied into the WORM handoff")
    parser.add_argument("--legal-hold", action="store_true")
    parser.add_argument("--compression-level", type=int, default=6, choices=range(0, 10))
    parser.add_argument("--lock-wait-timeout", default="30s")
    parser.add_argument("--free-space-factor", type=float, default=1.20)
    parser.add_argument("--minimum-free-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--include-globals", action="store_true")
    parser.add_argument("--pg-dump", default="pg_dump")
    parser.add_argument("--pg-dumpall", default="pg_dumpall")
    parser.add_argument("--pg-restore", default="pg_restore")
    parser.add_argument("--psql", default="psql")
    return parser.parse_args()


def system_identifier(psql: str, env: dict[str, str]) -> str | None:
    value = psql_scalar(
        psql,
        "SELECT system_identifier::text FROM pg_control_system();",
        env=env,
        allow_failure=True,
    )
    return value.strip() if value else None


def run_dump(command: list[str], env: dict[str, str], label: str) -> None:
    result = subprocess.run(command, env=env, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "no diagnostic").strip()[-4000:]
        raise OperatorError(f"{label} failed: {diagnostic}")


def main() -> int:
    args = arguments()
    output_dir = ensure_directory(args.output_dir, create=True)
    if os.name == "posix" and stat.S_IMODE(output_dir.stat().st_mode) & 0o077:
        raise OperatorError(
            "Backup output directory must not grant group/other permissions (expected mode 0700)"
        )
    environment = args.environment.lower()
    if not ENVIRONMENT_RE.fullmatch(environment):
        raise OperatorError(
            "--environment (or DEPLOYMENT_ENVIRONMENT) must be 2-64 lowercase "
            "letters, digits, underscores, or hyphens"
        )
    if environment in {"prod", "production"} and not args.database_id:
        raise OperatorError(
            "Production backups require --database-id (or LIANS_DATABASE_ID) so a "
            "restore drill can prove it is not targeting the primary"
        )
    if args.free_space_factor < 0 or args.minimum_free_bytes < 0:
        raise OperatorError("Free-space thresholds cannot be negative")
    if args.database_id and (len(args.database_id) > 512 or any(ch in args.database_id for ch in "\r\n\0")):
        raise OperatorError("Database ID is too long or contains control characters")
    if args.retention_until:
        try:
            retention = datetime.fromisoformat(args.retention_until.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OperatorError("--retention-until must be an RFC 3339 timestamp") from exc
        if retention.tzinfo is None or retention.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise OperatorError("--retention-until must be a future, timezone-aware timestamp")

    backup_id = args.backup_id or utc_now().replace(":", "").replace("-", "").lower().replace("z", "z")
    backup_id = f"lians-{backup_id}"
    if not BACKUP_ID_RE.fullmatch(backup_id):
        raise OperatorError("Backup ID must be 8-80 lowercase letters, digits, or hyphens")
    final_dir = output_dir / backup_id
    if final_dir.exists() or final_dir.is_symlink():
        raise OperatorError(f"Backup bundle already exists: {final_dir}")

    pg_dump = require_program(args.pg_dump)
    pg_restore = require_program(args.pg_restore)
    psql = require_program(args.psql)
    pg_dumpall = require_program(args.pg_dumpall) if args.include_globals else None
    env = libpq_environment("lians-logical-backup")
    started_at = utc_now()

    source = psql_json(psql, SOURCE_SQL, env=env)
    if not isinstance(source, dict):
        raise OperatorError("Could not identify source PostgreSQL database")
    if source.get("database_name") != env["PGDATABASE"]:
        raise OperatorError("Connected database identity does not match PGDATABASE")
    expected_free = max(
        args.minimum_free_bytes,
        int(int(source["database_size_bytes"]) * args.free_space_factor),
    )
    available = shutil.disk_usage(output_dir).free
    if available < expected_free:
        raise OperatorError(
            f"Insufficient backup filesystem space: need at least {expected_free} bytes; "
            f"only {available} bytes available"
        )

    tables = psql_json(psql, TABLES_SQL, env=env)
    extensions = psql_json(psql, EXTENSIONS_SQL, env=env)
    migrations = psql_json(psql, MIGRATIONS_SQL, env=env)
    integrity = psql_json(psql, INTEGRITY_SQL, env=env)
    audit = psql_json(psql, AUDIT_SQL, env=env, timeout=300)
    if not isinstance(integrity, dict) or int(integrity.get("invalid_indexes", 0)) != 0:
        raise OperatorError(
            "Source database has invalid or unready indexes; preserve a provider snapshot "
            "and repair or explicitly handle the incident before sealing a routine logical backup"
        )

    temporary = Path(tempfile.mkdtemp(prefix=f".{backup_id}.", dir=output_dir))
    try:
        os.chmod(temporary, 0o700)
        archive = temporary / "database.dump"
        dump_command = [
            pg_dump,
            "--format=custom",
            f"--compress={args.compression_level}",
            "--no-password",
            f"--lock-wait-timeout={args.lock_wait_timeout}",
            "--quote-all-identifiers",
            "--no-subscriptions",
            f"--file={archive}",
            f"--dbname={env['PGDATABASE']}",
        ]
        run_dump(dump_command, env, "pg_dump")
        if not archive.is_file() or archive.stat().st_size == 0:
            raise OperatorError("pg_dump completed without a non-empty archive")
        os.chmod(archive, 0o600)

        # Reading the table of contents catches a malformed custom archive before
        # the bundle can be handed to immutable storage.
        toc = subprocess.run(
            [pg_restore, "--list", str(archive)],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if toc.returncode != 0:
            raise OperatorError(f"pg_restore could not read the new archive: {toc.stderr.strip()[-2000:]}")
        toc_entries = sum(1 for line in toc.stdout.splitlines() if line and not line.startswith(";"))
        if toc_entries == 0:
            raise OperatorError("The new archive has an empty table of contents")

        artifacts: list[dict[str, Any]] = [
            {
                "filename": archive.name,
                "role": "database_archive",
                "media_type": "application/vnd.postgresql.pg-dump",
                "size_bytes": archive.stat().st_size,
                "sha256": sha256_file(archive),
            }
        ]
        if pg_dumpall:
            globals_file = temporary / "globals.sql"
            run_dump(
                [
                    pg_dumpall,
                    "--globals-only",
                    "--no-role-passwords",
                    "--no-password",
                    f"--file={globals_file}",
                ],
                env,
                "pg_dumpall --globals-only",
            )
            os.chmod(globals_file, 0o600)
            artifacts.append(
                {
                    "filename": globals_file.name,
                    "role": "cluster_globals_reference",
                    "media_type": "application/sql",
                    "size_bytes": globals_file.stat().st_size,
                    "sha256": sha256_file(globals_file),
                    "restore_automatically": False,
                }
            )

        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "backup_id": backup_id,
            "started_at": started_at,
            "completed_at": utc_now(),
            "backup_kind": "postgresql_logical",
            "pitr_capable": False,
            "consistency": "pg_dump_consistent_snapshot",
            "source": {
                **source,
                "environment": environment,
                "database_id": args.database_id or None,
                "endpoint_fingerprint_sha256": endpoint_fingerprint(env),
                "system_identifier": system_identifier(psql, env),
            },
            "schema_inventory": {
                "alembic_revisions": migrations,
                "tables": tables,
                "extensions": extensions,
                "pre_dump_integrity_observation": integrity,
                "audit_chain_pre_dump_observation": audit,
            },
            "archive": {
                "format": "custom",
                "compression_level": args.compression_level,
                "toc_entries": toc_entries,
                "ownership_and_acl_included": True,
                "subscriptions_included": False,
            },
            "tools": {
                "creator": "lians-create-logical-backup/v1",
                "pg_dump": command_version(pg_dump),
                "pg_restore": command_version(pg_restore),
                "psql": command_version(psql),
            },
            "worm_intent": {
                "retention_until": args.retention_until,
                "legal_hold": args.legal_hold,
                "provider_immutability_verified": False,
                "status": "pending_external_handoff",
            },
            "artifacts": artifacts,
        }
        manifest_path = temporary / "manifest.json"
        write_new_json(manifest_path, manifest)
        artifact_paths = [temporary / item["filename"] for item in artifacts]
        checksums = temporary / "SHA256SUMS"
        write_new_bytes(checksums, checksum_lines([*artifact_paths, manifest_path]))
        fsync_directory(temporary)
        os.replace(temporary, final_dir)
        fsync_directory(output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    print(
        json.dumps(
            {
                "status": "created",
                "backup_id": backup_id,
                "bundle": str(final_dir),
                "worm_status": "pending_external_handoff",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
