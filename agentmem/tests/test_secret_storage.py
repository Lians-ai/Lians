from datetime import datetime, timezone

import pytest
from cryptography.exceptions import InvalidTag

from lians.models import PendingAdmission, WebhookEndpoint
from lians.secret_storage import (
    PENDING_CONTENT_PURPOSE,
    WEBHOOK_SIGNING_PURPOSE,
    protect_legacy_sensitive_rows,
    seal_text,
    unseal_text,
)


def test_sealed_text_is_context_bound_and_tamper_evident():
    token = seal_text(
        "sensitive value",
        purpose=WEBHOOK_SIGNING_PURPOSE,
        context="tenant-a",
    )
    assert "sensitive value" not in token
    assert unseal_text(
        token,
        purpose=WEBHOOK_SIGNING_PURPOSE,
        context="tenant-a",
    ) == "sensitive value"

    with pytest.raises(InvalidTag):
        unseal_text(
            token,
            purpose=WEBHOOK_SIGNING_PURPOSE,
            context="tenant-b",
        )
    with pytest.raises(InvalidTag):
        unseal_text(
            token,
            purpose=PENDING_CONTENT_PURPOSE,
            context="tenant-a",
        )


@pytest.mark.asyncio
async def test_legacy_plaintext_rows_are_encrypted_idempotently(db):
    pending = PendingAdmission(
        namespace="legacy-ns",
        agent_id="agent",
        content="legacy patient secret",
        event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        metadata_={},
        risk_tags=[],
        reasons=[],
        status="pending",
    )
    webhook = WebhookEndpoint(
        namespace="legacy-ns",
        url="https://hooks.example.com/lians",
        secret="legacy signing secret",
        events=["memory.conflict"],
    )
    db.add_all([pending, webhook])
    await db.commit()

    # A page of one proves the compatibility pass commits and resumes instead
    # of materializing both tables at once.
    assert await protect_legacy_sensitive_rows(db, batch_size=1) == 2
    assert "legacy patient secret" not in pending.content
    assert "legacy signing secret" not in webhook.secret
    assert unseal_text(
        pending.content,
        purpose=PENDING_CONTENT_PURPOSE,
        context="legacy-ns",
    ) == "legacy patient secret"
    assert unseal_text(
        webhook.secret,
        purpose=WEBHOOK_SIGNING_PURPOSE,
        context="legacy-ns",
    ) == "legacy signing secret"

    assert await protect_legacy_sensitive_rows(db) == 0
