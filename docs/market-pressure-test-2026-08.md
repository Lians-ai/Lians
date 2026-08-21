# Lians market pressure test

Date: 2026-08-20

> Historical note: this Ship test has been superseded by the [2026-08-21
> 10 out of 10 pressure test](ten-out-of-ten-pressure-test.md). Keep this file
> as the evidence for rejecting generic Deploy All.

## Current decision

Generic idea-to-app creation fails the replacement test. The current market
already includes focused builders and general agents that can generate, check,
and deploy software from natural language.

The only adjacent problem worth a live test is the production gap after an
AI-built app already works in preview.

Read the complete [Deploy All replacement test](deploy-all-pressure-test.md).

## Evidence hierarchy

### Category evidence

- Lovable reported [$200 million ARR and 100,000 new projects per
  day](https://lovable.dev/blog/one-year-of-lovable) in November 2025.
- Wix reported approximately [304.2 million registered users at the end of
  2025](https://www.sec.gov/Archives/edgar/data/1576789/000162828026015222/wix-20251231.htm).
- Canva says more than [89 million websites have been
  created](https://www.canva.com/newsroom/news/canva-websites/) with Canva
  Websites.

This proves large creation demand and intense competition. It is not Lians
demand.

### Replacement evidence

- The [official Codex deployment
  workflow](https://learn.chatgpt.com/use-cases/deploy-app-or-website) already
  covers a rough idea or repository through checks, preview deployment, and a
  live URL.
- [Claude Code](https://claude.com/product/claude-code) says it can build,
  debug, and ship with deployment, database, monitoring, and version-control
  tools.
- [Replit in ChatGPT](https://docs.replit.com/references/platforms/chatgpt)
  already generates and automatically deploys applications from natural
  language.

This invalidates Deploy All as a differentiated product promise.

### Problem evidence

Recent public customer reports describe preview and production differences,
auth and database failures, infrastructure failures, lost credits, unstable
hosting costs, and repeated agent-created regressions:

- [Replit publishing failure, May
  2026](https://www.reddit.com/r/replit/comments/1to09km/replit_support_and_publishing_is_awful/)
- [Replit development versus production failure, April
  2026](https://www.reddit.com/r/replit/comments/1skorhm/should_proddev_once_deployed/)
- [Non-coder deployment account](https://www.reddit.com/r/replit/comments/1m58nzn)
- [Migration after reliability problems around 50
  users](https://www.reddit.com/r/replit/comments/1sqtbfh/my_clients_replit_app_hit_200_daily_users_heres/)
- [Bolt production help request, March
  2026](https://www.reddit.com/r/boltnewbuilders/comments/1s1k9s8/converting_development_to_production/)

These are anecdotes. They justify a direct test, not a market-size claim.

## Candidate wedge

> **Ship.**

> Your AI app works in preview. Lians gets it live and keeps it live.

The first cohort is restricted to GitHub repositories using React, Vite, and an
existing Supabase backend. Lians handles supported production blockers,
publishes an approved immutable release, tests one core flow, monitors it, and
can roll it back.

## Honest score

| Dimension | Score | Reason |
|---|---:|---|
| Pain clarity | 8/10 | Current users report concrete production failures and losses |
| Category demand | 9/10 | AI software creation is large and growing |
| Direct Lians demand | 0/10 | No qualified user has connected a stuck repository |
| Agent resistance | 4/10 | Ownership may help, but Codex and Claude remain strong substitutes |
| Differentiation | 3/10 | The production contract is a hypothesis, not a product |
| Scalability | 3/10 | Arbitrary rescue work becomes an agency quickly |
| Retention | 0/10 | No second-release behavior exists |
| Distribution | 0/10 | No referral loop has been observed |
| Revenue evidence | 1/10 | Rescue services show price points, not Lians willingness to pay |

## First 100 users do not come first

The first gate is ten qualified users, not one hundred broad signups.

A qualified user must have:

- a real repository;
- a flow that works in preview;
- a production blocker;
- permission to connect the project; and
- a reason the application needs to become live now.

Only after the ten-project test shows repeated repairs and declining human work
should Lians recruit a free first-100 cohort.

## Metrics

- qualified problem to repository connected;
- repository connected to supported;
- supported to verified live release;
- time to live;
- human engineering minutes;
- reusable repair rate;
- deployment regression and rollback rate;
- second release within 30 days; and
- qualified referrals.

## Decision rule

Continue only if real users hand over real stuck projects, a meaningful share
cannot get the same result from one ordinary Codex or Claude request, and the
human rescue burden falls with every cohort.

Otherwise, stop. Do not turn an agency workflow or a replaceable prompt into a
software company by changing the landing-page language.
