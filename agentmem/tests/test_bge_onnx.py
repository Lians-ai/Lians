from __future__ import annotations

import hashlib
import json
import sys
import types
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _small_spec(model: bytes, tokenizer: bytes):
    from lians.bge_onnx import BgeOnnxArtifactSpec

    provisional = BgeOnnxArtifactSpec(
        model_sha256=hashlib.sha256(model).hexdigest(),
        model_bytes=len(model),
        tokenizer_sha256=hashlib.sha256(tokenizer).hexdigest(),
        tokenizer_bytes=len(tokenizer),
        manifest_sha256="",
    )
    manifest_sha256 = hashlib.sha256(provisional.manifest_bytes()).hexdigest()
    return replace(provisional, manifest_sha256=manifest_sha256)


def test_pinned_export_manifest_hash_is_stable() -> None:
    from lians.bge_onnx import (
        BGE_ONNX_MANIFEST_SHA256,
        PINNED_BGE_ONNX_SPEC,
    )

    assert hashlib.sha256(PINNED_BGE_ONNX_SPEC.manifest_bytes()).hexdigest() == (
        BGE_ONNX_MANIFEST_SHA256
    )
    assert PINNED_BGE_ONNX_SPEC.manifest()["model"]["revision"] == (
        "d4aa6901d3a41ba39fb536a557fa166f842b0e09"
    )


def test_exporter_stages_and_revalidates_local_files(tmp_path: Path) -> None:
    from lians.bge_onnx import export_bge_onnx_artifact, validate_bge_onnx_artifact

    model = b"exact-model"
    tokenizer = b'{"exact":"tokenizer"}'
    spec = _small_spec(model, tokenizer)
    source_model = tmp_path / "source.onnx"
    source_tokenizer = tmp_path / "source-tokenizer.json"
    source_model.write_bytes(model)
    source_tokenizer.write_bytes(tokenizer)
    output = tmp_path / "artifact"

    exported = export_bge_onnx_artifact(
        model_path=source_model,
        tokenizer_path=source_tokenizer,
        output_dir=output,
        spec=spec,
    )

    assert exported.root == output.resolve()
    assert json.loads(exported.manifest.read_text(encoding="utf-8")) == spec.manifest()
    assert validate_bge_onnx_artifact(output, spec=spec).model.read_bytes() == model
    with pytest.raises(RuntimeError, match="new directory"):
        export_bge_onnx_artifact(
            model_path=source_model,
            tokenizer_path=source_tokenizer,
            output_dir=output,
            spec=spec,
        )


def test_validator_fails_closed_on_same_size_model_tamper(tmp_path: Path) -> None:
    from lians.bge_onnx import export_bge_onnx_artifact, validate_bge_onnx_artifact

    model = b"exact-model"
    tokenizer = b'{"exact":"tokenizer"}'
    spec = _small_spec(model, tokenizer)
    source_model = tmp_path / "source.onnx"
    source_tokenizer = tmp_path / "source-tokenizer.json"
    source_model.write_bytes(model)
    source_tokenizer.write_bytes(tokenizer)
    output = tmp_path / "artifact"
    export_bge_onnx_artifact(
        model_path=source_model,
        tokenizer_path=source_tokenizer,
        output_dir=output,
        spec=spec,
    )

    (output / "model.onnx").write_bytes(b"wrong-model")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        validate_bge_onnx_artifact(output, spec=spec)


@pytest.mark.parametrize("name", ["tokenizer.json", "manifest.json"])
def test_validator_fails_closed_on_tokenizer_or_manifest_tamper(tmp_path: Path, name: str) -> None:
    from lians.bge_onnx import export_bge_onnx_artifact, validate_bge_onnx_artifact

    model = b"exact-model"
    tokenizer = b'{"exact":"tokenizer"}'
    spec = _small_spec(model, tokenizer)
    source_model = tmp_path / "source.onnx"
    source_tokenizer = tmp_path / "source-tokenizer.json"
    source_model.write_bytes(model)
    source_tokenizer.write_bytes(tokenizer)
    output = tmp_path / "artifact"
    export_bge_onnx_artifact(
        model_path=source_model,
        tokenizer_path=source_tokenizer,
        output_dir=output,
        spec=spec,
    )

    target = output / name
    payload = target.read_bytes()
    target.write_bytes(bytes([payload[0] ^ 1]) + payload[1:])

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        validate_bge_onnx_artifact(output, spec=spec)


class _Encoding:
    def __init__(self, token: int):
        self.ids = [token, 0]
        self.attention_mask = [1, 0]
        self.type_ids = [0, 0]


class _FakeTokenizer:
    loaded_path = ""
    encoded_batches: list[list[str]] = []
    max_length = 0
    padding_enabled = False

    @classmethod
    def from_file(cls, path: str):
        cls.loaded_path = path
        return cls()

    def enable_truncation(self, *, max_length: int) -> None:
        type(self).max_length = max_length

    def enable_padding(self) -> None:
        type(self).padding_enabled = True

    def encode_batch(self, texts: list[str]) -> list[_Encoding]:
        type(self).encoded_batches.append(list(texts))
        return [_Encoding(index + 1) for index in range(len(texts))]


class _FakeSessionOptions:
    last: "_FakeSessionOptions | None" = None

    def __init__(self):
        type(self).last = self
        self.entries: dict[str, str] = {}
        self.intra_op_num_threads = 0
        self.inter_op_num_threads = 0
        self.execution_mode = None
        self.graph_optimization_level = None

    def add_session_config_entry(self, key: str, value: str) -> None:
        self.entries[key] = value


class _FakeSession:
    output_dimension = 1024
    last: "_FakeSession | None" = None

    def __init__(self, _path: str, *, sess_options, providers):
        type(self).last = self
        self.options = sess_options
        self.providers = providers

    def get_inputs(self):
        return [
            SimpleNamespace(name=name, type="tensor(int64)")
            for name in ("input_ids", "attention_mask", "token_type_ids")
        ]

    def get_outputs(self):
        return [
            SimpleNamespace(
                name="last_hidden_state",
                shape=["batch_size", "sequence_length", self.output_dimension],
            )
        ]

    def run(self, output_names, feed):
        assert output_names == ["last_hidden_state"]
        batch, sequence_length = feed["input_ids"].shape
        output = np.zeros((batch, sequence_length, self.output_dimension), dtype=np.float32)
        for index in range(batch):
            output[index, 0, index % self.output_dimension] = 1.0
        return [output]


def _fake_runtime_modules() -> dict[str, types.ModuleType]:
    ort = types.ModuleType("onnxruntime")
    ort.SessionOptions = _FakeSessionOptions
    ort.InferenceSession = _FakeSession
    ort.ExecutionMode = SimpleNamespace(ORT_SEQUENTIAL="sequential")
    ort.GraphOptimizationLevel = SimpleNamespace(ORT_ENABLE_ALL="all")
    tokenizers = types.ModuleType("tokenizers")
    tokenizers.Tokenizer = _FakeTokenizer
    return {"onnxruntime": ort, "tokenizers": tokenizers}


def _provider_settings() -> SimpleNamespace:
    return SimpleNamespace(
        bge_onnx_artifact_dir="/local/pinned-bge",
        bge_onnx_intra_op_threads=8,
        embedding_dim=1024,
    )


def _validated_artifact(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        model=tmp_path / "model.onnx",
        tokenizer=tmp_path / "tokenizer.json",
        assert_unchanged=MagicMock(),
    )


@pytest.mark.asyncio
async def test_provider_is_lazy_and_preserves_exact_query_semantics(
    tmp_path: Path,
) -> None:
    from lians.embeddings import BgeOnnxProvider

    _FakeTokenizer.encoded_batches.clear()
    artifact = _validated_artifact(tmp_path)
    with (
        patch("lians.embeddings.get_settings", return_value=_provider_settings()),
        patch("lians.bge_onnx.validate_bge_onnx_artifact", return_value=artifact) as validate,
        patch.dict(sys.modules, _fake_runtime_modules()),
    ):
        provider = BgeOnnxProvider()
        assert provider._runtime is None
        validate.assert_not_called()
        query = await provider.embed_query("where is the evidence?")

    assert validate.call_count == 1
    assert _FakeTokenizer.max_length == 512
    assert _FakeTokenizer.padding_enabled is True
    assert _FakeTokenizer.encoded_batches == [
        [
            "Represent this sentence for searching relevant passages: where is the evidence?",
            "where is the evidence?",
        ]
    ]
    assert query[0] == pytest.approx(2**-0.5)
    assert query[1] == pytest.approx(2**-0.5)
    assert np.linalg.norm(query) == pytest.approx(1.0)
    assert _FakeSessionOptions.last.entries["session.disable_prepacking"] == "1"
    assert _FakeSessionOptions.last.intra_op_num_threads == 8
    assert _FakeSessionOptions.last.inter_op_num_threads == 1
    assert _FakeSessionOptions.last.execution_mode == "sequential"
    assert _FakeSessionOptions.last.graph_optimization_level == "all"
    assert _FakeSession.last.providers == ["CPUExecutionProvider"]
    artifact.assert_unchanged.assert_called_once_with()


@pytest.mark.asyncio
async def test_provider_documents_remain_raw(tmp_path: Path) -> None:
    from lians.embeddings import BgeOnnxProvider

    _FakeTokenizer.encoded_batches.clear()
    artifact = _validated_artifact(tmp_path)
    with (
        patch("lians.embeddings.get_settings", return_value=_provider_settings()),
        patch("lians.bge_onnx.validate_bge_onnx_artifact", return_value=artifact),
        patch.dict(sys.modules, _fake_runtime_modules()),
    ):
        provider = BgeOnnxProvider()
        vectors = await provider.embed(["raw document one", "raw document two"])

    assert _FakeTokenizer.encoded_batches == [["raw document one", "raw document two"]]
    assert len(vectors) == 2
    assert all(len(vector) == 1024 for vector in vectors)


@pytest.mark.asyncio
async def test_provider_fails_closed_on_output_dimension_mismatch(tmp_path: Path) -> None:
    from lians.embeddings import BgeOnnxProvider

    artifact = _validated_artifact(tmp_path)
    _FakeSession.output_dimension = 384
    try:
        with (
            patch("lians.embeddings.get_settings", return_value=_provider_settings()),
            patch("lians.bge_onnx.validate_bge_onnx_artifact", return_value=artifact),
            patch.dict(sys.modules, _fake_runtime_modules()),
        ):
            provider = BgeOnnxProvider()
            with pytest.raises(ValueError, match="1024-dimensional"):
                await provider.embed_one("query")
    finally:
        _FakeSession.output_dimension = 1024


@pytest.mark.asyncio
async def test_provider_fails_closed_on_configured_dimension_mismatch() -> None:
    from lians.embeddings import BgeOnnxProvider

    settings = _provider_settings()
    settings.embedding_dim = 384
    with patch("lians.embeddings.get_settings", return_value=settings):
        provider = BgeOnnxProvider()
        with pytest.raises(ValueError, match="EMBEDDING_DIM=1024"):
            await provider.embed_one("query")

    assert provider._runtime is None


@pytest.mark.asyncio
async def test_provider_fails_closed_when_artifact_changes_during_load(
    tmp_path: Path,
) -> None:
    from lians.bge_onnx import BgeOnnxArtifactError
    from lians.embeddings import BgeOnnxProvider

    artifact = _validated_artifact(tmp_path)
    artifact.assert_unchanged.side_effect = BgeOnnxArtifactError(
        "BGE ONNX artifact changed during load: model.onnx"
    )
    with (
        patch("lians.embeddings.get_settings", return_value=_provider_settings()),
        patch("lians.bge_onnx.validate_bge_onnx_artifact", return_value=artifact),
        patch.dict(sys.modules, _fake_runtime_modules()),
    ):
        provider = BgeOnnxProvider()
        with pytest.raises(BgeOnnxArtifactError, match="changed during load"):
            await provider.embed_one("query")

    assert provider._runtime is None


def test_provider_is_registered_under_opt_in_name() -> None:
    from lians.embeddings import BgeOnnxProvider, get_provider

    settings = _provider_settings()
    settings.embedding_provider = "bge-onnx"
    with patch("lians.embeddings.get_settings", return_value=settings):
        assert isinstance(get_provider(), BgeOnnxProvider)
