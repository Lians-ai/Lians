# Lians consumer installer contract

The consumer package is one Lians installer per operating system, not a separate
installer for every AI client. The installer places one local Lians Bridge on
the device, detects compatible AI apps, and lets the user connect any of them
from one screen.

The reference implementation in
[`codefl0w/mtkclient-windows-installer`](https://github.com/codefl0w/mtkclient-windows-installer)
gets several operational details right: a single executable, an explicit step
engine, verification, a retry path, and a support log. Lians should keep those
mechanics while replacing the developer-facing presentation with a consumer
setup flow.

## What the user downloads

| Audience | Primary package | Secondary path |
|---|---|---|
| Windows user | Authenticode-signed `LiansSetup.exe` | Microsoft Store or `winget` after the direct package is trusted |
| macOS user | Developer ID-signed and notarized `Lians.dmg` | Homebrew for technical users |
| Linux user | Signed AppImage or Flatpak | Native packages when demand justifies them |
| Enterprise IT | Signed MSIX/PKG with silent options | MDM deployment and the existing CLI contract |

The consumer installer must bundle its runtime. It must not ask the user to
install Python, Git, `uv`, build tools, MCP tooling, or a model. Prefer a
per-user installation so setup does not request administrator access. If a
future capability truly requires elevation, ask at that step and explain the
specific reason before the operating-system prompt appears.

Downloaded Windows artifacts must also identify themselves before signing is
available: Explorer properties show **Lians Bridge**, the package version, the
original executable name, and the Lians lotus icon. Signing still remains a
separate release gate; product metadata must never be presented as a substitute
for a verified publisher.

The stable-release workflow keeps desktop publication disabled unless the
repository explicitly sets `PUBLISH_SIGNED_LIANS_DESKTOP=true`. Even with that
opt-in, upload stops unless Windows Authenticode verifies; macOS and Linux stop
until their notarized DMG and signed AppImage or Flatpak paths exist. Unsigned
pull-request artifacts remain technical test fixtures, not consumer releases.

## First-run experience

The setup sequence has four moments:

1. **Promise and trust.** Explain that Lians carries preferences and useful
   project context across the user's existing AI apps. State that memory is
   encrypted locally, existing settings are backed up, and no provider API key
   is required.
2. **Choose apps.** Detect installed clients, select them by default, and keep
   unsupported or absent clients behind **Add another AI app**. Encourage two
   connections because the cross-app handoff is the activation event.
3. **Set up.** Show one progress bar and three plain-language states:
   **Protect your existing settings**, **Connect your AI apps**, and **Check
   that memory is ready**.
4. **Prove value.** Copy a ready-made `remember` prompt, tell the user to open a
   new task in another app, and show the expected context receipt. Do not end on
   a generic "installation complete" message.

The user should see the outcome, not the implementation:

| Internal operation | Consumer copy |
|---|---|
| Back up and atomically update client configuration | Protect your existing settings |
| Register MCP server, hook, plugin, or project rule | Connect Cursor / Claude / Codex |
| Start Bridge and validate the client contract | Check that memory is ready |
| Display configuration paths, commands, and exceptions | Technical details |
| Create diagnostic archive | Save support report |

Do not show dependency names, environment variables, repository cloning,
terminal output, or messages such as "administrator access granted" in the
default flow. They increase perceived risk without helping a nontechnical user
make a decision.

## Reliability behavior behind the simple UI

The hidden setup engine must be more rigorous than the visible interface:

- preflight disk space, OS support, client versions, write permissions, and a
  single running installer instance;
- back up every existing configuration before the first mutation;
- use atomic writes and verify every selected integration after writing;
- retry an individual failed integration without repeating successful work;
- roll back the current setup transaction when verification cannot complete;
- preserve unrelated client settings byte-for-byte where the file format
  allows it;
- keep a local support report with sensitive values removed;
- make uninstall remove only Lians-managed entries, then ask whether encrypted
  memory should be kept or permanently erased; and
- let the user pause all recall from the Lians tray or control center without
  uninstalling anything.

Technical details are progressive disclosure, not a separate product. The same
engine powers the consumer setup screen, the advanced CLI, and enterprise MDM
deployment.

The Bridge preview now treats every selected AI integration as its own file
transaction. It snapshots the exact original configuration and permissions,
verifies both the memory connection and automatic-recall hook, restores a
failed integration without disturbing successful ones, removes transaction
backup residue, and returns only failed client IDs to the retry action. The GUI
also writes a user-selected JSON help report containing runtime state and
client outcomes, while deliberately excluding settings contents, memory
contents, exception text, credentials, and user paths.

Setup now holds an operating-system file lock so a second installer cannot
mutate the same client concurrently. Before changing each client, it atomically
writes a disk-backed rollback journal containing only validated target paths
and backup references. If the process stops after a file replacement, the next
setup launch restores the exact prior state before retrying. A frozen Windows
binary passed a forced-exit harness at that point, including cleanup and
unrelated-settings preservation. A clean virtual-machine reboot/power-loss run
remains a generally available release gate.

Critical file replacement now requests durable rename metadata as well as
flushing file content: Windows uses `MoveFileEx` with replace and write-through,
while POSIX systems `fsync` the parent directory after atomic rename. Settings
backups use the same writer. This narrows the remaining power-loss gate to
release-artifact and filesystem behavior rather than an unflushed application
write path.

## Package architecture

```text
LiansSetup.exe / Lians.dmg
  -> verifies publisher and package integrity
  -> installs one per-user Lians Bridge and Lians App
  -> detects supported AI clients
  -> configures selected integrations with backups
  -> starts Bridge and verifies a bounded recall receipt
  -> enables signed automatic updates
```

The installed product should expose:

- a Lians App for Home, Projects, Memory, Activity, Review, Integrations, and
  Settings;
- a small tray surface for status, pause, open Lians, and quit;
- the signed local Bridge with encrypted local memory and loopback-only access;
- optional cloud sign-in for sync and collaboration, never as a prerequisite
  for the local first run; and
- one integration manager that can add, repair, or remove clients later.

## Release gates

Do not call the installer generally available until all of these pass:

- a clean Windows 11 user with no Python or Git connects two detected clients
  without opening a terminal;
- a clean supported macOS user completes the same flow without a Gatekeeper
  bypass;
- Windows Authenticode, macOS Developer ID, notarization, package checksums,
  provenance, and update signatures verify in CI;
- setup succeeds without administrator access for the normal per-user path;
- an interrupted setup resumes or rolls back without corrupting client files,
  including a clean virtual-machine reboot/power-loss run;
- each supported client passes `remember -> new task -> bounded recall ->
  inspect -> correct -> forget` against the release artifact;
- uninstall removes only Lians-managed integration state and clearly separates
  **Keep my memory** from **Permanently erase my memory**; and
- a nontechnical usability test can complete the cross-app memory moment in
  under three minutes without coaching.

The core packaging decision is therefore: **one product, one Bridge, one setup
experience, multiple integrations**. The CLI and raw log remain valuable for
developers and IT, but normal users should never need to know they exist.
