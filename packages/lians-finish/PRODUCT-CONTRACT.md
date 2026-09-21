# Product contract: Lians Mission Control v0.11.0

## User

An AI-heavy individual or small team that uses more than one agent or model and
burns premium allowance on ordinary work, repeated context reconstruction, and
manual checking.

## Job

Accept one durable mission, apply the user's usage and risk policy, assign each
stage to the least expensive capable agent, and return verified work with an
auditable record of premium escalation.

## Required behavior

1. Planning consumes no model quota by default.
2. Every model choice records a reason.
3. Protect mode starts normal implementation below the premium tier.
4. Normal work reaches Astra only after deterministic verification fails.
5. High-risk changes receive a premium review.
6. The user supplies the verification command; Lians cannot self-declare done.
7. Premium calls never exceed the selected mode's budget.
8. Receipts distinguish planned routing, passing verification, and blocked work.
9. Users can lower but never raise a mode's premium budget.
10. Verifier commands are allowlisted before any model call and bound into the
    executed receipt.
11. Protect implementation overrides are immutable, narrowly validated, and
    cannot select Astra.
12. Existing receipts can be checked offline for tampering.
13. A non-technical local application exposes planning, execution, status, and
    receipt history without requiring the command line.
14. The application binds to loopback, protects its API with a per-launch
    token, and permits only one active run at a time.
15. Local agent detection does not open agents, read credentials, or imply a
    working connector. Only tested connectors are labeled routable.
16. Previewing records an append-only mission revision but invokes no model.
17. An unchanged preview and run reuse the same mission revision; changed
    constraints create a hash-linked successor.
18. Expired missions can be previewed but cannot be executed.
19. Proof discovery may suggest a repository test command, but never runs it
    before the user starts the mission.
20. Usage summaries aggregate observed local receipts and never present
    themselves as provider quota or billing data.
21. Queued work is persisted before its worker starts, and final job and receipt
    artifacts are published atomically.
22. A queued or running record left by a stopped process becomes an explicit
    `INTERRUPTED` terminal record when the application restarts.
23. Mission constraints and definitions of done receive the same credential
    screening as the task before they can enter the local ledger.
24. Mission revisions are checked as a complete hash chain before a successor
    can be appended.
25. Browser requests reject non-loopback hosts, cross-origin writes, duplicate
    JSON fields, non-finite numbers, and oversized bodies.
26. Export produces a checksummed portable agent containing mission, policy,
    route provenance, and proof configuration without recording or executing it.
27. Import treats the file as untrusted, rejects duplicate or unknown fields,
    verifies its checksum, rebuilds the route locally, and never auto-runs it.
28. Provider credentials, local receipts, and custom executable approvals are
    never included in a portable agent.
29. Export never discloses the full local workspace path, and import always
    requires the user to choose the destination directory.
30. The Windows download is self-contained, but remains an unsigned engineering
    build until code signing and an independent security review exist.
31. A distributed build never presents the founder's identity as the current
    user or exposes nonfunctional account controls.
32. The interface starts execution disabled, reports whether the Codex route is
    available, and enables Start only after detecting a routable connector.
33. The Windows archive includes a first-mission guide that explains the Codex
    prerequisite, Preview's no-model boundary, local storage, and portable-agent
    privacy behavior.
34. When supported, all model stages in one mission use one Codex app-server
    thread; the application does not replay prior stage output into later turns.
35. Each persistent-thread turn enforces its planned model, reasoning effort,
    working directory, no-approval policy, and read-only or workspace-write
    sandbox.
36. Unexpected background approval, permission, elicitation, or user-input
    requests never expand authority automatically.
37. Codex installations without app-server retain a tested one-shot `codex exec`
    compatibility path, and users can select either bridge explicitly.
38. The external beta export excludes task text, constraints, definition of done,
    prompts, repository paths, model output, verifier output, credentials,
    billing data, local job IDs, and Codex thread IDs.
39. Matched tasks are linked only by a keyed, opaque fingerprint computed from
    the normalized task contract, verifier, and starting Git state.
40. Every distributed Windows archive identifies its source commit and dirty
    state and includes the beta and Codex-bridge guides.

## Non-goals

- Promise a larger provider quota.
- Claim savings without a matched baseline.
- Infer correctness from a model's confidence.
- Rotate multiple consumer accounts.
- Replace the execution engines inside Codex, Cursor, Devin, or model providers.
- Route through Claude Code, Cursor Agent, or Gemini CLI before each official
  connector produces an auditable receipt and passes the same acceptance tests.
- Scrape consumer subscription pages, pool accounts, or bypass provider limits.
- Treat a portable-agent checksum as publisher identity, authenticity, or proof
  that the mission is correct.
- Claim that app-server is stable, that a persistent thread extends provider
  quota, or that local protocol tests prove user demand.
