# arXiv submission metadata

- Title: Can an Agent Memory Layer Prove What the Agent Knew? An Evaluation of Regulated-Memory Invariants
- Authors: Ethan Beirne
- Affiliation: Lians
- Primary category: cs.AI
- Suggested secondary category: cs.IR
- License: CC BY 4.0
- Source file: main.tex

## Abstract

Long-term agent-memory benchmarks primarily measure whether a system can retrieve useful evidence from conversational or interaction histories. Regulated uses add a different requirement: an operator may need to reconstruct what an agent knew at a past decision time, demonstrate that later facts could not influence that decision, and prove that erased material is no longer recoverable. We introduce an open evaluation of five regulated-memory invariants: stale-revision suppression, point-in-time recall, provable erasure, lookahead protection, and historical audit-state reconstruction. The harness evaluates product-level primitives rather than general architecture quality. Lians, mem0 OSS, and Graphiti OSS are executed live; Letta, Hindsight, and Supermemory are capability-assessed from their public API surfaces pending vendor-supplied live configurations. Under the stated scoring rule, Lians scores 5.0 of 5, Graphiti 2.0, Letta 1.0, Hindsight 1.0, Supermemory 1.0, and mem0 OSS 0.5. These results do not establish general memory superiority. They expose a distinct evaluation axis and provide runnable adapters, per-cell evidence, and a public correction process for testing it.

## Comments

4 pages. Open evaluation harness, runnable adapters, per-cell evidence, and public vendor correction process available at https://github.com/Lians-ai/Lians.
