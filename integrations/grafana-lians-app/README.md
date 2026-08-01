# Lians app for Grafana

This app bundles the Lians operational dashboard and a configuration page for
the OTLP fan-out architecture used by the integration.

This directory is catalog-submission source, not proof of catalog publication.
Grafana Labs must review and sign a public plugin before it can be listed.

## Development

```bash
npm install
npm run build
```

Provision Alloy with [`provisioning/alloy-lians.alloy`](provisioning/alloy-lians.alloy).
Set `LIANS_OTLP_ENDPOINT` to the Lians origin (without `/v1/traces`) and pass a
write-scoped Lians key through `LIANS_API_KEY`. The pipeline sends every received
partner span to both Grafana Cloud and Lians; no sampling processor is configured.

The configuration deliberately exposes a second receiver on `14317/14318` for
Lians' own runtime spans. Set Lians' `OTEL_EXPORTER_OTLP_ENDPOINT` to that
receiver. Those spans go only to Grafana Cloud, preventing the instrumented
`/v1/traces` endpoint from recursively exporting traces back into itself.

For protobuf ingestion, install the server with `pip install 'lians[otel-receiver]'`.
OTLP/HTTP JSON works with the base installation.
