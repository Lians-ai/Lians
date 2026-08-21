# Lians Deploy All replacement test

Date: 2026-08-20

## Verdict

**Hard no on the current idea as a company.**

> One idea in. One live application out.

This is a valuable capability, but it is already a product category and is now
an explicit workflow inside general AI agents. It is not enough to combine a
model, website tools, SEO checks, and deployment behind one prompt.

The strongest current replacement evidence is direct:

- OpenAI documents a Codex workflow that turns a repository, screenshot,
  design, or rough idea into a working website, runs checks, deploys a preview,
  and returns a live URL. Its starter prompt asks for almost the exact proposed
  Lians outcome. See the [official Codex deployment use
  case](https://learn.chatgpt.com/use-cases/deploy-app-or-website).
- Anthropic positions Claude Code across build, deploy, deployment management,
  monitoring, and end-to-end implementation. It also supports reusable skills
  for repeated shipping workflows. See [Claude Code common developer use
  cases](https://support.claude.com/en/articles/14553517-claude-code-common-developer-use-cases)
  and the [Claude Code product page](https://claude.com/product/claude-code).
- Replit already lets a user invoke it from ChatGPT, describe an app, and
  receive an automatically deployed link. See [Replit in
  ChatGPT](https://docs.replit.com/references/platforms/chatgpt).
- Replit also exposes a natural-language `create_app_from_prompt` tool that it
  describes as producing live deployed apps. See the [Replit MCP
  server](https://docs.replit.com/platforms/mcp-server).
- Lovable, Wix, Canva, Framer, Gamma, Squarespace, Bolt, and related builders
  already cover prompt-to-site or prompt-to-app creation and publishing.

The answer to the replacement question is therefore:

> Yes. Codex, Claude Code, Replit, and focused builders can already build and
> deploy an ordinary website or supported application from one strong prompt.

If Lians merely makes this workflow prettier, it is a wrapper.

## What market demand is actually proven

Demand for making software with AI is enormous. Lovable reported [$200 million
in ARR and 100,000 new projects per
day](https://lovable.dev/blog/one-year-of-lovable) in November 2025. Wix
reported approximately [304.2 million registered users at the end of
2025](https://www.sec.gov/Archives/edgar/data/1576789/000162828026015222/wix-20251231.htm),
and Canva says more than [89 million websites have been
created](https://www.canva.com/newsroom/news/canva-websites/) with Canva
Websites.

That validates the category. It does not validate Lians. The same evidence
shows that incumbents already own the obvious creation flow.

## The customer problem that survives

The recurring unsolved moment is later:

> My AI-built app works in preview, but it breaks in production, costs too much,
> or becomes unsafe to change.

Recent public customer reports describe:

- builds that pass but fail inside deployment infrastructure, with repeated
  attempts and lost client revenue in a [May 2026 Replit
  report](https://www.reddit.com/r/replit/comments/1to09km/replit_support_and_publishing_is_awful/);
- a non-coder finding that development and production behave differently in an
  [April 2026 Replit report](https://www.reddit.com/r/replit/comments/1skorhm/should_proddev_once_deployed/);
- auth, database, domain, SSL, and production failures that consumed about $100
  without resolving the app in a [deployment account from a
  non-coder](https://www.reddit.com/r/replit/comments/1m58nzn);
- an AI-built application that became unreliable around 50 users and was moved
  to a separate production stack in an [April 2026 migration
  account](https://www.reddit.com/r/replit/comments/1sqtbfh/my_clients_replit_app_hit_200_daily_users_heres/);
  and
- Bolt users asking for help with GitHub, domains, auth, secrets, environments,
  logging, and deployment in a [March 2026 production
  thread](https://www.reddit.com/r/boltnewbuilders/comments/1s1k9s8/converting_development_to_production/).

These are customer anecdotes, not population estimates. They are useful
problem signals, not proof of a scalable company.

The appearance of businesses such as
[AppStuck](https://www.appstuck.com/ai-app-rescue), which advertises a $350
minimum for an isolated production fix and larger rescue engagements, suggests
some willingness to pay. It also exposes the danger: arbitrary app rescue is
currently an engineering service with high exception handling, not proven
self-serve software.

## Replacement matrix

| Customer job | Best existing replacement | Lians advantage today | Decision |
|---|---|---|---|
| Turn a rough idea into a website | Codex plus Vercel, Replit, Lovable, Wix, Bolt | None proven | Reject |
| Generate a launch site and technical SEO | Focused website builders and coding agents | Tool bundling only | Reject |
| Create and deploy a simple app from ChatGPT | Replit integration | None proven | Reject |
| Fix an ordinary build error | Codex or Claude Code | None proven | Reject |
| Move a preview app into reliable production | Agent plus hosting, or a rescue agency | Possible ownership and automation | Test only |
| Keep an AI-built app healthy through later changes | Developer, agency, or managed platform | Possible continuous contract | Test only |

## The only version worth testing

The one-word action becomes:

> **Ship.**

The test promise is:

> Your AI app works in preview. Lians gets it live and keeps it live.

This is not Deploy All from a raw idea. It starts with something the user has
already built and cannot confidently put into production.

Deploy All can remain the name of an internal pipeline. It is not the public
product or the reason a customer chooses Lians.

## Exact first experiment

Accept one supported application shape:

- source is a GitHub repository exported from an AI builder;
- frontend is React with Vite;
- backend, when present, is an existing Supabase project;
- one core user flow already works in preview;
- no new product features are requested; and
- no payment, health, financial, or other high-consequence workflow is allowed.

Lians must:

1. import the repository without making the user understand its architecture;
2. identify build, environment, secret, auth, database, and routing blockers;
3. repair only the supported production blockers;
4. deploy an immutable preview on one managed infrastructure path;
5. run the core flow in a real browser at mobile and desktop sizes;
6. show the exact release and required permissions for approval;
7. publish the approved version;
8. monitor the live flow and cost boundary; and
9. roll back automatically when a later Lians release breaks the contract.

The product returns only four useful states:

```text
UNSUPPORTED
NEEDS YOU
READY TO SHIP
LIVE
```

`LIVE` means the approved revision is deployed and the agreed production flow
passes. It does not mean the app is secure at every layer, can handle arbitrary
scale, or will attract users.

## Why this may be more than a wrapper

The model can suggest code. Lians must own a production contract that survives
model substitution:

- one supported runtime and deployment shape;
- deterministic configuration and release rules;
- a real browser test bound to the deployed revision;
- least-privilege secret handling;
- continuous monitoring;
- cost limits;
- immutable releases and rollback; and
- a growing library of measured failure patterns and repairs.

If Lians returns code, sends the user to Vercel, asks them to debug with chat,
or stops after the first live URL, the replacement test fails.

## Ratings

These are research judgments, not customer results.

| Dimension | Generic Deploy All | Lians Ship hypothesis |
|---|---:|---:|
| Problem intensity | 5/10 | 8/10 |
| Existing demand | 9/10 | 5/10 |
| Resistance to Codex or Claude replacement | 1/10 | 4/10 |
| Current differentiation | 1/10 | 3/10 |
| Repeat use | 3/10 | 6/10 |
| Product-led distribution | 5/10 | 4/10 |
| Scalability | 6/10 | 3/10 |
| Margin potential | 5/10 | 4/10 |
| Direct Lians evidence | 0/10 | 0/10 |

No version deserves 10 out of 10 before real users hand over real projects.
Calling it 10 out of 10 now would hide the exact risks that can kill the
company.

## Free demand test before a platform build

Recruit ten people who already have a stuck AI-built application. Do not recruit
people who merely say they have an idea.

For each candidate:

1. record the live failure and the business consequence;
2. ask for repository access before offering a solution;
3. run the same plain request through Codex or Claude as the replacement
   baseline;
4. complete the first rescues manually behind the Lians flow;
5. classify every minute of human intervention;
6. identify which fixes repeat and can become product code; and
7. monitor the release for 30 days.

Continue only if:

- ten independent users provide a real repository and deployment problem;
- at least five are not solved by one ordinary Codex or Claude request;
- at least seven reach `LIVE` within 24 hours;
- median human engineering time falls below 30 minutes after the first three;
- no release exposes secrets, loses data, or creates a critical regression;
- at least four users ship another update within 30 days; and
- at least two users refer another stuck builder.

Kill or narrow the idea if:

- users will describe the problem but will not connect a repository;
- a normal Codex or Claude workflow solves most cases just as easily;
- every rescue needs unique architecture work;
- support time does not fall with each cohort;
- most problems involve unsupported high-consequence systems; or
- users disappear after the first deploy.

## Explicit non-goals

- another prompt-to-app chat box;
- arbitrary applications from one sentence;
- every tool from the supplied inventory in every build;
- a website design studio;
- an SEO ranking promise;
- new application features during the Ship experiment;
- support for every framework, database, and cloud;
- autonomous production changes without approval; and
- platform engineering before the ten-project demand test passes.
