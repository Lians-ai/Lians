# Lians Make web product

This is the active Lians consumer product.

> Describe what your group needs. Get a working app you can use and share.

## What works

- prompt to challenge, vote, quiz, leaderboard, tracker, or shared list;
- persistent shared state with Cloudflare D1;
- no participant account;
- native sharing with copy fallback;
- one-click remixing;
- first-party view, share, participation, and remix signals;
- responsive layouts; and
- reduced-motion support.

The generator produces constrained app schemas. It does not run arbitrary generated code.

## Local development

```bash
npm install
npm run dev
```

The local site is normally available at `http://localhost:3001` when that port is free.

## Checks

```bash
npm run lint
npm test
```

## Database

The D1 binding is named `DB`. Generate a migration after schema changes:

```bash
npm run db:generate
```

## Visual system

- near-black and aged-silver foundation;
- deep lilac as the only page accent;
- deep lilac Lians lotus on black;
- an IvyPresto-first editorial serif stack with Instrument Serif as the licensed web fallback;
- Bricolage Grotesque for compact utility text;
- oversized statements, square-edged surfaces, deep shadow, and subtle grain;
- GSAP, Anime.js, and Lenis for focused motion; and
- Paper Design Shaders for the liquid brand moment.

The tool inventory is a palette, not a checklist. A library is included only when it improves a real interaction or brand moment.
