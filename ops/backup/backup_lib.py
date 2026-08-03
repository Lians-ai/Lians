#!/usr/bin/env python3
"""Shared, dependency-free primitives for Lians backup operator tools.

The scripts intentionally use libpq environment variables instead of accepting a
database URL on the command line.  This keeps credentials out of process listings
and generated manifests.  Password delivery should use ``PGPASSFILE`` or a
platform-native secret mount; ``PGPASSWORD`` is supported by libpq but is less
desirable because process environments may be inspectable on some systems.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_SCHEMA = "urn:lians:ops:backup-manifest:v1"
HANDOFF_SCHEMA = "urn:lians:ops:worm-handoff:v1"
RESTORE_REPORT_SCHEMA = "urn:lians:ops:restore-drill-report:v1"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
BACKUP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,79}$")
ARTIFACT_ROLES = {"database_archive", "cluster_globals_reference"}


class OperatorError(RuntimeError):
    """A fail-closed operator error that is safe to show to a human."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(name: str) -> str:
    if not SAFE_FILE_RE.fullmatch(name) or Path(name).name != name:
        raise OperatorError(f"Unsafe artifact filename in manifest: {name!r}")
    return name


def ensure_directory(path: Path, *, create: bool = False) -> Path:
    if path.exists() and path.is_symlink():
        raise OperatorError(f"Refusing symlink directory: {path}")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise OperatorError(f"Not a directory: {resolved}")
    return resolved


def ensure_new_file(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise OperatorError(f"Refusing to overwrite existing path: {path}")


def fsync_directory(path: Path) -> None:
    """Best-effort directory sync (not available on every Windows filesystem)."""

    flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    ensure_new_file(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def write_new_json(path: Path, value: Any) -> None:
    write_new_bytes(path, canonical_json(value))


def require_program(program: str) -> str:
    resolved = shutil.which(program)
    if not resolved:
        raise OperatorError(f"Required executable was not found on PATH: {program}")
    return resolved


def command_version(program: str) -> str:
    result = subprocess.run(
        [program, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise OperatorError(f"Could not read version from {Path(program).name}")
    return (result.stdout or result.stderr).strip()[:500]


def libpq_environment(app_name: str) -> dict[str, str]:
    env = os.environ.copy()
    database = env.get("PGDATABASE", "").strip()
    host = env.get("PGHOST", "").strip()
    if not database:
        raise OperatorError("PGDATABASE must name the database explicitly")
    if not host:
        raise OperatorError("PGHOST must name the database endpoint explicitly")
    if "," in host:
        raise OperatorError(
            "Multi-host PGHOST is not accepted by safety tooling; use the resolved "
            "primary or isolated restore endpoint"
        )
    env["PGAPPNAME"] = app_name
    env.setdefault("PGCONNECT_TIMEOUT", "10")
    env.setdefault("PG_COLOR", "never")
    return env


def endpoint_fingerprint(env: dict[str, str]) -> str:
    host = env["PGHOST"].strip().rstrip(".").lower()
    port = env.get("PGPORT", "5432").strip()
    database = env["PGDATABASE"].strip()
    canonical = f"postgresql|{host}|{port}|{database}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_checked(
    command: list[str],
    *,
    env: dict[str, str],
    timeout: int | None = None,
    failure_label: str,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        env=env,
        check=False,
        capture_output=capture_output,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        # Database diagnostics can contain object names and topology.  Report a
        # bounded diagnostic, never the command/environment or connection URI.
        diagnostic = (result.stderr or result.stdout or "no diagnostic").strip()
        diagnostic = diagnostic[-2000:]
        raise OperatorError(f"{failure_label} failed: {diagnostic}")
    return result


def psql_scalar(
    psql: str,
    sql: str,
    *,
    env: dict[str, str],
    timeout: int = 30,
    allow_failure: bool = False,
) -> str | None:
    command = [
        psql,
        "--no-psqlrc",
        "--no-password",
        "--quiet",
        "--tuples-only",
        "--no-align",
        "--set=ON_ERROR_STOP=1",
        f"--dbname={env['PGDATABASE']}",
        f"--command={sql}",
    ]
    try:
        result = run_checked(
            command,
            env=env,
            timeout=timeout,
            failure_label="PostgreSQL metadata query",
        )
    except OperatorError:
        if allow_failure:
            return None
        raise
    output = result.stdout.strip()
    return output or None


def psql_json(
    psql: str,
    sql: str,
    *,
    env: dict[str, str],
    timeout: int = 30,
    allow_failure: bool = False,
) -> Any:
    output = psql_scalar(
        psql,
        sql,
        env=env,
        timeout=timeout,
        allow_failure=allow_failure,
    )
    if output is None:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise OperatorError("PostgreSQL metadata query returned invalid JSON") from exc


def read_json_file(path: Path, *, maximum: int = MAX_MANIFEST_BYTES) -> Any:
    if path.is_symlink() or not path.is_file():
        raise OperatorError(f"Expected a regular, non-symlink file: {path}")
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise OperatorError(f"JSON file has unsafe size ({size} bytes): {path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorError(f"Invalid JSON file: {path.name}") from exc


def parse_checksum_file(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise OperatorError("SHA256SUMS must be a regular, non-symlink file")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise OperatorError("SHA256SUMS is unexpectedly large")
    checksums: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise OperatorError("SHA256SUMS is not valid UTF-8") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_RE.fullmatch(parts[0]):
            raise OperatorError(f"Malformed SHA256SUMS line {line_number}")
        filename = safe_filename(parts[1])
        if filename in checksums:
            raise OperatorError(f"Duplicate SHA256SUMS entry: {filename}")
        checksums[filename] = parts[0]
    return checksums


def verify_bundle(bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = ensure_directory(bundle)
    manifest_path = bundle / "manifest.json"
    checksum_path = bundle / "SHA256SUMS"
    manifest = read_json_file(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise OperatorError("Unsupported backup manifest schema")
    backup_id = manifest.get("backup_id")
    if not isinstance(backup_id, str) or not BACKUP_ID_RE.fullmatch(backup_id):
        raise OperatorError("Backup manifest has an invalid backup_id")
    source = manifest.get("source")
    inventory = manifest.get("schema_inventory")
    archive_metadata = manifest.get("archive")
    if not isinstance(source, dict) or not isinstance(inventory, dict) or not isinstance(
        archive_metadata, dict
    ):
        raise OperatorError("Backup manifest is missing source, schema inventory, or archive metadata")
    fingerprint = source.get("endpoint_fingerprint_sha256")
    if not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint):
        raise OperatorError("Backup manifest has an invalid source endpoint fingerprint")
    toc_entries = archive_metadata.get("toc_entries")
    if type(toc_entries) is not int or toc_entries < 1:
        raise OperatorError("Backup manifest has an invalid archive table-of-contents count")
    if not isinstance(inventory.get("tables"), list) or not isinstance(
        inventory.get("alembic_revisions"), list
    ):
        raise OperatorError("Backup manifest has malformed schema inventory")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise OperatorError("Backup manifest has no artifacts")

    checksums = parse_checksum_file(checksum_path)
    required_checksum_names = {"manifest.json"}
    verified_artifacts: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    archive_count = 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise OperatorError("Malformed artifact entry in manifest")
        filename = safe_filename(str(artifact.get("filename", "")))
        if filename in seen_artifacts:
            raise OperatorError(f"Duplicate artifact filename in manifest: {filename}")
        seen_artifacts.add(filename)
        role = str(artifact.get("role", ""))
        if role not in ARTIFACT_ROLES:
            raise OperatorError(f"Unsupported artifact role in manifest: {role!r}")
        expected_hash = str(artifact.get("sha256", ""))
        expected_size = artifact.get("size_bytes")
        if role == "database_archive":
            archive_count += 1
        if not SHA256_RE.fullmatch(expected_hash):
            raise OperatorError(f"Invalid artifact digest for {filename}")
        if type(expected_size) is not int or expected_size < 1:
            raise OperatorError(f"Invalid artifact size for {filename}")
        artifact_path = bundle / filename
        if artifact_path.is_symlink() or not artifact_path.is_file():
            raise OperatorError(f"Artifact is missing or not a regular file: {filename}")
        actual_size = artifact_path.stat().st_size
        if actual_size != expected_size:
            raise OperatorError(
                f"Artifact size mismatch for {filename}: expected {expected_size}, got {actual_size}"
            )
        actual_hash = sha256_file(artifact_path)
        if actual_hash != expected_hash:
            raise OperatorError(f"Artifact checksum mismatch: {filename}")
        if checksums.get(filename) != actual_hash:
            raise OperatorError(f"SHA256SUMS mismatch: {filename}")
        required_checksum_names.add(filename)
        verified_artifacts.append(
            {"filename": filename, "role": role, "sha256": actual_hash, "size_bytes": actual_size}
        )

    if archive_count != 1:
        raise OperatorError("Backup bundle must contain exactly one database archive")
    manifest_hash = sha256_file(manifest_path)
    if checksums.get("manifest.json") != manifest_hash:
        raise OperatorError("SHA256SUMS mismatch: manifest.json")
    if set(checksums) != required_checksum_names:
        raise OperatorError("SHA256SUMS contains missing or unmanifested files")
    directory_entries = {entry.name for entry in bundle.iterdir()}
    if directory_entries != required_checksum_names | {"SHA256SUMS"}:
        unexpected = sorted(directory_entries - required_checksum_names - {"SHA256SUMS"})
        missing = sorted((required_checksum_names | {"SHA256SUMS"}) - directory_entries)
        raise OperatorError(
            f"Backup bundle contents differ from its sealed inventory; "
            f"unexpected={unexpected}, missing={missing}"
        )

    verification = {
        "manifest_sha256": manifest_hash,
        "checksums_sha256": sha256_file(checksum_path),
        "artifacts": verified_artifacts,
    }
    return manifest, verification


def database_archive(bundle: Path, manifest: dict[str, Any]) -> Path:
    for artifact in manifest["artifacts"]:
        if artifact.get("role") == "database_archive":
            return bundle / safe_filename(str(artifact["filename"]))
    raise OperatorError("Database archive is absent from manifest")


def checksum_lines(paths: Iterable[Path]) -> bytes:
    lines = [f"{sha256_file(path)}  {safe_filename(path.name)}" for path in paths]
    return ("\n".join(lines) + "\n").encode("utf-8")
