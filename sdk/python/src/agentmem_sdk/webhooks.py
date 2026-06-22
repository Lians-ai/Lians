"""
AgentMem Python SDK — webhook signature verification.

Every AgentMem webhook delivery is HMAC-SHA256-signed.
The signature is in the ``X-AgentMem-Signature`` header as ``sha256=<hex>``.

Usage::

    from agentmem_sdk.webhooks import verify_webhook_signature, parse_webhook_payload

    @app.post("/webhook")
    async def handle(request: Request):
        body = await request.body()
        header = request.headers.get("X-AgentMem-Signature", "")
        if not verify_webhook_signature(body, header, WEBHOOK_SECRET):
            raise HTTPException(status_code=401)
        payload = parse_webhook_payload(body, header, WEBHOOK_SECRET)
        ...
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def verify_webhook_signature(body: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify the HMAC-SHA256 signature on an AgentMem webhook delivery.

    Args:
        body:              Raw request body bytes.
        signature_header:  Value of the ``X-AgentMem-Signature`` header,
                           e.g. ``sha256=abc123...``
        secret:            The webhook secret returned at endpoint registration.

    Returns:
        True if the signature is valid, False otherwise.

    The comparison is constant-time to prevent timing attacks.
    """
    if not signature_header.startswith("sha256="):
        return False
    received = signature_header[len("sha256="):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


def parse_webhook_payload(
    body: bytes,
    signature_header: str,
    secret: str,
) -> dict[str, Any]:
    """
    Verify signature and parse webhook payload as a dict.

    Raises:
        ValueError: if signature verification fails.
        json.JSONDecodeError: if the body is not valid JSON.
    """
    if not verify_webhook_signature(body, signature_header, secret):
        raise ValueError(
            "Webhook signature verification failed. "
            "Ensure the secret matches the one returned at registration."
        )
    return json.loads(body)
