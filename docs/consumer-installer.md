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
backup residue, and returns only failed client IDs to the retry action. A
process-kill and reboot-resume harness remains a generally available release
gate.

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
- an interrupted setup resumes or rolls back without corrupting client files;
- each supported client passes `remember -> new task -> bounded recall ->
  inspect -> correct -> forget` against the release artifact;
- uninstall removes only Lians-managed integration state and clearly separates
  **Keep my memory** from **Permanently erase my memory**; and
- a nontechnical usability test can complete the cross-app memory moment in
  under three minutes without coaching.

The core packaging decision is therefore: **one product, one Bridge, one setup
experience, multiple integrations**. The CLI and raw log remain valuable for
developers and IT, but normal users should never need to know they exist.
