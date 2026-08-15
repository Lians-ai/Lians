"""Zero-knowledge device enrollment and opaque profile synchronization.

Identity providers authenticate an account, but they never receive a memory
encryption key. Existing devices approve new devices, every cloud object is an
authenticated encrypted profile snapshot, and the server only enforces a
public device registry plus a compare-and-swap revision chain.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .crypto import LocalCipher
from .portability import (
    _atomic_publish,
    _canonical,
    _profile_payload,
    _validate_payload,
    merge_profile_payload,
)
from .store import MemoryStore

STATE_FORMAT = "lians-sync-device-state"
REQUEST_FORMAT = "lians-device-enrollment-request"
APPROVAL_FORMAT = "lians-device-enrollment-approval"
GRANT_FORMAT = "lians-device-grant"
REVISION_FORMAT = "lians-encrypted-profile-revision"
SYNC_VERSION = 1
MAX_REVISION_BYTES = 128 * 1024 * 1024
MAX_STATE_BYTES = 1024 * 1024
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class SyncProtocolError(ValueError):
    """A sync document is invalid, tampered with, stale, or unexpected."""


class SyncPreconditionError(SyncProtocolError):
    """The cloud head changed and the caller must pull before retrying."""


def _now() -> datetime:
    # datetime.UTC is unavailable on the package's supported Python 3.10.
    return datetime.now(timezone.utc)  # noqa: UP017


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Sync timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()  # noqa: UP017


def _parse_timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 128:
        raise SyncProtocolError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SyncProtocolError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise SyncProtocolError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)  # noqa: UP017


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: Any, *, label: str, length: int | None = None) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_REVISION_BYTES * 2:
        raise SyncProtocolError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise SyncProtocolError(f"{label} is invalid") from exc
    if length is not None and len(decoded) != length:
        raise SyncProtocolError(f"{label} is invalid")
    return decoded


def _raw_public(key: Ed25519PublicKey | X25519PublicKey) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _exact(document: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != fields:
        raise SyncProtocolError(f"{label} contains unexpected or missing fields")
    return document


def _uuid(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise SyncProtocolError(f"{label} is invalid")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise SyncProtocolError(f"{label} is invalid") from exc
    if str(parsed) != value:
        raise SyncProtocolError(f"{label} is invalid")
    return value


def _display_name(value: str) -> str:
    rendered = value.strip()
    if not rendered or len(rendered) > 80 or _CONTROL.search(rendered):
        raise ValueError("Device name must contain 1 to 80 visible characters")
    return rendered


def _device_id(exchange_public: bytes, signing_public: bytes) -> str:
    return hashlib.sha256(
        b"lians-device-v1\0" + exchange_public + signing_public
    ).hexdigest()


def _validate_device(document: Any) -> dict[str, str]:
    device = _exact(
        document,
        {"device_id", "display_name", "exchange_public_key", "signing_public_key"},
        label="Device descriptor",
    )
    try:
        display_name = _display_name(device["display_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SyncProtocolError("Device descriptor is invalid") from exc
    exchange = _unb64(device["exchange_public_key"], label="Device exchange key", length=32)
    signing = _unb64(device["signing_public_key"], label="Device signing key", length=32)
    expected_id = _device_id(exchange, signing)
    if device["device_id"] != expected_id:
        raise SyncProtocolError("Device descriptor ID does not match its public keys")
    return {
        "device_id": expected_id,
        "display_name": display_name,
        "exchange_public_key": _b64(exchange),
        "signing_public_key": _b64(signing),
    }


@dataclass(frozen=True)
class DeviceIdentity:
    """Stable per-device keys derived from the locally protected Lians root."""

    display_name: str
    exchange_private: X25519PrivateKey
    signing_private: Ed25519PrivateKey

    @classmethod
    def from_store(cls, store: MemoryStore, display_name: str) -> DeviceIdentity:
        profile = store.profile.encode("utf-8")
        exchange_seed = store.cipher.derive_key(
            info=b"lians-sync-exchange-v1\0" + profile
        )
        signing_seed = store.cipher.derive_key(info=b"lians-sync-signing-v1\0" + profile)
        return cls(
            display_name=_display_name(display_name),
            exchange_private=X25519PrivateKey.from_private_bytes(exchange_seed),
            signing_private=Ed25519PrivateKey.from_private_bytes(signing_seed),
        )

    @property
    def descriptor(self) -> dict[str, str]:
        exchange = _raw_public(self.exchange_private.public_key())
        signing = _raw_public(self.signing_private.public_key())
        return {
            "device_id": _device_id(exchange, signing),
            "display_name": self.display_name,
            "exchange_public_key": _b64(exchange),
            "signing_public_key": _b64(signing),
        }

    @property
    def device_id(self) -> str:
        return self.descriptor["device_id"]

    def signature(self, value: bytes) -> dict[str, str]:
        return {"algorithm": "Ed25519", "value": _b64(self.signing_private.sign(value))}


@dataclass
class SyncState:
    """Device-local workspace key, trusted devices, and applied cloud head."""

    workspace_id: str
    epoch: int
    workspace_key: bytes
    device: dict[str, str]
    trusted_devices: dict[str, dict[str, str]]
    head_revision: int = 0
    head_hash: str | None = None

    @classmethod
    def create(cls, identity: DeviceIdentity) -> SyncState:
        descriptor = identity.descriptor
        return cls(
            workspace_id=str(uuid.uuid4()),
            epoch=1,
            workspace_key=os.urandom(32),
            device=descriptor,
            trusted_devices={descriptor["device_id"]: descriptor},
        )

    def trust(self, device: dict[str, str]) -> None:
        validated = _validate_device(device)
        existing = self.trusted_devices.get(validated["device_id"])
        if existing is not None and existing != validated:
            raise SyncProtocolError("A trusted device changed its public identity")
        self.trusted_devices[validated["device_id"]] = validated

    def save(
        self,
        path: str | Path,
        cipher: LocalCipher,
        identity: DeviceIdentity,
        *,
        overwrite: bool = True,
    ) -> None:
        if identity.device_id != self.device["device_id"]:
            raise SyncProtocolError("Sync state belongs to a different local device")
        header = {
            "format": STATE_FORMAT,
            "version": SYNC_VERSION,
            "workspace_id": self.workspace_id,
            "epoch": self.epoch,
            "device": self.device,
        }
        ciphertext, nonce = cipher.seal_bytes(
            self.workspace_key,
            associated_data=_canonical(header),
        )
        protected = {
            **header,
            "workspace_key": {
                "cipher": "AES-256-GCM",
                "nonce": _b64(nonce),
                "ciphertext": _b64(ciphertext),
            },
            "trusted_devices": sorted(
                self.trusted_devices.values(), key=lambda item: item["device_id"]
            ),
            "head": {"revision": self.head_revision, "object_hash": self.head_hash},
        }
        document = {**protected, "signature": identity.signature(_canonical(protected))}
        encoded = _canonical(document) + b"\n"
        if len(encoded) > MAX_STATE_BYTES:
            raise SyncProtocolError("Sync state is unexpectedly large")
        _atomic_publish(Path(path).expanduser().resolve(), encoded, overwrite=overwrite)

    @classmethod
    def load(
        cls,
        path: str | Path,
        cipher: LocalCipher,
        identity: DeviceIdentity,
    ) -> SyncState:
        state_path = Path(path).expanduser().resolve()
        if state_path.stat().st_size > MAX_STATE_BYTES:
            raise SyncProtocolError("Sync state is unexpectedly large")
        try:
            document = json.loads(state_path.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SyncProtocolError("Sync state is invalid") from exc
        _exact(
            document,
            {
                "format",
                "version",
                "workspace_id",
                "epoch",
                "device",
                "workspace_key",
                "trusted_devices",
                "head",
                "signature",
            },
            label="Sync state",
        )
        signature = document.pop("signature")
        try:
            _verify_signature(
                signature,
                identity.descriptor["signing_public_key"],
                _canonical(document),
                label="Sync state",
            )
        finally:
            document["signature"] = signature
        if document["format"] != STATE_FORMAT or document["version"] != SYNC_VERSION:
            raise SyncProtocolError("Sync state uses an unsupported format")
        workspace_id = _uuid(document["workspace_id"], label="Sync workspace ID")
        if type(document["epoch"]) is not int or document["epoch"] < 1:
            raise SyncProtocolError("Sync state epoch is invalid")
        device = _validate_device(document["device"])
        if device != identity.descriptor:
            raise SyncProtocolError("Sync state belongs to a different local device")
        wrapped = _exact(
            document["workspace_key"],
            {"cipher", "nonce", "ciphertext"},
            label="Wrapped workspace key",
        )
        if wrapped["cipher"] != "AES-256-GCM":
            raise SyncProtocolError("Wrapped workspace key cipher is unsupported")
        header = {key: document[key] for key in ("format", "version", "workspace_id", "epoch", "device")}
        try:
            workspace_key = cipher.open_bytes(
                _unb64(wrapped["ciphertext"], label="Wrapped workspace key"),
                _unb64(wrapped["nonce"], label="Workspace key nonce", length=12),
                associated_data=_canonical(header),
            )
        except InvalidTag as exc:
            raise SyncProtocolError("Sync state was changed or belongs to another OS account") from exc
        if len(workspace_key) != 32:
            raise SyncProtocolError("Workspace key is invalid")
        if not isinstance(document["trusted_devices"], list) or not document["trusted_devices"]:
            raise SyncProtocolError("Sync state has no trusted devices")
        trusted: dict[str, dict[str, str]] = {}
        for item in document["trusted_devices"]:
            validated = _validate_device(item)
            if validated["device_id"] in trusted:
                raise SyncProtocolError("Sync state contains a duplicate trusted device")
            trusted[validated["device_id"]] = validated
        if device["device_id"] not in trusted:
            raise SyncProtocolError("Sync state does not trust its local device")
        head = _exact(document["head"], {"revision", "object_hash"}, label="Sync head")
        if type(head["revision"]) is not int or head["revision"] < 0:
            raise SyncProtocolError("Sync head revision is invalid")
        if head["revision"] == 0:
            if head["object_hash"] is not None:
                raise SyncProtocolError("Empty sync head has an object hash")
        elif not isinstance(head["object_hash"], str) or not _HEX_64.fullmatch(head["object_hash"]):
            raise SyncProtocolError("Sync head object hash is invalid")
        return cls(
            workspace_id=workspace_id,
            epoch=document["epoch"],
            workspace_key=workspace_key,
            device=device,
            trusted_devices=trusted,
            head_revision=head["revision"],
            head_hash=head["object_hash"],
        )


def _verification_code(request_body: dict[str, Any]) -> str:
    value = hashlib.sha256(b"lians-enrollment-code-v1\0" + _canonical(request_body)).hexdigest()
    return f"{value[:4]}-{value[4:8]}".upper()


def create_enrollment_request(
    identity: DeviceIdentity,
    *,
    now: datetime | None = None,
    ttl_seconds: int = 600,
) -> dict[str, Any]:
    if not 60 <= ttl_seconds <= 1800:
        raise ValueError("Enrollment request lifetime must be between 60 and 1800 seconds")
    created = now or _now()
    body = {
        "format": REQUEST_FORMAT,
        "version": SYNC_VERSION,
        "request_id": str(uuid.uuid4()),
        "device": identity.descriptor,
        "created_at": _timestamp(created),
        "expires_at": _timestamp(created + timedelta(seconds=ttl_seconds)),
    }
    return {**body, "verification_code": _verification_code(body)}


def _validate_request(request: Any, *, now: datetime | None = None) -> dict[str, Any]:
    request = _exact(
        request,
        {
            "format",
            "version",
            "request_id",
            "device",
            "created_at",
            "expires_at",
            "verification_code",
        },
        label="Enrollment request",
    )
    if request["format"] != REQUEST_FORMAT or request["version"] != SYNC_VERSION:
        raise SyncProtocolError("Enrollment request uses an unsupported format")
    _uuid(request["request_id"], label="Enrollment request ID")
    device = _validate_device(request["device"])
    created = _parse_timestamp(request["created_at"], label="Enrollment creation time")
    expires = _parse_timestamp(request["expires_at"], label="Enrollment expiration time")
    current = (now or _now()).astimezone(timezone.utc)  # noqa: UP017
    if (
        expires <= created
        or expires - created > timedelta(minutes=30)
        or current > expires
        or created - current > timedelta(minutes=5)
    ):
        raise SyncProtocolError("Enrollment request expired or has an invalid lifetime")
    body = {key: request[key] for key in request if key != "verification_code"}
    if request["verification_code"] != _verification_code(body):
        raise SyncProtocolError("Enrollment request verification code does not match")
    return {**request, "device": device}


def _verify_signature(
    signature: Any,
    public_key: str,
    value: bytes,
    *,
    label: str,
) -> None:
    signature = _exact(signature, {"algorithm", "value"}, label=f"{label} signature")
    if signature["algorithm"] != "Ed25519":
        raise SyncProtocolError(f"{label} signature algorithm is unsupported")
    try:
        Ed25519PublicKey.from_public_bytes(
            _unb64(public_key, label=f"{label} public key", length=32)
        ).verify(_unb64(signature["value"], label=f"{label} signature", length=64), value)
    except InvalidSignature as exc:
        raise SyncProtocolError(f"{label} signature is invalid") from exc


def approve_enrollment(
    state: SyncState,
    identity: DeviceIdentity,
    request: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    request = _validate_request(request, now=now)
    if identity.device_id != state.device["device_id"]:
        raise SyncProtocolError("Only this workspace device can approve enrollment")
    recipient = request["device"]
    issued_at = _timestamp(now or _now())
    grant = {
        "format": GRANT_FORMAT,
        "version": SYNC_VERSION,
        "workspace_id": state.workspace_id,
        "epoch": state.epoch,
        "request_id": request["request_id"],
        "recipient_device": recipient,
        "approver_device": state.device,
        "issued_at": issued_at,
    }
    grant_signature = identity.signature(_canonical(grant))
    ephemeral = X25519PrivateKey.generate()
    ephemeral_public = _raw_public(ephemeral.public_key())
    recipient_public = X25519PublicKey.from_public_bytes(
        _unb64(recipient["exchange_public_key"], label="Recipient exchange key", length=32)
    )
    shared = ephemeral.exchange(recipient_public)
    wrap_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=uuid.UUID(request["request_id"]).bytes,
        info=b"lians-workspace-enrollment-v1",
    ).derive(shared)
    nonce = os.urandom(12)
    wrap_header = {
        "cipher": "X25519-HKDF-SHA256+A256GCM",
        "workspace_id": state.workspace_id,
        "epoch": state.epoch,
        "request_id": request["request_id"],
        "recipient_device_id": recipient["device_id"],
        "approver_device_id": identity.device_id,
        "ephemeral_public_key": _b64(ephemeral_public),
        "nonce": _b64(nonce),
    }
    ciphertext = AESGCM(wrap_key).encrypt(nonce, state.workspace_key, _canonical(wrap_header))
    state.trust(recipient)
    return {
        "format": APPROVAL_FORMAT,
        "version": SYNC_VERSION,
        "request_id": request["request_id"],
        "verification_code": request["verification_code"],
        "grant": grant,
        "grant_signature": grant_signature,
        "key_wrap": {**wrap_header, "ciphertext": _b64(ciphertext)},
    }


def _validate_grant(
    grant: Any,
    signature: Any,
    *,
    workspace_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    grant = _exact(
        grant,
        {
            "format",
            "version",
            "workspace_id",
            "epoch",
            "request_id",
            "recipient_device",
            "approver_device",
            "issued_at",
        },
        label="Device grant",
    )
    if grant["format"] != GRANT_FORMAT or grant["version"] != SYNC_VERSION:
        raise SyncProtocolError("Device grant uses an unsupported format")
    _uuid(grant["workspace_id"], label="Grant workspace ID")
    if workspace_id is not None and grant["workspace_id"] != workspace_id:
        raise SyncProtocolError("Device grant belongs to a different workspace")
    if type(grant["epoch"]) is not int or grant["epoch"] < 1:
        raise SyncProtocolError("Device grant epoch is invalid")
    _uuid(grant["request_id"], label="Grant request ID")
    _parse_timestamp(grant["issued_at"], label="Grant issue time")
    recipient = _validate_device(grant["recipient_device"])
    approver = _validate_device(grant["approver_device"])
    _verify_signature(
        signature,
        approver["signing_public_key"],
        _canonical(grant),
        label="Device grant",
    )
    return grant, recipient, approver


def accept_enrollment(
    store: MemoryStore,
    identity: DeviceIdentity,
    request: dict[str, Any],
    approval: dict[str, Any],
    state_path: str | Path,
    *,
    now: datetime | None = None,
) -> SyncState:
    request = _validate_request(request, now=now)
    if request["device"] != identity.descriptor:
        raise SyncProtocolError("Enrollment approval targets a different local device")
    approval = _exact(
        approval,
        {
            "format",
            "version",
            "request_id",
            "verification_code",
            "grant",
            "grant_signature",
            "key_wrap",
        },
        label="Enrollment approval",
    )
    if approval["format"] != APPROVAL_FORMAT or approval["version"] != SYNC_VERSION:
        raise SyncProtocolError("Enrollment approval uses an unsupported format")
    if (
        approval["request_id"] != request["request_id"]
        or approval["verification_code"] != request["verification_code"]
    ):
        raise SyncProtocolError("Enrollment approval does not match this request")
    grant, recipient, approver = _validate_grant(
        approval["grant"], approval["grant_signature"]
    )
    if grant["request_id"] != request["request_id"] or recipient != identity.descriptor:
        raise SyncProtocolError("Device grant does not match this request")
    issued = _parse_timestamp(grant["issued_at"], label="Grant issue time")
    created = _parse_timestamp(request["created_at"], label="Enrollment creation time")
    expires = _parse_timestamp(request["expires_at"], label="Enrollment expiration time")
    if not created <= issued <= expires:
        raise SyncProtocolError("Device grant was issued outside this request's lifetime")
    wrap = _exact(
        approval["key_wrap"],
        {
            "cipher",
            "workspace_id",
            "epoch",
            "request_id",
            "recipient_device_id",
            "approver_device_id",
            "ephemeral_public_key",
            "nonce",
            "ciphertext",
        },
        label="Enrollment key wrap",
    )
    expected_wrap = {
        "cipher": "X25519-HKDF-SHA256+A256GCM",
        "workspace_id": grant["workspace_id"],
        "epoch": grant["epoch"],
        "request_id": request["request_id"],
        "recipient_device_id": recipient["device_id"],
        "approver_device_id": approver["device_id"],
    }
    if any(wrap.get(key) != value for key, value in expected_wrap.items()):
        raise SyncProtocolError("Enrollment key wrap does not match the signed grant")
    ephemeral_public = X25519PublicKey.from_public_bytes(
        _unb64(wrap["ephemeral_public_key"], label="Enrollment ephemeral key", length=32)
    )
    shared = identity.exchange_private.exchange(ephemeral_public)
    wrap_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=uuid.UUID(request["request_id"]).bytes,
        info=b"lians-workspace-enrollment-v1",
    ).derive(shared)
    wrap_header = {key: wrap[key] for key in wrap if key != "ciphertext"}
    try:
        workspace_key = AESGCM(wrap_key).decrypt(
            _unb64(wrap["nonce"], label="Enrollment nonce", length=12),
            _unb64(wrap["ciphertext"], label="Wrapped workspace key"),
            _canonical(wrap_header),
        )
    except InvalidTag as exc:
        raise SyncProtocolError("Enrollment approval was changed or targets another device") from exc
    if len(workspace_key) != 32:
        raise SyncProtocolError("Enrollment workspace key is invalid")
    state = SyncState(
        workspace_id=grant["workspace_id"],
        epoch=grant["epoch"],
        workspace_key=workspace_key,
        device=recipient,
        trusted_devices={recipient["device_id"]: recipient, approver["device_id"]: approver},
    )
    state.save(state_path, store.cipher, identity, overwrite=False)
    return state


def apply_device_grant(
    state: SyncState,
    grant: dict[str, Any],
    signature: dict[str, str],
) -> dict[str, str]:
    validated, recipient, approver = _validate_grant(
        grant, signature, workspace_id=state.workspace_id
    )
    if validated["epoch"] != state.epoch:
        raise SyncProtocolError("Device grant belongs to another workspace-key epoch")
    trusted_approver = state.trusted_devices.get(approver["device_id"])
    if trusted_approver != approver:
        raise SyncProtocolError("Device grant was not signed by a trusted device")
    state.trust(recipient)
    return recipient


def prepare_revision(
    store: MemoryStore,
    state: SyncState,
    identity: DeviceIdentity,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if state.device != identity.descriptor:
        raise SyncProtocolError("Sync state belongs to a different local device")
    payload = _validate_payload(_profile_payload(store))
    nonce = os.urandom(12)
    header = {
        "format": REVISION_FORMAT,
        "version": SYNC_VERSION,
        "workspace_id": state.workspace_id,
        "epoch": state.epoch,
        "revision": state.head_revision + 1,
        "previous_hash": state.head_hash,
        "device_id": identity.device_id,
        "created_at": _timestamp(now or _now()),
        "cipher": {"name": "AES-256-GCM", "nonce": _b64(nonce)},
    }
    ciphertext = AESGCM(state.workspace_key).encrypt(
        nonce,
        _canonical(payload),
        _canonical(header),
    )
    signed = {**header, "ciphertext": _b64(ciphertext)}
    signature = identity.signature(_canonical(signed))
    protected = {**signed, "signature": signature}
    envelope = {
        **protected,
        "object_hash": hashlib.sha256(_canonical(protected)).hexdigest(),
    }
    if len(_canonical(envelope)) > MAX_REVISION_BYTES:
        raise SyncProtocolError("Encrypted sync revision exceeds the 128 MiB safety limit")
    return envelope


def _validated_revision_public(
    envelope: Any,
    trusted_devices: dict[str, dict[str, str]],
    *,
    workspace_id: str,
    epoch: int,
) -> dict[str, Any]:
    envelope = _exact(
        envelope,
        {
            "format",
            "version",
            "workspace_id",
            "epoch",
            "revision",
            "previous_hash",
            "device_id",
            "created_at",
            "cipher",
            "ciphertext",
            "signature",
            "object_hash",
        },
        label="Encrypted sync revision",
    )
    if len(_canonical(envelope)) > MAX_REVISION_BYTES:
        raise SyncProtocolError("Encrypted sync revision exceeds the safety limit")
    if envelope["format"] != REVISION_FORMAT or envelope["version"] != SYNC_VERSION:
        raise SyncProtocolError("Encrypted sync revision uses an unsupported format")
    if envelope["workspace_id"] != workspace_id or envelope["epoch"] != epoch:
        raise SyncProtocolError("Encrypted sync revision belongs to another workspace")
    if type(envelope["revision"]) is not int or envelope["revision"] < 1:
        raise SyncProtocolError("Encrypted sync revision number is invalid")
    previous_hash = envelope["previous_hash"]
    if previous_hash is not None and (
        not isinstance(previous_hash, str) or not _HEX_64.fullmatch(previous_hash)
    ):
        raise SyncProtocolError("Encrypted sync previous hash is invalid")
    if not isinstance(envelope["object_hash"], str) or not _HEX_64.fullmatch(
        envelope["object_hash"]
    ):
        raise SyncProtocolError("Encrypted sync object hash is invalid")
    _parse_timestamp(envelope["created_at"], label="Encrypted sync creation time")
    cipher = _exact(envelope["cipher"], {"name", "nonce"}, label="Sync revision cipher")
    if cipher["name"] != "AES-256-GCM":
        raise SyncProtocolError("Encrypted sync revision cipher is unsupported")
    _unb64(cipher["nonce"], label="Sync revision nonce", length=12)
    _unb64(envelope["ciphertext"], label="Sync revision ciphertext")
    protected = {key: envelope[key] for key in envelope if key != "object_hash"}
    if hashlib.sha256(_canonical(protected)).hexdigest() != envelope["object_hash"]:
        raise SyncProtocolError("Encrypted sync revision hash does not match")
    device = trusted_devices.get(envelope["device_id"])
    if device is None:
        raise SyncProtocolError("Encrypted sync revision came from an untrusted device")
    signed = {key: protected[key] for key in protected if key != "signature"}
    _verify_signature(
        envelope["signature"],
        device["signing_public_key"],
        _canonical(signed),
        label="Encrypted sync revision",
    )
    return envelope


def apply_revision(
    store: MemoryStore,
    state: SyncState,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    envelope = _validated_revision_public(
        envelope,
        state.trusted_devices,
        workspace_id=state.workspace_id,
        epoch=state.epoch,
    )
    if envelope["revision"] != state.head_revision + 1 or envelope["previous_hash"] != state.head_hash:
        raise SyncPreconditionError("Sync revision is missing, stale, or replayed")
    header = {
        key: envelope[key]
        for key in (
            "format",
            "version",
            "workspace_id",
            "epoch",
            "revision",
            "previous_hash",
            "device_id",
            "created_at",
            "cipher",
        )
    }
    try:
        plaintext = AESGCM(state.workspace_key).decrypt(
            _unb64(envelope["cipher"]["nonce"], label="Sync revision nonce", length=12),
            _unb64(envelope["ciphertext"], label="Sync revision ciphertext"),
            _canonical(header),
        )
        payload = json.loads(plaintext)
        if not isinstance(payload, dict):
            raise TypeError
        payload = _validate_payload(payload)
    except InvalidTag as exc:
        raise SyncProtocolError("Encrypted sync revision was changed or uses another key") from exc
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        if isinstance(exc, SyncProtocolError):
            raise
        raise SyncProtocolError("Encrypted sync profile is invalid") from exc
    report = merge_profile_payload(store, payload, sync=True)
    state.head_revision = envelope["revision"]
    state.head_hash = envelope["object_hash"]
    return {**report, "revision": state.head_revision, "object_hash": state.head_hash}


def acknowledge_revision(state: SyncState, envelope: dict[str, Any]) -> None:
    envelope = _validated_revision_public(
        envelope,
        state.trusted_devices,
        workspace_id=state.workspace_id,
        epoch=state.epoch,
    )
    if envelope["revision"] != state.head_revision + 1 or envelope["previous_hash"] != state.head_hash:
        raise SyncPreconditionError("Cloud acknowledgement does not extend the local sync head")
    state.head_revision = envelope["revision"]
    state.head_hash = envelope["object_hash"]


class OpaqueRevisionLog:
    """Reference cloud contract that never accepts a plaintext profile or key.

    A production service scopes every workspace to an authenticated account and
    persists these public grants plus encrypted envelopes transactionally.
    """

    def __init__(self) -> None:
        self._workspaces: dict[str, dict[str, Any]] = {}

    def create_workspace(self, state: SyncState) -> None:
        if state.workspace_id in self._workspaces:
            raise SyncPreconditionError("Sync workspace already exists")
        self._workspaces[state.workspace_id] = {
            "epoch": state.epoch,
            "devices": {state.device["device_id"]: copy.deepcopy(state.device)},
            "grants": [],
            "revisions": [],
        }

    def register_approval(self, approval: dict[str, Any]) -> None:
        approval = _exact(
            approval,
            {
                "format",
                "version",
                "request_id",
                "verification_code",
                "grant",
                "grant_signature",
                "key_wrap",
            },
            label="Enrollment approval",
        )
        grant, recipient, approver = _validate_grant(
            approval["grant"], approval["grant_signature"]
        )
        workspace = self._workspaces.get(grant["workspace_id"])
        if workspace is None or workspace["epoch"] != grant["epoch"]:
            raise SyncProtocolError("Device grant targets an unknown workspace")
        if workspace["devices"].get(approver["device_id"]) != approver:
            raise SyncProtocolError("Device grant approver is not registered")
        existing = workspace["devices"].get(recipient["device_id"])
        if existing is not None and existing != recipient:
            raise SyncProtocolError("Registered device public identity changed")
        workspace["devices"][recipient["device_id"]] = copy.deepcopy(recipient)
        pair = {"grant": copy.deepcopy(grant), "signature": copy.deepcopy(approval["grant_signature"])}
        if pair not in workspace["grants"]:
            workspace["grants"].append(pair)

    def grants(self, workspace_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._workspace(workspace_id)["grants"])

    def push(self, envelope: dict[str, Any]) -> dict[str, Any]:
        workspace = self._workspace(envelope.get("workspace_id"))
        envelope = _validated_revision_public(
            envelope,
            workspace["devices"],
            workspace_id=envelope["workspace_id"],
            epoch=workspace["epoch"],
        )
        revisions = workspace["revisions"]
        expected_revision = len(revisions) + 1
        expected_hash = revisions[-1]["object_hash"] if revisions else None
        if envelope["revision"] != expected_revision or envelope["previous_hash"] != expected_hash:
            raise SyncPreconditionError("Cloud sync head changed; pull before retrying")
        revisions.append(copy.deepcopy(envelope))
        return {"revision": envelope["revision"], "object_hash": envelope["object_hash"]}

    def revisions_after(self, workspace_id: str, revision: int) -> list[dict[str, Any]]:
        if type(revision) is not int or revision < 0:
            raise SyncProtocolError("Sync cursor is invalid")
        return copy.deepcopy(self._workspace(workspace_id)["revisions"][revision:])

    def _workspace(self, workspace_id: Any) -> dict[str, Any]:
        if not isinstance(workspace_id, str) or workspace_id not in self._workspaces:
            raise SyncProtocolError("Sync workspace does not exist")
        return self._workspaces[workspace_id]
