from __future__ import annotations

from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "staging-database-check.yml"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _named_step(workflow: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    assert marker in workflow
    remainder = workflow.split(marker, maxsplit=1)[1]
    return remainder.split("\n      - ", maxsplit=1)[0]


def test_dispatch_is_read_only_by_default_and_requires_exact_confirmation() -> None:
    workflow = _workflow()
    dispatch = workflow.split("  workflow_dispatch:\n", maxsplit=1)[1].split(
        "\npermissions:", maxsplit=1
    )[0]

    assert "      migrate:" in dispatch
    assert "        default: false" in dispatch
    assert "        type: boolean" in dispatch
    assert "      confirm:" in dispatch
    assert "Type MIGRATE STAGING" in dispatch
    assert '        default: "VERIFY ONLY"' in dispatch

    confirmation = _named_step(workflow, "Require explicit migration confirmation")
    assert "if: ${{ inputs.migrate }}" in confirmation
    assert "CONFIRM: ${{ inputs.confirm }}" in confirmation
    assert 'test "$CONFIRM" = "MIGRATE STAGING"' in confirmation

    for step_name in (
        "Install migration dependencies",
        "Create an immediate encrypted staging database snapshot",
        "Migrate staging through its private Fly tunnel",
    ):
        assert "if: ${{ inputs.migrate }}" in _named_step(workflow, step_name)

    verification = _named_step(workflow, "Open Fly tunnel and verify staging")
    assert "\n        if:" not in verification
    assert "--expected-revision 0030_force_hosted_mcp_rls" in verification


def test_optional_migration_snapshots_the_exact_encrypted_staging_volume_first() -> None:
    workflow = _workflow()
    snapshot = _named_step(workflow, "Create an immediate encrypted staging database snapshot")

    assert "FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}" in snapshot
    assert "flyctl volumes list --app agentmem-lotus-staging-db --json" in snapshot
    assert 'test "$volume_count" -eq 1' in snapshot
    assert "'.[0].encrypted'" in snapshot
    assert '= "true"' in snapshot
    assert "'.[0].state'" in snapshot
    assert '= "created"' in snapshot
    assert "volume_id=" in snapshot
    assert 'flyctl volumes snapshots create "$volume_id"' in snapshot
    assert "--app agentmem-lotus-staging-db" in snapshot
    assert "before_snapshot_ids" in snapshot
    assert "($before_ids | index($id) | not)" in snapshot
    assert "Fly did not confirm a new staging snapshot." in snapshot

    assert workflow.index(
        "Create an immediate encrypted staging database snapshot"
    ) < workflow.index("Migrate staging through its private Fly tunnel")
    assert workflow.index("Migrate staging through its private Fly tunnel") < workflow.index(
        "Open Fly tunnel and verify staging"
    )


def test_optional_migration_uses_existing_secrets_and_private_proxy() -> None:
    migration = _named_step(_workflow(), "Migrate staging through its private Fly tunnel")

    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in migration
    assert "FLY_API_TOKEN: ${{ secrets.FLY_API_TOKEN }}" in migration
    assert "MIGRATION_DATABASE_URL: ${{ secrets.DATABASE_URL }}" in migration
    assert "flyctl proxy 15433:5432" in migration
    assert "--app agentmem-lotus-staging-db" in migration
    assert "--expected-revision 0028_decision_envelopes" in migration
    assert "python scripts/migrate_via_fly_proxy.py --host 127.0.0.1 --port 15433" in migration
    assert migration.index("--expected-revision 0028_decision_envelopes") < migration.index(
        "python scripts/migrate_via_fly_proxy.py"
    )
    assert 'echo "$DATABASE_URL"' not in migration
    assert 'echo "$MIGRATION_DATABASE_URL"' not in migration
    assert "set -x" not in migration
