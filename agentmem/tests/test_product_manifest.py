import json
from datetime import date
from pathlib import Path


MANIFEST_PATH = Path(__file__).parents[2] / "product-manifest.json"


def _bounded_string(value: object, *, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def test_product_manifest_matches_console_contract() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["schemaVersion"] == 1
    date.fromisoformat(manifest["updatedAt"])
    assert manifest["source"] == {
        "repository": "Lians-ai/Lians",
        "branch": "master",
    }

    product = manifest["product"]
    assert _bounded_string(product["name"], maximum=80)
    assert _bounded_string(product["category"], maximum=160)
    assert _bounded_string(product["summary"], maximum=600)

    workflow = product["workflow"]
    assert 1 <= len(workflow) <= 8
    for step in workflow:
        assert set(step) == {"label", "detail"}
        assert _bounded_string(step["label"], maximum=80)
        assert _bounded_string(step["detail"], maximum=400)

    surfaces = product["surfaces"]
    assert 1 <= len(surfaces) <= 8
    for surface in surfaces:
        assert set(surface) == {"label", "status", "detail"}
        assert _bounded_string(surface["label"], maximum=80)
        assert _bounded_string(surface["status"], maximum=80)
        assert _bounded_string(surface["detail"], maximum=400)
