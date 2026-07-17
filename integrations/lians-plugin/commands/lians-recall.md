---
description: Recall current (non-stale) facts from Lians memory, optionally as-of a past date
argument-hint: <what to recall> [as of YYYY-MM-DD]
---

# /lians-recall

Retrieve the facts Lians holds that are relevant to **$ARGUMENTS**. By default you
get the *current* state - superseded/stale revisions are excluded at the database
layer, so you never reason over contaminated context.

## What to do

1. Parse the query and any "as of <date>" clause from **$ARGUMENTS**.
2. Recall:

```python
from lians import LiansClient  # or LocalLiansClient
mem = LiansClient(base_url=os.environ["LIANS_URL"], api_key=os.environ["LIANS_API_KEY"])

# Present state (default)
res = mem.recall(agent_id=os.environ.get("LIANS_AGENT_ID", "claude-session"),
                 query="<query>", k=5)

# Point-in-time - "what did we know on that date?"
res = mem.recall_at(agent_id=..., query="<query>",
                    as_of=datetime(YYYY, M, D, tzinfo=timezone.utc))

for m in res["memories"]:
    print(m["event_time"], m["content"])
```

3. Present the facts with their **event_time** and source so the user can judge
   recency and provenance. If a fact's `content` is `null`, it was crypto-shredded
   after erasure, say so rather than guessing.
4. If nothing is found, say so plainly. Do not fabricate recalled facts.

When the user asks an audit-style question ("what did we know on X", "before the
trade", "before the privilege cutoff"), always use `recall_at` with that date.
Do not make competitor capability claims without current, archived evidence.
