#!/usr/bin/env python3
"""Fail-closed WORM object-store adapters used by the backup handoff CLI.

Provider SDK imports are intentionally lazy.  Authentication comes only from
each official SDK's default credential chain; this module has no credential
parameters and never renders provider exception text.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import io
import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backup_lib import OperatorError, canonical_json, safe_filename, utc_now

AWS_ACCOUNT_RE = re.compile(r"^[0-9]{12}$")
GCP_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
GCP_PROJECT_NUMBER_RE = re.compile(r"^[0-9]{6,30}$")
AZURE_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
AZURE_ACCOUNT_RE = re.compile(r"^[a-z0-9]{3,24}$")
AZURE_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
SAFE_IDENTITY_RE = re.compile(r"^[^\x00-\x1f\x7f]{3,1024}$")
S3_SINGLE_PUT_LIMIT = 5 * 1024**3 - 1
S3_MIN_PART_SIZE = 64 * 1024**2
S3_MAX_PARTS = 10_000


@dataclass(frozen=True)
class UploadObject:
    source_filename: str
    source_path: Path
    destination: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ProviderRun:
    provider: dict[str, Any]
    verifier_principal: str
    software: list[str]
    objects: list[dict[str, Any]]


class HashingSink(io.RawIOBase):
    """A write-only sink used for bounded-memory provider downloads."""

    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.size = 0

    def writable(self) -> bool:
        return True

    def write(self, payload: bytes | bytearray) -> int:
        data = bytes(payload)
        self.digest.update(data)
        self.size += len(data)
        return len(data)

    def tell(self) -> int:
        return self.size

    @property
    def hexdigest(self) -> str:
        return self.digest.hexdigest()


def _required_env(name: str, pattern: re.Pattern[str] | None = None) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise OperatorError(f"{name} must identify the expected provider boundary")
    if not SAFE_IDENTITY_RE.fullmatch(value):
        raise OperatorError(f"{name} contains unsafe control characters or is too long")
    if pattern is not None and not pattern.fullmatch(value):
        raise OperatorError(f"{name} has an invalid identity format")
    return value


def verifier_workload_identity() -> str:
    value = _required_env("LIANS_WORM_VERIFIER_IDENTITY")
    if len(value) > 512:
        raise OperatorError("LIANS_WORM_VERIFIER_IDENTITY is longer than 512 characters")
    return value


def dependency_version(distribution: str) -> str:
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise OperatorError(f"Required provider package is not installed: {distribution}") from exc
    safe_version = re.sub(r"[^A-Za-z0-9_.+-]", "_", version)[:80]
    return f"{distribution}/{safe_version}"


def parse_rfc3339(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise OperatorError("Retention timestamp is not valid RFC 3339") from exc
    if parsed.tzinfo is None:
        raise OperatorError("Retention timestamp must include a timezone")
    return parsed.astimezone(UTC)


def format_rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        raise OperatorError("Provider returned a retention timestamp without a timezone")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def policy_revision(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def digest_file(path: Path, algorithm: str) -> bytes:
    # MD5 is used only where S3/Azure require a transport/provider checksum;
    # SHA-256 remains the Lians content-integrity boundary.
    digest = hashlib.new(algorithm, usedforsecurity=algorithm.lower() != "md5")
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.digest()


def base64_digest(path: Path, algorithm: str) -> str:
    return base64.b64encode(digest_file(path, algorithm)).decode("ascii")


def _checksum(
    algorithm: str,
    value: str,
    checksum_type: str = "provider_unspecified",
) -> dict[str, str]:
    return {
        "algorithm": algorithm,
        "value": value,
        "encoding": "base64",
        "checksum_type": checksum_type,
    }


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def _safe_cloud_error_code(error: BaseException) -> str:
    candidates: list[Any] = [getattr(error, "error_code", None), getattr(error, "code", None)]
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        candidates.extend(
            [
                response.get("Error", {}).get("Code")
                if isinstance(response.get("Error"), dict)
                else None,
                response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if isinstance(response.get("ResponseMetadata"), dict)
                else None,
            ]
        )
    for candidate in candidates:
        rendered = str(candidate or "")
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", rendered):
            return rendered
    return "provider_error"


def _validated_uri(value: str, scheme: str) -> tuple[str, list[str]]:
    parsed = urlparse(value)
    if (
        parsed.scheme != scheme
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "%" in value
        or "\\" in value
    ):
        raise OperatorError(f"Unsafe or malformed {scheme} destination URI")
    segments = [segment for segment in parsed.path.split("/") if segment]
    if any(segment in {".", ".."} for segment in segments):
        raise OperatorError("Provider destination contains an unsafe dot segment")
    return parsed.netloc, segments


def validate_provider_destination_budget(
    destination_prefix: str,
    source_filename: str,
) -> None:
    """Fail before upload when the synthetic object cannot fit provider key limits."""

    filename = safe_filename(source_filename)
    scheme = urlparse(destination_prefix).scheme
    if scheme in {"s3", "gs"}:
        _, segments = _validated_uri(destination_prefix, scheme)
        object_name = "/".join([*segments, filename])
        if len(object_name.encode("utf-8")) > 1024:
            raise OperatorError(f"{scheme} destination object name exceeds 1024 bytes")
        return
    if scheme == "azure":
        _, segments = _validated_uri(destination_prefix, scheme)
        if len(segments) < 2:
            raise OperatorError("Azure destination must be azure://ACCOUNT/CONTAINER/PREFIX")
        blob_segments = [*segments[1:], filename]
        blob_name = "/".join(blob_segments)
        if len(blob_name) > 1024:
            raise OperatorError("Azure destination blob name exceeds 1024 characters")
        if len(blob_segments) > 63:
            raise OperatorError("Azure destination blob name exceeds the safe segment budget")
        return
    raise OperatorError("Unsupported WORM provider scheme")


class S3Adapter:
    def __init__(
        self,
        destination_prefix: str,
        retention_until: datetime,
        legal_hold: bool,
    ) -> None:
        try:
            import boto3
            from botocore.exceptions import ClientError
        except ImportError as exc:
            raise OperatorError("AWS uploads require boto3 and botocore") from exc

        self._client_error = ClientError
        self.retention_until = retention_until
        self.legal_hold = legal_hold
        self.workload_identity = verifier_workload_identity()
        self.expected_account = _required_env("LIANS_WORM_AWS_ACCOUNT_ID", AWS_ACCOUNT_RE)
        bucket, prefix = _validated_uri(destination_prefix, "s3")
        if not prefix:
            raise OperatorError("S3 destination must include a dedicated backup prefix")
        self.bucket = bucket
        self.prefix = "/".join(prefix)

        session = boto3.session.Session()
        self.s3 = session.client("s3")
        self.sts = session.client("sts")
        identity = self.sts.get_caller_identity()
        actual_account = str(identity.get("Account", ""))
        principal = str(identity.get("Arn", ""))
        if actual_account != self.expected_account:
            raise OperatorError("AWS caller account differs from LIANS_WORM_AWS_ACCOUNT_ID")
        if not SAFE_IDENTITY_RE.fullmatch(principal):
            raise OperatorError("AWS STS did not return a usable caller ARN")
        self.principal = principal

        versioning = self.s3.get_bucket_versioning(
            Bucket=self.bucket,
            ExpectedBucketOwner=self.expected_account,
        )
        if versioning.get("Status") != "Enabled":
            raise OperatorError("S3 Object Lock destination must have versioning enabled")
        lock_config = self.s3.get_object_lock_configuration(
            Bucket=self.bucket,
            ExpectedBucketOwner=self.expected_account,
        ).get("ObjectLockConfiguration", {})
        if lock_config.get("ObjectLockEnabled") != "Enabled":
            raise OperatorError("S3 destination does not have Object Lock enabled")
        location = (
            self.s3.get_bucket_location(
                Bucket=self.bucket,
                ExpectedBucketOwner=self.expected_account,
            ).get("LocationConstraint")
            or "us-east-1"
        )
        policy_material = {
            "bucket": self.bucket,
            "owner": self.expected_account,
            "region": str(location),
            "versioning": versioning.get("Status"),
            "object_lock": lock_config,
        }
        self.provider = {
            "kind": "aws_s3",
            "owning_identity": f"aws:{self.expected_account}",
            "resource_id": f"arn:aws:s3:::{self.bucket}",
            "location": str(location),
            "immutable_capability": "s3_object_lock",
            "policy_revision_sha256": policy_revision(policy_material),
        }

    def _key(self, item: UploadObject) -> str:
        bucket, segments = _validated_uri(item.destination, "s3")
        key = "/".join(segments)
        expected = f"{self.prefix}/{item.source_filename}"
        if bucket != self.bucket or key != expected:
            raise OperatorError(f"S3 destination mismatch for {item.source_filename}")
        return key

    def _head(self, key: str, version_id: str | None = None) -> dict[str, Any] | None:
        parameters: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "ChecksumMode": "ENABLED",
            "ExpectedBucketOwner": self.expected_account,
        }
        if version_id:
            parameters["VersionId"] = version_id
        try:
            return self.s3.head_object(**parameters)
        except self._client_error as exc:
            code = _safe_cloud_error_code(exc)
            if not version_id and code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise

    def _single_put(self, item: UploadObject, key: str) -> str | None:
        with item.source_path.open("rb") as source:
            try:
                response = self.s3.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=source,
                    ContentLength=item.size_bytes,
                    ContentMD5=base64_digest(item.source_path, "md5"),
                    ChecksumSHA256=base64.b64encode(bytes.fromhex(item.sha256)).decode("ascii"),
                    ContentType="application/octet-stream",
                    IfNoneMatch="*",
                    ObjectLockMode="COMPLIANCE",
                    ObjectLockRetainUntilDate=self.retention_until,
                    ObjectLockLegalHoldStatus="ON" if self.legal_hold else "OFF",
                    ExpectedBucketOwner=self.expected_account,
                )
            except self._client_error as exc:
                if _safe_cloud_error_code(exc) in {"412", "PreconditionFailed"}:
                    return None
                raise
        version_id = str(response.get("VersionId", ""))
        if not version_id or version_id == "null":
            raise OperatorError("S3 upload did not return an immutable object version ID")
        return version_id

    def _multipart_put(self, item: UploadObject, key: str) -> str | None:
        part_size = max(S3_MIN_PART_SIZE, math.ceil(item.size_bytes / S3_MAX_PARTS))
        part_size = math.ceil(part_size / (1024 * 1024)) * 1024 * 1024
        response = self.s3.create_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            ContentType="application/octet-stream",
            Metadata={"lians-sha256": item.sha256},
            ChecksumAlgorithm="SHA256",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=self.retention_until,
            ObjectLockLegalHoldStatus="ON" if self.legal_hold else "OFF",
            ExpectedBucketOwner=self.expected_account,
        )
        upload_id = str(response.get("UploadId", ""))
        if not upload_id:
            raise OperatorError("S3 did not return a multipart upload ID")
        parts: list[dict[str, Any]] = []
        with item.source_path.open("rb") as source:
            part_number = 1
            while payload := source.read(part_size):
                if part_number > S3_MAX_PARTS:
                    raise OperatorError("S3 multipart upload would exceed 10,000 parts")
                part_sha = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
                uploaded = self.s3.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    PartNumber=part_number,
                    UploadId=upload_id,
                    Body=payload,
                    ContentLength=len(payload),
                    ContentMD5=base64.b64encode(
                        hashlib.md5(payload, usedforsecurity=False).digest()
                    ).decode("ascii"),
                    ChecksumSHA256=part_sha,
                    ExpectedBucketOwner=self.expected_account,
                )
                etag = str(uploaded.get("ETag", ""))
                returned_sha = str(uploaded.get("ChecksumSHA256", ""))
                if not etag or returned_sha != part_sha:
                    raise OperatorError("S3 multipart part acknowledgement was incomplete")
                parts.append(
                    {
                        "ETag": etag,
                        "ChecksumSHA256": returned_sha,
                        "PartNumber": part_number,
                    }
                )
                part_number += 1
        try:
            completed = self.s3.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
                ExpectedBucketOwner=self.expected_account,
            )
        except self._client_error as exc:
            if _safe_cloud_error_code(exc) in {"412", "PreconditionFailed"}:
                # Deliberately do not abort: the uploader has no deletion capability.
                # A bucket lifecycle rule owned by storage administration must expire
                # incomplete multipart uploads.
                return None
            raise
        version_id = str(completed.get("VersionId", ""))
        if not version_id or version_id == "null":
            raise OperatorError("S3 multipart completion returned no version ID")
        return version_id

    def _provider_checksums(self, head: dict[str, Any]) -> list[dict[str, str]]:
        checksum_type_raw = str(head.get("ChecksumType", "")).upper()
        checksum_type = {
            "FULL_OBJECT": "full_object",
            "COMPOSITE": "composite",
        }.get(checksum_type_raw, "provider_unspecified")
        result: list[dict[str, str]] = []
        for field, algorithm in (
            ("ChecksumCRC32", "crc32"),
            ("ChecksumCRC32C", "crc32c"),
            ("ChecksumCRC64NVME", "crc64nvme"),
            ("ChecksumSHA1", "sha1"),
            ("ChecksumSHA256", "sha256"),
        ):
            value = str(head.get(field, ""))
            if value:
                effective_type = (
                    "composite"
                    if checksum_type == "provider_unspecified"
                    and re.search(r"-[1-9][0-9]{0,4}$", value)
                    else checksum_type
                )
                result.append(_checksum(algorithm, value, effective_type))
        if not result:
            raise OperatorError("S3 returned no provider checksum for an uploaded object")
        return result

    def _stream_sha256(self, key: str, version_id: str) -> tuple[str, int]:
        response = self.s3.get_object(
            Bucket=self.bucket,
            Key=key,
            VersionId=version_id,
            ChecksumMode="ENABLED",
            ExpectedBucketOwner=self.expected_account,
        )
        digest = hashlib.sha256()
        size = 0
        body = response["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
                    size += len(chunk)
        finally:
            body.close()
        return digest.hexdigest(), size

    def _verify_content(
        self,
        item: UploadObject,
        key: str,
        version_id: str,
        head: dict[str, Any],
    ) -> tuple[list[dict[str, str]], dict[str, str]]:
        if int(head.get("ContentLength", -1)) != item.size_bytes:
            raise OperatorError(f"S3 object size mismatch for {item.source_filename}")
        checksums = self._provider_checksums(head)
        expected_b64 = base64.b64encode(bytes.fromhex(item.sha256)).decode("ascii")
        sha_entries = [entry for entry in checksums if entry["algorithm"] == "sha256"]
        if any(
            entry["value"] == expected_b64 and entry["checksum_type"] != "composite"
            for entry in sha_entries
        ):
            return checksums, {
                "method": "provider_sha256",
                "sha256": item.sha256,
                "verified_at": utc_now(),
            }

        streamed, size = self._stream_sha256(key, version_id)
        if size != item.size_bytes or streamed != item.sha256:
            raise OperatorError(f"S3 streamed SHA-256 mismatch for {item.source_filename}")
        if sha_entries and any(
            entry["checksum_type"] != "composite" and entry["value"] != expected_b64
            for entry in sha_entries
        ):
            raise OperatorError(f"S3 provider SHA-256 conflicts for {item.source_filename}")
        return checksums, {
            "method": "streamed_sha256",
            "sha256": streamed,
            "verified_at": utc_now(),
        }

    def _ensure_lock(self, key: str, version_id: str, head: dict[str, Any]) -> dict[str, Any]:
        mode = str(head.get("ObjectLockMode", ""))
        until = head.get("ObjectLockRetainUntilDate")
        until_utc = until.astimezone(UTC) if isinstance(until, datetime) else None
        if mode != "COMPLIANCE" or until_utc is None or until_utc < self.retention_until:
            self.s3.put_object_retention(
                Bucket=self.bucket,
                Key=key,
                VersionId=version_id,
                Retention={"Mode": "COMPLIANCE", "RetainUntilDate": self.retention_until},
                ExpectedBucketOwner=self.expected_account,
            )
        if self.legal_hold and head.get("ObjectLockLegalHoldStatus") != "ON":
            self.s3.put_object_legal_hold(
                Bucket=self.bucket,
                Key=key,
                VersionId=version_id,
                LegalHold={"Status": "ON"},
                ExpectedBucketOwner=self.expected_account,
            )
        verified = self._head(key, version_id)
        if verified is None:
            raise OperatorError("S3 object version disappeared during lock verification")
        self._verify_lock_state(verified)
        return verified

    def _verify_lock_state(self, head: dict[str, Any]) -> tuple[datetime, bool]:
        verified_until = head.get("ObjectLockRetainUntilDate")
        if (
            head.get("ObjectLockMode") != "COMPLIANCE"
            or not isinstance(verified_until, datetime)
            or verified_until.astimezone(UTC) < self.retention_until
        ):
            raise OperatorError("S3 object version lacks the required COMPLIANCE retention")
        effective_hold = head.get("ObjectLockLegalHoldStatus") == "ON"
        if self.legal_hold and not effective_hold:
            raise OperatorError("S3 object version lacks the required legal hold")
        return verified_until, effective_hold

    def _attestation_for(
        self,
        item: UploadObject,
        version_id: str,
        head: dict[str, Any],
        checksums: list[dict[str, str]],
        content_verification: dict[str, str],
        disposition: str,
    ) -> dict[str, Any]:
        retain_until, effective_hold = self._verify_lock_state(head)
        return {
            "source_filename": item.source_filename,
            "destination": item.destination,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "provider_object_id": version_id,
            "provider_etag": str(head.get("ETag", "")).strip('"'),
            "provider_checksums": checksums,
            "content_verification": content_verification,
            "retention": {
                "mode": "COMPLIANCE",
                "retain_until": format_rfc3339(retain_until),
                "legal_hold": effective_hold,
                "hold_type": "legal_hold" if effective_hold else "none",
                "policy_scope": "object_version",
                "locked": True,
            },
            "disposition": disposition,
        }

    def verify_existing(self, item: UploadObject, provider_object_id: str) -> dict[str, Any]:
        """Read and verify one exact S3 version without any provider mutation."""

        key = self._key(item)
        head = self._head(key, provider_object_id)
        if head is None:
            raise OperatorError("S3 immutable anchor version is absent")
        actual_version_id = str(head.get("VersionId", ""))
        if actual_version_id != provider_object_id:
            raise OperatorError("S3 returned a different immutable anchor version")
        checksums, content_verification = self._verify_content(
            item,
            key,
            provider_object_id,
            head,
        )
        self._verify_lock_state(head)
        return self._attestation_for(
            item,
            provider_object_id,
            head,
            checksums,
            content_verification,
            "reused_verified",
        )

    def upload(self, items: list[UploadObject]) -> ProviderRun:
        attestations: list[dict[str, Any]] = []
        for item in items:
            key = self._key(item)
            head = self._head(key)
            disposition = "reused_verified"
            if head is None:
                version_id = (
                    self._single_put(item, key)
                    if item.size_bytes <= S3_SINGLE_PUT_LIMIT
                    else self._multipart_put(item, key)
                )
                if version_id is not None:
                    disposition = "created"
                    head = self._head(key, version_id)
                else:
                    head = self._head(key)
            if head is None:
                raise OperatorError(f"S3 object is absent after upload: {item.source_filename}")
            version_id = str(head.get("VersionId", ""))
            if not version_id or version_id == "null":
                raise OperatorError("S3 verification requires a non-null object version ID")

            checksums, content_verification = self._verify_content(item, key, version_id, head)
            head = self._ensure_lock(key, version_id, head)
            final_checksums = self._provider_checksums(head)
            if final_checksums != checksums:
                raise OperatorError(
                    f"S3 provider checksums changed during lock verification: "
                    f"{item.source_filename}"
                )
            attestations.append(
                self._attestation_for(
                    item,
                    version_id,
                    head,
                    checksums,
                    content_verification,
                    disposition,
                )
            )
        return ProviderRun(
            provider=self.provider,
            verifier_principal=self.principal,
            software=[dependency_version("boto3"), dependency_version("botocore")],
            objects=attestations,
        )


class GCSAdapter:
    def __init__(
        self,
        destination_prefix: str,
        retention_until: datetime,
        legal_hold: bool,
        *,
        read_only: bool = False,
    ) -> None:
        try:
            import google.auth
            from google.api_core.exceptions import PreconditionFailed
            from google.cloud import storage
        except ImportError as exc:
            raise OperatorError(
                "GCS uploads require google-auth, google-cloud-storage, and google-crc32c"
            ) from exc

        self._precondition_failed = PreconditionFailed
        self.storage = storage
        self.retention_until = retention_until
        self.legal_hold = legal_hold
        self.workload_identity = verifier_workload_identity()
        self.expected_project = _required_env("LIANS_WORM_GCP_PROJECT_ID", GCP_PROJECT_RE)
        self.expected_project_number = _required_env(
            "LIANS_WORM_GCP_PROJECT_NUMBER", GCP_PROJECT_NUMBER_RE
        )
        bucket_name, prefix = _validated_uri(destination_prefix, "gs")
        if not prefix:
            raise OperatorError("GCS destination must include a dedicated backup prefix")
        self.bucket_name = bucket_name
        self.prefix = "/".join(prefix)

        storage_scope = (
            "https://www.googleapis.com/auth/devstorage.read_only"
            if read_only
            else "https://www.googleapis.com/auth/devstorage.full_control"
        )
        credentials, detected_project = google.auth.default(scopes=[storage_scope])
        if detected_project and detected_project != self.expected_project:
            raise OperatorError("GCP ADC project differs from LIANS_WORM_GCP_PROJECT_ID")
        self.client = storage.Client(project=self.expected_project, credentials=credentials)
        self.bucket = self.client.bucket(self.bucket_name)
        self.bucket.reload()
        if str(self.bucket.project_number or "") != self.expected_project_number:
            raise OperatorError("GCS bucket owner differs from LIANS_WORM_GCP_PROJECT_NUMBER")
        if self.bucket.object_retention_mode != "Enabled":
            raise OperatorError("GCS bucket does not have Object Retention Lock enabled")
        if not self.bucket.versioning_enabled:
            raise OperatorError("GCS WORM destination must have object versioning enabled")
        principal = (
            getattr(credentials, "service_account_email", None)
            or getattr(credentials, "signer_email", None)
            or self.workload_identity
        )
        self.principal = str(principal)
        if not SAFE_IDENTITY_RE.fullmatch(self.principal):
            raise OperatorError("GCP credentials did not expose a safe verifier principal")
        policy_material = {
            "bucket": self.bucket_name,
            "project_id": self.expected_project,
            "project_number": self.expected_project_number,
            "metageneration": str(self.bucket.metageneration),
            "object_retention_mode": self.bucket.object_retention_mode,
            "versioning_enabled": self.bucket.versioning_enabled,
            "bucket_retention_period": self.bucket.retention_period,
            "bucket_retention_locked": self.bucket.retention_policy_locked,
            "bucket_retention_effective_time": (
                format_rfc3339(self.bucket.retention_policy_effective_time)
                if self.bucket.retention_policy_effective_time
                else None
            ),
        }
        self.provider = {
            "kind": "google_cloud_storage",
            "owning_identity": (
                f"gcp:projects/{self.expected_project}/numbers/{self.expected_project_number}"
            ),
            "resource_id": f"//storage.googleapis.com/{self.bucket_name}",
            "location": str(self.bucket.location or "unknown"),
            "immutable_capability": "gcs_object_retention_lock",
            "policy_revision_sha256": policy_revision(policy_material),
        }

    def _name(self, item: UploadObject) -> str:
        bucket, segments = _validated_uri(item.destination, "gs")
        name = "/".join(segments)
        expected = f"{self.prefix}/{item.source_filename}"
        if bucket != self.bucket_name or name != expected:
            raise OperatorError(f"GCS destination mismatch for {item.source_filename}")
        return name

    def _stream_sha256(self, blob: Any) -> tuple[str, int]:
        sink = HashingSink()
        blob.download_to_file(
            sink,
            if_generation_match=blob.generation,
            checksum=None,
        )
        return sink.hexdigest, sink.size

    def _provider_checksums(self, blob: Any) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        if blob.crc32c:
            result.append(_checksum("crc32c", str(blob.crc32c), "full_object"))
        if blob.md5_hash:
            result.append(_checksum("md5", str(blob.md5_hash), "full_object"))
        if not result:
            raise OperatorError("GCS returned no CRC32C or MD5 checksum for an object")
        return result

    def _ensure_lock(self, blob: Any) -> Any:
        current = blob.retention
        current_mode = str(current.mode or "")
        current_until = current.retain_until_time
        needs_retention = (
            current_mode != "Locked"
            or current_until is None
            or current_until.astimezone(UTC) < self.retention_until
        )
        needs_hold = self.legal_hold and not bool(blob.temporary_hold)
        if needs_retention or needs_hold:
            if needs_retention:
                updated = blob.retention
                updated.retain_until_time = self.retention_until
                updated.mode = "Locked"
            if needs_hold:
                blob.temporary_hold = True
            blob.patch(
                if_generation_match=blob.generation,
                if_metageneration_match=blob.metageneration,
                override_unlocked_retention=current_mode != "Locked" and needs_retention,
            )
        blob.reload(if_generation_match=blob.generation)
        self._verify_lock_state(blob)
        return blob

    def _verify_lock_state(self, blob: Any) -> tuple[datetime, bool, str]:
        final = blob.retention
        final_until = final.retain_until_time
        if (
            final.mode != "Locked"
            or final_until is None
            or final_until.astimezone(UTC) < self.retention_until
        ):
            raise OperatorError("GCS generation lacks the required locked object retention")
        if self.legal_hold and not blob.temporary_hold:
            raise OperatorError("GCS generation lacks the required temporary hold")
        effective_hold = bool(blob.temporary_hold or blob.event_based_hold)
        if blob.temporary_hold:
            hold_type = "temporary_hold"
        elif blob.event_based_hold:
            hold_type = "event_based_hold"
        else:
            hold_type = "none"
        return final_until, effective_hold, hold_type

    def _attestation_for(
        self,
        item: UploadObject,
        blob: Any,
        checksums: list[dict[str, str]],
        content_verification: dict[str, str],
        disposition: str,
    ) -> dict[str, Any]:
        retain_until, effective_hold, hold_type = self._verify_lock_state(blob)
        return {
            "source_filename": item.source_filename,
            "destination": item.destination,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "provider_object_id": str(blob.generation),
            "provider_etag": str(blob.etag or "").strip('"'),
            "provider_checksums": checksums,
            "content_verification": content_verification,
            "retention": {
                "mode": "Locked",
                "retain_until": format_rfc3339(retain_until),
                "legal_hold": effective_hold,
                "hold_type": hold_type,
                "policy_scope": "object_generation",
                "locked": True,
            },
            "disposition": disposition,
        }

    def verify_existing(self, item: UploadObject, provider_object_id: str) -> dict[str, Any]:
        """Read and verify one exact GCS generation without provider mutation."""

        name = self._name(item)
        if not provider_object_id.isdigit() or int(provider_object_id) <= 0:
            raise OperatorError("GCS immutable anchor generation is invalid")
        generation = int(provider_object_id)
        blob = self.bucket.blob(name, generation=generation)
        blob.reload(if_generation_match=generation)
        if str(blob.generation) != provider_object_id:
            raise OperatorError("GCS returned a different immutable anchor generation")
        if int(blob.size or -1) != item.size_bytes:
            raise OperatorError(f"GCS object size mismatch for {item.source_filename}")
        checksums = self._provider_checksums(blob)
        streamed, size = self._stream_sha256(blob)
        if size != item.size_bytes or streamed != item.sha256:
            raise OperatorError(f"GCS streamed SHA-256 mismatch for {item.source_filename}")
        self._verify_lock_state(blob)
        return self._attestation_for(
            item,
            blob,
            checksums,
            {
                "method": "streamed_sha256",
                "sha256": streamed,
                "verified_at": utc_now(),
            },
            "reused_verified",
        )

    def upload(self, items: list[UploadObject]) -> ProviderRun:
        attestations: list[dict[str, Any]] = []
        for item in items:
            name = self._name(item)
            candidate = self.bucket.blob(name)
            retention = candidate.retention
            retention.retain_until_time = self.retention_until
            retention.mode = "Locked"
            disposition = "created"
            try:
                with item.source_path.open("rb") as source:
                    candidate.upload_from_file(
                        source,
                        size=item.size_bytes,
                        content_type="application/octet-stream",
                        if_generation_match=0,
                        checksum=None,
                    )
            except self._precondition_failed:
                disposition = "reused_verified"
                candidate = self.bucket.get_blob(name)
                if candidate is None:
                    raise OperatorError(
                        f"GCS create precondition failed but object is absent: {item.source_filename}"
                    )
            generation = candidate.generation
            if generation is None:
                raise OperatorError("GCS upload returned no immutable object generation")
            blob = self.bucket.blob(name, generation=int(generation))
            blob.reload(if_generation_match=int(generation))
            if int(blob.size or -1) != item.size_bytes:
                raise OperatorError(f"GCS object size mismatch for {item.source_filename}")
            checksums = self._provider_checksums(blob)
            streamed, size = self._stream_sha256(blob)
            if size != item.size_bytes or streamed != item.sha256:
                raise OperatorError(f"GCS streamed SHA-256 mismatch for {item.source_filename}")
            blob = self._ensure_lock(blob)
            if self._provider_checksums(blob) != checksums:
                raise OperatorError(
                    f"GCS provider checksums changed during lock verification: "
                    f"{item.source_filename}"
                )
            attestations.append(
                self._attestation_for(
                    item,
                    blob,
                    checksums,
                    {
                        "method": "streamed_sha256",
                        "sha256": streamed,
                        "verified_at": utc_now(),
                    },
                    disposition,
                )
            )
        return ProviderRun(
            provider=self.provider,
            verifier_principal=self.principal,
            software=[
                dependency_version("google-cloud-storage"),
                dependency_version("google-auth"),
                dependency_version("google-crc32c"),
            ],
            objects=attestations,
        )


class AzureAdapter:
    def __init__(
        self,
        destination_prefix: str,
        retention_until: datetime,
        legal_hold: bool,
    ) -> None:
        try:
            from azure.core.exceptions import ResourceExistsError
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.storage import StorageManagementClient
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise OperatorError(
                "Azure uploads require azure-identity, azure-mgmt-storage, and azure-storage-blob"
            ) from exc

        self._resource_exists = ResourceExistsError
        self._blob_service_client = BlobServiceClient
        self.retention_until = retention_until
        self.legal_hold = legal_hold
        self.workload_identity = verifier_workload_identity()
        self.tenant_id = _required_env("LIANS_WORM_AZURE_TENANT_ID", AZURE_UUID_RE)
        self.subscription_id = _required_env("LIANS_WORM_AZURE_SUBSCRIPTION_ID", AZURE_UUID_RE)
        self.resource_group = _required_env("LIANS_WORM_AZURE_RESOURCE_GROUP")
        expected_account = _required_env("LIANS_WORM_AZURE_STORAGE_ACCOUNT", AZURE_ACCOUNT_RE)
        account, segments = _validated_uri(destination_prefix, "azure")
        if account != expected_account:
            raise OperatorError("Azure URI account differs from LIANS_WORM_AZURE_STORAGE_ACCOUNT")
        if len(segments) < 2:
            raise OperatorError("Azure destination must be azure://ACCOUNT/CONTAINER/PREFIX")
        container, *prefix = segments
        if not AZURE_CONTAINER_RE.fullmatch(container):
            raise OperatorError("Azure destination contains an invalid container name")
        self.account = account
        self.container = container
        self.prefix = "/".join(prefix)

        self.credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        management = StorageManagementClient(self.credential, self.subscription_id)
        account_properties = management.storage_accounts.get_properties(
            self.resource_group,
            self.account,
        )
        expected_resource_id = (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.Storage/storageAccounts/{self.account}"
        )
        actual_resource_id = str(account_properties.id or "")
        if actual_resource_id.lower() != expected_resource_id.lower():
            raise OperatorError("Azure ARM returned an unexpected storage account resource ID")
        if _enum_value(account_properties.provisioning_state).lower() != "succeeded":
            raise OperatorError("Azure storage account is not in Succeeded provisioning state")
        service_properties = management.blob_services.get_service_properties(
            self.resource_group,
            self.account,
        )
        if not bool(getattr(service_properties, "is_versioning_enabled", False)):
            raise OperatorError("Azure WORM destination must have blob versioning enabled")

        account_url = f"https://{self.account}.blob.core.windows.net"
        self.service = self._blob_service_client(
            account_url=account_url,
            credential=self.credential,
        )
        container_client = self.service.get_container_client(self.container)
        container_properties = container_client.get_container_properties()
        if not bool(container_properties.immutable_storage_with_versioning_enabled):
            raise OperatorError("Azure container lacks version-level immutable storage support")
        client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
        self.principal = f"azure-client-id:{client_id}" if client_id else self.workload_identity
        policy_material = {
            "resource_id": actual_resource_id,
            "location": str(account_properties.location),
            "blob_versioning": True,
            "container": self.container,
            "container_etag": str(container_properties.etag),
            "immutable_storage_with_versioning_enabled": bool(
                container_properties.immutable_storage_with_versioning_enabled
            ),
            "has_container_immutability_policy": bool(container_properties.has_immutability_policy),
            "has_container_legal_hold": bool(container_properties.has_legal_hold),
        }
        self.provider = {
            "kind": "azure_blob_storage",
            "owning_identity": (
                f"azure:tenants/{self.tenant_id}/subscriptions/{self.subscription_id}"
            ),
            "resource_id": actual_resource_id,
            "location": str(account_properties.location or "unknown"),
            "immutable_capability": "azure_version_level_immutability",
            "policy_revision_sha256": policy_revision(policy_material),
        }

    def _name(self, item: UploadObject) -> str:
        account, segments = _validated_uri(item.destination, "azure")
        if len(segments) < 2:
            raise OperatorError(f"Azure destination mismatch for {item.source_filename}")
        container, *name_segments = segments
        name = "/".join(name_segments)
        expected = f"{self.prefix}/{item.source_filename}"
        if account != self.account or container != self.container or name != expected:
            raise OperatorError(f"Azure destination mismatch for {item.source_filename}")
        return name

    def _stream_sha256(self, blob_client: Any) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        download = blob_client.download_blob(validate_content=True)
        for chunk in download.chunks():
            digest.update(chunk)
            size += len(chunk)
        return digest.hexdigest(), size

    def _content_md5(self, properties: Any) -> str:
        raw = properties.content_settings.content_md5
        if not raw:
            return ""
        if isinstance(raw, str):
            return raw
        return base64.b64encode(bytes(raw)).decode("ascii")

    def _ensure_lock(self, blob_client: Any, properties: Any) -> Any:
        from azure.storage.blob import ImmutabilityPolicy

        policy = properties.immutability_policy
        mode = _enum_value(policy.policy_mode)
        expiry = policy.expiry_time
        expiry_utc = expiry.astimezone(UTC) if isinstance(expiry, datetime) else None
        if mode.lower() != "locked" or expiry_utc is None or expiry_utc < self.retention_until:
            blob_client.set_immutability_policy(
                ImmutabilityPolicy(
                    expiry_time=self.retention_until,
                    policy_mode="Locked",
                )
            )
        if self.legal_hold and not bool(properties.has_legal_hold):
            blob_client.set_legal_hold(True)
        verified = blob_client.get_blob_properties()
        self._verify_lock_state(verified)
        return verified

    def _verify_lock_state(self, properties: Any) -> tuple[datetime, bool]:
        verified = properties
        verified_policy = verified.immutability_policy
        verified_expiry = verified_policy.expiry_time
        if (
            _enum_value(verified_policy.policy_mode).lower() != "locked"
            or not isinstance(verified_expiry, datetime)
            or verified_expiry.astimezone(UTC) < self.retention_until
        ):
            raise OperatorError("Azure blob version lacks the required Locked retention")
        if self.legal_hold and not bool(verified.has_legal_hold):
            raise OperatorError("Azure blob version lacks the required legal hold")
        return verified_expiry, bool(verified.has_legal_hold)

    def _attestation_for(
        self,
        item: UploadObject,
        provider_object_id: str,
        properties: Any,
        provider_md5: str,
        content_verification: dict[str, str],
        disposition: str,
    ) -> dict[str, Any]:
        retain_until, effective_hold = self._verify_lock_state(properties)
        return {
            "source_filename": item.source_filename,
            "destination": item.destination,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "provider_object_id": provider_object_id,
            "provider_etag": str(properties.etag or "").strip('"'),
            "provider_checksums": [_checksum("md5", provider_md5, "full_object")],
            "content_verification": content_verification,
            "retention": {
                "mode": "Locked",
                "retain_until": format_rfc3339(retain_until),
                "legal_hold": effective_hold,
                "hold_type": "legal_hold" if effective_hold else "none",
                "policy_scope": "object_version",
                "locked": True,
            },
            "disposition": disposition,
        }

    def verify_existing(self, item: UploadObject, provider_object_id: str) -> dict[str, Any]:
        """Read and verify one exact Azure blob version without provider mutation."""

        name = self._name(item)
        if not provider_object_id or len(provider_object_id) > 1024:
            raise OperatorError("Azure immutable anchor version ID is invalid")
        blob_client = self.service.get_blob_client(
            container=self.container,
            blob=name,
            version_id=provider_object_id,
        )
        properties = blob_client.get_blob_properties()
        if str(properties.version_id or "") != provider_object_id:
            raise OperatorError("Azure returned a different immutable anchor version")
        if int(properties.size or -1) != item.size_bytes:
            raise OperatorError(f"Azure blob size mismatch for {item.source_filename}")
        provider_md5 = self._content_md5(properties)
        if not provider_md5:
            raise OperatorError(f"Azure returned no Content-MD5 for {item.source_filename}")
        local_md5 = base64_digest(item.source_path, "md5")
        if provider_md5 != local_md5:
            raise OperatorError(f"Azure provider MD5 mismatch for {item.source_filename}")
        streamed, size = self._stream_sha256(blob_client)
        if size != item.size_bytes or streamed != item.sha256:
            raise OperatorError(f"Azure streamed SHA-256 mismatch for {item.source_filename}")
        self._verify_lock_state(properties)
        return self._attestation_for(
            item,
            provider_object_id,
            properties,
            provider_md5,
            {
                "method": "streamed_sha256",
                "sha256": streamed,
                "verified_at": utc_now(),
            },
            "reused_verified",
        )

    def upload(self, items: list[UploadObject]) -> ProviderRun:
        from azure.storage.blob import ContentSettings, ImmutabilityPolicy

        attestations: list[dict[str, Any]] = []
        for item in items:
            name = self._name(item)
            current_client = self.service.get_blob_client(
                container=self.container,
                blob=name,
            )
            local_md5 = base64_digest(item.source_path, "md5")
            disposition = "created"
            try:
                with item.source_path.open("rb") as source:
                    uploaded = current_client.upload_blob(
                        source,
                        length=item.size_bytes,
                        overwrite=False,
                        content_settings=ContentSettings(
                            content_type="application/octet-stream",
                            content_md5=base64.b64decode(local_md5),
                        ),
                        validate_content=True,
                        immutability_policy=ImmutabilityPolicy(
                            expiry_time=self.retention_until,
                            policy_mode="Locked",
                        ),
                        legal_hold=self.legal_hold,
                    )
                version_id = str(
                    uploaded.get("version_id") or uploaded.get("x-ms-version-id") or ""
                )
                if not version_id:
                    raise OperatorError("Azure upload did not return an immutable blob version ID")
            except self._resource_exists:
                disposition = "reused_verified"
                current = current_client.get_blob_properties()
                version_id = str(current.version_id or "")
                if not version_id:
                    raise OperatorError("Existing Azure blob has no immutable version ID")

            blob_client = self.service.get_blob_client(
                container=self.container,
                blob=name,
                version_id=version_id,
            )
            properties = blob_client.get_blob_properties()
            if int(properties.size or -1) != item.size_bytes:
                raise OperatorError(f"Azure blob size mismatch for {item.source_filename}")
            provider_md5 = self._content_md5(properties)
            streamed, size = self._stream_sha256(blob_client)
            if size != item.size_bytes or streamed != item.sha256:
                raise OperatorError(f"Azure streamed SHA-256 mismatch for {item.source_filename}")
            if not provider_md5:
                raise OperatorError(f"Azure returned no Content-MD5 for {item.source_filename}")
            if provider_md5 != local_md5:
                raise OperatorError(f"Azure provider MD5 mismatch for {item.source_filename}")

            properties = self._ensure_lock(blob_client, properties)
            provider_md5 = self._content_md5(properties)
            if provider_md5 != local_md5:
                raise OperatorError(
                    f"Azure post-lock provider checksum mismatch for {item.source_filename}"
                )
            attestations.append(
                self._attestation_for(
                    item,
                    version_id,
                    properties,
                    provider_md5,
                    {
                        "method": "streamed_sha256",
                        "sha256": streamed,
                        "verified_at": utc_now(),
                    },
                    disposition,
                )
            )
        return ProviderRun(
            provider=self.provider,
            verifier_principal=self.principal,
            software=[
                dependency_version("azure-identity"),
                dependency_version("azure-mgmt-storage"),
                dependency_version("azure-storage-blob"),
            ],
            objects=attestations,
        )


def _provider_adapter(
    destination_prefix: str,
    retention_until: datetime,
    legal_hold: bool,
    *,
    read_only: bool = False,
) -> Any:
    scheme = urlparse(destination_prefix).scheme
    if scheme == "s3":
        return S3Adapter(destination_prefix, retention_until, legal_hold)
    if scheme == "gs":
        return GCSAdapter(
            destination_prefix,
            retention_until,
            legal_hold,
            read_only=read_only,
        )
    if scheme == "azure":
        return AzureAdapter(destination_prefix, retention_until, legal_hold)
    raise OperatorError("Unsupported WORM provider scheme")


def run_provider(
    destination_prefix: str,
    retention_until: datetime,
    legal_hold: bool,
    objects: list[UploadObject],
) -> ProviderRun:
    scheme = urlparse(destination_prefix).scheme
    try:
        adapter = _provider_adapter(destination_prefix, retention_until, legal_hold)
        return adapter.upload(objects)
    except OperatorError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider errors must be sanitized.
        code = _safe_cloud_error_code(exc)
        raise OperatorError(
            f"{scheme or 'provider'} WORM operation failed ({code}); inspect provider audit logs"
        ) from None


def verify_provider_object(
    destination_prefix: str,
    retention_until: datetime,
    legal_hold: bool,
    item: UploadObject,
    provider_object_id: str,
) -> ProviderRun:
    """Re-read one exact immutable object without create, overwrite, or lock mutation."""

    scheme = urlparse(destination_prefix).scheme
    try:
        adapter = _provider_adapter(
            destination_prefix,
            retention_until,
            legal_hold,
            read_only=True,
        )
        attestation = adapter.verify_existing(item, provider_object_id)
        return ProviderRun(
            provider=adapter.provider,
            verifier_principal=adapter.principal,
            software=(
                [dependency_version("boto3"), dependency_version("botocore")]
                if scheme == "s3"
                else [
                    dependency_version("google-cloud-storage"),
                    dependency_version("google-auth"),
                    dependency_version("google-crc32c"),
                ]
                if scheme == "gs"
                else [
                    dependency_version("azure-identity"),
                    dependency_version("azure-mgmt-storage"),
                    dependency_version("azure-storage-blob"),
                ]
            ),
            objects=[attestation],
        )
    except OperatorError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider errors must be sanitized.
        code = _safe_cloud_error_code(exc)
        raise OperatorError(
            f"{scheme or 'provider'} WORM verification failed ({code}); inspect provider audit logs"
        ) from None


def ensure_unique_provider_object_ids(objects: Iterable[dict[str, Any]]) -> None:
    """Reject duplicate destination/version pairs.

    Generation and version identifiers are scoped by object key on some stores,
    so the destination is deliberately part of the uniqueness boundary.
    """

    seen: set[tuple[str, str]] = set()
    for item in objects:
        identity = (
            str(item.get("destination", "")),
            str(item.get("provider_object_id", "")),
        )
        if identity in seen:
            raise OperatorError("Provider returned a duplicate destination/version pair")
        seen.add(identity)
