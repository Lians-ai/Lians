from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPOSITORY_ROOT / "packages" / "lians-easy"


def test_primary_github_and_desktop_surfaces_share_one_product_promise() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    direction = (REPOSITORY_ROOT / "docs" / "product-direction.md").read_text(
        encoding="utf-8"
    )
    consumer_contract = (
        REPOSITORY_ROOT / "docs" / "consumer-installer.md"
    ).read_text(encoding="utf-8")
    gui = (PACKAGE_ROOT / "lians_easy" / "gui.py").read_text(encoding="utf-8")
    manifest = json.loads((REPOSITORY_ROOT / "product-manifest.json").read_text())

    assert "Recover the task. Reject stale state. Block unsupported done." in readme
    assert "current-state and completion guard for AI coding agents" in " ".join(
        direction.split()
    )
    assert "Recover the task. Guard what done means." in consumer_contract
    assert "Recover the task. Guard what done means." in gui
    assert (
        manifest["product"]["category"]
        == "Current-state and completion guard for AI coding agents"
    )
    assert "Connect Lians Guard" in gui
    assert "provider API key" in readme


def test_public_copy_does_not_promise_provider_quota_extension() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8").lower()
    direction = (REPOSITORY_ROOT / "docs" / "product-direction.md").read_text(
        encoding="utf-8"
    ).lower()

    for unsupported_claim in (
        "guaranteed token savings",
        "double your usage",
        "extend your claude plan",
        "extend your cursor plan",
        "extend your codex plan",
    ):
        assert unsupported_claim not in readme
        assert unsupported_claim not in direction
