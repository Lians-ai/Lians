# Lians app for Grafana

This app bundles the Lians operational dashboard and a configuration page for
the OTLP fan-out architecture used by the integration.

This directory is catalog-submission source, not proof of catalog publication.
Grafana Labs must review and sign a public plugin before it can be listed.

## Development

```bash
npm ci
npm run verify
```

The lab build target is Grafana 12.4.6. Grafana, React, and their shared browser
libraries are supplied by the Grafana host through SystemJS; they are installed
as development dependencies only for typechecking and compilation. The
production build fails if React, any `@grafana` package, or an audited host
library is copied into `dist/module.js`.

## Dependency security boundary

The lockfile applies only compatible patch/minor refreshes on top of Grafana
12.4.6: DOMPurify 3.4.12, Immutable 5.1.9, Lodash 4.18.1, react-use 17.6.1
(which moves js-cookie to 3.0.8), UUID 11.1.1, and OpenTelemetry Core 2.8.0.
This clears 26 of the 28 Dependabot findings that existed in this directory.

`npm audit` retains two moderate React Router advisories in the development-only
`@grafana/ui -> react-router-dom-v5-compat -> react-router@6.30.4` path. The
published fix requires React Router 7.18.0, an incompatible major that Grafana
12.4.6 does not request. Lians imports no router API, and the production bundle
inspection proves that this host-owned graph is not shipped in the plugin. Do
not force React Router 7 into the Grafana 12 lab; consume Grafana's compatible
upstream update instead. See Grafana's documentation on
[dynamically linked plugin dependencies](https://grafana.com/developers/plugin-tools/key-concepts/npm-dependencies).

Provision Alloy with [`provisioning/alloy-lians.alloy`](provisioning/alloy-lians.alloy).
Set `LIANS_OTLP_ENDPOINT` to the Lians origin (without `/v1/traces`) and pass a
write-scoped Lians key through `LIANS_API_KEY`. The pipeline sends every received
span to both Grafana Cloud and Lians; no sampling processor is configured.

For protobuf ingestion, install the server with `pip install 'lians[otel-receiver]'`.
OTLP/HTTP JSON works with the base installation.
