# PydanticAI + Lians memory

This example gives a PydanticAI agent current and point-in-time memory through
two explicit Lians tools. It runs locally with SQLite and PydanticAI's
deterministic `TestModel`, so it needs no LLM key, Lians API key, server, or
Docker.

Tested with `pydantic-ai-slim==2.29.0`. The slim package is PydanticAI's
provider-free distribution; it includes the agent and test-model APIs used
here without installing an external model provider.

From the repository root:

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e "agentmem/sdk/python[local]" pydantic-ai-slim==2.29.0
python agentmem/examples/pydantic_ai_memory/main.py
python agentmem/examples/pydantic_ai_memory/verify.py
```

The script stores an original shipping estimate and a later revision. The
first PydanticAI tool returns only the current estimate. The second calls
`recall_at` for a time before the revision and returns only the original
estimate. Replace `TestModel` with your normal PydanticAI model to use the same
tools in a live agent.
