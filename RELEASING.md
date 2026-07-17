# Releasing Lians

One command cuts a release across all five SDKs. Versions are kept in lock-step.

```bash
# 1. Bump versions (already 0.3.0 — see the files below) and update CHANGELOG.md
# 2. Tag and push:
git tag v0.3.0
git push origin v0.3.0
```

Pushing a `vX.Y.Z` tag triggers:

| Workflow | Does |
|----------|------|
| `publish-lian.yml` | Builds + publishes **Python** `lians-sdk` to PyPI (OIDC trusted publishing) |
| `publish-lian-npm.yml` | `npm publish` **TypeScript** `@lians-ai/lians` (needs `NPM_TOKEN`) |
| `release.yml` → `java-jar` | Attaches the **Java** jar to the GitHub Release |
| `release.yml` → `c-tarball` | Attaches `lians-c-<version>.tar.gz` (the **C** source) to the Release |
| `release.yml` → `go-tag` | Mirrors the tag to `agentmem/sdk/go/vX.Y.Z` so `go get …@vX.Y.Z` resolves |
| `release.yml` → `maven-central` | Publishes **Java** to Maven Central — only when opted in (below) |

## Version locations (keep in sync)

- Python: `agentmem/sdk/python/pyproject.toml` → `version`
- TypeScript: `agentmem/sdk/typescript/package.json` → `version`
- Java: `agentmem/sdk/java/pom.xml` → `<version>`
- C: `agentmem/sdk/c/CMakeLists.txt` → `project(... VERSION ...)` **and** `src/lians.c` user-agent string
- MCP: `server.json`; Claude plugin: `.claude-plugin/marketplace.json` + `integrations/lians-plugin/.claude-plugin/plugin.json`
- Go: `agentmem/sdk/go/version.go` → `Version` const (the resolvable version is still the git tag)

## Required secrets / setup (one-time)

| Registry | Setup |
|----------|-------|
| **PyPI** | Configure a *Trusted Publisher* for `lians-sdk` pointing at `publish-lian.yml` (no token needed). |
| **npm** | Create the `@lians-ai` org (or your chosen scope), add repo secret `NPM_TOKEN` with publish rights. |
| **Maven Central** | Create a [Central Portal](https://central.sonatype.com) account for `ai.lians` (verified via a TXT record on lians.ai); add secrets `OSSRH_USERNAME`, `OSSRH_PASSWORD`, `MAVEN_GPG_KEY` (ASCII-armored private key), `MAVEN_GPG_PASSPHRASE`; set repo **variable** `PUBLISH_MAVEN_CENTRAL=true`. Until then, the jar is attached to the GitHub Release. |
| **Go / pkg.go.dev** | Nothing — `go-tag` creates the resolvable tag automatically. |

## After a release

- **Publish to the MCP registry — manual, easy to forget** (0.3.3 and the
  first day of 0.3.4 were missing because this step lives outside the
  tag-triggered pipeline):

  ```bash
  # from the repo root (reads server.json, which the version bump updated)
  mcp-publisher login github     # interactive device flow; token expires
  mcp-publisher publish
  # verify:
  curl -s "https://registry.modelcontextprotocol.io/v0/servers/io.github.ebeirne%2Flians/versions/latest"
  ```

- Verify: `pip install lians-sdk==X.Y.Z`, `npm view @lians-ai/lians`, `go get github.com/Lians-ai/Lians/agentmem/sdk/go@vX.Y.Z`, and the Maven Central listing.
- **Verify the wheel outside the monorepo**: `pip install "lians-sdk[local]==X.Y.Z"` in a clean venv and run a `LocalLiansClient` round-trip — the local mode imports the vendored engine, which only a from-scratch install exercises (the 0.3.2 wheel shipped broken because all testing ran inside the repo).
- Update the npm scope decision if `@lians-ai` is not your final choice. It is referenced in `package.json`, `README.md`, `docs/`, and `integrations/lians-plugin/README.md`.
