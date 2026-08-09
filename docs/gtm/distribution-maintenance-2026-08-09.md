# Distribution maintenance ledger

Date: 2026-08-09

## Verified package state

| Surface | Verified state | Required action |
| --- | --- | --- |
| GitHub | v0.5.0 public, 2 stars, 2 forks | Improve the release notes and route launch traffic to the repository |
| PyPI | `lians-sdk` 0.5.0 | Keep install examples pinned to 0.5.0 |
| npm | `@lians-ai/lians` 0.4.0, with 31 downloads from August 2 through August 8 | Authorize GitHub trusted publishing for `Lians-ai/Lians` and the workflow file, then rerun the existing v0.5.0 package workflow |
| LangChain | Documentation pull request 4949 merged | Publish the point-in-time memory tutorial and request a reshare |
| MCP Registry | Listing verified | Refresh version and repository metadata after the public SDK contains the bounded profile |
| Glama | Listing reported approved | Verify the displayed version and repository before using an approval claim in public copy |
| RoninForge | Current August 3 census is healthy, 7 of 7 checks passed | No correction request is needed; preserve the current `Lians-ai/Lians` evidence |

## npm authorization evidence

GitHub Actions run `31287160248` checked out v0.5.0, verified the release version, installed dependencies, built, and passed tests. The publish step returned npm `E404` for `@lians-ai/lians@0.5.0`, which npm also uses when the current identity lacks package permission.

Repair checklist:

1. Sign in to npm as an owner of the `@lians-ai/lians` package.
2. Open package settings and configure a trusted publisher for GitHub Actions.
3. Match organization `Lians-ai`, repository `Lians`, and workflow `publish-lian-npm.yml` exactly.
4. Add an environment name only if the npm publisher configuration and GitHub job both use the same environment.
5. Rerun the workflow with release tag `v0.5.0`.
6. Verify `npm view @lians-ai/lians version` returns `0.5.0` before announcing TypeScript parity.

## RoninForge verification

The current public page for `io.github.ebeirne/lians` reports Lians healthy in the August 3 census with 7 of 7 checks passed. It resolves the repository to `https://github.com/Lians-ai/Lians`, detects PyPI 0.5.0, and identifies the Apache 2.0 license. The earlier degraded state is no longer current, so no correction request should be sent.

## Open directory queue

Current verification and next actions:

- TeleAI-UAGI Awesome Agent Memory pull request 61 merged on July 18. Treat this as an independent directory win and link it in launch amplification.
- TensorBlock Awesome MCP Servers pull request 1261 merged on July 17. Treat this as an independent directory win and link it in launch amplification.
- IAAR-Shanghai Awesome AI Memory pull request 124 remains open and mergeable.
- cxxz Awesome Agent Memory pull request 17 remains open and mergeable.
- kyrolabs Awesome Agents pull request 649 was closed without merging. Do not resubmit unless the maintainer invites a revised entry.
- Docker MCP Catalog pull request 4464 is open and still requires review.
- Cline marketplace issue 2050 is open with no maintainer comment.
- The recorded mcp.so listing URL returns 404. Its current submission form requires a $39 one-time payment, so do not resubmit while keeping this campaign on the current billing plan.
- MCPServers.org has a public Lians listing.
- MCP.Directory
- MCP Server Hub
- AgentNDX
- DeepYard
- MCP Market
- Smithery has a public Lians server record. The upstream CLI issue 797 remains the deployment follow-up.
- PulseMCP manual ingestion request

Use one canonical description:

`Lians is an Apache 2.0 bitemporal memory and decision-evidence layer for AI agents, with point-in-time recall, provenance, governed memory lifecycle controls, and a small MCP tool surface.`

Do not purchase expedited placement. Record a public listing URL or review identifier before marking any submission complete.
