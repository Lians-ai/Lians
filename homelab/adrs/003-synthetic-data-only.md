# ADR 003: synthetic telemetry only

Status: accepted

## Decision

The lab accepts only synthetic scenarios. Prompts, model outputs, subject IDs,
credentials, and production/customer exports are prohibited.

## Why

OTLP attributes and logs can silently contain sensitive content. A founder demo
environment is not a customer-approved processing boundary, regardless of
whether it runs locally.

## Consequence

Scenario fixtures must be reviewable in Git. A future spec must add automated
content scanning and explicit retention controls before any realistic data can
be considered.
