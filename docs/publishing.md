# Publishing the SDKs

Lians ships Python, TypeScript, Go, Java, and C SDKs. A unified `vX.Y.Z` tag runs the language-specific publication workflows and creates GitHub Release artifacts.

## Release checklist

1. Set the same version in every lock-step surface:
   - `pyproject.toml`
   - `agentmem/src/lians/version.py` and root `uv.lock`
   - `agentmem/sdk/python/pyproject.toml`
   - `agentmem/sdk/python/lians/client.py` and its `uv.lock`
   - `sdk/python/pyproject.toml` (private compatibility/conformance package)
   - `sdk/python/src/lians/client.py` and its `uv.lock`
   - `agentmem/sdk/typescript/package.json`
   - `agentmem/sdk/typescript/package-lock.json` and `src/client.ts`
   - `agentmem/sdk/java/pom.xml` and `LiansClient.VERSION`
   - `agentmem/sdk/go/version.go`
   - `agentmem/sdk/c/CMakeLists.txt` and `include/lians.h`
   - `server.json`
   - `deploy/helm/lians/Chart.yaml` (`version` and `appVersion`)
   - `k8s/kustomization.yaml` (application version label)
   - manual publisher defaults and CI OpenAPI snapshot paths under `.github/workflows/`
   - `CITATION.cff`, current installation examples, and `specs/openapi/README.md`
2. Update package README installation examples.
3. Merge the release PR only after the full CI matrix passes.
4. Confirm PyPI trusted publishing, npm trusted publishing, and Maven Central credentials are configured.
5. Create and push one annotated tag:

```bash
git tag -a v0.5.0 -m "release: v0.5.0"
git push origin v0.5.0
```

6. Monitor the SDK workflows and the production supply-chain workflow until completion:
   - `publish-lian.yml`
   - `publish-lian-npm.yml`
   - `release.yml`
   - `publish-mcp-container.yml`
   - `supply-chain.yml`
   - `backup-supply-chain.yml`
   - `publish-helm-chart.yml`
7. Verify the published version from each public registry. A successful workflow is not proof that a registry search index has propagated.
8. Only after the Python SDK is resolvable from PyPI, advance
   `integrations/mcpb/manifest.json` and `integrations/mcpb/uv.lock`, rebuild the
   MCPB, verify it against the public package in a clean host, and update the
   `VERIFIED_MCPB_PUBLICATION` guard in
   `.github/scripts/check_release_versions.py` before publishing that
   independently versioned downstream bundle. Never pre-lock an MCPB to an
   unpublished SDK.

The production container is published to GHCR as a signed, attested multi-architecture image only after its vulnerability gate passes. Deploy the digest printed by `supply-chain.yml`, never a mutable tag. See [Software supply-chain security](supply-chain-security.md) for independent verification and Kubernetes admission enforcement.

## Registry matrix

| SDK | Registry | Publication path | Authentication |
|---|---|---|---|
| Python | [PyPI](https://pypi.org/project/lians-sdk/) | `publish-lian.yml` builds the sdist and wheel, then publishes them | PyPI trusted publisher through GitHub OIDC |
| TypeScript | [npm](https://www.npmjs.com/package/@lians-ai/lians) | `publish-lian-npm.yml` builds, tests, and runs `npm publish --provenance` | npm trusted publisher through GitHub OIDC; no repository token |
| Go | proxy.golang.org and pkg.go.dev | `release.yml` creates a module-path tag | GitHub token supplied to the workflow |
| Java | Maven Central and GitHub Release JAR | `release.yml` signs and deploys with the `release` Maven profile | Sonatype credentials and GPG signing secrets |
| C | GitHub Release source archive | `release.yml` creates `lians-c-<version>.tar.gz` | GitHub token supplied to the workflow |
| Helm | GHCR OCI chart | `publish-helm-chart.yml` packages, attests, signs, publishes, re-pulls, and verifies the chart | GitHub token for GHCR plus GitHub OIDC for keyless signing |

## npm trusted publishing

Configure the existing `@lians-ai/lians` package on npmjs.com with this trusted publisher:

| Field | Value |
|---|---|
| Provider | GitHub Actions |
| Organization or user | `Lians-ai` |
| Repository | `Lians` |
| Workflow filename | `publish-lian-npm.yml` |
| Allowed action | `npm publish` |

The workflow uses a GitHub-hosted runner, Node 24, npm trusted publishing, and
`id-token: write`. It intentionally has no `NPM_TOKEN` fallback.

The workflow also supports manual dispatch for registry recovery. The operator
must supply the exact unpublished version, and the shared release-contract script
requires every platform/SDK/MCP/Helm metadata file to match before publication.

## Go module tags

The Go module lives in a subdirectory. `release.yml` mirrors `vX.Y.Z` to `agentmem/sdk/go/vX.Y.Z` automatically so consumers can run:

```bash
go get github.com/Lians-ai/Lians/agentmem/sdk/go@v0.5.0
```

## Maven Central

The Maven job requires these repository secrets:

- `OSSRH_USERNAME`
- `OSSRH_PASSWORD`
- `MAVEN_GPG_KEY`
- `MAVEN_GPG_PASSPHRASE`

It also requires the repository variable `PUBLISH_MAVEN_CENTRAL=true`. The Maven `release` profile builds source and Javadoc archives, signs every artifact, and deploys through the Sonatype Central Portal. Search indexing can lag behind a successful deployment.

## C source archive

The C SDK is distributed as source. `release.yml` packages `agentmem/sdk/c` into `lians-c-<version>.tar.gz` for consumers to vendor into their own build.

## Release evidence

`.github/scripts/check_release_versions.py` is the single version contract used
by Python, npm, SDK artifacts, API/backup images, the MCP image, and the Helm OCI
chart. Tagged paths cannot publish one surface at a different version.
It also verifies that the independently released MCPB is internally consistent,
exactly pins the SDK resolved by its lockfile, and is not ahead of the platform;
the MCPB is deliberately not required to equal the lock-step release version.

The Java JAR and deterministic C archive receive GitHub artifact attestations.
API, backup, and MCP images are vulnerability-gated, multi-architecture,
SBOM/provenance-bearing, keyless-signed, and self-verified after publication. The
Helm OCI chart is independently attested and keyless-signed, then pulled by digest
and byte-compared with the packaged artifact. Consumers must verify the exact
artifact, image, or chart digest rather than treating a successful workflow or
mutable version tag as trust evidence.
