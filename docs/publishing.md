# Publishing the SDKs

Lians ships five SDKs. This is the release process and the registry path for each.
Releases are cut by pushing a semver tag; `release.yml` builds the language
artifacts and attaches them to the GitHub Release.

```bash
git tag v0.2.1
git push origin v0.2.1
```

| SDK | Registry | How it's published | Secret(s) needed |
|-----|----------|--------------------|------------------|
| **Python** | [PyPI](https://pypi.org/project/lians-sdk) | `publish-lian.yml` builds sdist+wheel and uploads via `twine` | `PYPI_API_TOKEN` |
| **TypeScript** | [npm](https://www.npmjs.com/package/@ebeirne/lians) | `publish-lian-npm.yml` runs `npm publish` | `NPM_TOKEN` |
| **Go** | proxy.golang.org / pkg.go.dev | **No build step** — a module tag *is* the release (see below) | none |
| **Java** | Maven Central / GitHub Packages | `mvn deploy` (or jar attached to the Release) | `OSSRH_USERNAME`, `OSSRH_PASSWORD`, `MAVEN_GPG_KEY`, `MAVEN_GPG_PASSPHRASE` |
| **C** | source tarball on the GitHub Release | packaged by `release.yml` (vendored into the consumer build) | none |

## Go module tags

Because the Go module lives in a subdirectory, `go get` resolves a version from a
tag **prefixed with the module path**:

```bash
git tag agentmem/sdk/go/v0.2.1
git push origin agentmem/sdk/go/v0.2.1
```

Then consumers use:

```bash
go get github.com/Lians-ai/Lians/agentmem/sdk/go@v0.2.1
```

The plain `v0.2.1` release tag covers Python/TS/Java/C; the prefixed tag covers Go.

## Java → Maven Central

`release.yml` attaches the built jar to the GitHub Release out of the box (no
secrets). To publish to **Maven Central** instead, add the OSSRH + GPG secrets
above and switch the Java job to `mvn -B deploy -P release` with a
`distributionManagement` block and the `maven-gpg-plugin` in `pom.xml`. To publish
to **GitHub Packages** (simpler, no GPG), point `distributionManagement` at
`https://maven.pkg.github.com/Lians-ai/Lians` and authenticate with `GITHUB_TOKEN`.

## C

The C SDK is distributed as source (header + `.c` files) — the idiomatic model for
a small libcurl client. `release.yml` packages `agentmem/sdk/c` into
`lians-c-<version>.tar.gz` on the Release; consumers vendor it and build with
their own CMake/Make. (A future option: an apt/conan/vcpkg recipe.)
