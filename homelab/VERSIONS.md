# Tested homelab component set

The Compose file pins tags so a demo does not silently jump to a new major
release. Record resolved image digests in partner proof bundles.

| Component | Tag |
|---|---|
| Lians | local image from the current Git commit |
| PostgreSQL/pgvector | `pgvector/pgvector:pg16` |
| Redis | `redis:7-alpine` |
| Prometheus | `prom/prometheus:v3.12.0` |
| Grafana | `grafana/grafana:12.3.0` |
| Grafana Alloy | `grafana/alloy:v1.18.0` |
| Grafana Tempo | `grafana/tempo:2.10.0` |
| Grafana Loki | `grafana/loki:3.7.2` |
| Node plugin builder | `node:22-alpine` |
| Workload runtime | `python:3.12-alpine` |

Upgrade one component family at a time, run `lab verify`, inspect dashboards and
logs, and update this file only after the full acceptance procedure passes.
