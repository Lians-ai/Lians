"""Envelope-style authenticated encryption for sensitive application strings."""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .kms import get_master_key

_PREFIX = "lians-sealed:v1:"
PENDING_CONTENT_PURPOSE = "pending-admission-content"
WEBHOOK_SIGNING_PURPOSE = "webhook-hmac-secret"


def _purpose_key(purpose: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"lians/{purpose}/v1".encode("utf-8"),
    ).derive(get_master_key())


def seal_text(plaintext: str, *, purpose: str, context: str) -> str:
    """Encrypt a string with a purpose-separated key and context-bound AAD."""
    nonce = os.urandom(12)
    ciphertext = AESGCM(_purpose_key(purpose)).encrypt(
        nonce,
        plaintext.encode("utf-8"),
        context.encode("utf-8"),
    )
    token = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
    return _PREFIX + token


def unseal_text(value: str, *, purpose: str, context: str) -> str:
    """Decrypt a sealed string; accept legacy plaintext for rolling upgrades."""
    if not value.startswith(_PREFIX):
        return value
    packed = base64.urlsafe_b64decode(value[len(_PREFIX):].encode("ascii"))
    nonce, ciphertext = packed[:12], packed[12:]
    return AESGCM(_purpose_key(purpose)).decrypt(
        nonce,
        ciphertext,
        context.encode("utf-8"),
    ).decode("utf-8")


async def protect_legacy_sensitive_rows(db: AsyncSession) -> int:
    """Seal plaintext rows left by releases that predated encrypted storage."""
    from .models import PendingAdmission, WebhookEndpoint

    changed = 0
    pending_rows = (
        await db.execute(select(PendingAdmission))
    ).scalars().all()
    for row in pending_rows:
        if not row.content.startswith(_PREFIX):
            row.content = seal_text(
                row.content,
                purpose=PENDING_CONTENT_PURPOSE,
                context=row.namespace,
            )
            changed += 1

    webhook_rows = (
        await db.execute(select(WebhookEndpoint))
    ).scalars().all()
    for row in webhook_rows:
        if not row.secret.startswith(_PREFIX):
            row.secret = seal_text(
                row.secret,
                purpose=WEBHOOK_SIGNING_PURPOSE,
                context=row.namespace,
            )
            changed += 1

    if changed:
        await db.commit()
    return changed
