# Regulated-memory evaluation preprint

This directory contains the source for a preprint about evaluating agent-memory
systems as auditable records rather than only as conversational recall systems.

## Build

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

The repository does not vendor a TeX distribution. The source is intentionally
limited to common arXiv-compatible packages.

The source was also built successfully with Tectonic 0.16.9 on July 17, 2026.
The verified output is four letter-sized pages. Text extraction reported no
unresolved references, replacement glyphs, or Unicode em dashes. All four rendered
pages were visually inspected after hiding PDF link borders.

## Evidence status

- Lians, mem0 OSS, and Graphiti OSS were executed live.
- Letta, Hindsight, and Supermemory are capability-assessed from public APIs.
- Every vendor has a public right of reply.
- Capability-assessed columns must not be described as live results until their
  adapters are executed against a supported vendor configuration.

## Before submission

- Incorporate material vendor corrections.
- Record exact dependency versions and run identifiers.
- Archive machine-readable per-cell evidence.
- Complete author and affiliation metadata.
- Build and visually inspect the PDF. Completed July 17, 2026.
- Run an external methodology review.

