# Publishing the SDKs

Lians ships Python, TypeScript, Go, Java, and C SDKs. A unified `vX.Y.Z` tag runs the language-specific publication workflows and creates GitHub Release artifacts.

## Release checklist

1. Set the same version in:
   - `agentmem/sdk/python/pyproject.toml`
   - `agentmem/sdk/typescript/package.json`
   - `agentmem/sdk/typescript/package-lock.json`
   - `agentmem/sdk/java/pom.xml`
2. Update package README installation examples.
3. Merge the release PR only after the full CI matrix passes.
4. Confirm PyPI trusted publishing, npm trusted publishing, and Maven Central credentials are configured.
5. Create and push one annotated tag:

```bash
git tag -a v0.4.2 -m "release: v0.4.2"
git push origin v0.4.2
```

6. Monitor all three workflows until completion:
   - `publish-lian.yml`
   - `publish-lian-npm.yml`
   - `release.yml`
7. Verify the published version from each public registry. A successful workflow is not proof that a registry search index has propagated.

## Registry matrix

| SDK | Registry | Publication path | Authentication |
|---|---|---|---|
| Python | [PyPI](https://pypi.org/project/lians-sdk/) | `publish-lian.yml` builds the sdist and wheel, then publishes them | PyPI trusted publisher through GitHub OIDC |
| TypeScript | [npm](https://www.npmjs.com/package/@lians-ai/lians) | `publish-lian-npm.yml` builds, tests, and runs `npm publish` | npm trusted publisher through GitHub OIDC, with `NPM_TOKEN` retained temporarily as a migration fallback |
| Go | proxy.golang.org and pkg.go.dev | `release.yml` creates a module-path tag | GitHub token supplied to the workflow |
| Java | Maven Central and GitHub Release JAR | `release.yml` signs and deploys with the `release` Maven profile | Sonatype credentials and GPG signing secrets |
| C | GitHub Release source archive | `release.yml` creates `lians-c-<version>.tar.gz` | GitHub token supplied to the workflow |

## npm trusted publishing

Configure the existing `@lians-ai/lians` package on npmjs.com with this trusted publisher:

| Field | Value |
|---|---|
| Provider | GitHub Actions |
| Organization or user | `Lians-ai` |
| Repository | `Lians` |
| Workflow filename | `publish-lian-npm.yml` |
| Allowed action | `npm publish` |

The workflow uses a GitHub-hosted runner, Node 24, npm 11.5.1 or later, and `id-token: write`. After one OIDC publication succeeds, remove the `NPM_TOKEN` repository secret and the fallback environment variable from the workflow.

The workflow also supports manual dispatch. This is useful when registry authorization fails after a tag has already published successfully to the other registries. A manual rerun publishes the version currently present on the selected branch, so verify that the npm version is still unpublished first.

## Go module tags

The Go module lives in a subdirectory. `release.yml` mirrors `vX.Y.Z` to `agentmem/sdk/go/vX.Y.Z` automatically so consumers can run:

```bash
go get github.com/Lians-ai/Lians/agentmem/sdk/go@v0.4.1
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
