# Lians Website Layout

> **One-line positioning:** Lians is the **system of record and control for
> consequential AI decisions**—preserving what the system knew, why it acted,
> who authorized it, and what changed next.

The site has **one job on the home page**: make a buyer experience an answerable
AI decision. Every claim should resolve to an inspectable receipt, incident,
policy, memory record, or reproducible benchmark. Core software and proof are
the hero—not adjectives, company mythology, or generic infrastructure language.

---

## 1. Global navigation

Keep it lean. Primary nav (left → right):

- **Decision System** (dropdown: Proof loop · Memory Studio · Authority Gate · Impact Intelligence)
- **Solutions** (dropdown by vertical: Financial services · Healthcare · Public sector)
- **Docs**
- **Pricing**
- **Compare** (vs mem0 / Zep — the regulated-eval table)
- Right side: **GitHub** (star count) · **Sign in** · **Get API key** (primary button)

Footer: Product · SDKs (Python/TS/Go/Java/C) · Security & compliance (SEC 17a-4, GDPR) ·
Docs · Changelog · Status · Legal.

---

## 2. Home page (top to bottom)

### 2.1 Hero
- **Headline:** "Make every AI decision answerable."
- **Subhead:** Lians gives teams the memory, authority, evidence, and controls to
  understand what AI knew, why it acted, who authorized it, and what changed
  next—across every model, agent, and provider.
- **Primary CTA:** See a decision investigated · **Secondary CTA:** Open Memory Studio
- **Proof object:** a real, downloadable Decision Receipt with visible integrity state.
- Standard beneath: "What did it know? · Why did it act? · Who authorized it? · What changed next?"

### 2.2 The answerability standard (one screen)
Four compact questions define the category:
1. **What did it know?** Point-in-time memory and provenance.
2. **Why did it act?** A complete decision evidence graph.
3. **Who authorized it?** Identity-bound policy and approval.
4. **What changed next?** Blast-radius detection and remediation.

### 2.3 How Lians works (the core software — the centerpiece)
A labeled architecture diagram + 4 capability cards. This is the most important section:

- **Bitemporal model** — every fact has valid-time *and* system-time; query memory
  "as of" any past moment. (Backtest, snapshot, lineage.)
- **Tamper-evident audit chain** — Merkle-hashed, append-only, WORM-compatible
  (SEC 17a-4). Anyone can verify the chain.
- **Crypto-shred / GDPR erasure** — per-subject DEK encryption; delete the key to
  provably erase, no re-index.
- **Memory admission control** *(flagship)* — policy gate on what's allowed to be
  written/recalled; the thing competitors don't have.

Each card → links to its docs deep-dive.

### 2.4 Proof: regulated eval vs the field
Embed the **head-to-head table** (Lians vs mem0 vs Zep) from the regulated eval. Make it
runnable/reproducible — link to the eval harness in the repo. Numbers, not adjectives.

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
2. **Every compliance claim links to evidence** — a doc, the audit-chain verifier, or the
   eval harness. No unbacked "enterprise-grade."
3. **Show real code that runs.** Hero snippet and quickstarts must be copy-paste correct.
4. **Differentiate on answerability**—memory, authority, evidence, and impact
   belong to one decision record. Regulated controls prove the depth of that
   category rather than becoming the whole brand.
5. **Honest install matrix.** Don't imply Maven Central until Java actually ships there.

---

## 5. Open items before launch

- [ ] Decide whether Java goes to Maven Central (flip `PUBLISH_MAVEN_CENTRAL`) or the SDK
      page stays "download the jar" — copy depends on this.
- [ ] Finalize pricing tiers / what's gated behind enterprise.
- [ ] Confirm which compliance claims are certified vs "designed to support" — legal review.
- [ ] Produce the architecture diagram asset for §2.3.
