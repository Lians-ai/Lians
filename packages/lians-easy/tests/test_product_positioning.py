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

    for surface in (readme, direction, consumer_contract, gui):
        assert "Use less context. Get more AI." in surface
    assert manifest["product"]["category"] == "AI efficiency for the tools you already use"
    assert "Optimize my AI apps" in gui
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
