# Lians Bridge (technical preview)

Lians Bridge is the local service behind the guided Lians installer. It keeps
one encrypted memory store for supported AI clients and exposes a small MCP
surface, automatic bounded recall, project handoffs, and signed context
receipts. The same package now carries the React Lians App for Memory, Activity,
Review, Integrations, and Settings; normal users do not install or locate a
separate web bundle.

The current desktop artifacts are development builds and are not yet published
with trusted operating-system signatures. Context receipts are Ed25519-signed,
but that does not replace Windows Authenticode or Apple Developer ID signing and
notarization. Until a signed release is published, developers and IT teams
should evaluate the Bridge from source:

```bash
python -m lians_easy install --clients antigravity,claude,cursor,gemini,codex --yes
python -m lians_easy doctor --json
python -m lians_easy app
```

## Developer package

The public Python distribution is named `lians-bridge`. It is built as a
platform-independent wheel and carries the same encrypted runtime and bundled
Lians App as the desktop package. After its first verified PyPI release,
developers will be able to install it without cloning this repository:

```bash
pipx install lians-bridge
# or: uv tool install lians-bridge

lians doctor --json
lians install --clients detected --plan --json
lians install --clients detected --yes --json
lians app
```

For a disposable diagnostic after publication:

```bash
uvx --from lians-bridge lians doctor --json
```

`lians-bridge` and the older `lians-easy` executable are compatibility aliases
for `lians`. The distribution is not on PyPI yet, so these commands are a
release contract rather than a claim that the package can already be
downloaded. The gated publication workflow builds and exercises the exact
wheel, verifies that its version matches an immutable stable tag, and uses PyPI
trusted publishing without a stored API token.

Before removing a `pipx` or `uv` installation, disconnect the managed client
entries while the command still exists. This preserves encrypted memory:

```bash
lians uninstall --clients all --yes --json
pipx uninstall lians-bridge
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

The macOS workflow builds separate native Apple-silicon and Intel
`Lians-<version>-macos-<architecture>.dmg` images. Each has a conventional
**Lians.app -> Applications** drag-and-drop layout and is mounted, copied, and
exercised on its matching macOS runner. Pull-request DMGs are ad-hoc-signed test
fixtures. A stable asset requires the exact Lians Developer ID identity, Apple
notarization, a stapled ticket, Gatekeeper acceptance, and a checksum. The app
now exposes separate, confirmed controls for disconnecting AI clients while
keeping memory and for erasing memory while leaving integrations ready. Moving
an app to Trash does not silently erase memory. macOS remains a technical
preview until the real publisher credentials and clean-Mac usability gate pass.

**Check for updates** is user-initiated in this preview. The Bridge recognizes
only stable releases on the official Lians GitHub repository and offers an
architecture-specific Windows or macOS package only when its checksum is also
published. It opens the official release for review and never downloads or
executes an installer in the background.

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
