"""Standalone durable side-effect worker."""

from __future__ import annotations

import asyncio


async def _run() -> None:
    from .config import get_settings
    from .db import AsyncSessionLocal
    from .durable_jobs import run_durable_job_worker
    from .job_handlers import default_job_handlers
    from .kms import load_master_key
    from .logging_config import setup_logging

    settings = get_settings()
    setup_logging(level=settings.log_level, json_logs=settings.log_json)
    await load_master_key()
    await run_durable_job_worker(
        AsyncSessionLocal,
        default_job_handlers(),
        poll_seconds=settings.durable_job_poll_seconds,
    )


def main() -> None:
    """Run until interrupted."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
