---
description: Store a fact in Lians memory with its business event-time and metadata
argument-hint: <fact to remember> [as of YYYY-MM-DD]
---

# /lians-remember

Persist a fact to Lians memory so it survives across this and future sessions.
Lians applies supersession automatically: if the new fact contradicts an existing
one (same ticker+metric, same patient+condition, same matter+claim), the older
fact is marked superseded and disappears from future recall - but stays auditable
via point-in-time queries.

## What to do

1. Parse the fact from: **$ARGUMENTS**
2. Determine the **event_time** - *when the fact became true* (business time), not
   now. If the user wrote "as of <date>", use it. Otherwise default to today and
   say so.
3. Infer structured metadata where obvious, so supersession can key on it:
   - finance → `{"ticker": "...", "metric": "..."}`
   - healthcare → `{"patient_id": "...", "condition": "..."}`
   - legal → `{"matter_id": "...", "claim_type": "..."}`
4. Write it. Prefer the Python SDK if `lians` is importable; otherwise call the
   REST API with the env vars `LIANS_URL` / `LIANS_API_KEY`.

```python
from lians import LiansClient  # or LocalLiansClient for local SQLite
from datetime import datetime, timezone

mem = LiansClient(base_url=os.environ["LIANS_URL"], api_key=os.environ["LIANS_API_KEY"])
mem.add(
    agent_id=os.environ.get("LIANS_AGENT_ID", "claude-session"),
    content="<the fact>",
    event_time=datetime(YYYY, M, D, tzinfo=timezone.utc),
    metadata={...},
)
```

5. Confirm what was stored, the event_time used, and any metadata inferred.

Never invent an event_time precision you don't have - if the user only gave a
date, store the date, not a fabricated timestamp.
