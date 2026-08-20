# Lians Guard repository instructions

Lians Guard is the current-state and completion guard for AI coding agents.
Keep the primary product loop focused on four results: `RECOVERED`, `STALE`,
`BLOCKED`, and `READY FOR HUMAN REVIEW`.

- Preserve the local-first recovery path and existing compatibility.
- Treat agent prose, touched files, and caller-supplied test results as
  `agent_attested` or `inferred_activity`, never as measured proof.
- Agent-facing tools cannot grant `measured_local`, `measured_ci`, or
  `human_confirmed` trust. Trusted evidence requires a Lians-owned verifier, an
  exact attested CI import, or interactive human confirmation.
- Bind positive evidence to current repository and task state. A mismatch must
  become stale instead of silently updating old evidence.
- `READY FOR HUMAN REVIEW` does not mean correct, approved, merged, or safe to
  deploy.
- Do not introduce claims about time saved, token savings, revenue, retention,
  or product-market fit without matching external evidence.
- Keep advanced memory, graph, compliance, and research capabilities available
  through progressive disclosure, but do not make them the initial Guard story.

For Guard changes, run the task-contract, CI-evidence, session-capture, Bridge,
MCP, installer, and verification tests in `packages/lians-easy/tests`.
