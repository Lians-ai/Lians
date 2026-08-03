"""Run Anthropic with privacy-safe Lians API-boundary evidence.

Requires a running Lians API and::

    pip install 'lians-sdk[anthropic]'

Set ``LIANS_API_KEY`` and ``ANTHROPIC_API_KEY``. ``LIANS_API_URL`` defaults to
``http://localhost:8000``. Production deployments should also set a secret
``LIANS_RECORDER_COMMITMENT_KEY`` of at least 32 bytes.

This example records the Messages API request commitment and HTTP outcome. It
does not claim response-body or local tool-execution coverage; see
``docs/recorder-native-hooks.md`` for the exact boundary.
"""

from __future__ import annotations

import asyncio
import os

from anthropic import AsyncAnthropic
from lians import (
    AsyncLiansClient,
    AsyncRecorderSink,
    RecorderAttribution,
    build_anthropic_recorder_middleware,
)


async def main() -> None:
    lians_client = AsyncLiansClient(
        base_url=os.environ.get("LIANS_API_URL", "http://localhost:8000"),
        api_key=os.environ["LIANS_API_KEY"],
    )
    recorder = AsyncRecorderSink(
        lians_client,
        commitment_key=os.environ.get("LIANS_RECORDER_COMMITMENT_KEY"),
    )
    async with lians_client, recorder:
        middleware = build_anthropic_recorder_middleware(
            recorder,
            attribution=RecorderAttribution(
                claimed_agent_id="anthropic-api-quickstart",
                capture_mode="hash_only",
            ),
        )
        async with AsyncAnthropic(middleware=[middleware]) as claude:
            response = await claude.messages.create(
                model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                max_tokens=64,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with the single word recorded.",
                    }
                ],
            )
        await middleware.aflush(timeout=15)

        gaps = recorder.capture_gaps()
        if gaps:
            raise RuntimeError(f"Recorder reported {len(gaps)} capture gap(s)")
        # Do not print generated content in an observability quickstart.
        print(
            {
                "provider_model": response.model,
                "provider_stop_reason": response.stop_reason,
                "recorder": recorder.stats(),
            }
        )


if __name__ == "__main__":
    asyncio.run(main())
