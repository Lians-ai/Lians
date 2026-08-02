# Publishing the SDKs

Lians ships Python, TypeScript, Go, Java, and C SDKs plus an MCP container and
Registry manifest. A unified annotated `vX.Y.Z` tag starts four GitHub Actions
workflows; the MCP Registry manifest is published separately after the matching
Python package is live.

## Release checklist

1. Set the same version in `VERSION` and every release manifest, including:
   - `agentmem/sdk/python/pyproject.toml`
   - `agentmem/sdk/typescript/package.json`
   - `agentmem/sdk/typescript/package-lock.json`
   - `agentmem/sdk/java/pom.xml`
   - `agentmem/sdk/go/version.go`
   - `agentmem/sdk/c/CMakeLists.txt`
   - `server.json`
2. Update package README installation examples.
3. Run `python scripts/check_release_contract.py`, then merge the release PR only
   after the full CI matrix passes.
4. Confirm PyPI and npm trusted publishing, Maven Central credentials and opt-in,
   GHCR package permissions, and GitHub Release permissions are configured.
5. Create and push one annotated tag:

```bash
git tag -a vX.Y.Z -m "release: vX.Y.Z"
git push origin vX.Y.Z
```

6. Monitor all four tag-triggered workflows until completion:
   - `publish-lian.yml`
   - `publish-lian-npm.yml`
   - `release.yml`
   - `publish-mcp-container.yml`
7. After PyPI exposes the exact release, publish `server.json` to the MCP Registry:

   ```bash
   mcp-publisher login github
   mcp-publisher publish server.json
   ```

8. Verify the published version from every public registry. A successful
   workflow is not proof that a registry or search index has propagated.

## Registry matrix

| SDK | Registry | Publication path | Authentication |
|---|---|---|---|
| Python | [PyPI](https://pypi.org/project/lians-sdk/) | `publish-lian.yml` builds the sdist and wheel, then publishes them | PyPI trusted publisher through GitHub OIDC |
| TypeScript | [npm](https://www.npmjs.com/package/@lians-ai/lians) | `publish-lian-npm.yml` builds, tests, and runs `npm publish` | npm trusted publisher through GitHub OIDC; no long-lived publish token |
| Go | proxy.golang.org and pkg.go.dev | `release.yml` creates a module-path tag | GitHub token supplied to the workflow |
| Java | Maven Central and GitHub Release JAR | `release.yml` signs and deploys with the `release` Maven profile | Sonatype credentials and GPG signing secrets |
| C | GitHub Release source archive | `release.yml` creates `lians-c-<version>.tar.gz` | GitHub token supplied to the workflow |
| MCP container | GitHub Container Registry | `publish-mcp-container.yml` waits for the exact PyPI release, smoke-tests the server, and publishes multi-architecture `X.Y.Z` and `latest` tags with SBOM and provenance | GitHub package permissions plus OIDC attestation permission |
| MCP manifest | Official MCP Registry | `mcp-publisher publish server.json` after the referenced PyPI package is live | Short-lived GitHub login for the `io.github.ebeirne/lians` namespace |

## npm trusted publishing

Configure the existing `@lians-ai/lians` package on npmjs.com with this trusted publisher:

| Field | Value |
|---|---|
| Provider | GitHub Actions |
| Organization or user | `Lians-ai` |
| Repository | `Lians` |
| Workflow filename | `publish-lian-npm.yml` |
| Allowed action | `npm publish` |

The workflow uses a GitHub-hosted runner, Node 24, npm 11.5.1 or later, and `id-token: write`. Publishing is OIDC-only: configure the trusted publisher before dispatching the workflow, and do not add a long-lived `NPM_TOKEN` fallback.

The workflow also supports manual dispatch. This is useful when registry authorization fails after a tag has already published successfully to the other registries. Supply the existing release tag; the workflow checks out that immutable tag and verifies its package version before publishing. Verify that the npm version is still unpublished first.

## Go module tags

The Go module lives in a subdirectory. `release.yml` mirrors `vX.Y.Z` to `agentmem/sdk/go/vX.Y.Z` automatically so consumers can run:

```bash
go get github.com/Lians-ai/Lians/agentmem/sdk/go@vX.Y.Z
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

## Container and MCP Registry

`publish-mcp-container.yml` validates the unprefixed `X.Y.Z` tag against
`VERSION`, waits for that exact `lians-sdk` version on PyPI, smoke-tests the
stdio MCP server, and publishes `ghcr.io/lians-ai/lians-mcp:X.Y.Z` plus
`latest`. Verify both tags resolve to the same OCI index and that it contains
`linux/amd64` and `linux/arm64` images.

The public Registry identity remains `io.github.ebeirne/lians`; the GitHub
organization hosting the repository does not change that established package
name. Publish the checked-in manifest explicitly with
`mcp-publisher publish server.json`, then verify exactly one active `isLatest`
entry for that identity.
