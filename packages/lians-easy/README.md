# Lians Bridge (technical preview)

Lians Bridge is the local service behind the guided Lians installer. It keeps
one encrypted memory store for supported AI clients and exposes a small MCP
surface, automatic bounded recall, project handoffs, and signed context
receipts. The same package now carries the React Lians App for Memory, Activity,
Review, Integrations, and Settings; normal users do not install or locate a
separate web bundle.

The current Windows executable is a development build and is not yet
Authenticode-signed. Context receipts are Ed25519-signed, but that does not
replace operating-system code signing. Until a signed release is published,
developers and IT teams should evaluate the Bridge from source:

```bash
python -m lians_easy install --clients antigravity,claude,cursor,gemini,codex --yes
python -m lians_easy doctor --json
python -m lians_easy app
```

Open the executable without arguments for guided setup. Its final action opens
the bundled control center through the loopback-only Bridge. Once at least one
AI client is connected, later launches return to that control center directly.

The Windows build workflow also wraps the frozen runtime in a per-user
`Lians-Setup-<version>.exe`. It creates Start-menu entries, does not request
administrator access, and is exercised through install, launch, and uninstall
on a fresh runner. Silent removal preserves encrypted memory; interactive
removal asks separately before erasure. Pull-request installers are unsigned
technical fixtures until the publisher-gated release job signs both the runtime
and setup executable.

No Lians account, API key, database server, model download, or manual JSON
editing is required. The local store uses AES-GCM; on Windows its root key is
protected with DPAPI. The full Lians engine remains available when a team needs
semantic retrieval, collaboration, governance, or a shared server deployment.

Antigravity, Claude, Codex, and Gemini CLI receive bounded context through
prompt hooks. Gemini CLI uses `BeforeAgent`; Google's current Antigravity client
uses a first-invocation `PreInvocation` hook and an ephemeral context step, so
the memory is not appended again on every model call in the same agent loop.
Antigravity CLI users should mount the active repository with
`agy --add-dir /path/to/project ...`; when Antigravity reports no workspace,
Lians safely injects global preferences only and excludes project memories.
Cursor receives the same MCP tools and a generated project rule because current
Cursor hooks do not provide a reliable dynamic prompt-injection contract. Every
context pack records which memories appeared, why they appeared, what was
excluded, and the estimated token cost.

Run the deterministic token-budget benchmark from the repository root:

```bash
PYTHONPATH=packages/lians-easy python packages/lians-easy/benchmarks/token_reduction.py
```

The checked fixture saves 63 memories and currently estimates 2,159 tokens for
full-catalog replay versus 210 for the three-memory Lians context pack, a 90.3%
reduction. This is a reproducible character-based estimate, not a claim about a
provider's billed tokenizer or production workload.
