# Homelab outage drills

These are manual learning drills for synthetic data. Run `verify` before and
after each drill and record observed behavior; do not infer production SLAs.

## Alloy unavailable

1. `docker compose --env-file .env -f compose.yaml stop alloy`
2. Confirm Lians REST writes and recalls still work.
3. Confirm the workload reports OTLP delivery failure rather than fabricating a
   completed proof.
4. Start Alloy, wait for readiness, and run `verify` again.

The MVP uses in-memory collector queues, so spans created during a full Alloy
outage may be lost. A file-backed queue requires a separate validation spec.

## Redis unavailable

1. Stop Redis.
2. Inspect `/readyz`, Lians JSON logs, and the degradation metrics/alerts.
3. Observe the current fail-open cache behavior: database-backed recalls and
   decisions may still complete, but Redis-backed caching must not be reported as
   healthy. Record the actual readiness and degradation signals rather than
   treating a completed decision as a Redis durability proof.
4. Start Redis, wait for the cache health signal to recover, and run `verify`.

## Grafana unavailable

1. Stop Grafana while leaving Alloy, Tempo, Loki, and Prometheus running.
2. Confirm telemetry ingestion and Lians evidence linkage continue.
3. Restart Grafana and confirm the provisioned dashboard returns without manual
   configuration.

## Lians unavailable

1. Stop Lians and confirm Grafana can still query telemetry already retained in
   Tempo, Loki, and Prometheus. The bundled workload depends on Lians before it
   emits its partner trace, so this drill does not claim independent new traffic.
2. Confirm Prometheus fires `LiansApiDown` after its declared window.
3. Restart Lians and run `verify`.

## Reset and recovery

`lab.ps1 reset` / `lab.sh reset` is destructive: it removes all named lab
volumes, including Postgres, telemetry, Grafana, and generated proof state. It
preserves the ignored `.env` and exported `artifacts/` directory. The command
requires an explicit confirmation unless its force flag is supplied.
