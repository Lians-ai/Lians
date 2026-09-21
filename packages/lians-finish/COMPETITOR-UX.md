# Competitor UI/UX audit — 2026-09-20

This audit inspected the current public product surfaces for Devin, Cursor
Agents, Memorable, OpenRouter, and Portkey. It evaluates interaction hierarchy,
not brand taste.

## What the strongest interfaces do

### Cursor Agents

The signed-out product surface is almost empty: a narrow history sidebar, one
large task composer, one model selector, and one submit button. Advanced
capability is present but not explained before the user acts.

**Keep:** one dominant input; history secondary; configuration beside the send
action; large unused space that makes the next action obvious.

Source: <https://cursor.com/agents>

### Devin

The public page uses one literal category sentence — “Devin, the AI software
engineer” — followed by two actions and a large product screenshot. The product
screenshot communicates sessions, work, and evidence without a paragraph for
each concept.

**Keep:** describe the category in a single sentence; show output and evidence
after the action; avoid explaining internal architecture in the primary view.

Source: <https://devin.ai/>

### OpenRouter

The page presents one promise, two actions, and four numbers. Model routing,
privacy, availability, and pricing depth sit below the first viewport.

**Keep:** state the unifying outcome before the mechanisms; reserve metrics for
proof; separate selection from observability.

Source: <https://openrouter.ai/>

### Memorable

The marketing surface is visually distinctive but content-heavy. Its strongest
interaction is still compact: a direct dashboard action and a copyable install
command. Case-study numbers appear as proof rather than setup instructions.

**Keep:** strong identity, one installation action, and proof numbers. Do not
copy the long architecture narrative into the working product.

Source: <https://www.memorable.sh/>

### Portkey / Prisma AIRS AI Gateway

Portkey exposes gateway, observability, guardrails, governance, prompts, model
catalog, and enterprise security together. It communicates breadth, but the
first screen asks the user to understand the platform before doing anything.

**Avoid:** naming every subsystem in the working interface; presenting product
architecture as navigation; placing governance detail ahead of the user's job.

Source: <https://portkey.ai/>

## Lians decision

Lians should feel like Cursor's single composer with one extra promise: the
mission remains governed after it leaves the box.

The default view therefore contains only:

1. one outcome field;
2. one three-position usage policy;
3. Preview and Start;
4. three one-line signals: usage ceiling, proof readiness, mission state.

Workspace selection, constraints, validity, definition of done, verifier
command, agent inventory, and receipt totals remain available through two
progressive-disclosure controls. Route detail appears only after Preview. Run
events and receipt metrics appear only after Start.

## Copy ceiling

- Primary heading: seven words or fewer.
- Primary explanation: one sentence.
- Every control label: three words or fewer where practical.
- No roadmap, architecture diagram, or research claim in the working surface.
- Any claim about saved usage must appear only after observed receipts exist.

The application is a place to act. The website and documentation are the place
to explain.

## v0.9 implementation

The live public surfaces were visually rechecked before the v0.9 redesign.
Cursor Agents uses a matte sidebar, one centered composer, low-contrast borders,
and a deliberately empty work canvas. Devin's product presentation separates the
active session from its artifacts and proof. Memorable establishes identity with
one strong graphic system instead of filling the working surface with controls.

Lians now combines those interaction principles without copying their branding:

- a 238px navigation and recent-mission rail;
- one 760px primary work column with a single composer;
- a mission-context rail that appears only after a preview or run exists;
- a row-based route timeline instead of four competing stage cards;
- 11–14px working typography instead of micro-labels;
- blue used for state and action, not as a continuous glow; and
- the canonical Lians wordmark reduced to a compact brand mark and quiet watermark.

At narrow widths, navigation compresses to one bar, the context rail becomes a
two-column status grid, and route rows preserve their scan order. In the idle
state, the composer is the only working object: advanced mission controls live
behind its plus tool and the evidence rail stays absent until evidence exists.
v0.9.1 also establishes a 12px minimum interface-text size and reuses the exact
transparent blue wordmark referenced by the canonical GitHub README—including
its lotus above the “i”—as the shared sidebar, composer, and evidence asset.
It is served byte-for-byte from the packaged application rather than redrawn.
