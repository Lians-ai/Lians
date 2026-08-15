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
python -m lians_easy install --clients antigravity,claude,cursor,gemini,codex,cline,opencode --yes
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
lians --version
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
on a fresh runner. On an upgrade, Setup asks the existing Bridge to release the
runtime, keeps a hidden previous-runtime copy, runs a local health check on the
candidate, and commits only after it passes. A failed candidate restores the
working runtime without changing memory or AI-client configuration. Silent
removal preserves encrypted memory; interactive removal asks separately before
erasure. Pull-request installers are unsigned technical fixtures until the
publisher-gated release job signs both the runtime and setup executable.

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

### Review memory before another AI uses it

The App's **Review** queue compares active memories within the same kind,
scope, and project boundary. A newer item with the same explicit topic or
strongly overlapping wording but different content is labeled a possible
conflict and held out of recall while the existing precedent remains active.
The queue also holds project handoffs after 14 days and project facts,
decisions, and project memories after 180 days. Preferences do not become stale
from age alone.

Each card shows the exact source client, source reference, time, scope, and
reason for exclusion. Choose **Keep existing**, **Use newer**, or **Both are
valid**; stale items can be reaffirmed, paused, or permanently forgotten with a
second click. Reaffirmation starts a fresh review interval. A held item is
absent from the normal active-memory view and context pack, and the signed
receipt records it under `excluded.review`.

Only identifiers and the resolution are stored in the review audit event.
Memory content remains encrypted, and the resulting pause state plus resolution
event synchronize to other approved devices. This is a deterministic possible-
conflict check, not a claim of universal semantic contradiction detection.

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
