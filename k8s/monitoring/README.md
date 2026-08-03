# Lians monitoring overlay

This optional overlay requires the Prometheus Operator CRDs and a Blackbox
Exporter service. Replace the public readiness target, create the metrics token
Secret through an external secret manager, and align each resource's selector
labels with the Prometheus installation before applying.

The ServiceMonitors produce the exact jobs consumed by
`ops/prometheus/lians-rules.yaml`: `agentmem`, `lians-otel-collector`, and the
optional `lians-gate-mediator`. The Probe explicitly sets `lians-api`. The
mediator scrape requires a distinct bearer-token Secret and serving-CA Secret
in `monitoring`; its certificate must cover the configured cluster DNS name.

Package the canonical rules as a ConfigMap with:

```sh
kubectl apply -k ops/prometheus/
```

Then mount `lians-prometheus-rules/lians-rules.yaml` through the deployment's
rule-file mechanism, or translate its `groups` unchanged into a PrometheusRule.
Replace relative runbook annotations with immutable release URLs first. Validate
with the exact deployed `promtool`; this overlay intentionally does not bypass
that release gate.
