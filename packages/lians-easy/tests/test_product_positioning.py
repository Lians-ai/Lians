from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPOSITORY_ROOT / "packages" / "lians-easy"


def test_primary_github_and_desktop_surfaces_share_one_product_promise() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    direction = (REPOSITORY_ROOT / "docs" / "product-direction.md").read_text(encoding="utf-8")
    consumer_contract = (REPOSITORY_ROOT / "docs" / "consumer-installer.md").read_text(
        encoding="utf-8"
    )
    gui = (PACKAGE_ROOT / "lians_easy" / "gui.py").read_text(encoding="utf-8")
    manifest = json.loads((REPOSITORY_ROOT / "product-manifest.json").read_text())

    assert "Your AI says it is done. Lians checks the receipts." in readme
    assert "evidence-backed proof layer for AI work" in " ".join(direction.split())
    assert "Your AI says it is done. Lians checks the receipts." in consumer_contract
    assert "Your AI says it is done. Lians checks the receipts." in gui
    assert manifest["product"]["category"] == "Evidence-backed proof layer for AI work"
    assert "Connect Lians" in gui
    assert "provider API key" in readme


def test_public_copy_does_not_promise_provider_quota_extension() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8").lower()
    direction = (
        (REPOSITORY_ROOT / "docs" / "product-direction.md").read_text(encoding="utf-8").lower()
    )

    for unsupported_claim in (
        "guaranteed token savings",
        "double your usage",
        "extend your claude plan",
        "extend your cursor plan",
        "extend your codex plan",
    ):
        assert unsupported_claim not in readme
        assert unsupported_claim not in direction


def test_repository_contains_no_em_dash_characters_or_escapes() -> None:
    forbidden = (chr(0x2014), "\\" + "u2014")
    violations: list[str] = []
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")

    for relative in tracked:
        if not relative:
            continue
        path = REPOSITORY_ROOT / relative.decode()
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(marker in content for marker in forbidden):
            violations.append(str(path.relative_to(REPOSITORY_ROOT)))

    assert not violations, f"em dash found in: {', '.join(sorted(violations))}"
