"""
Signed human-readable memory export.

Renders an agent's complete point-in-time knowledge state (the same exhaustive
set /v1/snapshot returns) as a Markdown document with YAML frontmatter — the
transparency developers love about file-based memory systems, produced from a
store that keeps encryption, barriers, and bitemporal correctness underneath.

The document is anchored in the tamper-evident audit chain: its SHA-256 is
written as the ``content_hash`` of an ``export_markdown`` event, so the hash is
part of the chain's canonical fields and any later edit to the document (or to
the event) is detectable by ``verify_chain``. A footer states the hash, the
anchoring event, and the verification procedure.

Verification procedure (also stated in the footer):
  1. Remove the final integrity comment block (everything from the line
     ``<!-- lians:integrity`` to the end of the file).
  2. SHA-256 the remaining bytes (UTF-8) — must equal ``document_sha256``.
  3. Confirm the audit chain holds an ``export_markdown`` event whose
     ``content_hash`` equals ``document_sha256`` and whose ``row_hash`` matches.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .audit_chain import chain_log
from .schemas import MarkdownExportResult, MemoryOut

_FORMAT = "lians-memory-export/v1"
_INTEGRITY_MARK = "<!-- lians:integrity"


def _stamp(dt: Optional[datetime]) -> str:
    if dt is None:
        return "null"
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _render_memory(m: MemoryOut) -> list[str]:
    lines = [f"## {_stamp(m.event_time)}" + (f" — {m.source}" if m.source else "")]
    if m.content is not None:
        lines.append("")
        lines.append(m.content)
    elif m.erased_at is not None:
        lines.append("")
        lines.append("*[ERASED — content crypto-shredded; existence and metadata preserved]*")
    else:
        lines.append("")
        lines.append("*[content unavailable — subject key not accessible]*")
    lines.append("")
    lines.append(f"- id: `{m.id}`")
    lines.append(f"- content_hash: `{m.content_hash}`")
    valid_to = _stamp(m.valid_to) if m.valid_to else "(open)"
    lines.append(f"- valid: {_stamp(m.valid_from)} → {valid_to}")
    if m.subject_id:
        lines.append(f"- subject: `{m.subject_id}`")
    if m.barrier_group:
        lines.append(f"- barrier_group: `{m.barrier_group}`")
    materiality = (m.metadata or {}).get("materiality")
    if materiality:
        lines.append(f"- materiality: {materiality}")
    if m.superseded_by:
        lines.append(f"- superseded_by: `{m.superseded_by}`")
    if m.erased_at:
        lines.append(f"- erased_at: {_stamp(m.erased_at)}")
    lines.append("")
    return lines


async def export_memory_markdown(
    db: AsyncSession,
    namespace: str,
    agent_id: str,
    as_of: Optional[datetime] = None,
    limit: int = 1000,
) -> MarkdownExportResult:
    """Render, hash, chain-anchor, and return the memory statement."""
    from .memory_service import get_knowledge_snapshot

    generated_at = datetime.now(timezone.utc)
    effective_as_of = as_of or generated_at
    items = await get_knowledge_snapshot(db, namespace, agent_id, effective_as_of, limit)

    lines: list[str] = [
        "---",
        f"format: {_FORMAT}",
        f"namespace: {namespace}",
        f"agent_id: {agent_id}",
        f"as_of: {_stamp(effective_as_of)}",
        f"generated_at: {_stamp(generated_at)}",
        f"memory_count: {len(items)}",
        "---",
        "",
        f"# Memory statement — `{agent_id}` as of {_stamp(effective_as_of)}",
        "",
        f"Every fact valid at the stated time, oldest first ({len(items)} total). "
        "Erased facts appear as erasure markers — existence is preserved, content is unrecoverable.",
        "",
    ]
    for m in items:
        lines.extend(_render_memory(m))

    body = "\n".join(lines)
    document_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()

    event = await chain_log(
        db, namespace=namespace, agent_id=agent_id,
        op="export_markdown",
        content_hash=document_sha256,
        payload={
            "format": _FORMAT,
            "as_of": _stamp(effective_as_of),
            "memory_count": len(items),
        },
    )
    await db.commit()

    footer = "\n".join([
        "",
        _INTEGRITY_MARK,
        f"document_sha256: {document_sha256}",
        f"audit_event_id: {event.id}",
        f"audit_row_hash: {event.row_hash}",
        "verify: remove this comment block (from the '" + _INTEGRITY_MARK + "' line to EOF),",
        "SHA-256 the remaining UTF-8 bytes, compare with document_sha256, then confirm the",
        "audit chain holds an export_markdown event with this content_hash and row_hash.",
        "-->",
        "",
    ])

    return MarkdownExportResult(
        markdown=body + footer,
        document_sha256=document_sha256,
        audit_event_id=event.id,
        audit_row_hash=event.row_hash or "",
        namespace=namespace,
        agent_id=agent_id,
        as_of=effective_as_of,
        generated_at=generated_at,
        memory_count=len(items),
    )


def strip_integrity_footer(markdown: str) -> str:
    """Return the hashable body — everything before the integrity comment."""
    idx = markdown.rfind("\n" + _INTEGRITY_MARK)
    return markdown[:idx] if idx != -1 else markdown


def verify_export_document(markdown: str) -> tuple[str, Optional[str]]:
    """
    Recompute the document hash and read the stated one from the footer.

    Returns ``(recomputed_sha256, stated_sha256)`` — equal for an untampered
    document. Chain-side confirmation still requires the audit log.
    """
    body = strip_integrity_footer(markdown)
    recomputed = hashlib.sha256(body.encode("utf-8")).hexdigest()
    stated: Optional[str] = None
    for line in markdown.splitlines():
        if line.startswith("document_sha256:"):
            stated = line.split(":", 1)[1].strip()
    return recomputed, stated
