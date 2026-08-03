# Standalone Gate mediator deployment

This directory is intentionally separate from the Lians API deployment. It
reuses the signed Lians image but overrides its command with the dedicated
`lians-gate-mediator` entrypoint.

Before applying the example:

1. Replace the Gate origin, exact route, canonical target, provider binding,
   and DNS/IP allowlists in `mediator-config.example.json`. Set the image to the
   signature-verified release subject before rendering:

   ```bash
   cd deploy/gate-mediator
   kustomize edit set image agentmem=ghcr.io/OWNER/REPOSITORY@sha256:DIGEST
   ```
2. Provide an internal TLS endpoint for Lians. The example assumes an
   authenticated service-mesh/API sidecar on port 8443; clear-text cluster HTTP
   is not a supported permit path.
3. Create a dedicated barrier-scoped Lians credential with `write`, call
   `/v1/identity/whoami`, and pin the returned principal, namespace, and barrier
   in the config.
4. Create secret files without trailing newlines:

   ```bash
   kubectl -n agentmem create secret generic lians-gate-mediator-secrets \
     --from-file=caller-token=./caller-token \
     --from-file=metrics-token=./metrics-token \
     --from-file=gate-api-key=./gate-api-key \
     --from-file=provider-credential=./provider-credential \
     --from-file=tls.crt=./mediator-tls.crt \
     --from-file=tls.key=./mediator-tls.key
   ```

5. Add one exact provider `ipBlock` to `networkpolicy.yaml`, or replace that
   policy with a Cilium/service-mesh/egress-gateway policy that enforces the
   configured FQDN. The shipped policy intentionally omits provider egress, so
   an unreviewed example cannot perform a side effect.
6. Apply with `kubectl apply -k deploy/gate-mediator/`.

There is no Ingress resource. The CLI requires an inbound TLS certificate;
`Service` is cluster-local and NetworkPolicy
admits only pods labelled `app.kubernetes.io/component: evaluator`. The Lians
API NetworkPolicy must also admit pods labelled
`app.kubernetes.io/component: gate-mediator`; the raw K8s and Helm policies in
this repository include that support. The example rolls with zero unavailable
replicas, keeps one replica available during voluntary disruption, and grants
up to 120 seconds for an in-flight consume-then-dispatch sequence to drain.

`/metrics` requires its own bearer token and exposes only the mediator's
fixed-cardinality provider outcome and latency families. Keep that token
different from the evaluator caller token. If Prometheus Operator is installed,
review and apply `servicemonitor.example.yaml` separately; create its bearer
token and CA Secrets in the `monitoring` namespace, and match the certificate's
DNS SAN. The example NetworkPolicy admits only Prometheus-labelled pods from
that namespace. Never add the ServiceMonitor CR to the base Kustomization when
the CRD is absent.

The Docker Compose file requires
`LIANS_IMAGE=ghcr.io/OWNER/REPOSITORY@sha256:DIGEST`; it binds only to loopback,
runs read-only/non-root, and mounts secrets read-only. Docker Compose cannot
express a reliable FQDN egress allowlist; use a host firewall or an authenticated
egress gateway before using it for real provider authority. On Linux, make the
mounted secret files readable by UID/GID `10001:10001` but never world-readable
or group/world-writable; the mediator rejects unsafe modes.
