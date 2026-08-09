"""Dependency-light ONNX cross-encoder inference for recall reranking.

The heavy PyTorch/Transformers stack is useful when exporting a model, but it
dominates cold-start latency when all recall needs is an already-exported ONNX
graph and its tokenizer.  This module deliberately imports ONNX Runtime and
``tokenizers`` lazily so the normal Lians path has no new startup cost.

The configured graph is expected to accept the standard BERT inputs
(``input_ids``, ``attention_mask``, and optionally ``token_type_ids``) and to
return one relevance logit per query/document pair.
"""

from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any, Sequence

import numpy as np


class OnnxRerankerError(RuntimeError):
    """Raised when the optional ONNX reranker cannot produce valid scores."""


class OnnxCrossEncoder:
    """Lazy ONNX cross-encoder with a ``CrossEncoder.predict``-like surface."""

    def __init__(
        self,
        model_path: str | os.PathLike[str],
        *,
        tokenizer_path: str | os.PathLike[str] | None = None,
        max_length: int = 256,
        batch_size: int = 64,
        intra_op_threads: int = 4,
    ) -> None:
        self.model_path = Path(model_path).expanduser().resolve()
        self.tokenizer_path = (
            Path(tokenizer_path).expanduser().resolve()
            if tokenizer_path
            else self.model_path.with_name("tokenizer.json")
        )
        if max_length < 8:
            raise ValueError("ONNX reranker max_length must be at least 8")
        if batch_size < 1:
            raise ValueError("ONNX reranker batch_size must be positive")
        if intra_op_threads < 1:
            raise ValueError("ONNX reranker intra_op_threads must be positive")
        self.max_length = int(max_length)
        self.batch_size = int(batch_size)
        self.intra_op_threads = int(intra_op_threads)
        self._session: Any = None
        self._tokenizer: Any = None
        self._input_names: frozenset[str] = frozenset()
        self._output_name = ""
        self._pad_id = 0
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._load_lock:
            if self._session is not None:
                return
            if not self.model_path.is_file():
                raise OnnxRerankerError(f"ONNX reranker model does not exist: {self.model_path}")
            if not self.tokenizer_path.is_file():
                raise OnnxRerankerError(
                    f"ONNX reranker tokenizer does not exist: {self.tokenizer_path}"
                )

            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise OnnxRerankerError(
                    "ONNX reranking requires the onnxruntime and tokenizers packages"
                ) from exc

            tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
            tokenizer.enable_truncation(
                max_length=self.max_length,
                strategy="longest_first",
            )
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = min(
                self.intra_op_threads,
                max(1, os.cpu_count() or 1),
            )
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                str(self.model_path),
                providers=["CPUExecutionProvider"],
                sess_options=options,
            )
            input_names = frozenset(item.name for item in session.get_inputs())
            if not {"input_ids", "attention_mask"}.issubset(input_names):
                raise OnnxRerankerError("ONNX reranker graph lacks input_ids or attention_mask")
            outputs = session.get_outputs()
            if not outputs:
                raise OnnxRerankerError("ONNX reranker graph has no outputs")

            self._tokenizer = tokenizer
            self._session = session
            self._input_names = input_names
            self._output_name = outputs[0].name
            self._pad_id = tokenizer.token_to_id("[PAD]") or 0

    def _inputs(self, pairs: Sequence[tuple[str, str]]) -> dict[str, np.ndarray]:
        encodings = self._tokenizer.encode_batch(list(pairs))
        if len(encodings) != len(pairs):
            raise OnnxRerankerError("tokenizer returned an incomplete reranker batch")
        width = max((len(item.ids) for item in encodings), default=0)
        if width < 1:
            raise OnnxRerankerError("tokenizer returned an empty reranker batch")
        batch = len(encodings)
        input_ids = np.full((batch, width), self._pad_id, dtype=np.int64)
        attention_mask = np.zeros((batch, width), dtype=np.int64)
        token_type_ids = np.zeros((batch, width), dtype=np.int64)
        for index, item in enumerate(encodings):
            length = len(item.ids)
            input_ids[index, :length] = item.ids
            attention_mask[index, :length] = item.attention_mask
            token_type_ids[index, :length] = item.type_ids
        values = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        return {name: values[name] for name in self._input_names if name in values}

    def predict(
        self,
        pairs: Sequence[tuple[str, str]],
        *,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """Return one finite float32 relevance score per pair.

        ``show_progress_bar`` is accepted for compatibility with
        ``sentence_transformers.CrossEncoder`` and is intentionally ignored.
        """

        del show_progress_bar
        if not pairs:
            return np.asarray([], dtype=np.float32)
        self._load()
        scores: list[float] = []
        for offset in range(0, len(pairs), self.batch_size):
            batch = pairs[offset : offset + self.batch_size]
            outputs = self._session.run(
                [self._output_name],
                self._inputs(batch),
            )
            if len(outputs) != 1:
                raise OnnxRerankerError("ONNX reranker returned an invalid output list")
            values = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
            if len(values) != len(batch):
                raise OnnxRerankerError("ONNX reranker returned the wrong number of scores")
            scores.extend(float(value) for value in values)
        if len(scores) != len(pairs) or not all(math.isfinite(value) for value in scores):
            raise OnnxRerankerError("ONNX reranker returned invalid scores")
        return np.asarray(scores, dtype=np.float32)
