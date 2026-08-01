# Releasing Lians

One tag starts the release train across the language artifacts. A tag is not the
same as a completed release: every public registry must be verified before the
website or documentation advertises the new version.

```bash
# 1. Merge the approved release scope and update CHANGELOG.md.
# 2. Set VERSION and every source manifest to X.Y.Z.
python scripts/check_release_contract.py
python scripts/check_openapi_contract.py

# 3. Run the full test/build/package matrix and inspect the release diff.
# 4. Confirm PyPI trusted publishing, npm scope authorization, Maven secrets,
#    and the Maven opt-in variable before creating the tag.
git tag vX.Y.Z
git push origin vX.Y.Z
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
| `publish-mcp-container.yml` | Waits for the exact `lians-sdk` version on PyPI, then publishes normalized GHCR tags (`X.Y.Z`, never `vX.Y.Z`) |

## Version locations (keep in sync)

- Python: `agentmem/sdk/python/pyproject.toml` → `version`
- TypeScript: `agentmem/sdk/typescript/package.json` → `version`
- Java: `agentmem/sdk/java/pom.xml` → `<version>`
- C: `agentmem/sdk/c/CMakeLists.txt` → `project(... VERSION ...)` **and** `src/lians.c` user-agent string
- MCP: `server.json`; Claude plugin: `.claude-plugin/marketplace.json` + `integrations/lians-plugin/.claude-plugin/plugin.json`
- MCPB: `integrations/mcpb/manifest.json` and
  `integrations/mcpb/pyproject.toml`; generate `integrations/mcpb/uv.lock` only
  after the exact Python release is live on PyPI
- Go: `agentmem/sdk/go/version.go` → `Version` const (the resolvable version is still the git tag)

`check_release_contract.py` verifies source-manifest synchronization only. It
does not prove that any registry accepted the release.

## Required secrets / setup (one-time)

| Registry | Setup |
|----------|-------|
| **PyPI** | Configure a *Trusted Publisher* for `lians-sdk` pointing at `publish-lian.yml` (no token needed). |
| **npm** | Create the `@lians-ai` org (or your chosen scope), add repo secret `NPM_TOKEN` with publish rights. |
| **Maven Central** | Create a [Central Portal](https://central.sonatype.com) account for `ai.lians` (verified via a TXT record on lians.ai); add secrets `OSSRH_USERNAME`, `OSSRH_PASSWORD`, `MAVEN_GPG_KEY` (ASCII-armored private key), `MAVEN_GPG_PASSPHRASE`; set repo **variable** `PUBLISH_MAVEN_CENTRAL=true`. Until then, the jar is attached to the GitHub Release. |
| **Go / pkg.go.dev** | Nothing — `go-tag` creates the resolvable tag automatically. |

## After a release

1. Wait for every tag-triggered workflow and inspect each publisher log.
2. Verify clean external installs for Python, TypeScript, Go, Java, and the C
   release asset. Run the installed Python wheel outside this monorepo.
   Then run `uv lock --directory integrations/mcpb` and rerun
   `python scripts/check_release_contract.py` before packaging the MCPB.
3. Publish and verify the MCP Registry manifest, then verify the GHCR version and
   `latest` tags resolve to the intended digest. The container workflow strips
   the Git tag's leading `v`, validates the result against `VERSION`, and uses
   that same value for the installed Python distribution and runtime image tag.
4. Update `docs/published-release-status.json` only from observed registry state,
   then run:

   ```bash
   python scripts/check_published_artifacts.py
   python scripts/check_published_artifacts.py --require-source-sync
   docker buildx imagetools inspect ghcr.io/lians-ai/lians-mcp:X.Y.Z
   ```

   The script checks registries with stable unauthenticated metadata endpoints.
   The C release asset and GHCR container remain explicit manual checks; both are
   still required before `--require-source-sync` may be treated as a release gate.

5. Update public install instructions only after both commands pass. If any
   publisher fails, leave the matrix split and record the failure explicitly.

- **Publish to the MCP registry — manual, easy to forget** (0.3.3 and the
  first day of 0.3.4 were missing because this step lives outside the
  tag-triggered pipeline):

  ```bash
  # from the repo root (reads server.json, which the version bump updated)
  mcp-publisher login github     # interactive device flow; token expires
  mcp-publisher publish
  # verify:
  curl -fsS "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.ebeirne%2Flians&version=latest"
  ```

- Verify: `pip install lians-sdk==X.Y.Z`, `npm view @lians-ai/lians`, `go get github.com/Lians-ai/Lians/agentmem/sdk/go@vX.Y.Z`, the direct Maven Central metadata, the C release asset, GHCR, and the MCP Registry response.
- **Verify the wheel outside the monorepo**: `pip install "lians-sdk[local]==X.Y.Z"` in a clean venv and run a `LocalLiansClient` round-trip — the local mode imports the vendored engine, which only a from-scratch install exercises (the 0.3.2 wheel shipped broken because all testing ran inside the repo).
- Update the npm scope decision if `@lians-ai` is not your final choice. It is referenced in `package.json`, `README.md`, `docs/`, and `integrations/lians-plugin/README.md`.
