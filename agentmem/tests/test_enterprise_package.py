from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_enterprise_extra_carries_operational_integrations() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        extras = tomllib.load(handle)["project"]["optional-dependencies"]

    rendered = "\n".join(extras["enterprise"])
    for dependency in (
        "stripe",
        "prometheus-client",
        "opentelemetry-sdk",
        "boto3",
        "azure-identity",
        "azure-keyvault-secrets",
        "hvac",
    ):
        assert dependency in rendered


def test_operator_image_has_stable_identity_and_reduced_runtime_surface() -> None:
    dockerfile = _read("Dockerfile")

    assert f"ARG LIANS_VERSION={VERSION}" in dockerfile
    assert 'org.opencontainers.image.title="Lians Engine"' in dockerfile
    assert 'org.opencontainers.image.source="https://github.com/Lians-ai/Lians"' in dockerfile
    assert "COPY --chown=10001:10001 agentmem/alembic /app/agentmem/alembic" in dockerfile
    assert "COPY --chown=10001:10001 agentmem/alembic.ini /app/agentmem/alembic.ini" in dockerfile
    assert "COPY --chown=10001:10001 agentmem/ /app/agentmem/" not in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_engine_publish_is_immutable_multiarch_and_evidence_bearing() -> None:
    workflow = _read(".github/workflows/publish-engine-container.yml")

    assert 'release_tag must be an existing stable semver tag (vX.Y.Z)' in workflow
    assert 'git rev-list -n 1 "$RELEASE_TAG"' in workflow
    assert 'git rev-parse HEAD' in workflow
    assert "vars.PUBLISH_LIANS_ENGINE_CONTAINER == 'true'" in workflow
    assert "environment: ghcr-lians-engine" in workflow
    assert "ghcr.io/lians-ai/lians-engine" in workflow
    assert 'already exists; release tags are immutable' in workflow
    assert "platforms: linux/amd64,linux/arm64" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "EXTRAS=enterprise" in workflow
    assert 'LIANS_BUILD_SHA=$(git rev-parse HEAD)' in workflow
    assert "LIANS_BUILD_SHA=${{ env.LIANS_BUILD_SHA }}" in workflow
    assert "0032_device_enrollment_exchange (head)" in workflow
    assert "10001:10001" in workflow
    assert ":latest" not in workflow

    for action in re.findall(r"uses:\s+([^\s]+)", workflow):
        if action.startswith("./"):
            continue
        assert re.search(r"@[0-9a-f]{40}$", action), action


def test_kustomize_bundle_excludes_secrets_and_matches_runtime_contract() -> None:
    kustomization = _read("k8s/kustomization.yaml")
    deployment = _read("k8s/deployment.yaml")
    migration = _read("k8s/migrate-job.yaml")

    assert "  - secret.yaml" not in kustomization
    assert "secret.example.yaml" not in kustomization
    assert "newName: ghcr.io/lians-ai/lians-engine" in kustomization
    assert f'newTag: "{VERSION}"' in kustomization
    assert f"image: ghcr.io/lians-ai/lians-engine:{VERSION}" in migration
    assert "runAsUser: 10001" in deployment
    assert "runAsGroup: 10001" in deployment
    assert "path: /readyz" in deployment
    assert "path: /livez" in deployment
    assert "wait-for-postgres" not in deployment
    assert "postgres.agentmem.svc.cluster.local" not in deployment
    assert (ROOT / "k8s" / "secret.example.yaml").is_file()
    assert (ROOT / "k8s" / "secret.env.example").is_file()
    assert not (ROOT / "k8s" / "secret.yaml").exists()


def test_operator_guide_uses_audited_provisioning_and_digest_pinning() -> None:
    guide = _read("k8s/README.md")
    deploy = _read("docs/deploy.md")

    assert "@sha256:..." in guide
    assert "kubectl kustomize k8s/" in guide
    assert "--from-env-file=/secure/path/lians-engine.env" in guide
    assert "v1/admin/api-keys" in guide
    assert "INSERT INTO api_keys" not in deploy
    assert "agentmem/docker-compose.yml" in deploy
    assert "logs -f api" in deploy
