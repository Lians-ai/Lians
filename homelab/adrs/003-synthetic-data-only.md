# ADR 003: synthetic telemetry only

Status: superseded in part by ADR 004

## Decision

Lians-operated demos, committed fixtures, and CI accept only synthetic scenarios.
Prompts, model outputs, subject IDs, credentials, and production/customer exports
are prohibited in those environments. ADR 004 defines the narrower exception for
an explicitly acknowledged, de-identified sample inside a customer-run local lab.

## Why

OTLP attributes and logs can silently contain sensitive content. A founder demo
environment is not a customer-approved processing boundary, regardless of
whether it runs locally.

## Consequence

Scenario fixtures must be reviewable in Git. A future spec must add automated
content scanning and explicit retention controls before any realistic data can
be considered.
