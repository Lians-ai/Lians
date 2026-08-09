# Lians Memory universal submission bundle

This directory is the portal-review package for the OpenAI universal plugin directory. It is not the locally installed Codex plugin in `plugins/lians-memory` and is not intended for `codex plugin add`.

The uploaded `lians-memory` skill declares its hosted Streamable HTTP dependency in `skills/lians-memory/agents/openai.yaml`. The OpenAI submission flow binds that dependency to the canonical OAuth-protected endpoint at `https://mcp.lians.ai/mcp`; no local command, bundled runtime, or static bearer credential belongs in this package.

The endpoint is currently marked planned and not live. Do not submit this bundle until every go/no-go item in `docs/openai-universal-plugin-production.md` is complete and `submission/metadata.json` no longer contains a pending production gate.
