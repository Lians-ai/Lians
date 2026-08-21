# Lians product parameters

These parameters decide what Lians may build. A concept that fails a required
parameter does not enter product implementation.

## Core test

Lians must create or maintain a real external outcome with less work than a
general AI agent.

The candidate action is:

> **Ship.**

The candidate outcome is a supported AI-built application that moves from a
working preview to a verified, monitored production release.

## Required parameters

### 1. Pass the direct replacement test

Write the shortest credible Codex, Claude, Replit, or incumbent workflow for
the same job before writing product code.

Reject the concept when Lians only:

- hides a prompt;
- calls the same model with a longer system message;
- bundles tools that the agent can already call;
- returns the same code or URL; or
- adds a cosmetic interface without taking more responsibility.

### 2. Solve an existing painful moment

The first Ship experiment begins only after the user already has:

- a real repository;
- a flow that works in preview;
- a production blocker; and
- a consequence for remaining stuck.

An app idea or statement of interest does not count as demand.

### 3. Create the end result

A report, checklist, code patch, or preview is not the end result.

Completion requires an approved revision at a live URL whose agreed core flow
passes in a real browser.

### 4. Own the ongoing production contract

The release must be bound to:

- the exact source revision;
- the exact deployed revision;
- one tested user flow;
- declared infrastructure and cost limits;
- monitoring; and
- a rollback path.

A one-time deploy with no continuing responsibility is too easy to replace.

### 5. Use a narrow technical scope and a broad trigger

Market the moment, not a profession:

> It works in preview. Ship it for real.

Constrain the first implementation to React, Vite, and an existing Supabase
backend. The scope can expand only after repair patterns repeat.

### 6. Keep the human in control

Require explicit approval before:

- reading or changing production secrets;
- changing a production database or auth configuration;
- replacing a live release;
- connecting a domain;
- increasing a cost limit; or
- rolling forward after a failed deployment.

Prefer rollback over improvisation when production evidence fails.

### 7. Ask for less knowledge than the alternatives

The user should not have to know the framework, output directory, hosting
provider, environment-variable model, DNS record, CI system, or browser-test
tool.

If support requires the user to debug through chat, the product has failed.

### 8. Learn from measured failures

Record privacy-safe failure classes, attempted repairs, deployed outcomes,
rollbacks, and human intervention time.

The system becomes more defensible only when each cohort creates reusable
deterministic repair logic and reduces future manual work.

### 9. Earn expansion

Add a framework, backend, or cloud only when:

- at least five qualified projects require it;
- the same production contract can verify it;
- the repair is safe and reversible;
- human intervention is declining; and
- the existing scope has repeated releases and referrals.

## Product test

Before a feature enters the roadmap, answer:

1. What is the painful external state before Lians?
2. What exact live state exists after Lians?
3. Can Codex or Claude reach it from one ordinary request?
4. What responsibility does Lians keep after the model stops?
5. What evidence opens the release gate?
6. What requires approval?
7. What happens when the release fails?
8. Which part becomes cheaper or more reliable with every project?
9. Why will the user return or refer another user?

Any vague answer blocks implementation.

## Validation gates

Before platform work:

- ten independent users connect real stuck repositories;
- five cases survive a one-request Codex or Claude baseline;
- seven reach `LIVE` within 24 hours;
- median human engineering time drops below 30 minutes after the first three;
- no critical regression, data loss, or secret exposure occurs;
- four users ship a second update in 30 days; and
- two qualified referrals occur.

## Explicit non-goals

- claiming a 10 out of 10 idea before customer evidence;
- generic Deploy All as a public promise;
- another AI coding chat;
- arbitrary app generation;
- a design editor;
- code or an audit as the final outcome;
- every tool used by default;
- every framework or cloud;
- high-consequence applications in the first cohort;
- autonomous production changes without approval;
- SEO ranking promises; and
- pricing before activation, repeat use, and declining manual work.
