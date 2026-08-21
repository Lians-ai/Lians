<p align="center">
  <img src="docs/assets/lians-lotus.svg" width="150" alt="Lians lotus">
</p>

<p align="center"><strong>Come to Lians to Make.</strong></p>

<p align="center">Describe what your group needs. Get a working app you can use and share.</p>

## Make

Lians turns one sentence into a focused shared app.

```text
Make a 30-day reading challenge for my friends.
```

Lians returns the live challenge, shared progress, and one link. Friends open it and join without creating accounts.

The first release makes six safe app shapes:

- challenges;
- votes;
- quizzes;
- leaderboards;
- trackers; and
- shared lists.

This is not a chatbot that writes instructions for making an app. It creates the app, saves the shared state, and gives the group a usable result.

## Why this wedge

General coding agents can build almost anything, but that power still asks people to understand code, setup, hosting, and debugging. Lians removes those decisions for common group moments.

The loop is intentionally short:

```text
Describe it -> Make it -> Share it -> People use it -> Remix it
```

Every completed app is both the result and the distribution surface for the next app.

## Run the web product

The active product is in [`web`](web/README.md).

```bash
cd web
npm install
npm run dev
```

Quality checks:

```bash
npm run lint
npm test
```

## Product rules

- One sentence in, one usable result out.
- No account required for participants.
- Human-readable templates instead of arbitrary generated code.
- One obvious share action on every result.
- Remixing starts from a working app.
- Free access during the demand test.
- Large, direct language with no technical setup in the main flow.

## The test

The first target is 100 people who create a real app and invite at least one other person.

The main metric is the share loop:

```text
created app -> first participant -> share -> remix
```

Signups and page views do not count as product success. A creator is activated only after another person uses the app.

See [`marketing/make/LAUNCH_TODAY.md`](marketing/make/LAUNCH_TODAY.md) for the launch sequence, demo scripts, metrics, and stop rules.

## Non-goals

- a general coding agent;
- arbitrary websites from a prompt;
- production software for payments, health, finance, or other high-consequence work;
- a chat interface that returns code;
- enterprise permissions;
- subscriptions before repeat use exists; and
- adding every design library to every page.

## Existing Lians work

The repository still contains the original local memory, MCP, SDK, and evidence-checking systems. They remain available for existing users, but they are not the active consumer product promise.

## License

Apache 2.0. See [`LICENSE`](LICENSE).
