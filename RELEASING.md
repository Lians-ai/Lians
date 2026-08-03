# Releasing Lians

One annotated `vX.Y.Z` tag releases the lock-step platform, five SDKs, API and
backup images, MCP image, and signed OCI Helm chart. The private package under
`sdk/python` is retained only for compatibility/conformance testing and is never
uploaded. The desktop MCPB under `integrations/mcpb` is a downstream bundle, not
a lock-step artifact: it advances only after the corresponding `lians-sdk` is
resolvable from PyPI and has been verified independently.

## Preflight

1. Update every version listed in [docs/publishing.md](docs/publishing.md), release
   notes, and supported installation examples.
2. Prove the shared contract locally:

   ```bash
   python .github/scripts/check_release_versions.py X.Y.Z
   ```

3. Merge only after the complete CI, dependency review, CodeQL, schema/chart, SDK,
   site, and image-candidate gates pass.
4. Confirm protected `pypi` and `npm` environments have the exact OIDC trusted
   publishers; confirm Maven Central secrets and the
   `PUBLISH_MAVEN_CENTRAL=true` variable when Java publication is intended.
5. Protect the release tag pattern and require an approved maintainer for
   environment deployments.

## Cut the release

```bash
git tag -a vX.Y.Z -m "release: vX.Y.Z"
git push origin vX.Y.Z
```

Every publishing workflow calls the same version-contract script. A mismatched
platform, Python, private compatibility, TypeScript lock/package, Java, Go, C,
MCP, Helm chart, or Helm application version stops that path before publication.

| Workflow | Result |
|---|---|
| `publish-lian.yml` | Hash-constrained Python wheel/sdist to PyPI through OIDC |
| `publish-lian-npm.yml` | TypeScript package to npm through OIDC with provenance |
| `release.yml` | Attested Java JAR, deterministic attested C archive, Go module tag, optional signed Maven Central release |
| `supply-chain.yml` | Vulnerability-gated, SBOM/provenance-bearing, keyless-signed API image |
| `backup-supply-chain.yml` | Equivalent hardened WORM backup image |
| `publish-mcp-container.yml` | Contract-smoked, scanned, attested, signed MCP image |
| `publish-helm-chart.yml` | Rendered, attested, keyless-signed, re-pulled OCI Helm chart |

## Verify before announcing

- Install `lians-sdk==X.Y.Z` into a clean environment outside the monorepo and
  exercise both remote and `[local]` modes.
- Resolve and test the npm, Go, Maven, Java, and C artifacts by immutable
  version/digest; verify GitHub artifact attestations for Java/C.
- Verify Cosign and GitHub provenance for the exact API, backup, and MCP image
  digests and confirm the vulnerability decision and SBOM correspond to them.
- Verify the Helm OCI digest and workflow identity, pull by digest, and render it
  with the protected production values before promotion.
- Confirm the Go module-path tag `agentmem/sdk/go/vX.Y.Z` points to the release
  commit.
- Publish `server.json` to the MCP registry using its authenticated publisher
  and verify the registry returns exactly `X.Y.Z`; container publication does not
  update that registry entry.
- After PyPI propagation is verified, advance and relock the independently
  versioned MCPB against the public SDK, pack it, verify it in a clean host, and
  publish it through its separate channel. Never create an MCPB lock that points
  at an SDK version the registry cannot yet resolve.
- Record registry URLs, digests, attestation results, workflow run IDs, release
  commit, approver, and UTC time in the release evidence package.

Do not rerun a failed publisher against an already-published immutable version.
Diagnose registry state first; use the explicit npm/MCP manual version input only
when the requested version is still unpublished and the release contract passes.
