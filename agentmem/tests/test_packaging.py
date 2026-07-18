from pathlib import Path
import tomllib


def test_local_entry_point_extras_include_real_semantic_embeddings() -> None:
    pyproject = Path(__file__).parents[1] / "sdk" / "python" / "pyproject.toml"
    package = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    extras = package["project"]["optional-dependencies"]

    for extra in ("local", "mcp"):
        assert any(
            dependency.startswith("sentence-transformers")
            for dependency in extras[extra]
        ), f"lians-sdk[{extra}] must not fall back to test-grade embeddings"
