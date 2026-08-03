#!/usr/bin/env python3
"""Restore a Lians logical backup into a proven-isolated empty database.

This tool has no overwrite mode.  It refuses production targets, source/primary
database identities, same-cluster system identifiers, non-empty databases, and
targets whose acknowledgement does not exactly match PGDATABASE.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from backup_lib import (
    RESTORE_REPORT_SCHEMA,
    OperatorError,
    database_archive,
    endpoint_fingerprint,
    ensure_directory,
    libpq_environment,
    psql_json,
    psql_scalar,
    require_program,
    sha256_file,
    utc_now,
    verify_bundle,
    write_new_bytes,
    write_new_json,
)


SAFE_TARGET_RE = re.compile(r"^lians_restore_[a-z0-9_]{4,80}$")

TARGET_SQL = r"""
SELECT jsonb_build_object(
  'database_name', current_database(),
  'server_version', current_setting('server_version'),
  'server_version_num', current_setting('server_version_num')::integer,
  'in_recovery', pg_is_in_recovery(),
  'current_user', current_user,
  'captured_at', clock_timestamp()
)::text;
"""

NONEMPTY_SQL = r"""
SELECT count(*)::text
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
  AND n.nspname <> 'information_schema'
  AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\';
"""

POST_RESTORE_SQL = r"""
SELECT jsonb_build_object(
  'alembic_revisions', CASE WHEN to_regclass('public.alembic_version') IS NULL
    THEN '[]'::jsonb
    ELSE (SELECT COALESCE(jsonb_agg(version_num ORDER BY version_num), '[]'::jsonb)
          FROM alembic_version) END,
  'tables', (
    SELECT COALESCE(jsonb_agg(
      jsonb_build_object(
        'schema', n.nspname,
        'name', c.relname,
        'rls_enabled', c.relrowsecurity,
        'rls_forced', c.relforcerowsecurity
      ) ORDER BY n.nspname, c.relname
    ), '[]'::jsonb)
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p')
      AND n.nspname <> 'information_schema'
      AND n.nspname NOT LIKE 'pg\_%' ESCAPE '\'
  ),
  'invalid_indexes', (
    SELECT count(*) FROM pg_index WHERE NOT indisvalid OR NOT indisready
  ),
  'unvalidated_constraints', (
    SELECT count(*) FROM pg_constraint WHERE NOT convalidated
  )
)::text;
"""

AUDIT_VERIFY_SQL = r"""
SELECT CASE WHEN to_regclass('public.event_log') IS NULL THEN
  jsonb_build_object('present', false)
ELSE jsonb_build_object(
  'present', true,
  'event_count', (SELECT count(*) FROM event_log),
  'namespace_count', (SELECT count(DISTINCT namespace) FROM event_log),
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
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--target-environment", required=True)
    parser.add_argument("--target-database-id", default=os.getenv("LIANS_TARGET_DATABASE_ID", "").strip())
    parser.add_argument("--primary-database-id", default=os.getenv("LIANS_PRIMARY_DATABASE_ID", "").strip())
    parser.add_argument(
        "--acknowledge-target",
        required=True,
        help="Must exactly equal PGDATABASE; this is an isolated destructive-operation acknowledgement",
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--pg-restore", default="pg_restore")
    parser.add_argument("--psql", default="psql")
    return parser.parse_args()


def optional_system_identifier(psql: str, env: dict[str, str]) -> str | None:
    return psql_scalar(
        psql,
        "SELECT system_identifier::text FROM pg_control_system();",
        env=env,
        allow_failure=True,
    )


def compare_schema(manifest: dict[str, Any], restored: dict[str, Any], audit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    inventory = manifest.get("schema_inventory", {})
    expected_revisions = inventory.get("alembic_revisions", [])
    if restored.get("alembic_revisions") != expected_revisions:
        errors.append("Alembic revision set differs from backup manifest")

    expected_tables = {
        (item["schema"], item["name"]): item
        for item in inventory.get("tables", [])
        if isinstance(item, dict) and "schema" in item and "name" in item
    }
    actual_tables = {
        (item["schema"], item["name"]): item
        for item in restored.get("tables", [])
        if isinstance(item, dict) and "schema" in item and "name" in item
    }
    missing = sorted(set(expected_tables) - set(actual_tables))
    if missing:
        errors.append(f"Missing restored tables: {missing[:20]}")
    for key, expected in expected_tables.items():
        actual = actual_tables.get(key)
        if not actual:
            continue
        if bool(actual.get("rls_enabled")) != bool(expected.get("rls_enabled")):
            errors.append(f"RLS enabled state differs for {key[0]}.{key[1]}")
        if bool(actual.get("rls_forced")) != bool(expected.get("rls_forced")):
            errors.append(f"FORCE RLS state differs for {key[0]}.{key[1]}")
    if int(restored.get("invalid_indexes", 0)) != 0:
        errors.append("Restored database contains invalid or unready indexes")
    source_integrity = inventory.get("pre_dump_integrity_observation") or {}
    if int(restored.get("unvalidated_constraints", 0)) != int(
        source_integrity.get("unvalidated_constraints", 0)
    ):
        errors.append("Unvalidated-constraint count differs from the backup manifest")

    source_audit = inventory.get("audit_chain_pre_dump_observation") or {}
    if source_audit.get("present"):
        if not audit.get("present"):
            errors.append("event_log is absent after restore")
        if int(audit.get("event_count", 0)) < int(source_audit.get("event_count_lower_bound", 0)):
            errors.append("Restored event_log is below the pre-dump lower bound")
    if int(audit.get("fork_count", 0)) != 0:
        errors.append("Restored event_log contains forked predecessors")
    if int(audit.get("orphan_count", 0)) != 0:
        errors.append("Restored event_log contains orphaned predecessors")
    return errors


def main() -> int:
    args = arguments()
    target_environment = args.target_environment.strip().lower()
    if target_environment in {"prod", "production"}:
        raise OperatorError("Restore drills categorically refuse production target environments")
    if target_environment not in {"restore-drill", "ephemeral-restore", "disaster-recovery-test"}:
        raise OperatorError("Target environment must explicitly identify an isolated restore drill")
    if not args.target_database_id:
        raise OperatorError("--target-database-id (or LIANS_TARGET_DATABASE_ID) is required")
    if not args.primary_database_id:
        raise OperatorError(
            "--primary-database-id (or LIANS_PRIMARY_DATABASE_ID) is required; "
            "restore safety cannot depend only on a network alias"
        )
    for label, value in {
        "target database ID": args.target_database_id,
        "primary database ID": args.primary_database_id,
    }.items():
        if len(value) > 512 or any(ch in value for ch in "\r\n\0"):
            raise OperatorError(f"{label} is too long or contains control characters")

    bundle = ensure_directory(args.bundle)
    report_dir = ensure_directory(args.report_dir, create=True)
    manifest, bundle_verification = verify_bundle(bundle)
    env = libpq_environment("lians-restore-drill")
    database_name = env["PGDATABASE"]
    if args.acknowledge_target != database_name:
        raise OperatorError("--acknowledge-target must exactly equal PGDATABASE")
    if not SAFE_TARGET_RE.fullmatch(database_name):
        raise OperatorError("Restore target database must match lians_restore_[a-z0-9_]{4,80}")

    source = manifest.get("source", {})
    forbidden_ids = {
        value for value in [source.get("database_id"), args.primary_database_id] if value
    }
    if args.target_database_id in forbidden_ids:
        raise OperatorError("Restore target database identity matches the source/configured primary")
    target_fingerprint = endpoint_fingerprint(env)
    forbidden_fingerprints = {
        value
        for value in [
            source.get("endpoint_fingerprint_sha256"),
            os.getenv("LIANS_PRIMARY_ENDPOINT_FINGERPRINT", "").strip(),
        ]
        if value
    }
    if target_fingerprint in forbidden_fingerprints:
        raise OperatorError("Restore target endpoint matches the source/configured primary")

    pg_restore = require_program(args.pg_restore)
    psql = require_program(args.psql)
    target = psql_json(psql, TARGET_SQL, env=env)
    if not isinstance(target, dict) or target.get("database_name") != database_name:
        raise OperatorError("Connected database identity does not match PGDATABASE")
    if target.get("in_recovery"):
        raise OperatorError("Restore target is read-only/in recovery")
    target_system_id = optional_system_identifier(psql, env)
    source_system_id = source.get("system_identifier")
    if target_system_id and source_system_id and target_system_id == source_system_id:
        raise OperatorError("Restore target is on the same PostgreSQL cluster as the source")

    nonempty = psql_scalar(psql, NONEMPTY_SQL, env=env)
    if nonempty is None or int(nonempty) != 0:
        raise OperatorError(
            f"Restore target is not empty ({nonempty or 'unknown'} user objects); no overwrite mode exists"
        )

    started_at = utc_now()
    archive = database_archive(bundle, manifest)
    command = [
        pg_restore,
        "--no-password",
        "--no-owner",
        "--no-privileges",
        "--no-subscriptions",
        "--no-tablespaces",
        "--exit-on-error",
        "--single-transaction",
        f"--dbname={database_name}",
        str(archive),
    ]
    result = subprocess.run(command, env=env, check=False, capture_output=True, text=True)
    restore_error = None
    if result.returncode != 0:
        restore_error = (result.stderr or result.stdout or "no diagnostic").strip()[-4000:]

    restored: dict[str, Any] = {}
    audit: dict[str, Any] = {}
    verification_errors: list[str] = []
    if restore_error is None:
        try:
            restored = psql_json(psql, POST_RESTORE_SQL, env=env, timeout=300)
            audit = psql_json(psql, AUDIT_VERIFY_SQL, env=env, timeout=600)
            if not isinstance(restored, dict) or not isinstance(audit, dict):
                verification_errors.append("Post-restore SQL verification did not return structured results")
            else:
                verification_errors.extend(compare_schema(manifest, restored, audit))
        except OperatorError as exc:
            verification_errors.append(f"Post-restore SQL verification failed: {exc}")

    status = "passed" if restore_error is None and not verification_errors else "failed"
    report = {
        "schema": RESTORE_REPORT_SCHEMA,
        "status": status,
        "started_at": started_at,
        "completed_at": utc_now(),
        "backup_id": manifest["backup_id"],
        "source_manifest_sha256": bundle_verification["manifest_sha256"],
        "target": {
            **target,
            "environment": target_environment,
            "database_id": args.target_database_id,
            "endpoint_fingerprint_sha256": target_fingerprint,
            "system_identifier": target_system_id,
        },
        "safety_checks": {
            "production_refused": True,
            "primary_database_id_checked": True,
            "endpoint_fingerprint_checked": True,
            "system_identifier_checked": bool(target_system_id and source_system_id),
            "empty_target_checked": True,
            "overwrite_mode_available": False,
        },
        "restore_error": restore_error,
        "verification_errors": verification_errors,
        "schema_verification": restored,
        "audit_topology_verification": audit,
        "limitations": [
            "SQL topology checks do not recompute application-versioned event row hashes.",
            "Application-level audit verification and sampled decision reconstruction remain required.",
            "The isolated target has not been promoted and is not production-ready.",
        ],
    }
    report_path = report_dir / f"restore-{manifest['backup_id']}-{started_at.replace(':', '')}.json"
    write_new_json(report_path, report)
    checksum_path = report_path.with_name(report_path.name + ".sha256")
    write_new_bytes(
        checksum_path,
        f"{sha256_file(report_path)}  {report_path.name}\n".encode("utf-8"),
    )
    print(json.dumps({"status": status, "report": str(report_path)}, sort_keys=True))
    return 0 if status == "passed" else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OperatorError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
