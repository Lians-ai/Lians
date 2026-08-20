# Lians Guard (developer preview)

**Recover the task. Reject stale state. Block unsupported done.**

Lians Guard is the local current-state and completion guard for AI coding
agents. It recovers interrupted work, checks whether a saved checkpoint still
matches the repository, and keeps `done` behind an evidence-backed human-review
gate. It connects through supported MCP, hook, and rule surfaces without
replacing Claude Code, Codex, Git, CI, or the user's editor. Users do not give
Lians their AI account credentials or provider API keys.

Free local memory remains the recovery wedge. A connected agent can resume a
bounded current task without replaying a full transcript. The broader Guard
workflow adds task contracts, typed evidence, workspace fingerprints, stale
state, and an explicit readiness gate.

Positive evidence has one of three trusted classes: `measured_local`,
`measured_ci`, or `human_confirmed`. Agent summaries use `agent_attested`, and
file activity uses `inferred_activity`; both remain useful recovery context but
cannot satisfy a completion criterion. Agent-facing tools cannot promote their
own evidence to a trusted class. Trusted local evidence comes from a Lians-owned
verifier, attested CI evidence must match the exact workflow and commit, and
its check-to-criterion mapping must be interactively authorized. Human
confirmation is also interactive.

The user-facing state is deliberately small:

- `RECOVERED`: the bounded current task was restored;
- `STALE`: the saved checkpoint no longer matches current workspace state;
- `BLOCKED`: a criterion, constraint, or dependency prevents review; and
- `READY FOR HUMAN REVIEW`: the configured evidence gate passed and a person
  must review the work.

The first supported path centers on Claude Code, Codex, local Git, and GitHub
Actions. A long-running task carries an encrypted continuity contract with its
goal, success criteria, checkpoint, evidence, constraints, decisions, questions,
next action, sources, and blockers. Another supported agent can continue from a
bounded signed brief without rereading the transcript.

A related reliability problem is stale working state. Agents can declare which memories, files,
tests, documents, analyses, and outputs depend on a current fact or decision.
When that state changes, Lians blocks invalidated memories from normal recall,
shows the transitive blast radius in the Work Graph, and supplies a bounded
repair brief containing the verified replacement plus only the affected work.
Unrelated work remains untouched. Dependency references, labels, reasons, and
repair evidence stay encrypted locally.

The next reliability problem is unverified completion. For repository work,
Lians can bind a task contract to approved paths, map each changed file to a
success criterion, scan
the real Git diff for scope violations, whitespace errors, credential patterns,
and selected risky constructs, then combine that with task evidence and current
state. The result is an encrypted, Ed25519-signed verification receipt tied to
the base commit and exact diff hash. It never runs arbitrary project commands;
test evidence supplied by an agent is explicitly caller-attested. A clean
receipt means ready for human review, not proven correctness, autonomous
approval, merge approval, or deployment safety.

Advanced state graphs, control modes, research tools, video ingestion, temporal
reconstruction, and bounded proof backends remain in the repository behind
progressive disclosure. They are not the launch story or requirements for the
Guard activation loop.

The ordinary product surface remains intentionally small:

1. Connect Lians Guard to a supported AI coding app.
2. Start a task contract with a goal, success criteria, and constraints.
3. Record checkpoints with typed evidence and a local workspace fingerprint.
4. Resume with `lians continue`, repair any `STALE` or `BLOCKED` state, and
   review the underlying work when the gate says `READY FOR HUMAN REVIEW`.

Memory controls, receipts, backup, cloud-sync preview, graphs, modes, and
deployment options remain available as progressive technical disclosure.

The current desktop artifacts are development builds and are not yet published
with trusted operating-system signatures. Context receipts are Ed25519-signed,
but that does not replace Windows Authenticode or Apple Developer ID signing and
notarization. Until a signed release is published, developers and IT teams
should evaluate the Bridge from source:

```bash
python -m lians_easy optimize --clients antigravity,claude,cursor,gemini,codex,cline,opencode --yes
python -m lians_easy status
python -m lians_easy doctor --json
python -m lians_easy app
```

<details>
<summary><strong>Advanced experiments and research utilities</strong></summary>

To test the product hypothesis before changing the desktop experience, build
the offline Claude comparison plan. A live run is a separate, explicit action
and refuses API-key or cloud-provider authentication:

```bash
python -m lians_easy experiment claude
python -m lians_easy experiment claude --run --output claude-context-report.json
```

The paired synthetic test checks exact answer quality and Claude-reported input
usage for full replay versus bounded Lians context. It does not claim to extend
the ordinary interactive Claude Pro allowance. See the
[method and claim boundary](../../docs/benchmarks/claude-code-baseline.md).

The larger research-history gate is available explicitly:

```bash
python -m lians_easy experiment claude --scenario market-research \
  --max-context-tokens 2048 --repetitions 2 --run \
  --output claude-market-research-report.json
```

For a large post export or browser-work ledger, compile the raw local history
into one bounded AI-ready brief:

```bash
lians brief research posts.jsonl --output research-brief.json
lians brief browser browser-events.jsonl --output browser-brief.json
```

This command makes no Claude, Codex, Cursor, or hosted Lians request. It accepts
a JSON array or JSON Lines file, removes repeated research text or superseded
browser history locally, preserves representative evidence and a hash receipt,
and refuses credential-like records. See the
[large-workload method and measured boundary](../../docs/benchmarks/work-per-token-2026-08-16.md).

The synthetic capacity gate is also offline by default. A live run requires
subscription-backed sign-in, and raw paired replay is separately explicit and
safety-capped:

```bash
lians experiment stretch --workload social-research
lians experiment stretch --workload social-research --records 1000 \
  --run --provider claude --paired --output report.json
```

For video research at corpus scale, import completed analysis outputs from any
vision or transcription provider as JSON Lines. Lians encrypts each result,
commits in resumable batches, skips exact replays, and keeps the large corpus
outside latency-sensitive agent memory:

```bash
lians video ingest --input video-analysis.jsonl --run-id research-2026-08
lians video search "onboarding friction" --limit 10
lians video summarize --remember
```

Each line needs an `external_id` plus a `summary` or `findings`; optional fields
include `title`, `source_uri`, `tags`, `provider`, `model`, `occurred_at`, and
`metadata`. `summarize --remember` promotes one bounded deterministic
consolidation, not 10,000 raw records, into cross-agent memory. This pipeline
does not claim to make video-model inference faster: it scales the encrypted
ingestion, recovery, search, and consolidation of completed provider outputs.

</details>

## Developer package

The public Python distribution is named `lians-bridge`. It is built as a
platform-independent wheel and carries the same encrypted runtime and bundled
Lians App as the desktop package. After its first verified PyPI release,
developers will be able to install it without cloning this repository:

```bash
pipx install lians-bridge
# or: uv tool install lians-bridge

lians doctor --json
lians --version
lians optimize --clients detected --plan --json
lians optimize --clients detected --yes --json
lians status --json
lians continue
lians app
```

For a disposable diagnostic after publication:

```bash
uvx --from lians-bridge lians doctor --json
```

### MCPB bundle

The package root is also a self-contained MCPB source for local MCP catalogs
such as Smithery. It uses the cross-platform UV runtime, starts the same
credential-free stdio server as `lians mcp`, and does not introduce a second
memory implementation:

```bash
npx @anthropic-ai/mcpb validate packages/lians-easy
npx @anthropic-ai/mcpb pack packages/lians-easy lians-memory.mcpb
```

The bundle metadata, runtime version, fifteen advertised tools, and stdio
initialization contract are covered by `tests/test_mcpb.py`.

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

The Windows build workflow packages the native windowed companion, its quiet
launcher, and its console-only MCP sidecar in a per-user
`Lians-Setup-<version>.exe`. It creates Start-menu entries, does not request
administrator access, and is exercised through install, launch, and uninstall
on a fresh runner. Application files live under the user's Programs directory;
encrypted memories remain in a separate private data directory. On an upgrade,
Setup asks every Lians process to exit, stages and health-checks the complete
candidate bundle, and atomically swaps it only after it passes. A failed
candidate restores the prior launcher and app directory without changing
memory or AI-client configuration. Silent removal preserves encrypted memory;
interactive removal asks separately before erasure. Pull-request installers
are unsigned technical fixtures until the publisher-gated release job signs
the launcher, windowed app, MCP sidecar, and setup executable. The free build
path still writes a SHA-256 checksum beside every desktop artifact and records
GitHub OIDC build provenance for same-repository pull requests and manual
builds. After downloading an artifact, verify its origin with:

```bash
gh attestation verify PATH_TO_ARTIFACT --repo Lians-ai/Lians
```

This proves that the file was built by the public Lians repository at a named
commit. It does not create a Windows publisher signature or Apple Developer ID,
so unsigned Windows and macOS previews can still show operating-system trust
warnings. Use these builds for transparent closed testing, not as a substitute
for a future signed public release.

The desktop footer can save a **Help report** directly to Downloads. The report
contains version, platform, client connection state, recovery state, and a
bounded list of structural crash fingerprints. It never includes prompts,
memory contents, exception messages, API keys, settings, or absolute user
paths. Stable Windows installers also receive GitHub OIDC build provenance;
the release workflow refuses to overwrite an existing desktop asset.

Public Windows releases use Microsoft Azure Artifact Signing with GitHub OIDC,
so no exportable publisher private key is stored in GitHub. After Microsoft
approves the Public Trust identity and its certificate profile has the
**Artifact Signing Certificate Profile Signer** role, configure these repository
variables: `AZURE_ARTIFACT_SIGNING_CLIENT_ID`,
`AZURE_ARTIFACT_SIGNING_TENANT_ID`,
`AZURE_ARTIFACT_SIGNING_SUBSCRIPTION_ID`,
`AZURE_ARTIFACT_SIGNING_ENDPOINT`, `AZURE_ARTIFACT_SIGNING_ACCOUNT`,
`AZURE_ARTIFACT_SIGNING_PROFILE`, and the exact certificate subject in
`WINDOWS_SIGNING_SUBJECT`. Finally set `PUBLISH_SIGNED_LIANS_DESKTOP=true`.
The stable job refuses to publish if any variable, signature, subject match,
fresh-runner lifecycle test, checksum, or provenance verification is missing.

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

The Linux workflow builds an install-free x86_64
`Lians-<version>-linux-x86_64.AppImage`. A user can make the single file
executable and open it without administrator access or a package manager. The
workflow extracts the finished image, verifies its desktop identity and native
architecture, and exercises the bundled encrypted Bridge. Stable publication
is separately gated and adds an exact checksum plus GitHub build provenance;
Linux remains a technical preview until clean-device desktop integration and
upgrade/rollback tests pass.

**Check for updates** is user-initiated in this preview. The Bridge recognizes
only stable releases on the official Lians GitHub repository and offers an
architecture-specific Windows, macOS, or x86_64 Linux package only when its
checksum is also published. A separate **Download verified update** action
fetches the tiny checksum first, streams the exact package under a 512 MiB cap,
verifies its complete SHA-256 digest, and saves it to Downloads without
overwriting an existing file. Nothing downloads in the background and nothing
opens after the download. A second confirmed action re-hashes the saved file.
Signed Windows and macOS builds open it only when the candidate matches the
installed publisher and the operating system accepts the signature; otherwise
Lians only selects the file in Downloads for the user to review.

Supported targets are Claude Desktop, Cursor, Windsurf, Antigravity CLI, Gemini
CLI, Codex, Cline CLI, and OpenCode. Cline uses its documented CLI settings
file at `~/.cline/data/settings/cline_mcp_settings.json`; OpenCode uses its
documented global configuration file at
`~/.config/opencode/opencode.json`.

No Lians account, API key, database server, model download, or manual JSON
editing is required for local mode. Optional Lians Cloud continuity uses an
ordinary browser sign-in; credentials are never entered in the React app. The
local store uses AES-GCM; on Windows its root key is protected with DPAPI. The
full Lians engine remains available when a team needs semantic retrieval,
collaboration, governance, or a shared server deployment.

### Move memory to another device

Portable backups never copy a Windows-only DPAPI key and never write plaintext
memory JSON. The command prompts for the passphrase privately, then encrypts the
complete memory lineage, activity history, and signed receipts into one
`.liansbackup` file:

```bash
lians backup export --output "Lians Memory.liansbackup"
lians backup verify --input "Lians Memory.liansbackup"
lians backup import --input "Lians Memory.liansbackup" --yes
```

Import verifies the AES-GCM envelope, fixed scrypt parameters, record hashes,
lineage, and every Ed25519 receipt before opening one database transaction. It
re-encrypts memory with the destination device's local key, skips equivalent
IDs, and rejects the entire import if any existing ID has different history.
Backup format v3 also carries the encrypted dependency and invalidation graph,
so moved memory cannot silently forget which downstream work requires review.
The passphrase is never accepted as a command-line argument, where process-list
tools could expose it. Keep the passphrase separately; Lians cannot recover it.

Nontechnical users can do the same from **Lians App → Move memory safely**.
The App downloads the encrypted file directly, reviews its memory, activity,
and receipt counts before import, and makes clear that existing history is
never overwritten. The browser surface caps imports at 32 MiB; the CLI retains
the full 128 MiB format limit for larger profiles.

### Zero-knowledge cloud sync technical preview

`lians_easy.sync` implements the device-side contract for future Lians Cloud
continuity. An existing device approves a short-lived enrollment request, wraps
the random workspace key specifically for the new device with X25519 and
AES-GCM, and signs the device grant. Profile revisions are encrypted before
upload, signed, hash-chained, and accepted by the opaque reference service only
when they extend the current head. Permanent forgetting wins over stale content
on another device; divergent corrections fail atomically for human review.
The same opaque revision now preserves dependency edges, open invalidations,
and monotonic repair resolutions without exposing references or reasons to the
cloud service.

The Bridge now includes a public native OAuth client with system-browser PKCE,
an OS-root-encrypted rotating-token vault, and automatic encrypted pull before
context use plus write-through after memory changes. MCP and hook tests prove a
Cursor-origin preference can appear on a separate Codex device, be corrected
for Claude, and then be forgotten everywhere. Cloud failure leaves the local
save successful and reports that encrypted sync is pending.

The packaged Lians App now provides the ordinary Add Device path. A new device
signs into the same account, chooses **Add this device**, and displays a
short-lived matching code. An existing connected device reviews the public
device name and code, explicitly approves it, and wraps the workspace key only
for that recipient. The request survives an app restart in locally encrypted
state, is removed after acceptance, and never asks the user for a workspace ID,
JSON file, terminal command, API key, or recovery phrase.

The same panel can verify connected devices and remove an old device without
showing keys or workspace identifiers. Removal advances the encryption epoch,
wraps a fresh workspace key only to surviving devices, deletes obsolete
encrypted cloud revisions, and immediately publishes a fresh encrypted
snapshot. The UI deliberately says that memory already downloaded to the old
device may remain there; removal protects future cloud memory and does not
pretend to remotely erase that computer.

If every trusted device is unavailable, choose **Recover from encrypted
backup** after signing in on a clean device. Lians verifies the user-held
`.liansbackup`, shows its memory, activity, and receipt counts, imports it only
after a second confirmation, re-encrypts it for the replacement device, and
starts a fresh encrypted cloud workspace. The backup passphrase cannot be reset
by Lians, and an inaccessible old encrypted cloud copy may remain until account
deletion.

Development builds opt into this path with deployment-provided public values;
there is no client secret:

```bash
LIANS_CLOUD_URL=https://api.lians.ai
LIANS_OAUTH_ISSUER=https://YOUR_ISSUER
LIANS_OAUTH_CLIENT_ID=YOUR_PUBLIC_NATIVE_CLIENT_ID
LIANS_OAUTH_AUDIENCE=https://api.lians.ai
```

This technical preview is not a claim that hosted sync or cloud-only account
recovery is generally available. **Move memory safely** is the supported
zero-knowledge migration and all-devices-lost recovery path until the production
identity provider, provider-outage qualification, external cryptographic review,
and signed release gates are complete. The complete boundary is documented in
[`docs/cloud-sync-protocol.md`](../../docs/cloud-sync-protocol.md).

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
