from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from lians.local_client import _ensure_src_importable

_ensure_src_importable()

from src.lians import bge_onnx, embeddings  # noqa: E402


def _small_artifact(root: Path) -> tuple[Path, bge_onnx.BgeOnnxArtifactSpec]:
    root.mkdir()
    model = b"small-model"
    tokenizer = b'{"version":"1.0"}'
    provisional = bge_onnx.BgeOnnxArtifactSpec(
        model_sha256=hashlib.sha256(model).hexdigest(),
        model_bytes=len(model),
        tokenizer_sha256=hashlib.sha256(tokenizer).hexdigest(),
        tokenizer_bytes=len(tokenizer),
        manifest_sha256="",
    )
    spec = replace(
        provisional,
        manifest_sha256=hashlib.sha256(provisional.manifest_bytes()).hexdigest(),
    )
    (root / "model.onnx").write_bytes(model)
    (root / "tokenizer.json").write_bytes(tokenizer)
    (root / "manifest.json").write_bytes(spec.manifest_bytes())
    return root, spec


def test_pinned_manifest_hash_is_self_consistent() -> None:
    spec = bge_onnx.PINNED_BGE_ONNX_SPEC
    assert hashlib.sha256(spec.manifest_bytes()).hexdigest() == spec.manifest_sha256


def test_artifact_validation_accepts_exact_regular_files(tmp_path: Path) -> None:
    root, spec = _small_artifact(tmp_path / "artifact")
    artifact = bge_onnx.validate_bge_onnx_artifact(root, spec=spec)

    assert artifact.root == root.resolve()
    artifact.assert_unchanged()


def test_artifact_validation_rejects_changed_model(tmp_path: Path) -> None:
    root, spec = _small_artifact(tmp_path / "artifact")
    (root / "model.onnx").write_bytes(b"changed")

    with pytest.raises(bge_onnx.BgeOnnxArtifactError, match="size mismatch"):
        bge_onnx.validate_bge_onnx_artifact(root, spec=spec)


def test_provider_registration_uses_bge_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "bge-onnx")
    monkeypatch.setenv("BGE_ONNX_ARTIFACT_DIR", "C:/verified-bge")
    monkeypatch.setenv("BGE_ONNX_INTRA_OP_THREADS", "3")
    from src.lians.config import get_settings

    get_settings.cache_clear()
    provider = embeddings.get_provider()

    assert isinstance(provider, embeddings.BgeOnnxProvider)
    assert provider._artifact_dir == "C:/verified-bge"
    assert provider._intra_op_threads == 3


def test_provider_cls_pooling_is_finite_and_normalized() -> None:
    class Tokenizer:
        def encode_batch(self, texts: list[str]) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(ids=[1, 2], attention_mask=[1, 1], type_ids=[0, 0])
                for _ in texts
            ]

    class Session:
        def run(self, _outputs: list[str], feed: dict[str, np.ndarray]) -> list[np.ndarray]:
            batch = feed["input_ids"].shape[0]
            result = np.zeros((batch, 2, 1024), dtype=np.float32)
            result[:, 0, 0] = 3.0
            result[:, 0, 1] = 4.0
            return [result]

    runtime = embeddings._BgeOnnxRuntime(
        session=Session(),
        tokenizer=Tokenizer(),
        inference_lock=threading.Lock(),
    )
    vectors = embeddings.BgeOnnxProvider._encode_sync(runtime, ["a", "b"])

    assert len(vectors) == 2
    assert vectors[0][:2] == pytest.approx([0.6, 0.8])
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0)


def test_query_embedding_combines_instructed_and_raw() -> None:
    provider = object.__new__(embeddings.BgeOnnxProvider)

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        assert texts[0].startswith(bge_onnx.BGE_ONNX_QUERY_INSTRUCTION)
        first = [0.0] * 1024
        second = [0.0] * 1024
        first[0] = 1.0
        second[1] = 1.0
        return [first, second]

    provider.embed = fake_embed
    result = asyncio.run(provider.embed_query("memory query"))

    assert result[:2] == pytest.approx([2**-0.5, 2**-0.5])
    assert np.linalg.norm(result) == pytest.approx(1.0)
