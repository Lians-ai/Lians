from pathlib import Path

from scripts.check_published_artifacts import (
    compare_live,
    load_status,
    parse_go_versions,
    parse_maven_metadata,
    source_sync_drift,
    validate_status,
)

ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_release_status_is_valid_and_explicit_about_drift():
    status = load_status(ROOT / "docs" / "published-release-status.json")
    errors = validate_status(status, (ROOT / "VERSION").read_text().strip())
    assert errors == []
    assert source_sync_drift(status) == {
        "pypi": "0.4.2",
        "npm": "0.4.0",
        "go": "0.4.1",
        "maven": "0.4.1",
        "c": "0.4.1",
        "ghcr_mcp": "0.4.1",
        "mcp_registry": "0.4.1",
        "github_release": "0.4.1",
    }


def test_registry_parsers_select_authoritative_release_versions():
    assert parse_go_versions(b"v0.4.0\nv0.3.4\nv0.4.1\n") == "0.4.1"
    assert (
        parse_maven_metadata(
            b"<metadata><versioning><release>0.4.1</release></versioning></metadata>"
        )
        == "0.4.1"
    )


def test_live_comparison_reports_expected_and_actual_versions():
    status = load_status(ROOT / "docs" / "published-release-status.json")
    live = {
        "production_api": "0.5.0",
        "pypi": "0.4.2",
        "npm": "0.4.1",
    }
    assert compare_live(status, live) == {"npm": ("0.4.0", "0.4.1")}


def test_production_verification_uses_version_endpoint() -> None:
    status = load_status(ROOT / "docs" / "published-release-status.json")
    assert status["production_api"]["verification_url"] == (
        "https://agentmem-lotus.fly.dev/version"
    )

    status["production_api"]["verification_url"] = (
        "https://agentmem-lotus.fly.dev/openapi.json"
    )
    errors = validate_status(status, (ROOT / "VERSION").read_text().strip())
    assert errors == [
        "production_api.verification_url must use the public /version endpoint"
    ]


def test_runtime_docker_context_excludes_generated_dependency_trees():
    rules = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("**/node_modules/", "**/dist/", "**/build/", "**/target/"):
        assert pattern in rules


def test_ghcr_runtime_uses_normalized_version_tag():
    status = load_status(ROOT / "docs" / "published-release-status.json")
    artifact = status["artifacts"]["ghcr_mcp"]
    assert artifact["runtime"] == (
        f"ghcr.io/lians-ai/lians-mcp:{artifact['version']}"
    )


def test_glama_default_tracks_verified_public_image():
    status = load_status(ROOT / "docs" / "published-release-status.json")
    version = status["artifacts"]["ghcr_mcp"]["version"]
    dockerfile = (ROOT / "Dockerfile.glama").read_text(encoding="utf-8").splitlines()
    version_arg = next(line for line in dockerfile if line.startswith("ARG LIANS_VERSION="))
    assert version_arg == f"ARG LIANS_VERSION={version}"


def test_release_status_rejects_prefixed_ghcr_runtime_tag():
    status = load_status(ROOT / "docs" / "published-release-status.json")
    status["artifacts"]["ghcr_mcp"]["runtime"] = (
        "ghcr.io/lians-ai/lians-mcp:v0.4.1"
    )
    errors = validate_status(status, (ROOT / "VERSION").read_text().strip())
    assert errors == [
        (
            "artifacts.ghcr_mcp.runtime must use the normalized, unprefixed "
            "version tag ghcr.io/lians-ai/lians-mcp:0.4.1"
        )
    ]
