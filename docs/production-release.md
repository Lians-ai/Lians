# Production release runbook

This runbook is the go/no-go gate for deploying Lians to the Fly.io
production application `agentmem-lotus`.

## Release topology

| Component | Production target |
|---|---|
| Application | Fly app `agentmem-lotus` |
| Database | Fly Postgres app `agentmem-lotus-db` |
| Public health endpoint | `https://mcp.lians.ai/readyz` |
| Deployment branch | `master` only |
| Deployment workflow | **Production deploy** |
| Rollback workflow | **Production rollback** |
| Expected schema after release | `0031_zero_knowledge_sync` |

The production workflow migrates the database to the reviewed Alembic head
`0031_zero_knowledge_sync` before the application image is promoted. The latest
verified production release below predates this candidate and remains at
`0030_force_hosted_mcp_rls`; do not represent 0031 as deployed until a new
guarded production workflow passes.

## Latest verified production release

Production workflow [31349405257](https://github.com/Lians-ai/Lians/actions/runs/31349405257) is the latest successful verification of `master` build `e72fad2c7f98ecf54b6553a90bf8d862046c1abc`; GitHub recorded its final workflow update at `2026-08-10T02:31:24Z`.

- The staging recheck passed before the production job started.
- The protected environment recorded independent approval and the full five-minute wait timer.
- A fresh encrypted database snapshot was confirmed before migration.
- Production migrated to and verified `0030_force_hosted_mcp_rls`.
- The blue-green deployment, exact-machine resolution, and public health, liveness, readiness, authentication-boundary, and documentation checks passed.
- The workflow's no-token OpenAI MCP boundary check passed HTTPS, protected-resource metadata, and the unauthenticated challenge; it intentionally skipped authenticated MCP because no bearer token was supplied.
- During minute `2026-08-10T03:41Z`, a separate sanitized operator-run production OAuth E2E passed protected-resource metadata, OIDC discovery, DCR, browser login, authorization callback, token exchange, repository JWT verification, the authenticated endpoint checker, MCP remember/recall/confirmed-forget calls, and session cleanup. The final MCP completion event was `2026-08-10T03:41:10.126400Z`; generic provider tool labels are mapped only by the harness's fixed remember/recall/forget order. Auth0 displayed the reviewer account's latest login at `2026-08-10T03:40:58Z`.
- The authorization server did not advertise DCR cleanup. The exact temporary client was manually deleted and the registered-client inventory was verified as zero. No credentials, tokens, client identifiers, memory payloads or references, raw responses, or local paths are retained in this evidence.

Three consecutive production rehearsals qualified the cold-boot boundary. The recorded timing basis is machine start to first readiness at `1/1 passing`, not total workflow or image-build duration. Each rehearsal used a distinct new Machine ID and reached readiness below the 360-second hosted startup timeout. Each workflow then recorded a single immediate post-MCP result with health, liveness, and readiness `ok`; the cited run does not attest an extended observation window or later degradation state.

| Production workflow | Machine ID | Production job completed (UTC) | Machine start to first `1/1 passing` | Immediate post-MCP health result |
| --- | --- | --- | --- | --- |
| [31347743399](https://github.com/Lians-ai/Lians/actions/runs/31347743399) | `28691d1b640298` | `2026-08-10T01:59:04Z` | `197.528s` (`01:55:07.0366956Z` to `01:58:24.5647632Z`) | health/liveness/readiness `ok` at `01:59:01.1331951Z` |
| [31348671152](https://github.com/Lians-ai/Lians/actions/runs/31348671152) | `7841659cd4d6e8` | `2026-08-10T02:15:28Z` | `197.947s` (`02:11:27.3152732Z` to `02:14:45.2623950Z`) | health/liveness/readiness `ok` at `02:15:24.2071289Z` |
| [31349405257](https://github.com/Lians-ai/Lians/actions/runs/31349405257) | `8dd9e0ce170928` | `2026-08-10T02:31:23Z` | `197.963s` (`02:27:24.4377031Z` to `02:30:42.4003935Z`) | health/liveness/readiness `ok` at `02:31:18.7557165Z` |

The maximum observed cold boot was `197.963s`, below the configured 360-second application startup timeout. This does **not** prove that Fly honored the configured 420-second health-check grace: every deploy log warned, `Service HTTP check has a grace period greater than 1 minute (7m0s); this will be lowered to 1 minute`. The effective Fly grace was one minute, so the configured 420 seconds was **not honored**.

The later portal inspection verified the `Lians, Ai` business identity and validated the submitter's Apps Management owner role. This production release and the operator OAuth E2E do not mean the universal plugin passed domain verification, the skill or tool scans, developer-mode rehearsal, portal country selection, submission, approval, publication, or directory listing.

## Controls already enforced

- Production deploys are manual. A merge or push does not release the app.
- The operator must run the workflow from `master` and type `DEPLOY`.
- The GitHub `production` environment accepts only `master`.
- The environment has a five-minute, non-bypassable wait timer.
- Fly credentials are app-scoped and expire after 90 days.
- The workflow rechecks the private staging database before touching
  production.
- The workflow records the prior good image and requests a fresh encrypted
  database-volume snapshot before deploying. If Fly coalesces that request,
  it accepts only an existing `created` snapshot no older than two hours.
- Fly gates promotion on `/readyz`; current deploy logs lower the configured
  420-second grace period to an effective one minute, so operators must not
  represent the configured 420 seconds as honored.
- The next workflow verifies revision `0031_zero_knowledge_sync`, the public API
  surface, and the unauthenticated OpenAI MCP boundary after deployment.

## Go/no-go checklist

All items must be true before starting the workflow:

- [ ] Pull request CI is green for the exact commit being released.
- [ ] The production workflow files on `master` match the reviewed release
      candidate.
- [ ] The staging database check passes at revision
      `0031_zero_knowledge_sync`.
- [ ] A sanitized staging-data migration rehearsal has passed.
- [ ] The release-candidate container build succeeds.
- [ ] `https://mcp.lians.ai/readyz` is healthy before the release.
- [ ] Fly reports exactly one attached, encrypted production database volume.
- [ ] A recent automatic database snapshot exists.
- [ ] The on-call operator has the prior complete Fly image reference.
- [ ] No unrelated infrastructure incident is active.

Do not proceed if any item is unknown.

## Release sequence

1. Merge the approved release pull request into `master`.
2. Open GitHub Actions and select **Production deploy**.
3. Choose the `master` branch, enter `DEPLOY`, and start the workflow.
4. Confirm that **Recheck staging database** passes.
5. Let the protected production job complete its five-minute wait.
6. Confirm the workflow records a prior image and selects a `created`
   database snapshot no older than two hours.
7. Confirm the Fly release migration reaches `0031_zero_knowledge_sync`.
8. Confirm the deployment and public smoke checks pass.
9. Inspect `/health`, `/readyz`, error rate, and request latency for at least
   15 minutes.

## Abort criteria

Abort or stop promotion if:

- the staging database check fails;
- no production snapshot is both `created` and less than two hours old;
- the release command fails or reports any revision other than
  `0031_zero_knowledge_sync`;
- `/readyz` does not become healthy within the workflow timeout;
- the public OpenAPI surface is incomplete;
- authenticated traffic shows a material increase in errors or latency.

## Rollback

Use the **Production rollback** workflow with the prior image reference
published by the deploy job. Run it from `master` and type `ROLLBACK`.

Application rollback intentionally uses `--skip-release-command`. Do not run
an Alembic downgrade during incident response. Migrations through 0031 are
additive, so the prior application image can run against the advanced schema
while the incident is investigated. A database restore is a separate,
last-resort recovery procedure.

After rollback, verify `/livez`, `/health`, and `/readyz`, then preserve the
failed release logs and snapshot identifiers.

## Known nonblocking follow-ups

- Redis is required for hosted-MCP rate limits. Continue monitoring its
  production connectivity, bounded local fallback, and degradation telemetry
  now that the public MCP surface is enabled.
- Fly automatic snapshots retain five days. Enable off-platform WAL/archive
  backups before treating the current setup as the final disaster-recovery
  posture.
- Keep the named independent reviewer and self-review prevention on the GitHub
  `production` environment; do not bypass either release control.
