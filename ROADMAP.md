# Lians product roadmap

Lians provides persistent project memory for AI coding tools. The near-term
product goal is simple: a developer should be able to install it in minutes,
understand what was remembered, and carry current project context between
supported AI clients.

This roadmap communicates priorities, not contractual release dates.

## Now

- Make the Codex, Claude Code, and Cursor quickstarts reliable on clean machines.
- Ship signed and notarized Lians Easy installers for Windows and macOS.
- Keep `remember`, `recall`, `list`, `correct`, and confirmed `forget`
  consistent across the desktop runtime, MCP, HTTP, Python, and TypeScript.
- Prove that two different AI clients can use one local memory profile.
- Keep the README, demos, package metadata, and releases focused on persistent
  project memory before introducing advanced capabilities.

## Next

- Add a visual memory manager for developers who do not want to ask an agent to
  inspect or correct saved information.
- Publish a hosted connector for clients such as ChatGPT and for multi-device
  continuity.
- Add import, export, profile selection, and migration from Lians Easy to the
  full engine.
- Validate silent installation, diagnostics, and managed configuration with
  enterprise design partners.

## Later

- Offer an optional small semantic index without making model downloads part of
  first run.
- Expand shared-team deployment, access controls, retention, and admin tooling.
- Broaden provider integrations and independently reproducible memory quality
  evaluations.

## Non-goals for the first-run product

- Replacing the user's AI assistant or model.
- Asking a new user to understand vector databases, temporal schemas, or
  governance terminology.
- Claiming automatic ChatGPT installation when the client requires a hosted
  connector.
