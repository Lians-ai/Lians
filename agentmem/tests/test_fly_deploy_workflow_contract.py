from __future__ import annotations

import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "fly-deploy.yml"
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
