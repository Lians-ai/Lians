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
| TypeScript | [npm](https://www.npmjs.com/package/@lians-ai/lians) | `publish-lian-npm.yml` builds, tests, and runs `npm publish` | npm trusted publisher through GitHub OIDC; no long-lived publish token |
| Go | proxy.golang.org and pkg.go.dev | `release.yml` creates a module-path tag | GitHub token supplied to the workflow |
| Java | Maven Central and GitHub Release JAR | `release.yml` signs and deploys with the `release` Maven profile | Sonatype credentials and GPG signing secrets |
| C | GitHub Release source archive | `release.yml` creates `lians-c-<version>.tar.gz` | GitHub token supplied to the workflow |

## Windows desktop package

The Windows consumer job is disabled by default. Before setting the repository
variable `PUBLISH_SIGNED_LIANS_DESKTOP=true`, configure:

- repository secret `WINDOWS_SIGNING_CERT_PFX_BASE64`: the publisher PFX encoded
  as one base64 string;
- repository secret `WINDOWS_SIGNING_CERT_PASSWORD`: the PFX password; and
- repository variable `WINDOWS_SIGNING_CERT_SHA1`: the exact SHA-1 thumbprint of
  the expected publisher certificate, without relying on the PFX contents alone.

The release runner imports the certificate into its ephemeral current-user
store, confirms the thumbprint, signs `LiansMemory.exe`, builds the per-user
NSIS setup, signs the setup executable, and validates both Authenticode chains.
It then performs an actual silent install, opens the bundled Lians App through
the installed Bridge, verifies local runtime discovery, silently uninstalls,
and proves that encrypted memory was preserved. Only then does it upload
`Lians-Setup-<version>.exe` and its SHA-256 checksum. The certificate is removed
from the runner in an `always()` cleanup step; the PFX file is removed
immediately after import.

This flag publishes Windows only. Do not infer macOS notarization or Linux
package signing from a successful Windows job.

## macOS desktop packages

The Apple-silicon and Intel consumer jobs are independently disabled by
default. Before setting `PUBLISH_SIGNED_LIANS_MACOS=true`, configure:

- repository secret `MACOS_SIGNING_CERT_P12_BASE64`: the Developer ID
  Application certificate and private key encoded as one base64 string;
- repository secret `MACOS_SIGNING_CERT_PASSWORD`: the P12 password;
- repository variable `MACOS_SIGNING_IDENTITY`: the exact full identity, such
  as `Developer ID Application: Example, Inc. (TEAMID)`;
- repository variable `MACOS_SIGNING_TEAM_ID`: the expected Apple Team ID;
- repository secret `APPLE_NOTARY_KEY_P8_BASE64`: an App Store Connect API key
  encoded as one base64 string;
- repository variable `APPLE_NOTARY_KEY_ID`: that API key's Key ID; and
- repository variable `APPLE_NOTARY_ISSUER_ID`: its Issuer ID.

The release matrix builds on native `macos-15` (`arm64`) and
`macos-15-intel` (`x86_64`) runners. It imports the publisher credential into
an ephemeral keychain and requires exactly one valid identity matching the
configured full name. The Developer ID identity is passed to PyInstaller during
the one-file build so its embedded native libraries receive trusted signatures;
signing only the finished outer executable is not sufficient. The job then
builds and signs `Lians.app` and the architecture-labelled DMG.

`notarytool` must return `Accepted`. The workflow staples and validates the
ticket, asks Gatekeeper to assess the disk image, mounts it, checks the bundle
ID, version, architecture, signing authority, and Team ID, copies the app into
a temporary Applications directory, and exercises the bundled encrypted
Bridge. Only then does it upload the DMG and SHA-256 checksum. An `always()`
step deletes the temporary keychain, P12, API key, and notary response.

Passing this publisher gate proves the package is trusted and executable. Do
not promote the macOS build to normal users until the Lians App also exposes
plain-language removal controls that disconnect managed AI integrations and
separately offer a default-safe choice to keep or permanently erase memory.

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
