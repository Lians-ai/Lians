---
description: Wire Lians memory into the current repository with a test-driven, minimal-diff integration
argument-hint: [framework: langchain | langgraph | crewai | openai-agents | autogen | raw]
---

# /lians-integrate

Integrate Lians as the memory layer for the agent in **this** repository. Work
test-first and keep the diff minimal - you are adding a memory layer, not
rewriting the app.

## Procedure

1. **Survey.** Identify the agent loop: where the model is called, where context
   is assembled, and where turns complete. Detect the framework (or honor the one
   named in **$ARGUMENTS**). If none, use the raw harness.

2. **Branch.** Create a feature branch `lians-integration` before editing.

3. **Add the dependency.** Choose the matching extra:
   - `pip install lians-sdk[langchain]` / `[langgraph]` / `[crewai]` /
     `[openai-agents]` / `[autogen]`
   - raw / unknown framework → `pip install lians-sdk` and use the harness.

4. **Wire it.** Prefer the harness for raw loops:
   ```python
   from lians import LiansClient, LiansMemoryHarness
   harness = LiansMemoryHarness(
       LiansClient(base_url=os.environ["LIANS_URL"], api_key=os.environ["LIANS_API_KEY"]),
       agent_id="<this-agent>",
       domain="finance",          # or healthcare / legal
   )
   # before model call:  context = harness.recall_context(user_query)
   # after model call:    harness.remember(response)
   # or both at once:     answer = harness.run_turn(user_query, generate=call_model)
   ```
   For a framework, import its integration module instead
   (`from lians.langchain_integration import build_tools`, etc.).

5. **Test.** Add a test that proves recall-before/remember-after works and that
   a superseding write hides the stale fact. Use `LocalLiansClient` so the test
   needs no server or API key. Run the suite.

6. **Report.** Summarize the diff, the env vars required
   (`LIANS_URL`, `LIANS_API_KEY`, `LIANS_AGENT_ID`), and how to verify locally.

Do not commit. Leave the branch for the user to review. If tests fail, stop and
report - do not mark the integration done.
