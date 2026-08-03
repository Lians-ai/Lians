"""Under-15-minute native Recorder callback quickstart (LangChain/LangGraph).

Requires a running Lians API plus::

    pip install 'lians-sdk[langchain]'

Set LIANS_API_URL and LIANS_API_KEY, then run this file.  The sample Runnable
is intentionally local; replace it with an existing chain or compiled graph.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from langchain_core.runnables import RunnableLambda
from lians import (
    AsyncLiansClient,
    AsyncRecorderSink,
    RecorderAttribution,
    RecorderSinkConfig,
    build_langchain_recorder_handler,
)


async def main() -> None:
    client = AsyncLiansClient(
        base_url=os.environ.get("LIANS_API_URL", "http://localhost:8000"),
        api_key=os.environ["LIANS_API_KEY"],
    )
    sink = AsyncRecorderSink(
        client,
        config=RecorderSinkConfig(
            max_buffered_events=2_048,
            backpressure="block",
            delivery_failure="halt",
        ),
        # Set this to a deployment secret of at least 32 bytes in production.
        # Omitting it keeps interoperable SHA-256, which is not hiding for
        # guessable low-entropy callback values.
        commitment_key=os.environ.get("LIANS_RECORDER_COMMITMENT_KEY"),
    )
    attribution = RecorderAttribution(
        claimed_agent_id="quickstart-reviewer",
        # Actor labels are caller claims. The API key authenticates ingestion.
        capture_mode="hash_only",
    )

    async def review(value: dict[str, object]) -> dict[str, object]:
        return {"approved": bool(value.get("synthetic")), "policy": "review-v1"}

    runnable = RunnableLambda(review)
    async with client, sink:
        handler = build_langchain_recorder_handler(
            sink,
            attribution=attribution,
            max_active_runs=10_000,
        )
        result = await runnable.ainvoke(
            {"synthetic": True},
            config={
                "callbacks": [handler],
                "metadata": {"thread_id": f"demo-{uuid4()}"},
            },
        )
        await sink.flush(timeout=15)

        gaps = sink.capture_gaps()
        if gaps:
            raise RuntimeError(f"Recorder reported {len(gaps)} capture gap(s)")
        print({"result": result, "recorder": sink.stats()})


if __name__ == "__main__":
    asyncio.run(main())
