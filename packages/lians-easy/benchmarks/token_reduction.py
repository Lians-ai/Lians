"""Reproduce the bounded-context token comparison used by Lians Bridge."""

from __future__ import annotations

import gc
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lians_easy.project import Project
from lians_easy.store import MemoryStore


def estimate_tokens(value: str) -> int:
    """Use the same documented conservative estimate as the Bridge receipt."""
    return max(1, (len(value) + 3) // 4)


def main() -> None:
    with TemporaryDirectory(prefix="lians-token-benchmark-") as directory:
        root = Path(directory)
        project = Project(
            id="project-fastapi",
            name="FastAPI app",
            root=str(root),
            origin="github.com/example/fastapi-app",
        )
        store = MemoryStore(root / "memory.sqlite3")

        store.remember(
            "Do not use em dashes in anything written for me.",
            kind="preference",
            scope="global",
            source="explicit user instruction",
            source_client="cursor",
        )
        store.remember(
            "We use FastAPI and never write migrations manually.",
            kind="preference",
            scope="project",
            project_id=project.id,
            source="explicit user instruction",
            source_client="cursor",
        )
        store.remember(
            "The prior task completed schemas; next create routes and migration checks.",
            kind="handoff",
            scope="project",
            project_id=project.id,
            source="task handoff",
            source_client="cursor",
        )
        for index in range(60):
            store.remember(
                f"Archived decision {index:02d}: stakeholder feedback selected compact "
                "navigation, confirmed ownership, recorded review state, captured delivery notes.",
                kind="project",
                scope="project",
                project_id=project.id,
                source="archived session",
                source_client="cursor",
            )

        catalog = "\n".join(item["content"] or "" for item in store.list(limit=200))
        pack = store.context_pack(
            "Continue FastAPI API work; prepare migration checks.",
            project=project,
            client="codex",
            limit=3,
            max_tokens=512,
        )
        replay_tokens = estimate_tokens(catalog)
        recalled_tokens = pack["receipt"]["token_estimate"]
        reduction = round((1 - recalled_tokens / replay_tokens) * 100, 1)
        result = {
            "saved_memories": len(store.list(limit=200)),
            "full_catalog_token_estimate": replay_tokens,
            "lians_context_token_estimate": recalled_tokens,
            "memories_used": pack["receipt"]["memory_count"],
            "estimated_token_reduction_percent": reduction,
            "receipt": pack["receipt_line"],
        }
        if recalled_tokens > 512 or result["memories_used"] != 3 or reduction < 80:
            raise SystemExit(f"Token-budget benchmark failed: {json.dumps(result)}")
        print(json.dumps(result, indent=2))
        # Release transient sqlite handles before Windows removes the fixture.
        del store
        gc.collect()


if __name__ == "__main__":
    main()
