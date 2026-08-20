<p align="center">
  <a href="https://github.com/Lians-ai/Lians">
    <img src="assets/logo-blue.png" width="360" alt="Lians lotus logo">
  </a>
</p>

# Lians student and community kit

Run a practical AI-memory session without buying software, collecting student
data, or changing the model your community already uses.

Lians gives Claude, Codex, Cursor, and other MCP-compatible agents durable
memory across chats. The free Community version runs locally with SQLite and
needs no Lians account or API key.

This kit is for:

- AI, machine-learning, computer-science, and developer clubs;
- student hackathons and project nights;
- capstone, research, and independent-study teams; and
- campus community hosts who want a short hands-on workshop.

## Choose a format

| Format | Time | Best for | Outcome |
|---|---:|---|---|
| Two-chat challenge | 3–10 minutes | One person or a club announcement | Prove cross-chat recall, correction, inspection, and deletion. |
| Club workshop | 20 minutes | Meetings, demo nights, and Discord communities | Every participant installs Lians and completes a safe memory loop. |
| Project track | 60–90 minutes | Hackathons and build sprints | Add governed memory to an agent project and demonstrate why it helps. |
| Capstone component | One project milestone | Courses and research teams | Define, test, and report a memory policy for a real agent workflow. |

For a first-time, nontechnical example, use the
[synthetic market-research walkthrough](../examples/market-research/). It shows
the complete remember, separate-chat recall, correction, inspection, confirmed
deletion, and verification loop in Cursor without real respondent or company
data.

## Before the session

1. Ask participants which supported client they use:
   [Cursor](../integrations/cursor),
   [Claude Code](../integrations/lians-plugin), or
   [Codex](../integrations/codex).
2. Ask everyone to install [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
   before the session unless they use Cursor's one-click installer.
3. Tell participants to use a harmless test fact - not a password, API key,
   private research record, personal identifier, or graded answer.
4. Check the course or event's AI-use policy. Lians does not override academic
   integrity, research ethics, privacy, or acceptable-use rules.
5. Plan for the first local memory operation to download and initialize a
   semantic model. On a clean machine, allow a few minutes for that warmup.

## The three-minute, two-chat challenge

The public challenge and feedback thread is
[GitHub Discussion #122](https://github.com/Lians-ai/Lians/discussions/122).

In the first chat, ask:

```text
Remember that this project's release color is amber.
```

Open a separate chat in the same client and ask:

```text
What is this project's release color?
```

Then test control over the memory:

```text
Correct that memory: the release color is now cobalt.
```

```text
Show me the current release-color memory and its previous version.
```

```text
Forget the release-color memory. Ask me to confirm before deleting it.
```

The challenge passes when the participant can:

- recall the harmless fact in a separate chat;
- replace the stale value without returning both as current;
- inspect what is stored; and
- confirm deletion and verify that current recall no longer returns it.

## Twenty-minute club workshop

### 0–3 minutes: explain the problem

An AI chat can contain useful context, but a new chat or different client may
not share it. Lians provides a separate memory layer that the agent can call
through MCP. The model stays the same.

### 3–8 minutes: install

Participants choose the shortest supported path:

- **Cursor:** [one-click MCP installer](../integrations/cursor).
- **Claude Code:** [two plugin commands](../integrations/lians-plugin).
- **Codex app, CLI, or IDE:** [one-command MCP setup](../integrations/codex).

### 8–15 minutes: run the challenge

Use the five prompts above. Approve memory-tool calls when the client asks.
Participants should share only pass/fail results, not the contents of any real
memory database.

### 15–20 minutes: discuss the design

Ask:

- Which project context is worth keeping across chats?
- What should never be stored?
- When should a fact be corrected instead of duplicated?
- Who should be able to inspect or delete a memory?
- Would the same memory still be useful after switching models or clients?

## Hackathon or build-sprint track

### Challenge

Add Lians to an agent project where persistent context makes the workflow more
useful, understandable, or controllable. Demonstrate a complete memory
lifecycle, not just a database write.

Examples include:

- a research assistant that remembers source and methodology preferences;
- a coding agent that carries project constraints between sessions;
- a study assistant that remembers a learner's chosen format without storing
  private or graded material;
- a campus-information agent that corrects outdated facts; or
- a multi-client workflow that moves from Cursor to Claude or Codex without
  losing approved project context.

### Suggested judging rubric

| Dimension | Points | Evidence |
|---|---:|---|
| Cross-chat usefulness | 25 | A later session uses an earlier approved memory. |
| Correction behavior | 25 | A changed fact supersedes the stale value. |
| User control | 25 | The demo includes inspection and confirmed deletion. |
| Project quality | 25 | Memory solves a clear problem without storing unnecessary data. |

Organizers may use this rubric without offering a cash prize or paid product.
Lians does not promise prizes, credits, sponsorship, or judging unless those
terms are separately confirmed in writing.

## Capstone or research integration

Treat memory as a designed component rather than an invisible transcript.
Require each team to submit:

1. a list of facts the agent is allowed to remember;
2. a list of prohibited or sensitive data;
3. the event that triggers a write;
4. the retrieval limit or scope used for recall;
5. a correction and deletion test; and
6. a short evaluation of whether recalled memory actually improved the task.

For Python projects, start with the
[local SDK example](../README.md#use-lians-in-python). For agent clients, use
the MCP setup paths above.

## Community host and ambassador pilot

A community host can run the program without becoming a reseller or making a
sales commitment.

1. Pick one supported client and complete the challenge yourself.
2. Schedule one 20-minute session or add the challenge to a project night.
3. Share the announcement below without implying university endorsement.
4. Collect only aggregate results: participants, successful installs, completed
   challenges, issues opened, and projects demonstrated.
5. Post technical feedback in
   [Discussion #122](https://github.com/Lians-ai/Lians/discussions/122) or open
   a reproducible [GitHub issue](https://github.com/Lians-ai/Lians/issues).

Email `info@lians.ai` if your community wants a free virtual setup session or a
review of its workshop plan. Host titles, compensation, credits, certificates,
or formal partnerships are not implied and must be separately agreed.

## Ready-to-share announcement

```text
Does your AI actually remember across chats?

We're running a free, three-minute test with Lians, an open-source local memory
layer for Claude, Codex, Cursor, and other MCP agents. You'll save one harmless
project fact, recall it in a separate chat, correct it, inspect it, and delete
it. Local mode uses SQLite and needs no Lians account or API key.

Challenge: https://github.com/Lians-ai/Lians/discussions/122
Repository: https://github.com/Lians-ai/Lians

Use a test fact only - never a password, API key, private record, or graded answer.
```

## Pricing and age-appropriate promotion

- The local Community version is free and Apache-2.0 licensed.
- Lians Personal is an optional managed workspace for **US$10/month** with
  setup support and no local maintenance.
- A purchase, subscription, star, follow, testimonial, or public post is never
  required to join a workshop, complete the challenge, or submit feedback.
- For communities serving anyone under 18, promote only the free open-source
  activity. Do not market or facilitate the paid plan to minors.

## Privacy and measurement

Local Community mode is intended to keep the memory store on the participant's
machine by default. Organizers should not ask for memory-database files,
screenshots containing private facts, credentials, or student records.

Useful aggregate measures are:

- number of participants;
- successful installations;
- completed two-chat challenges;
- reproducible issues or pull requests; and
- agent projects that demonstrate correction and deletion.

Stars can help other developers discover the project, but they are optional and
must not be exchanged for access, prizes, certificates, or support.

## Brand assets

Use the Lians lotus as the default mark for a workshop card or event listing:

- [blue lotus logo](assets/logo-blue.png)
- [repository](https://github.com/Lians-ai/Lians)
- [33-second demo](https://github.com/Lians-ai/Lians/releases/download/lians-memory-openai-demo-v1.0.0/Lians-Memory-OpenAI-submission-demo-v1.0.0.mp4)

Do not imply that a university, club, model provider, or event endorses Lians
unless that organization has explicitly approved the statement.
