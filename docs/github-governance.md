# GitHub governance for Lians

This document defines how Lians repositories should be separated and protected.
Organization owners apply the settings in GitHub; contributors cannot enforce
repository rules through source files alone.

## Repository portfolio

| Repository | Visibility | Access |
|---|---|---|
| `Lians-ai/Lians` | Public | Public read; maintainers merge reviewed Community changes |
| `Lians-ai/Lians-Platform` | Private | Founders and approved platform engineers only |
| `Lians-ai/Lians-Deployments` | Private | Founders and approved operators only |
| customer repositories | Private | Minimum team required by the contract; one repository per customer |

Do not use public forks or public issues for private product, production, or
customer work.

## Public repository metadata

- **Name:** Lians
- **Description:** `Lians Community: open decision-evidence formats, SDKs, verifiers, and self-hosted foundations for consequential AI agents.`
- **Homepage:** `https://www.lians.ai/`
- **Suggested topics:** `ai-agents`, `decision-evidence`, `open-core`,
  `model-context-protocol`, `agent-memory`, `bitemporal`, `provenance`,
  `self-hosted`, `python`, `typescript`
- Keep Issues enabled.
- Disable the Wiki unless it has an active owner; canonical documentation lives
  in the repository and on the Lians website.
- Disable Projects on the public repository if roadmaps or commercial work may
  appear there. Use a private organization project instead.

## Default branch ruleset

Create an active branch ruleset for `master` with:

- require a pull request before merging;
- require at least one approval;
- require review from Code Owners;
- dismiss stale approvals when new commits are pushed;
- require all review conversations to be resolved;
- require the test, dependency review, CodeQL, and `Public repository boundary`
  checks to pass;
- require branches to be up to date before merge;
- block force pushes and branch deletion;
- restrict bypass to organization owners for emergencies; and
- prefer squash merge and automatically delete merged branches.

Protect release tags with a second ruleset and restrict tag creation or deletion
to maintainers responsible for releases.

## Security settings

Enable dependency graph, Dependabot alerts and security updates, secret
scanning, push protection, private vulnerability reporting, dependency review,
and CodeQL. Security reports must use GitHub Security Advisories or
`security@lians.ai`, never a public issue.

## Private repository rules

- Default every new company repository to private.
- Give access through teams, not individual collaborators where possible.
- Keep billing, identity, production, and customer repositories founder-only
  until another role has a demonstrated need.
- Require pull requests and the same no-force-push rules on private default
  branches.
- Store secrets in deployment or organization secret stores, never Git.
- Keep customer repositories separate from each other and record offboarding in
  the contract closeout checklist.

## Release boundary

Community packages are built only from the public repository and reference a
public commit and tag. Commercial services may consume those packages, but a
public release must never depend on a private repository to install, verify
evidence, or run the documented Community quickstart.
