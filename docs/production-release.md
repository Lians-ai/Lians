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
| Expected schema after release | `0030_force_hosted_mcp_rls` |

The production workflow migrates the database to the reviewed Alembic head
`0030_force_hosted_mcp_rls` before the application image is promoted.

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
- Fly gates promotion on `/readyz`.
- The workflow verifies revision `0030_force_hosted_mcp_rls`, the public API
  surface, and the unauthenticated OpenAI MCP boundary after deployment.

## Go/no-go checklist

All items must be true before starting the workflow:

- [ ] Pull request CI is green for the exact commit being released.
- [ ] The production workflow files on `master` match the reviewed release
      candidate.
- [ ] The staging database check passes at revision
      `0030_force_hosted_mcp_rls`.
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
7. Confirm the Fly release migration reaches `0030_force_hosted_mcp_rls`.
8. Confirm the deployment and public smoke checks pass.
9. Inspect `/health`, `/readyz`, error rate, and request latency for at least
   15 minutes.

## Abort criteria

Abort or stop promotion if:

- the staging database check fails;
- no production snapshot is both `created` and less than two hours old;
- the release command fails or reports any revision other than
  `0030_force_hosted_mcp_rls`;
- `/readyz` does not become healthy within the workflow timeout;
- the public OpenAPI surface is incomplete;
- authenticated traffic shows a material increase in errors or latency.

## Rollback

Use the **Production rollback** workflow with the prior image reference
published by the deploy job. Run it from `master` and type `ROLLBACK`.

Application rollback intentionally uses `--skip-release-command`. Do not run
an Alembic downgrade during incident response. Migrations through 0030 are
additive, so the prior application image can run against the advanced schema
while the incident is investigated. A database restore is a separate,
last-resort recovery procedure.

After rollback, verify `/livez`, `/health`, and `/readyz`, then preserve the
failed release logs and snapshot identifiers.

## Known nonblocking follow-ups

- Redis is required for hosted-MCP rate limits. Confirm its staged secret is
  deployed and reachable before enabling the public MCP surface.
- Fly automatic snapshots retain five days. Enable off-platform WAL/archive
  backups before treating the current setup as the final disaster-recovery
  posture.
- Keep the named independent reviewer and self-review prevention on the GitHub
  `production` environment; do not bypass either release control.
