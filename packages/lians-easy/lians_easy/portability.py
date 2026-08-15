"""Passphrase-encrypted, device-portable Lians profile backups."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .store import MemoryStore, _reject_sensitive, _token_estimate

BACKUP_FORMAT = "lians-portable-memory"
BACKUP_VERSION = 1
BACKUP_SUFFIX = ".liansbackup"
MAX_BACKUP_BYTES = 128 * 1024 * 1024
MAX_RECORDS = 100_000
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SYNC_DIVERGENCE_EVENT = "sync_divergence_detected"
SYNC_DIVERGENCE_REASON = "two_devices_corrected_the_same_memory"
MAX_DIVERGENT_BRANCHES = 64


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if not passphrase or len(passphrase) > 1024:
        raise ValueError("Backup passphrase is empty or too long")
    return Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P).derive(
        passphrase.encode("utf-8")
    )


def _memory_record(store: MemoryStore, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "content": store._content(row),
        "content_sha256": row["content_sha256"],
        "token_estimate": row["token_estimate"],
        "kind": row["kind"],
        "scope": row["scope"],
        "project_id": row["project_id"],
        "source": row["source"],
        "source_client": row["source_client"],
        "source_ref": row["source_ref"],
        "topic": row["topic"],
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "supersedes_id": row["supersedes_id"],
        "superseded_by_id": row["superseded_by_id"],
        "paused_at": row["paused_at"],
        "forgotten_at": row["forgotten_at"],
    }


def _activity_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "event": row["event"],
        "memory_id": row["memory_id"],
        "project_id": row["project_id"],
        "client": row["client"],
        "details": json.loads(row["details_json"] or "{}"),
        "created_at": row["created_at"],
    }


def _receipt_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "client": row["client"],
        "token_estimate": row["token_estimate"],
        "memory_count": row["memory_count"],
        "receipt": json.loads(row["receipt_json"]),
        "created_at": row["created_at"],
    }


def _profile_payload(store: MemoryStore) -> dict[str, Any]:
    with store._connect() as database:
        memories = database.execute(
            "SELECT * FROM memories WHERE profile = ? ORDER BY created_at, id",
            (store.profile,),
        ).fetchall()
        activity = database.execute(
            "SELECT * FROM bridge_activity WHERE profile = ? ORDER BY created_at, id",
            (store.profile,),
        ).fetchall()
        receipts = database.execute(
            "SELECT * FROM context_receipts WHERE profile = ? ORDER BY created_at, id",
            (store.profile,),
        ).fetchall()
        if max(len(memories), len(activity), len(receipts)) > MAX_RECORDS:
            raise ValueError("Profile has too many records for one portable backup")
        return {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "backup_id": str(uuid.uuid4()),
            "created_at": datetime.now().astimezone().isoformat(),
            "source_profile": store.profile,
            "memories": [_memory_record(store, row) for row in memories],
            "activity": [_activity_record(row) for row in activity],
            "receipts": [_receipt_record(row) for row in receipts],
        }


def _atomic_publish(path: Path, content: bytes, *, overwrite: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        if overwrite:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise FileExistsError(f"Backup already exists: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def export_backup(
    store: MemoryStore,
    destination: str | Path,
    passphrase: str,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write a full encrypted profile backup without exposing plaintext records."""

    if len(passphrase) < 12:
        raise ValueError("Backup passphrase must contain at least 12 characters")
    path = Path(destination).expanduser().resolve()
    if path.suffix.lower() != BACKUP_SUFFIX:
        raise ValueError(f"Portable backups must use the {BACKUP_SUFFIX} extension")

    payload = _validate_payload(_profile_payload(store))
    encoded_payload = _canonical(payload)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "kdf": {
            "name": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {"name": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode("ascii")},
    }
    ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(
        nonce, encoded_payload, _canonical(header)
    )
    document = {**header, "ciphertext": base64.b64encode(ciphertext).decode("ascii")}
    encoded_document = _canonical(document) + b"\n"
    if len(encoded_document) > MAX_BACKUP_BYTES:
        raise ValueError("Portable backup exceeds the 128 MiB safety limit")
    _atomic_publish(path, encoded_document, overwrite=overwrite)
    return {
        "status": "exported",
        "path": str(path),
        "profile": store.profile,
        "memories": len(payload["memories"]),
        "activity": len(payload["activity"]),
        "receipts": len(payload["receipts"]),
        "bytes": len(encoded_document),
        "sha256": hashlib.sha256(encoded_document).hexdigest(),
        "encrypted": True,
    }


def _decode_backup(source: str | Path, passphrase: str) -> tuple[Path, dict[str, Any]]:
    path = Path(source).expanduser().resolve()
    size = path.stat().st_size
    if size <= 0 or size > MAX_BACKUP_BYTES:
        raise ValueError("Portable backup is empty or exceeds the 128 MiB safety limit")
    try:
        document = json.loads(path.read_bytes())
        if not isinstance(document, dict):
            raise TypeError
        if set(document) != {"format", "version", "kdf", "cipher", "ciphertext"}:
            raise ValueError("Lians backup envelope contains unexpected fields")
        kdf = document["kdf"]
        cipher = document["cipher"]
        if not isinstance(kdf, dict) or not isinstance(cipher, dict):
            raise TypeError
        if document.get("format") != BACKUP_FORMAT or document.get("version") != BACKUP_VERSION:
            raise ValueError("Unsupported Lians backup format or version")
        if kdf != {
            "name": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": kdf.get("salt"),
        }:
            raise ValueError("Unsupported Lians backup key-derivation parameters")
        if cipher != {"name": "AES-256-GCM", "nonce": cipher.get("nonce")}:
            raise ValueError("Unsupported Lians backup cipher parameters")
        salt = base64.b64decode(kdf["salt"], validate=True)
        nonce = base64.b64decode(cipher["nonce"], validate=True)
        ciphertext = base64.b64decode(document["ciphertext"], validate=True)
        if len(salt) != 16 or len(nonce) != 12:
            raise ValueError("Lians backup encryption parameters are invalid")
        header = {key: document[key] for key in ("format", "version", "kdf", "cipher")}
        plaintext = AESGCM(_derive_key(passphrase, salt)).decrypt(
            nonce, ciphertext, _canonical(header)
        )
        payload = json.loads(plaintext)
        if not isinstance(payload, dict):
            raise TypeError
    except InvalidTag as exc:
        raise ValueError("Backup passphrase is incorrect or the backup was changed") from exc
    except (KeyError, TypeError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("Unsupported", "Lians backup")):
            raise
        raise ValueError("Lians backup is invalid or incomplete") from exc
    try:
        validated = _validate_payload(payload)
    except TypeError as exc:
        raise ValueError("Lians backup contains a field with the wrong type") from exc
    return path, validated


def _text(value: Any, name: str, *, optional: bool = False, maximum: int = 4096) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"Backup field {name} is invalid")
    return value


def _timestamp(value: Any, name: str, *, optional: bool = False) -> str | None:
    rendered = _text(value, name, optional=optional, maximum=128)
    if rendered is None:
        return None
    try:
        datetime.fromisoformat(rendered)
    except ValueError as exc:
        raise ValueError(f"Backup field {name} is not an ISO-8601 timestamp") from exc
    return rendered


def _records(payload: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = payload.get(name)
    if not isinstance(value, list) or len(value) > MAX_RECORDS:
        raise ValueError(f"Backup collection {name} is invalid or too large")
    if not all(isinstance(record, dict) for record in value):
        raise ValueError(f"Backup collection {name} contains an invalid record")
    identifiers = [record.get("id") for record in value]
    if not all(isinstance(identifier, str) for identifier in identifiers):
        raise TypeError(f"Backup collection {name} contains a non-text ID")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError(f"Backup collection {name} contains duplicate IDs")
    return value


def _validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("format") != BACKUP_FORMAT or payload.get("version") != BACKUP_VERSION:
        raise ValueError("Encrypted payload has an unsupported format or version")
    _text(payload.get("backup_id"), "backup_id", maximum=128)
    _timestamp(payload.get("created_at"), "created_at")
    _text(payload.get("source_profile"), "source_profile", maximum=128)
    memories = _records(payload, "memories")
    activity = _records(payload, "activity")
    receipts = _records(payload, "receipts")
    memory_ids = set()
    for record in memories:
        memory_id = _text(record.get("id"), "memory.id", maximum=128)
        assert memory_id is not None
        memory_ids.add(memory_id)
        content = record.get("content")
        forgotten_at = _timestamp(record.get("forgotten_at"), "memory.forgotten_at", optional=True)
        if content is None:
            if forgotten_at is None:
                raise ValueError("Only forgotten memory may omit content")
            if record.get("content_sha256") is not None or record.get("token_estimate") != 0:
                raise ValueError("Forgotten memory retains content metadata")
        else:
            if not isinstance(content, str) or not content or len(content.encode()) > 1_000_000:
                raise ValueError("Backup memory content is invalid or too large")
            expected_hash = hashlib.sha256(content.encode()).hexdigest()
            if record.get("content_sha256") != expected_hash:
                raise ValueError("Backup memory content hash does not match")
            if record.get("token_estimate") != _token_estimate(content):
                raise ValueError("Backup memory token estimate does not match")
            _reject_sensitive(content)
        if record.get("scope") not in {"global", "project"}:
            raise ValueError("Backup memory scope is invalid")
        project_id = _text(record.get("project_id"), "memory.project_id", optional=True)
        if record["scope"] == "project" and project_id is None:
            raise ValueError("Project memory is missing its project ID")
        for field in ("kind", "source"):
            _text(record.get(field), f"memory.{field}")
        for field in ("source_client", "source_ref", "topic"):
            _text(record.get(field), f"memory.{field}", optional=True)
        if not isinstance(record.get("metadata"), dict):
            raise TypeError("Backup memory metadata is invalid")
        _timestamp(record.get("created_at"), "memory.created_at")
        _timestamp(record.get("updated_at"), "memory.updated_at")
        _timestamp(record.get("paused_at"), "memory.paused_at", optional=True)
        for field in ("supersedes_id", "superseded_by_id"):
            _text(record.get(field), f"memory.{field}", optional=True, maximum=128)
    memories_by_id = {record["id"]: record for record in memories}
    for record in memories:
        for field in ("supersedes_id", "superseded_by_id"):
            related = record.get(field)
            if related is not None and related not in memory_ids:
                raise ValueError("Backup memory lineage references a missing record")
        if record.get("supersedes_id") is not None:
            previous = memories_by_id[record["supersedes_id"]]
            if previous.get("superseded_by_id") != record["id"]:
                raise ValueError("Backup memory lineage is not reciprocal")
        if record.get("superseded_by_id") is not None:
            replacement = memories_by_id[record["superseded_by_id"]]
            if replacement.get("supersedes_id") != record["id"]:
                raise ValueError("Backup memory lineage is not reciprocal")
    completed_lineages: set[str] = set()
    for memory_id in memory_ids:
        lineage: set[str] = set()
        current_id: str | None = memory_id
        while current_id is not None and current_id not in completed_lineages:
            if current_id in lineage:
                raise ValueError("Backup memory lineage contains a cycle")
            lineage.add(current_id)
            current_id = memories_by_id[current_id].get("superseded_by_id")
        completed_lineages.update(lineage)
    for record in activity:
        _text(record.get("id"), "activity.id", maximum=128)
        _text(record.get("event"), "activity.event")
        activity_memory_id = _text(
            record.get("memory_id"), "activity.memory_id", optional=True, maximum=128
        )
        if activity_memory_id is not None and activity_memory_id not in memory_ids:
            raise ValueError("Backup activity references a missing memory")
        _text(record.get("project_id"), "activity.project_id", optional=True)
        _text(record.get("client"), "activity.client", optional=True)
        if not isinstance(record.get("details"), dict):
            raise TypeError("Backup activity details are invalid")
        _timestamp(record.get("created_at"), "activity.created_at")
    for record in receipts:
        _text(record.get("id"), "receipt.id", maximum=128)
        _text(record.get("project_id"), "receipt.project_id", optional=True)
        _text(record.get("client"), "receipt.client")
        for field in ("token_estimate", "memory_count"):
            if type(record.get(field)) is not int or record[field] < 0:
                raise ValueError(f"Backup receipt {field} is invalid")
        if not isinstance(record.get("receipt"), dict):
            raise TypeError("Backup receipt body is invalid")
        _timestamp(record.get("created_at"), "receipt.created_at")
        _validate_receipt(record, memory_ids=memory_ids)
    return payload


def _validate_receipt(record: dict[str, Any], *, memory_ids: set[str]) -> None:
    receipt = record["receipt"]
    try:
        if (
            receipt.get("id") != record["id"]
            or receipt.get("client") != record["client"]
            or receipt.get("created_at") != record["created_at"]
            or receipt.get("memory_count") != record["memory_count"]
            or receipt.get("token_estimate") != record["token_estimate"]
        ):
            raise ValueError("Backup receipt index does not match its signed body")
        selected = receipt.get("memories")
        if not isinstance(selected, list) or len(selected) != record["memory_count"]:
            raise ValueError("Backup receipt memory index is invalid")
        if any(not isinstance(item, dict) or item.get("id") not in memory_ids for item in selected):
            raise ValueError("Backup receipt references a missing memory")
        signature = receipt["signature"]
        if not isinstance(signature, dict) or signature.get("algorithm") != "Ed25519":
            raise TypeError
        public_key = base64.b64decode(signature["public_key"], validate=True)
        signed_value = base64.b64decode(signature["value"], validate=True)
        protected = {key: value for key, value in receipt.items() if key != "signature"}
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signed_value,
            _canonical(protected),
        )
    except InvalidSignature as exc:
        raise ValueError("Backup contains a receipt with an invalid signature") from exc
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Backup receipt"):
            raise
        raise ValueError("Backup receipt signature is invalid or incomplete") from exc


def verify_backup(source: str | Path, passphrase: str) -> dict[str, Any]:
    path, payload = _decode_backup(source, passphrase)
    return {
        "status": "verified",
        "path": str(path),
        "source_profile": payload["source_profile"],
        "memories": len(payload["memories"]),
        "activity": len(payload["activity"]),
        "receipts": len(payload["receipts"]),
        "encrypted": True,
    }


def _existing_record(
    database: sqlite3.Connection,
    table: str,
    record_id: str,
) -> sqlite3.Row | None:
    return database.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()


def _ordered_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def _sync_divergence_groups(
    activity: list[dict[str, Any]],
) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for record in activity:
        if record["event"] != SYNC_DIVERGENCE_EVENT:
            continue
        details = record.get("details")
        if not isinstance(details, dict) or set(details) != {
            "candidate_memory_ids",
            "original_memory_id",
            "reason",
        }:
            raise ValueError("Synchronized divergence record is invalid")
        original_id = details.get("original_memory_id")
        candidates = details.get("candidate_memory_ids")
        if (
            not isinstance(original_id, str)
            or not original_id
            or not isinstance(candidates, list)
            or not 2 <= len(candidates) <= MAX_DIVERGENT_BRANCHES
            or not all(isinstance(candidate, str) and candidate for candidate in candidates)
            or len(set(candidates)) != len(candidates)
            or original_id in candidates
            or details.get("reason") != SYNC_DIVERGENCE_REASON
            or record.get("memory_id") != original_id
        ):
            raise ValueError("Synchronized divergence record is invalid")
        groups.setdefault(original_id, set()).update(candidates)
    return groups


def _lineage_divergence(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> set[str]:
    """Identify only legitimate concurrent correction branches for one identity."""

    immutable_fields = (
        "id",
        "kind",
        "scope",
        "project_id",
        "source",
        "source_client",
        "source_ref",
        "topic",
        "created_at",
        "supersedes_id",
    )
    if any(existing[field] != incoming[field] for field in immutable_fields):
        return set()
    if existing["forgotten_at"] is None and incoming["forgotten_at"] is None:
        protected_fields = ("content", "content_sha256", "token_estimate", "metadata")
        if any(existing[field] != incoming[field] for field in protected_fields):
            return set()
    candidates = {
        value
        for value in (existing["superseded_by_id"], incoming["superseded_by_id"])
        if value is not None
    }
    return candidates if len(candidates) > 1 else set()


def _divergence_activity_record(
    *,
    profile: str,
    original: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate_ids = sorted(candidate["id"] for candidate in candidates)
    protected = {
        "profile": profile,
        "original_memory_id": original["id"],
        "candidate_memory_ids": candidate_ids,
    }
    event_id = "sync-divergence-" + hashlib.sha256(_canonical(protected)).hexdigest()[:32]
    return {
        "id": event_id,
        "event": SYNC_DIVERGENCE_EVENT,
        "memory_id": original["id"],
        "project_id": original["project_id"],
        "client": "lians-sync",
        "details": {
            "original_memory_id": original["id"],
            "candidate_memory_ids": candidate_ids,
            "reason": SYNC_DIVERGENCE_REASON,
        },
        "created_at": max(
            (candidate["created_at"] for candidate in candidates),
            key=_ordered_timestamp,
        ),
    }


def _normalize_divergence_groups(
    memories: dict[str, dict[str, Any]],
    groups: dict[str, set[str]],
    *,
    profile: str,
) -> list[dict[str, Any]]:
    generated: list[dict[str, Any]] = []
    for original_id, candidate_ids in sorted(groups.items()):
        original = memories.get(original_id)
        candidates = [memories.get(candidate_id) for candidate_id in sorted(candidate_ids)]
        if original is None or any(candidate is None for candidate in candidates):
            raise ValueError("Synchronized divergence references a missing memory")
        candidate_records = [candidate for candidate in candidates if candidate is not None]
        if len(candidate_records) < 2:
            raise ValueError("Synchronized divergence has too few correction branches")
        for candidate in candidate_records:
            if candidate["supersedes_id"] not in {None, original_id}:
                raise ValueError("Synchronized divergence has invalid correction lineage")
            metadata = candidate["metadata"]
            if (
                candidate["forgotten_at"] is None
                and metadata.get("correction_of") != original_id
                and metadata.get("scope_change_of") != original_id
            ):
                raise ValueError("Synchronized divergence lacks correction provenance")

        detected_at = max(
            (candidate["created_at"] for candidate in candidate_records),
            key=_ordered_timestamp,
        )
        original["superseded_by_id"] = None
        if original["forgotten_at"] is None:
            original["paused_at"] = max(
                (value for value in (original["paused_at"], detected_at) if value is not None),
                key=_ordered_timestamp,
            )
            original["updated_at"] = max(
                (original["updated_at"], original["paused_at"]),
                key=_ordered_timestamp,
            )
        for candidate in candidate_records:
            candidate["supersedes_id"] = None
        generated.append(
            _divergence_activity_record(
                profile=profile,
                original=original,
                candidates=candidate_records,
            )
        )
    return generated


def _merge_sync_memory(
    existing: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge the mutable state of one immutable memory identity.

    Content and provenance never use last-writer-wins. Only pause/resume state,
    the forward lineage pointer, and permanent forgetting may advance. A
    forget tombstone always wins so an offline device cannot resurrect erased
    content.
    """

    if existing == incoming:
        return existing
    immutable_fields = (
        "id",
        "kind",
        "scope",
        "project_id",
        "source",
        "source_client",
        "source_ref",
        "topic",
        "created_at",
        "supersedes_id",
    )
    if any(existing[field] != incoming[field] for field in immutable_fields):
        raise ValueError(f"Sync conflict for memory ID {incoming['id']}")

    existing_forgotten = existing["forgotten_at"] is not None
    incoming_forgotten = incoming["forgotten_at"] is not None
    if not existing_forgotten and not incoming_forgotten:
        protected_fields = ("content", "content_sha256", "token_estimate", "metadata")
        if any(existing[field] != incoming[field] for field in protected_fields):
            raise ValueError(f"Sync conflict for memory ID {incoming['id']}")

    forward_values = {
        value
        for value in (existing["superseded_by_id"], incoming["superseded_by_id"])
        if value is not None
    }
    if len(forward_values) > 1:
        raise ValueError(f"Sync conflict for memory ID {incoming['id']}")
    superseded_by_id = next(iter(forward_values), None)

    if existing_forgotten or incoming_forgotten:
        tombstones = [
            record for record in (existing, incoming) if record["forgotten_at"] is not None
        ]
        winner = max(tombstones, key=lambda record: _ordered_timestamp(record["forgotten_at"]))
        merged = dict(winner)
        merged.update(
            {
                "content": None,
                "content_sha256": None,
                "token_estimate": 0,
                "metadata": {},
                "updated_at": max(
                    (existing["updated_at"], incoming["updated_at"]),
                    key=_ordered_timestamp,
                ),
                "superseded_by_id": superseded_by_id,
                "paused_at": None,
            }
        )
        return merged

    existing_updated = _ordered_timestamp(existing["updated_at"])
    incoming_updated = _ordered_timestamp(incoming["updated_at"])
    if existing_updated == incoming_updated and existing["paused_at"] != incoming["paused_at"]:
        raise ValueError(f"Sync conflict for memory ID {incoming['id']}")
    winner = incoming if incoming_updated > existing_updated else existing
    merged = dict(winner)
    merged["superseded_by_id"] = superseded_by_id
    return merged


def _validate_combined_lineage(memories: dict[str, dict[str, Any]]) -> None:
    for record in memories.values():
        for field in ("supersedes_id", "superseded_by_id"):
            related = record[field]
            if related is not None and related not in memories:
                raise ValueError("Synchronized memory lineage references a missing record")
        if record["supersedes_id"] is not None:
            previous = memories[record["supersedes_id"]]
            if previous["superseded_by_id"] != record["id"]:
                raise ValueError("Synchronized memory lineage is not reciprocal")
        if record["superseded_by_id"] is not None:
            replacement = memories[record["superseded_by_id"]]
            if replacement["supersedes_id"] != record["id"]:
                raise ValueError("Synchronized memory lineage is not reciprocal")
    completed: set[str] = set()
    for memory_id in memories:
        lineage: set[str] = set()
        current_id: str | None = memory_id
        while current_id is not None and current_id not in completed:
            if current_id in lineage:
                raise ValueError("Synchronized memory lineage contains a cycle")
            lineage.add(current_id)
            current_id = memories[current_id]["superseded_by_id"]
        completed.update(lineage)


def _propagate_forgotten_lineages(
    memories: dict[str, dict[str, Any]],
    divergence_groups: dict[str, set[str]],
) -> None:
    """Make a tombstone cover every version, including an offline branch."""

    neighbors: dict[str, set[str]] = {memory_id: set() for memory_id in memories}
    for record in memories.values():
        for related in (record["supersedes_id"], record["superseded_by_id"]):
            if related is not None and related in neighbors:
                neighbors[record["id"]].add(related)
                neighbors[related].add(record["id"])
    for original_id, candidate_ids in divergence_groups.items():
        if original_id not in neighbors:
            continue
        for candidate_id in candidate_ids:
            if candidate_id in neighbors:
                neighbors[original_id].add(candidate_id)
                neighbors[candidate_id].add(original_id)

    visited: set[str] = set()
    for memory_id in memories:
        if memory_id in visited:
            continue
        component: set[str] = set()
        pending = [memory_id]
        while pending:
            candidate = pending.pop()
            if candidate in component:
                continue
            component.add(candidate)
            pending.extend(neighbors[candidate] - component)
        visited.update(component)
        tombstones = [
            memories[candidate]["forgotten_at"]
            for candidate in component
            if memories[candidate]["forgotten_at"] is not None
        ]
        if not tombstones:
            continue
        forgotten_at = max(tombstones, key=_ordered_timestamp)
        for candidate in component:
            record = memories[candidate]
            record.update(
                {
                    "content": None,
                    "content_sha256": None,
                    "token_estimate": 0,
                    "metadata": {},
                    "updated_at": max((record["updated_at"], forgotten_at), key=_ordered_timestamp),
                    "paused_at": None,
                    "forgotten_at": forgotten_at,
                }
            )


def merge_profile_payload(
    store: MemoryStore,
    payload: dict[str, Any],
    *,
    sync: bool = False,
) -> dict[str, Any]:
    """Atomically merge a validated plaintext profile into the local store.

    Portable backup imports remain strict. Sync mode additionally accepts
    monotonic state changes, including deletion tombstones, while surfacing
    divergent corrections as reviewable conflicts.
    """

    payload = _validate_payload(payload)
    incoming_memories = {record["id"]: record for record in payload["memories"]}
    incoming_activity = {record["id"]: record for record in payload["activity"]}
    incoming_receipts = {record["id"]: record for record in payload["receipts"]}
    imported = {"memories": 0, "activity": 0, "receipts": 0}
    updated = {"memories": 0}
    skipped = {"memories": 0, "activity": 0, "receipts": 0}

    with store._connect() as database:
        local_rows = database.execute(
            "SELECT * FROM memories WHERE profile = ?",
            (store.profile,),
        ).fetchall()
        stored_local_memories = {row["id"]: _memory_record(store, row) for row in local_rows}
        local_memories = {
            memory_id: {**record, "metadata": dict(record["metadata"])}
            for memory_id, record in stored_local_memories.items()
        }
        local_activity_rows = database.execute(
            "SELECT * FROM bridge_activity WHERE profile = ?",
            (store.profile,),
        ).fetchall()
        local_activity = [_activity_record(row) for row in local_activity_rows]

        divergence_groups: dict[str, set[str]] = {}
        if sync:
            divergence_groups = _sync_divergence_groups(
                [*local_activity, *incoming_activity.values()]
            )
            for memory_id in set(local_memories) & set(incoming_memories):
                candidates = _lineage_divergence(
                    local_memories[memory_id], incoming_memories[memory_id]
                )
                if candidates:
                    divergence_groups.setdefault(memory_id, set()).update(candidates)
            for original_id, candidates in divergence_groups.items():
                for collection in (local_memories, incoming_memories):
                    original = collection.get(original_id)
                    if original is not None and original["superseded_by_id"] is not None:
                        candidates.add(original["superseded_by_id"])

        candidate_owners: dict[str, str] = {}
        for original_id, candidates in divergence_groups.items():
            for candidate_id in candidates:
                owner = candidate_owners.setdefault(candidate_id, original_id)
                if owner != original_id:
                    raise ValueError("Synchronized divergence branches overlap")

        def normalized(record: dict[str, Any]) -> dict[str, Any]:
            result = {**record, "metadata": dict(record["metadata"])}
            if result["id"] in divergence_groups:
                result["superseded_by_id"] = None
            owner = candidate_owners.get(result["id"])
            if owner is not None:
                if result["supersedes_id"] not in {None, owner}:
                    raise ValueError("Synchronized divergence has invalid correction lineage")
                result["supersedes_id"] = None
            return result

        local_memories = {
            memory_id: normalized(record) for memory_id, record in local_memories.items()
        }
        incoming_memories = {
            memory_id: normalized(record) for memory_id, record in incoming_memories.items()
        }
        combined_memories = {
            memory_id: {**record, "metadata": dict(record["metadata"])}
            for memory_id, record in local_memories.items()
        }
        for record in incoming_memories.values():
            existing = _existing_record(database, "memories", record["id"])
            if existing is None:
                combined_memories[record["id"]] = {
                    **record,
                    "metadata": dict(record["metadata"]),
                }
            elif existing["profile"] != store.profile:
                raise ValueError(f"Import conflict for memory ID {record['id']}")
            else:
                current = local_memories[record["id"]]
                if current == record:
                    skipped["memories"] += 1
                elif not sync:
                    raise ValueError(f"Import conflict for memory ID {record['id']}")
                else:
                    merged = _merge_sync_memory(current, record)
                    combined_memories[record["id"]] = merged
        generated_activity: list[dict[str, Any]] = []
        if sync:
            generated_activity = _normalize_divergence_groups(
                combined_memories,
                divergence_groups,
                profile=store.profile,
            )
            _propagate_forgotten_lineages(combined_memories, divergence_groups)
        _validate_combined_lineage(combined_memories)
        missing_memories = [
            record
            for memory_id, record in combined_memories.items()
            if memory_id not in stored_local_memories
        ]
        changed_memories = [
            record
            for memory_id, record in combined_memories.items()
            if memory_id in stored_local_memories and record != stored_local_memories[memory_id]
        ]
        skipped["memories"] = sum(
            1
            for memory_id in incoming_memories
            if memory_id in stored_local_memories
            and combined_memories[memory_id] == stored_local_memories[memory_id]
        )

        merged_activity = dict(incoming_activity)
        for record in generated_activity:
            existing_generated = merged_activity.get(record["id"])
            if existing_generated is not None and existing_generated != record:
                raise ValueError(f"Import conflict for activity ID {record['id']}")
            merged_activity[record["id"]] = record

        missing_activity: list[dict[str, Any]] = []
        for record in merged_activity.values():
            existing = _existing_record(database, "bridge_activity", record["id"])
            if existing is None:
                missing_activity.append(record)
            elif existing["profile"] != store.profile or _activity_record(existing) != record:
                raise ValueError(f"Import conflict for activity ID {record['id']}")
            else:
                skipped["activity"] += 1

        missing_receipts: list[dict[str, Any]] = []
        for record in incoming_receipts.values():
            existing = _existing_record(database, "context_receipts", record["id"])
            if existing is None:
                missing_receipts.append(record)
            elif existing["profile"] != store.profile or _receipt_record(existing) != record:
                raise ValueError(f"Import conflict for receipt ID {record['id']}")
            else:
                skipped["receipts"] += 1

        missing_memory_ids = {record["id"] for record in missing_memories}
        for record in (*missing_memories, *changed_memories):
            content = record["content"]
            if content is None:
                ciphertext = nonce = None
            else:
                ciphertext, nonce = store.cipher.seal(
                    content,
                    associated_data=store._associated_data(record["id"], store.profile),
                )
            if record["id"] in missing_memory_ids:
                database.execute(
                    """INSERT INTO memories
                   (id, profile, content_cipher, content_nonce, content_sha256, token_estimate,
                    kind, scope, project_id, source, source_client, source_ref, topic,
                    metadata_json, created_at, updated_at, paused_at, forgotten_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record["id"],
                        store.profile,
                        ciphertext,
                        nonce,
                        record["content_sha256"],
                        record["token_estimate"],
                        record["kind"],
                        record["scope"],
                        record["project_id"],
                        record["source"],
                        record["source_client"],
                        record["source_ref"],
                        record["topic"],
                        json.dumps(record["metadata"], sort_keys=True),
                        record["created_at"],
                        record["updated_at"],
                        record["paused_at"],
                        record["forgotten_at"],
                    ),
                )
            else:
                database.execute(
                    """UPDATE memories SET content = NULL, content_cipher = ?, content_nonce = ?,
                       content_sha256 = ?, token_estimate = ?, metadata_json = ?, updated_at = ?,
                       paused_at = ?, forgotten_at = ? WHERE profile = ? AND id = ?""",
                    (
                        ciphertext,
                        nonce,
                        record["content_sha256"],
                        record["token_estimate"],
                        json.dumps(record["metadata"], sort_keys=True),
                        record["updated_at"],
                        record["paused_at"],
                        record["forgotten_at"],
                        store.profile,
                        record["id"],
                    ),
                )
        for record in (*missing_memories, *changed_memories):
            database.execute(
                "UPDATE memories SET supersedes_id = ?, superseded_by_id = ? WHERE id = ?",
                (record["supersedes_id"], record["superseded_by_id"], record["id"]),
            )
        imported["memories"] = len(missing_memories)
        updated["memories"] = len(changed_memories)

        for record in missing_activity:
            database.execute(
                """INSERT INTO bridge_activity
                   (id, profile, event, memory_id, project_id, client, details_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    store.profile,
                    record["event"],
                    record["memory_id"],
                    record["project_id"],
                    record["client"],
                    json.dumps(record["details"], sort_keys=True),
                    record["created_at"],
                ),
            )
        imported["activity"] = len(missing_activity)

        for record in missing_receipts:
            database.execute(
                """INSERT INTO context_receipts
                   (id, profile, project_id, client, token_estimate, memory_count,
                    receipt_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    store.profile,
                    record["project_id"],
                    record["client"],
                    record["token_estimate"],
                    record["memory_count"],
                    json.dumps(record["receipt"], sort_keys=True),
                    record["created_at"],
                ),
            )
        imported["receipts"] = len(missing_receipts)

    return {
        "status": "synchronized" if sync else "imported",
        "source_profile": payload["source_profile"],
        "target_profile": store.profile,
        "imported": imported,
        "updated": updated,
        "already_present": skipped,
        "divergences_detected": len(generated_activity),
        "re_encrypted_for_this_device": True,
    }


def import_backup(store: MemoryStore, source: str | Path, passphrase: str) -> dict[str, Any]:
    """Merge a verified backup atomically and re-encrypt memory for this device."""

    path, payload = _decode_backup(source, passphrase)
    return {
        **merge_profile_payload(store, payload),
        "path": str(path),
    }
