# Lians product direction

## Decision

Do not build or market generic Deploy All as the company.

OpenAI already documents a Codex workflow from a rough idea to a checked live
URL, Anthropic positions Claude Code across build and deployment, and Replit can
generate and automatically deploy an application directly from ChatGPT. A
Lians prompt box that combines those steps would be replaceable.

The current repository product remains Lians Check, the evidence-backed proof
layer for AI work. The next product direction is an experiment, not an
implemented product or a proven pivot.

Read the full [Deploy All replacement test](deploy-all-pressure-test.md) and the
binding [product parameters](product-parameters.md) before expanding the build.

## Candidate experiment

The one-word action is:

> **Ship.**

The customer moment is:

> My AI-built app works in preview, but I cannot get it working reliably in
> production.

The promise to test is:

> Your AI app works in preview. Lians gets it live and keeps it live.

This is a universal moment among AI builders, not a profession. The first
technical scope can still be narrow.

## Why the raw idea fails

```text
GENERIC DEPLOY ALL
IDEA -> GENERATE -> DEPLOY -> URL

EXISTING REPLACEMENTS
CODEX + HOSTING -> URL
CLAUDE CODE + HOSTING -> URL
REPLIT IN CHATGPT -> URL
LOVABLE / BOLT / WIX -> URL
```

More tools do not create differentiation. SEO checks, animation libraries,
deployment adapters, models, and browser tests are capabilities competitors can
copy or already provide.

## What Lians would have to own

```text
STUCK PREVIEW
  -> IMPORT
  -> DIAGNOSE PRODUCTION BLOCKERS
  -> REPAIR SUPPORTED FAILURES
  -> DEPLOY IMMUTABLE PREVIEW
  -> TEST LIVE CORE FLOW
  -> APPROVE
  -> PUBLISH
  -> MONITOR
  -> ROLLBACK IF NEEDED
```

The value is responsibility for the live production contract, not generation.

Lians must own:

- one opinionated production stack;
- build and environment configuration;
- least-privilege secret intake;
- deterministic release gates;
- browser verification against the deployed revision;
- fixed resource and cost limits;
- monitoring and incident visibility;
- safe rollback; and
- plain unsupported boundaries.

## Exact first scope

The free validation cohort accepts only:

- a GitHub repository exported from an AI builder;
- React with Vite;
- an existing Supabase backend when a backend is needed;
- one core flow that already works in preview;
- production configuration and deployment defects; and
- reversible changes with human approval.

It excludes new feature development, new auth systems, payment flows, data
migrations, native mobile apps, arbitrary backends, regulated data, and custom
cloud architecture.

## User experience

The user:

1. connects the repository;
2. shows the flow that works in preview;
3. supplies only the missing production authority or secret;
4. approves the exact release; and
5. receives the live URL.

The user should not need to read code, understand a framework, choose a cloud,
write deployment commands, diagnose logs, or manage a prompt loop.

Visible states remain plain:

```text
UNSUPPORTED
NEEDS YOU
READY TO SHIP
LIVE
```

## Why Codex and Claude still matter

They are the baseline, not an enemy to ignore.

Every first-cohort project must be tested against the easiest available agent
workflow. If a user can ask Codex or Claude once and receive the same working,
monitored release with the same effort, Lians has no reason to exist for that
case.

Lians only survives when its supported production system removes setup,
reduces failure, and continues owning the release after a coding agent would
stop.

## First proof gate

Do not build the full platform first. Run a free concierge test with ten real
stuck applications.

The experiment passes only when:

- ten independent users connect real repositories;
- at least five problems survive a one-request agent baseline;
- seven applications reach a verified live state within 24 hours;
- human rescue time falls below 30 minutes at the median after the first three;
- four creators ship a second update within 30 days; and
- two creators refer another qualified project.

If the fixes stay unique, Lians Ship is an agency. If the fixes repeat and the
manual work falls, there may be a scalable product.

## Product-led distribution

The live app is the distribution surface:

- a privacy-safe optional `Shipped with Lians` link;
- a public release receipt that reveals no private code or secrets;
- a one-click path for another builder to connect a stuck project;
- short demos showing preview failure, Lians repair, and the verified live flow;
  and
- use-case pages for concrete failure moments such as auth, environment,
  routing, domain, and production-only failures.

Do not publish generic SEO pages that restate “AI app builder.”

## Metrics

- qualified stuck project to repository connected;
- connected repository to supported contract;
- supported contract to `LIVE`;
- median time to `LIVE`;
- human engineering minutes per release;
- cost per monitored live app;
- production regression and rollback rate;
- second release within 30 days;
- referred qualified project; and
- percentage of failures represented by a reusable repair.

The activation metric is **a stuck preview becoming a verified live release**.

The scalability metric is **human engineering time falling as successful
releases increase**.

## Current implementation boundary

Lians Ship is not implemented. Lians Check and Guard can support release
evidence and confirmation, but they do not import, repair, deploy, monitor, or
roll back an application today.

No public copy may imply otherwise.

## Explicit non-goals

- generic prompt-to-app generation;
- an IDE or visual website editor;
- code as the end result;
- every application framework;
- user-managed deployment;
- arbitrary feature completion;
- autonomous releases without approval;
- SEO ranking guarantees;
- pricing before repeated use; and
- claims that the experiment is proven before live cohort evidence exists.
