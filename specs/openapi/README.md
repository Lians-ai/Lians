# Versioned OpenAPI contracts

Lians checks in independent OpenAPI 3.1 contracts for its public tenant API and
its separately deployed administrative API. The documents are generated from
the same canonical `lians.main` application imported by the production wheel,
with `API_SURFACE` fixed in a fresh process for each contract.

For release `0.5.0`:

- `public-v0.5.0.json` is the customer/workload surface.
- `admin-v0.5.0.json` is the isolated operator surface.

Regenerate intentionally after reviewing an API change:

```powershell
uv run --offline python .github/scripts/openapi_contract.py `
  --surface public --output specs/openapi/public-v0.5.0.json
uv run --offline python .github/scripts/openapi_contract.py `
  --surface admin --output specs/openapi/admin-v0.5.0.json
```

CI renders each surface in its own process and fails on byte-for-byte drift. A
release version change must create newly versioned snapshots and update the CI
paths; do not silently overwrite the contract for an already published release.

These documents describe wire shape and authorization declarations. They do not
replace the Decision Receipt, Universal Recorder, or control-plane semantic
specifications in the adjacent directories.
