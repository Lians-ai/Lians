from __future__ import annotations

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "fly-deploy.yml"
ROLLBACK_WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "fly-rollback.yml"
STAGING_WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "staging-database-check.yml"
)
FLY_CONFIG_PATH = REPOSITORY_ROOT / "fly.toml"


def test_production_deploy_uses_bluegreen_cutover() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "- name: Deploy with blue-green health-gated cutover" in workflow
    assert "--strategy bluegreen \\" in workflow
    assert "--strategy canary" not in workflow
    assert "--strategy rolling" not in workflow
    assert "--strategy immediate" not in workflow


def test_bluegreen_prerequisites_remain_configured() -> None:
    with FLY_CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)

    service = config["http_service"]
    assert service["min_machines_running"] >= 1
    assert any(
        check.get("method") == "GET" and check.get("path") == "/readyz"
        for check in service["checks"]
    )
    assert "mounts" not in config


def test_cold_start_budget_fits_deployment_wait_windows() -> None:
    with FLY_CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)

    readiness_check = next(
        check
        for check in config["http_service"]["checks"]
        if check.get("method") == "GET" and check.get("path") == "/readyz"
    )
    assert config["env"]["HOSTED_MCP_STARTUP_TIMEOUT_SECONDS"] == "360"
    assert readiness_check["grace_period"] == "420s"

    for workflow_path in (WORKFLOW_PATH, ROLLBACK_WORKFLOW_PATH):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "--wait-timeout 10m" in workflow


def test_database_gates_require_current_alembic_head() -> None:
    for workflow_path in (WORKFLOW_PATH, STAGING_WORKFLOW_PATH):
        workflow = workflow_path.read_text(encoding="utf-8")

        assert "--expected-revision 0030_force_hosted_mcp_rls" in workflow

    production_workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    staging_workflow = STAGING_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert production_workflow.count("--expected-revision") == 1
    assert staging_workflow.count("--expected-revision 0030_force_hosted_mcp_rls") == 1


def test_post_deploy_release_identity_and_smoke_gates_remain() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'image_label="deployment-github-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in workflow
    assert '--image-label "$image_label"' in workflow
    assert '--build-arg "LIANS_BUILD_SHA=$GITHUB_SHA"' in workflow
    assert '--expected-image "$DEPLOYED_IMAGE"' in workflow
    assert '--expected-sha "$GITHUB_SHA"' in workflow
    assert "Verify schema revision inside the production app" in workflow
    assert "python scripts/verify_production_schema.py" in workflow
    assert '--machine-id "$PRODUCTION_MACHINE_ID"' in workflow
    schema_step = workflow.split(
        "- name: Verify schema revision inside the production app", maxsplit=1
    )[1].split("\n      - name:", maxsplit=1)[0]
    assert "FLY_API_TOKEN: ${{ secrets.FLY_APP_TOKEN }}" in schema_step
    assert "FLY_MACHINE_EXEC_TOKEN" not in schema_step
    assert "flyctl tokens attenuate" in schema_step
    assert "tokens create" not in schema_step
    assert "tokens revoke" not in schema_step
    assert 'schema_shell_command="cd /app/agentmem && /opt/venv/bin/alembic -c alembic.ini current"' in schema_step
    assert 'args: ["/bin/sh", "-c", $shell]' in schema_step
    assert "exact: true" in schema_step
    assert 'else: "r"' in schema_step
    assert 'body: {not_before: $nb, not_after: $na}' in schema_step
    assert '"$((now - 30))"' in schema_step
    assert '"$((now + 600))"' in schema_step
    assert 'echo "::add-mask::$schema_token"' in schema_step
    assert 'test "$schema_token" != "$base_token"' in schema_step
    assert 'FLY_API_TOKEN="$schema_token" python scripts/verify_production_schema.py' in schema_step
    assert "unset FLY_API_TOKEN FLY_ACCESS_TOKEN" in schema_step
    assert schema_step.index("unset FLY_API_TOKEN FLY_ACCESS_TOKEN") < schema_step.index(
        'FLY_API_TOKEN="$schema_token" python'
    )
    assert schema_step.index('base_token=""') < schema_step.index(
        'FLY_API_TOKEN="$schema_token" python'
    )
    assert "FLY_MACHINE_EXEC_TOKEN" not in workflow
    assert "scripts/check_production_deployment.py" in workflow
    assert '--expected-build-sha "$GITHUB_SHA"' in workflow
    assert "--base-url https://mcp.lians.ai" in workflow
    assert "scripts/check_openai_plugin_endpoint.py" in workflow
    assert "--resource-url https://mcp.lians.ai/mcp" in workflow


def test_mcp_boundary_is_followed_by_a_fresh_health_gate() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    boundary = "python scripts/check_openai_plugin_endpoint.py"
    post_boundary_gate = (
        "python scripts/check_production_deployment.py \\\n"
        "            --base-url https://mcp.lians.ai \\\n"
        "            --health-only"
    )

    assert workflow.count(boundary) == 1
    assert workflow.count(post_boundary_gate) == 1
    assert workflow.index(boundary) < workflow.index(post_boundary_gate)


def test_production_config_enables_hosted_mcp_with_personal_tenancy() -> None:
    with FLY_CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)

    environment = config["env"]
    assert environment["HOSTED_MCP_ENABLED"] == "true"
    assert environment["HOSTED_MCP_RESOURCE_URL"] == "https://mcp.lians.ai"
    assert environment["HOSTED_MCP_TENANT_CLAIM"] == "sub"
