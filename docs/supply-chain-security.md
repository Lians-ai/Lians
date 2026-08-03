# Software supply-chain security

Lians builds each supported production platform exactly once, stages each result
by immutable digest, and scans every exact staged payload for high and critical
vulnerabilities. Only those scanned digests are composed into the release index;
the workflow signs and attests that exact index. The release identity is its OCI
digest, never a mutable tag.

The separate `backup-supply-chain.yml` workflow applies the same scan, SBOM, provenance, keyless-signature, and self-verification controls to the all-provider WORM backup image at `ghcr.io/<owner>/<repository>-backup-worm`. `publish-mcp-container.yml` applies the same evidence model plus an MCP contract smoke check to `ghcr.io/lians-ai/lians-mcp`. Every deployment pins each image independently.

The `.github/workflows/supply-chain.yml` workflow produces four independent forms of evidence:

1. Per-platform SARIF vulnerability results for each exact deployable manifest.
2. A downloadable SPDX JSON SBOM generated from the exact promoted index.
3. GitHub build-provenance and SBOM attestations bound to that index digest and pushed to the registry.
4. A keyless Cosign signature bound to the repository, workflow, ref, and GitHub Actions OIDC issuer.

All third-party Actions across `.github/workflows` are pinned to full commit
digests. `security.yml` enforces that invariant, runs dependency review, and
performs CodeQL analysis. Dependabot proposes controlled updates rather than
allowing a mutable action tag to change executable release code without review.

The application build is independently reproducible at its dependency boundaries:
Python and uv bases are pinned by multi-architecture digest, `uv.lock` records
exact runtime distributions and hashes, `build-constraints.lock` hash-pins the
PEP 517 build backend and transitives, and the image builds then installs the
local wheel without dependency re-resolution. The canonical Python SDK and MCP
image use the same constrained build path. The baked embedding model is fetched
from an immutable repository revision with remote model code disabled. Updating
any input is a reviewed source change visible in provenance.

## Release behavior

- Pull requests build and scan separate `amd64` and `arm64` payloads but never
  publish or sign them.
- A same-repository push publishes only when its branch is the repository's
  actual default branch or its protected `v*` tag commit is reachable from that
  branch. Manual publication is restricted to the default-branch ref.
- Publication builds `amd64` and `arm64` once, pushes each without a mutable tag,
  scans `image@sha256:<platform-digest>`, transfers those digests through bounded
  workflow artifacts, and creates the index from exactly those two references.
  Staging disables attached BuildKit attestations so each scanned digest is the
  actual platform manifest; promotion then asserts the registry-reported index
  has exactly those two digest/platform pairs before generating final evidence.
- High or critical findings stop publication. The SARIF report is uploaded to GitHub code scanning when the event has permission to do so.
- Published images carry a `sha-<commit>` tag. Version tags are conveniences only; production manifests must pin `image@sha256:<digest>`.
- Checked-in Kustomize, Gate-mediator, and backup examples contain an all-zero,
  deliberately non-runnable internal-image digest until an operator substitutes
  the verified release subject. External helper images are pinned to resolved
  multi-platform digests. CI rejects mutable image tags across these manifests;
  the Compose example refuses to render unless `LIANS_IMAGE` is supplied.
- On release tags, `.github/scripts/check_release_versions.py` requires the
  platform, public/private Python metadata, TypeScript package and lock, Java,
  Go, C, and MCP metadata to match before any image or SDK path publishes.
- The workflow verifies both its Cosign signature and GitHub provenance before it reports success.
- For the default-branch production channel it exports a checksummed
  `image@sha256:<index-digest>` subject only after verification. `fly-deploy.yml`
  accepts that artifact only from the successful same-repository push run,
  re-verifies both trust paths without the Fly token, and calls
  `flyctl deploy --image`; it never gives production credentials to a source
  build. `fly.toml` has neither a source-build stanza nor a release command:
  migrations remain a separately reviewed operation under a distinct database
  identity. Manual redeploy
  requires the exact current default-branch SHA and the corresponding successful
  push run ID.

Java JARs and deterministic C archives receive GitHub artifact attestations.
Python publishes through PyPI OIDC with a hash-constrained build; npm publishes
through its OIDC trusted publisher with registry provenance. Registry accounts,
protected environments, branch/tag policy, and admission enforcement remain
independent controls.

Every SDK, plugin, Helm, MCP, and GitHub-release publisher checks out complete
history and fails unless a tag is protected and its commit is reachable from the
repository's actual default branch. Manual publishers accept only the exact
default-branch ref. PyPI/npm and general release jobs additionally cross their
protected GitHub Environment before receiving publication authority. A tag name
and matching package version are necessary but are not sufficient source
authorization.

The lock-step Helm chart is distributed separately as
`oci://ghcr.io/<owner>/charts/lians`. `publish-helm-chart.yml` lints and renders
every supported production posture, packages the exact application version,
attests both the archive and OCI subject, signs the OCI digest with the workflow's
GitHub OIDC identity, re-pulls the digest, byte-compares it with the package, and
verifies the signature and GitHub attestation before reporting success. Install
the chart by digest; a semantic-version tag is discovery metadata, not the
deployment identity.

GitHub artifact attestations are available for public repositories on current
GitHub plans and for private or internal repositories on GitHub Enterprise
Cloud. If that feature is unavailable, set the repository variable
`LIANS_ATTESTATIONS_ENABLED=false`; OCI provenance, SBOM, the exact-payload
vulnerability gate, and Cosign remain mandatory for ordinary publication. The
automated Fly production channel deliberately refuses this reduced posture
because it requires both Cosign and GitHub provenance.

## Consumer verification

Substitute the published digest and the repository that produced it:

```bash
IMAGE=ghcr.io/OWNER/REPOSITORY@sha256:DIGEST
REPOSITORY=OWNER/REPOSITORY

gh attestation verify "oci://${IMAGE}" --repo "${REPOSITORY}"

cosign verify "${IMAGE}" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --certificate-identity-regexp "^https://github.com/${REPOSITORY}/.github/workflows/supply-chain.yml@refs/(heads|tags)/.+$"
```

Verification must be performed against the digest placed in the deployment manifest. Verifying a tag and later deploying that tag leaves a time-of-check/time-of-use gap.

The same rule applies to the chart:

```bash
CHART=ghcr.io/OWNER/charts/lians@sha256:DIGEST
REPOSITORY=OWNER/REPOSITORY

gh attestation verify "oci://${CHART}" --repo "${REPOSITORY}"

cosign verify "${CHART}" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  --certificate-identity-regexp "^https://github.com/${REPOSITORY}/.github/workflows/publish-helm-chart.yml@refs/(heads|tags)/.+$"
```

## Kubernetes admission enforcement

For Kubernetes 1.27 or later, install the Sigstore policy controller and GitHub trust policy before enabling enforcement. Replace the organization and image pattern, pin the Helm chart versions deliberately, and test in a staging namespace first:

```bash
helm upgrade policy-controller --install --atomic \
  --create-namespace --namespace artifact-attestations \
  oci://ghcr.io/sigstore/helm-charts/policy-controller \
  --version 0.10.5

helm upgrade trust-policies --install --atomic \
  --namespace artifact-attestations \
  oci://ghcr.io/github/artifact-attestations-helm-charts/trust-policies \
  --version v0.7.0 \
  --set policy.enabled=true \
  --set policy.organization=OWNER \
  --set-json 'policy.images=["ghcr.io/OWNER/REPOSITORY**"]'

kubectl label namespace agentmem policy.sigstore.dev/include=true
```

The namespace label is intentionally absent from the base manifests: adding it before the controller and trust policy are installed can make every Pod inadmissible. Once enabled, exercise a negative deployment with an unsigned image as part of the release drill.

## Incident response and revocation

1. Stop promotion of the affected digest. Do not retag it.
2. Revoke deployment approval in the environment or GitOps repository.
3. Record the digest, SBOM, provenance bundle, scanner result, and affected Decision Receipts in the incident case.
4. Build a corrected image from a reviewed commit. Never overwrite an existing version or digest reference.
5. Verify the replacement independently, update the manifest by digest, and preserve the superseded evidence according to retention policy.

Cryptographic evidence proves origin and integrity; it does not prove that a build is safe. Admission verification, vulnerability policy, runtime controls, and the Lians Gate remain separate required controls.
