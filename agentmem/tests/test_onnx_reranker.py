from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from lians import ranking
from lians.onnx_reranker import OnnxCrossEncoder, OnnxRerankerError


class _Encoding:
    def __init__(self, ids: list[int], types: list[int]) -> None:
        self.ids = ids
        self.attention_mask = [1] * len(ids)
        self.type_ids = types


class _Tokenizer:
    loaded_path = ""
    truncation: tuple[int, str] | None = None

    @classmethod
    def from_file(cls, path: str) -> _Tokenizer:
        cls.loaded_path = path
        return cls()

    def enable_truncation(self, *, max_length: int, strategy: str) -> None:
        type(self).truncation = (max_length, strategy)

    def token_to_id(self, token: str) -> int | None:
        return 7 if token == "[PAD]" else None

    def encode_batch(self, pairs: list[tuple[str, str]]) -> list[_Encoding]:
        return [
            _Encoding([index + 1, 11, 12 + index], [0, 0, 1]) for index, _pair in enumerate(pairs)
        ]


class _SessionOptions:
    graph_optimization_level = None
    intra_op_num_threads = 0
    inter_op_num_threads = 0


class _Session:
    latest_inputs: dict[str, np.ndarray] | None = None

    def __init__(self, path: str, *, providers: list[str], sess_options: object) -> None:
        self.path = path
        self.providers = providers
        self.options = sess_options

    def get_inputs(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name="input_ids"),
            SimpleNamespace(name="attention_mask"),
            SimpleNamespace(name="token_type_ids"),
        ]

    def get_outputs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name="logits")]

    def run(self, names: list[str], inputs: dict[str, np.ndarray]) -> list[np.ndarray]:
        assert names == ["logits"]
        type(self).latest_inputs = inputs
        return [inputs["input_ids"][:, :1].astype(np.float32)]


@pytest.fixture
def fake_onnx_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            SessionOptions=_SessionOptions,
            GraphOptimizationLevel=SimpleNamespace(ORT_ENABLE_ALL="all"),
            InferenceSession=_Session,
        ),
    )
    monkeypatch.setitem(sys.modules, "tokenizers", SimpleNamespace(Tokenizer=_Tokenizer))


def test_onnx_cross_encoder_batches_and_returns_one_score_per_pair(
    tmp_path: Path,
    fake_onnx_modules: None,
) -> None:
    model = tmp_path / "model.onnx"
    tokenizer = tmp_path / "tokenizer.json"
    model.write_bytes(b"model")
    tokenizer.write_text("{}", encoding="utf-8")

    encoder = OnnxCrossEncoder(
        model,
        tokenizer_path=tokenizer,
        max_length=64,
        batch_size=2,
        intra_op_threads=2,
    )
    scores = encoder.predict([("q1", "d1"), ("q2", "d2"), ("q3", "d3")])

    assert scores.tolist() == [1.0, 2.0, 1.0]
    assert _Tokenizer.loaded_path == str(tokenizer.resolve())
    assert _Tokenizer.truncation == (64, "longest_first")
    assert _Session.latest_inputs is not None
    assert set(_Session.latest_inputs) == {
        "input_ids",
        "attention_mask",
        "token_type_ids",
    }


def test_onnx_cross_encoder_rejects_missing_artifacts(tmp_path: Path) -> None:
    encoder = OnnxCrossEncoder(tmp_path / "missing.onnx")
    with pytest.raises(OnnxRerankerError, match="model does not exist"):
        encoder.predict([("q", "d")])


def test_cross_encoder_diagnostics_are_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def predict(self, pairs: object, *, show_progress_bar: bool) -> np.ndarray:
            raise RuntimeError("boom")

    monkeypatch.setattr(ranking, "RERANKER_MODEL", "broken")
    monkeypatch.setattr(ranking, "RERANKER_ONNX_MODEL", "")
    monkeypatch.setattr(ranking, "_reranker", Broken())
    diagnostics: dict[str, object] = {}
    scored = [("first", 0.8, "a"), ("second", 0.7, "b")]

    assert ranking.rerank_cross_encoder("q", scored, 1, diagnostics) == scored[:1]
    assert diagnostics == {
        "reranker_complete": False,
        "reranker_backend": "sentence-transformers",
        "reranker_candidates": 2,
        "reranker_error_type": "RuntimeError",
    }


def test_cross_encoder_reorders_and_marks_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    class Working:
        def predict(self, pairs: object, *, show_progress_bar: bool) -> np.ndarray:
            return np.asarray([0.1, 0.9], dtype=np.float32)

    monkeypatch.setattr(ranking, "RERANKER_MODEL", "working")
    monkeypatch.setattr(ranking, "RERANKER_ONNX_MODEL", "")
    monkeypatch.setattr(ranking, "_reranker", Working())
    diagnostics: dict[str, object] = {}
    scored = [("first", 0.8, "a"), ("second", 0.7, "b")]

    assert ranking.rerank_cross_encoder("q", scored, 1, diagnostics) == [scored[1]]
    assert diagnostics == {
        "reranker_complete": True,
        "reranker_backend": "sentence-transformers",
        "reranker_candidates": 2,
    }


def test_cross_encoder_keeps_unavailable_content_at_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Working:
        def predict(self, pairs: object, *, show_progress_bar: bool) -> np.ndarray:
            assert pairs == [("q", "a"), ("q", "b")]
            return np.asarray([0.1, 0.9], dtype=np.float32)

    monkeypatch.setattr(ranking, "RERANKER_MODEL", "working")
    monkeypatch.setattr(ranking, "RERANKER_ONNX_MODEL", "")
    monkeypatch.setattr(ranking, "_reranker", Working())
    diagnostics: dict[str, object] = {}
    scored = [
        ("first", 0.8, "a"),
        ("unavailable", 0.75, None),
        ("second", 0.7, "b"),
    ]

    assert ranking.rerank_cross_encoder("q", scored, 3, diagnostics) == [
        scored[2],
        scored[0],
        scored[1],
    ]
    assert diagnostics["reranker_candidates"] == 2
