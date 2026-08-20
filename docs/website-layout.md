# Lians Website Layout

> **One-line positioning:** Lians is the **memory layer for regulated AI** - bitemporal,
> auditable, and crypto-shreddable, so teams in finance, healthcare, and the public
> sector can give agents long-term memory without failing an audit.

The site has **one job on the home page**: make a technical buyer believe Lians is the
only memory layer they can put in front of a compliance officer. Everything below serves
that. Core software is the hero - not the company, not the blog.

---

## 1. Global navigation

Keep it lean. Primary nav (left → right):

- **Product** (dropdown: How it works · Audit & compliance · Security · SDKs)
- **Solutions** (dropdown by vertical: Financial services · Healthcare · Public sector)
- **Docs**
- **Pricing**
- **Compare** (vs mem0 / Zep - the regulated-eval table)
- Right side: **GitHub** (star count) · **Sign in** · **Get API key** (primary button)

Footer: Product · SDKs (Python/TS/Go/Java/C) · Security & compliance (SEC 17a-4, GDPR) ·
Docs · Changelog · Status · Legal.

---

## 2. Home page (top to bottom)

### 2.1 Hero
- **Headline:** "AI memory your auditor can trust."
- **Subhead:** Bitemporal memory with a tamper-evident audit chain (SEC 17a-4 / WORM)
  and GDPR crypto-shred - for agents that operate in regulated environments.
- **Primary CTA:** Get an API key · **Secondary CTA:** Read the architecture
- **Code-in-hero:** a 5-line install + write/recall snippet, language-tabbed
  (`pip install lians-sdk`, `npm i @lians-ai/lians`, `go get`). Show it actually working.
- Trust strip beneath: "Five-language SDKs · OIDC-signed releases · RLS tenant isolation."

### 2.2 The problem (one screen)
Three short columns naming what generic memory (mem0/Zep/vector DBs) can't do:
1. **No audit trail** - you can't prove what the agent knew, and when.
2. **No right-to-be-forgotten** - deleting a user means re-indexing everything.
3. **No tenant isolation guarantees** - one query bug leaks across customers.

### 2.3 How Lians works (the core software - the centerpiece)
A labeled architecture diagram + 4 capability cards. This is the most important section:

- **Bitemporal model** - every fact has valid-time *and* system-time; query memory
  "as of" any past moment. (Backtest, snapshot, lineage.)
- **Tamper-evident audit chain** - Merkle-hashed, append-only, WORM-compatible
  (SEC 17a-4). Anyone can verify the chain.
- **Crypto-shred / GDPR erasure** - per-subject DEK encryption; delete the key to
  provably erase, no re-index.
- **Memory admission control** *(flagship)* - policy gate on what's allowed to be
  written/recalled; the thing competitors don't have.

Each card → links to its docs deep-dive.

### 2.4 Proof: regulated eval vs the field
Embed the **head-to-head table** (Lians vs mem0 vs Zep) from the regulated eval. Make it
runnable/reproducible - link to the eval harness in the repo. Numbers, not adjectives.

### 2.5 Built for production
Compact grid: RLS multi-tenant isolation · idempotency + retries · health/readiness
probes · RBAC + SIEM hooks · DEK/session caching for latency. One line each, link to docs.

### 2.6 Install on every stack
Five SDK logos with the one-liner for each (Python, TypeScript, Go, Java, C). Honest
state: Python/TS/Go = registry installs; Java/C = released artifacts. Link to quickstarts.

### 2.7 Vertical solutions teaser
Three cards (Financial services · Healthcare · Public sector) → solution pages.

### 2.8 Final CTA band
"Give your agents memory that holds up in an audit." → Get API key · Talk to us.

---

## 3. Supporting pages

| Page | Purpose | Must contain |
|---|---|---|
| **Product / How it works** | Deep technical narrative | Architecture, bitemporal model, audit chain, admission control |
| **Audit & compliance** | The compliance-officer page | SEC 17a-4 / WORM posture, GDPR crypto-shred, audit-chain verification, data residency |
| **Security** | Buyer due-diligence | RLS isolation, encryption/DEK, RBAC, SIEM, non-superuser DB posture, signed releases |
| **SDKs** | Install hub | Per-language quickstart, version badges, links to PyPI/npm/pkg.go.dev/Release |
| **Compare** | Win the bake-off | Regulated-eval table, feature matrix vs mem0/Zep, "why audit matters" |
| **Pricing** | Convert | Tiers, what's gated (admission control, WORM, SSO), enterprise/contact |
| **Docs** | Activate | Quickstart, API reference, recipes (snapshot, backtest, erasure cert) |
| **Solutions/{vertical}** | Speak the buyer's language | Vertical use case, the specific regulation it satisfies, proof |

---

## 4. Design & messaging principles

1. **The software is the hero.** Lead with architecture and proof, not company story.
2. **Every compliance claim links to evidence** - a doc, the audit-chain verifier, or the
   eval harness. No unbacked "enterprise-grade."
3. **Show real code that runs.** Hero snippet and quickstarts must be copy-paste correct.
4. **Differentiate on the regulated wedge** - audit chain, crypto-shred, admission control,
   tenant isolation. That's the moat; say it on every page.
5. **Honest install matrix.** Don't imply Maven Central until Java actually ships there.

---

## 5. Open items before launch

- [ ] Decide whether Java goes to Maven Central (flip `PUBLISH_MAVEN_CENTRAL`) or the SDK
      page stays "download the jar" - copy depends on this.
- [ ] Finalize pricing tiers / what's gated behind enterprise.
- [ ] Confirm which compliance claims are certified vs "designed to support" - legal review.
- [ ] Produce the architecture diagram asset for §2.3.
