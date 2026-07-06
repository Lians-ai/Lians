"""
Signed human-readable Markdown export.

The memory statement renders the exhaustive point-in-time knowledge state as
Markdown with YAML frontmatter; its SHA-256 is anchored in the tamper-evident
audit chain (content_hash of an export_markdown event), so the document is
verifiable offline (hash) and online (chain).
"""
from __future__ import annotations

import hashlib
import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from src.lians.models import EventLog
from src.lians.schemas import MemoryAdd
from src.lians.memory_service import add_memory, erase_subject
from src.lians.export_markdown import (
    export_memory_markdown,
    strip_integrity_footer,
    verify_export_document,
)
from src.lians.audit_chain import verify_chain

NS = "export-ns"
AGENT = "export-agent"


async def _seed(db):
    now = datetime.now(timezone.utc)
    await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="client mandate: no tobacco exposure in any account",
        event_time=now - timedelta(days=40),
        source="onboarding-call",
        metadata={"materiality": "critical"},
    ))
    await add_memory(db, NS, MemoryAdd(
        agent_id=AGENT,
        content="client mentioned preferring email over phone",
        event_time=now - timedelta(days=10),
        source="crm-note",
        subject_id="client-42",
    ))


@pytest.mark.asyncio
async def test_export_renders_frontmatter_and_facts(db):
    await _seed(db)

    result = await export_memory_markdown(db, NS, AGENT)

    assert result.memory_count == 2
    assert result.markdown.startswith("---\nformat: lians-memory-export/v1")
    assert f"namespace: {NS}" in result.markdown
    assert "no tobacco exposure" in result.markdown
    assert "materiality: critical" in result.markdown
    assert "[onboarding-call]" not in result.markdown  # sources render as headings
    assert "— onboarding-call" in result.markdown


@pytest.mark.asyncio
async def test_document_hash_is_self_verifiable(db):
    await _seed(db)

    result = await export_memory_markdown(db, NS, AGENT)

    recomputed, stated = verify_export_document(result.markdown)
    assert stated == result.document_sha256
    assert recomputed == stated

    body = strip_integrity_footer(result.markdown)
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == result.document_sha256


@pytest.mark.asyncio
async def test_tampered_document_fails_verification(db):
    await _seed(db)
    result = await export_memory_markdown(db, NS, AGENT)

    tampered = result.markdown.replace("no tobacco exposure", "tobacco allowed")
    recomputed, stated = verify_export_document(tampered)
    assert recomputed != stated


@pytest.mark.asyncio
async def test_export_is_anchored_in_the_audit_chain(db):
    await _seed(db)
    result = await export_memory_markdown(db, NS, AGENT)

    rows = (await db.execute(
        select(EventLog).where(EventLog.namespace == NS, EventLog.op == "export_markdown")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].content_hash == result.document_sha256
    assert rows[0].row_hash == result.audit_row_hash
    assert rows[0].id == result.audit_event_id

    report = await verify_chain(db, NS)
    assert report["status"] == "ok"


@pytest.mark.asyncio
async def test_erased_facts_render_as_erasure_markers(db):
    await _seed(db)
    await erase_subject(db, NS, "client-42", request_ref="dsar-001")

    result = await export_memory_markdown(db, NS, AGENT)

    assert result.memory_count == 2  # existence preserved
    assert "ERASED — content crypto-shredded" in result.markdown
    assert "preferring email" not in result.markdown  # content unrecoverable


@pytest.mark.asyncio
async def test_point_in_time_export_excludes_later_facts(db):
    await _seed(db)
    checkpoint = datetime.now(timezone.utc) - timedelta(days=20)

    result = await export_memory_markdown(db, NS, AGENT, as_of=checkpoint)

    assert result.memory_count == 1  # only the 40-day-old mandate existed then
    assert "no tobacco exposure" in result.markdown
    assert "preferring email" not in result.markdown
