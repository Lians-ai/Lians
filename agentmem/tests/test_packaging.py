import tomllib
from pathlib import Path


def test_local_entry_point_extras_include_real_semantic_embeddings() -> None:
    pyproject = Path(__file__).parents[1] / "sdk" / "python" / "pyproject.toml"
    package = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    extras = package["project"]["optional-dependencies"]

    for extra in ("local", "mcp"):
        assert any(
            dependency.startswith("sentence-transformers")
            for dependency in extras[extra]
        ), f"lians-sdk[{extra}] must not fall back to test-grade embeddings"


def test_local_runtime_extras_include_sqlalchemy_asyncio_support() -> None:
    pyproject = Path(__file__).parents[1] / "sdk" / "python" / "pyproject.toml"
    package = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    extras = package["project"]["optional-dependencies"]

    for extra in ("local", "bge-onnx", "mcp"):
        assert "sqlalchemy[asyncio]>=2.0" in extras[extra], (
            f"lians-sdk[{extra}] must install SQLAlchemy's asyncio dependencies"
        )
