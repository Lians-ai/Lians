# ADR 002: split runtime and partner trace pipelines

Status: accepted

## Decision

Alloy exposes a partner receiver on the standard OTLP ports 4317/4318 and an
internal runtime receiver on 14317/14318. Runtime spans go only to Tempo.
Partner spans fan out to Tempo and Lians.

## Why

Lians instruments FastAPI, including `/v1/traces`. If its runtime spans entered
the fan-out path, accepting one batch would create another instrumented ingestion
request and could recurse indefinitely. Separate receivers make the safety
boundary obvious and testable.

## Consequence

External partner applications use the evidence fan-out ports 4317 (gRPC) or
4318 (HTTP). Lians and other platform services that should not become decision
evidence use internal ports 14317/14318.
