"""Privacy-safe, user-facing system checks for Lians Bridge."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from . import __version__


def _now() -> str:
    # datetime.UTC is unavailable on the package's supported Python 3.10.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _check(key: str, status: str, title: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "key": key,
        "status": status,
        "title": title,
        "message": message,
        **details,
    }


def system_check(
    store: Any,
    *,
    cloud: Mapping[str, Any],
    integrations: Mapping[str, Any],
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a shareable health report with no prompt or memory content."""

    checks: list[dict[str, Any]] = [
        _check(
            "bridge",
            "ready",
            "Lians Bridge",
            "The private memory service is running on this device.",
        )
    ]

    try:
        memory_health = store.health()
    except Exception:  # noqa: BLE001 - report a safe category, never exception text
        memory_health = {
            "status": "problem",
            "database_integrity": False,
            "foreign_key_integrity": False,
            "encryption_round_trip": False,
            "record_checked": False,
            "record_readable": False,
            "encrypted": True,
            "key_protection": "unknown",
        }
    try:
        memory_stats = store.stats()
    except Exception:  # noqa: BLE001 - content and local paths stay inside the Bridge
        memory_stats = {}

    memory_ready = memory_health.get("status") == "ready"
    record_checked = bool(memory_health.get("record_checked"))
    counts = {
        "active": max(0, int(memory_stats.get("current") or 0)),
        "held_for_review": max(0, int(memory_stats.get("held_for_review") or 0)),
        "paused": max(0, int(memory_stats.get("paused") or 0)),
    }
    checks.append(
        _check(
            "memory",
            "ready" if memory_ready else "problem",
            "Encrypted memory",
            (
                (
                    "Memory is encrypted, and an existing record opened correctly on this device."
                    if record_checked
                    else "The encrypted memory store and protected device key are ready."
                )
                if memory_ready
                else "Local memory needs repair before it can be trusted. Keep the app open and share the safe help report with support."
            ),
            counts=counts,
            protection=str(memory_health.get("key_protection") or "unknown"),
            database_integrity=bool(memory_health.get("database_integrity")),
            encryption_ready=bool(memory_health.get("encryption_round_trip")),
            existing_memory_checked=record_checked,
            existing_memory_readable=(
                bool(memory_health.get("record_readable")) if record_checked else None
            ),
        )
    )

    configured = sorted(
        str(target.label)
        for target in integrations.values()
        if bool(getattr(target, "configured", False))
    )
    detected = sum(bool(getattr(target, "detected", False)) for target in integrations.values())
    if len(configured) >= 2:
        integration_message = (
            f"{len(configured)} AI tools are connected. Cross-tool memory is ready."
        )
        integration_status = "ready"
    elif len(configured) == 1:
        integration_message = f"{configured[0]} is connected. Connect one more AI tool to experience cross-tool memory."
        integration_status = "attention"
    elif detected:
        integration_message = (
            "Lians found an AI tool, but none is connected yet. Reopen Lians Setup to connect it."
        )
        integration_status = "attention"
    else:
        integration_message = "No supported AI tools were found yet. Install or open an AI tool, then reopen Lians Setup."
        integration_status = "attention"
    checks.append(
        _check(
            "integrations",
            integration_status,
            "Connected AI tools",
            integration_message,
            connected_count=len(configured),
            detected_count=detected,
            connected=configured,
        )
    )

    cloud_state = str(cloud.get("state") or "unavailable")
    sync_state = str(cloud.get("sync_state") or "not_started")
    retry = cloud.get("sync_retry") if isinstance(cloud.get("sync_retry"), Mapping) else {}
    retry_active = bool(retry.get("active"))
    cloud_problem = cloud_state == "needs_attention" or sync_state == "invalid" or retry_active
    cloud_connected = cloud_state in {"connected", "current", "synced", "refresh_required"}
    if cloud_problem:
        cloud_status = "attention"
        cloud_message = (
            "Cloud continuity needs attention, but local memory is still working on this device."
        )
    elif cloud_connected and sync_state == "ready":
        cloud_status = "ready"
        cloud_message = "Encrypted cloud continuity is connected and ready."
    else:
        cloud_status = "ready"
        cloud_message = "Local memory is ready. Cloud continuity is optional and is not active."
    checks.append(
        _check(
            "cloud",
            cloud_status,
            "Memory continuity",
            cloud_message,
            mode="encrypted-cloud" if cloud_connected else "local-only",
            sync_state=sync_state,
        )
    )

    held = counts["held_for_review"]
    checks.append(
        _check(
            "review",
            "attention" if held else "ready",
            "Trust Review",
            (
                f"{held} {('memory is' if held == 1 else 'memories are')} held from AI until you decide. Close this check and open Review to choose what happens."
                if held
                else "No memory decision is waiting before the next AI chat."
            ),
            waiting=held,
        )
    )

    statuses = {item["status"] for item in checks}
    overall = (
        "problem" if "problem" in statuses else "attention" if "attention" in statuses else "ready"
    )
    summary = {
        "ready": "Lians is ready for your next AI chat.",
        "attention": "Lians is working, with a setup or review action recommended.",
        "problem": "Lians found a local problem that should be repaired before relying on memory.",
    }[overall]
    return {
        "schema": "lians-system-check/v1",
        "generated_at": generated_at or _now(),
        "lians_version": __version__,
        "overall": overall,
        "summary": summary,
        "checks": checks,
        "privacy": {
            "prompt_content_included": False,
            "memory_content_included": False,
            "credentials_included": False,
            "account_identifiers_included": False,
            "local_paths_included": False,
            "key_material_included": False,
        },
    }
