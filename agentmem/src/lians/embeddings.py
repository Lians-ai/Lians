from __future__ import annotations
import asyncio
import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List
from .config import get_settings


class EmbeddingWorkloadSaturatedError(RuntimeError):
    """No bounded native-inference slot became available in time."""


class _BoundedInferenceExecutor:
    """Keep capacity occupied until native work actually finishes."""

    def __init__(self, *, max_workers: int, queue_timeout: float | None) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="lians-embedding",
        )
        self._slots = asyncio.BoundedSemaphore(max_workers)
        self._queue_timeout = queue_timeout

    async def run(self, function):
        try:
            if self._queue_timeout is None:
                await self._slots.acquire()
            else:
                await asyncio.wait_for(
                    self._slots.acquire(),
                    timeout=self._queue_timeout,
                )
        except TimeoutError as exc:
            raise EmbeddingWorkloadSaturatedError(
                "Embedding capacity is temporarily saturated"
            ) from exc

        loop = asyncio.get_running_loop()
        try:
            concurrent_future = self._executor.submit(function)
        except Exception:
            self._slots.release()
            raise

        def release_slot(_future) -> None:
            loop.call_soon_threadsafe(self._slots.release)

        concurrent_future.add_done_callback(release_slot)
        return await asyncio.shield(asyncio.wrap_future(concurrent_future))


def _inference_executor(settings) -> _BoundedInferenceExecutor:
    hosted = getattr(settings, "hosted_mcp_enabled", False) is True
    workers = int(settings.hosted_mcp_max_concurrent_inference) if hosted else 4
    queue_timeout = (
        float(settings.hosted_mcp_inference_queue_timeout_seconds)
        if hosted
        else None
    )
    return _BoundedInferenceExecutor(max_workers=workers, queue_timeout=queue_timeout)


class EmbeddingProvider(ABC):
    dim: int

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    async def embed_one(self, text: str) -> List[float]:
        results = await self.embed([text])
        return results[0]

    async def embed_query(self, text: str) -> List[float]:
        """Embed a retrieval *query* (as opposed to a document).

        Default is identical to ``embed_one``; providers whose models are
        trained with an asymmetric query instruction (e.g. bge) override this.
        Document embeddings are never prefixed, so existing stores stay valid.
        """
        return await self.embed_one(text)

    async def embed_queries(self, texts: List[str]) -> List[List[float]]:
        """Batch query embeddings when a provider has no specialized path."""
        return await asyncio.gather(*(self.embed_query(text) for text in texts))


class VoyageProvider(EmbeddingProvider):
    """Voyage finance/domain embedding model."""
    dim = 1024

    def __init__(self):
        import voyageai
        settings = get_settings()
        self._client = voyageai.AsyncClient(api_key=settings.voyage_api_key)
        # voyage-finance-2: domain-tuned for financial text, ~4pt MTEB gain over general models.
        # Verify the current model name and pricing at docs.voyageai.com before migration.
        self._model = "voyage-finance-2"

    async def embed(self, texts: List[str]) -> List[List[float]]:
        result = await self._client.embed(texts, model=self._model, input_type="document")
        return result.embeddings


class OpenAIProvider(EmbeddingProvider):
    """Cheap fallback for dev / CI."""
    dim = 1536  # text-embedding-3-small native dim, we'll truncate to 1024

    def __init__(self):
        from openai import AsyncOpenAI
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        resp = await self._client.embeddings.create(
            input=texts,
            model="text-embedding-3-small",
            dimensions=1024,  # request truncated output directly
        )
        return [item.embedding for item in resp.data]


class SentenceTransformerProvider(EmbeddingProvider):
    """
    Fully self-hosted embeddings — no data leaves the machine.

    Uses sentence-transformers running in a thread-pool executor so inference
    does not block the async event loop.  The model is loaded lazily on first
    call so startup stays fast even for large models.

    Default model: BAAI/bge-large-en-v1.5 (1024-dim, strong general quality,
    Apache 2.0 license).  For a truly air-gapped deployment, pre-download the
    model files and point SENTENCE_TRANSFORMER_MODEL at the local directory:

        SENTENCE_TRANSFORMER_MODEL=/opt/models/bge-large-en-v1.5

    sentence-transformers will load from disk without any network calls.
    """
    dim = 1024

    def __init__(self):
        settings = get_settings()
        self._model_name = settings.sentence_transformer_model
        self._model_revision = settings.sentence_transformer_revision
        self._model = None
        self._load_lock = asyncio.Lock()
        self._executor = _inference_executor(settings)
        self._load_task: asyncio.Task | None = None

    def _load(self):
        from sentence_transformers import SentenceTransformer
        model_kwargs = (
            {"revision": self._model_revision}
            if self._model_revision
            else {}
        )
        model = SentenceTransformer(self._model_name, **model_kwargs)
        # Cap the sequence length: long-context models (arctic: 8192) accept
        # pasted-document-sized inputs whose attention buffers OOM commodity
        # machines (one 8k-token text = ~1GB). 512 tokens is the standard
        # retrieval cap — embeddings truncate; stored content is unaffected.
        msl = getattr(model, "max_seq_length", None)
        if isinstance(msl, int) and msl > 512:
            model.max_seq_length = 512
        # SentenceTransformer exposes the output dimension from model metadata.
        # Reading it avoids a full CPU inference pass during model load.
        actual_dim = model.get_sentence_embedding_dimension()
        if actual_dim is None:
            raise ValueError(
                f"Model '{self._model_name}' did not report an embedding dimension."
            )
        if actual_dim != self.dim:
            raise ValueError(
                f"Model '{self._model_name}' produces {actual_dim}-dim embeddings "
                f"but the database schema expects {self.dim} dims. "
                f"Use a 1024-dim model (e.g. BAAI/bge-large-en-v1.5, "
                f"intfloat/e5-large-v2) or reprovision with a matching EMBEDDING_DIM."
            )
        return model

    async def _get_model(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is not None:
                return self._model
            if self._load_task is None:
                self._load_task = asyncio.create_task(self._executor.run(self._load))
            self._model = await asyncio.shield(self._load_task)
            return self._model

    # Asymmetric retrieval models embed *queries* with a trained instruction
    # while documents stay raw; using the wrong (or no) prompt costs real
    # recall. Gate on model name, per each family's model card.
    _BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
    _QUERY_PREFIX_FAMILIES = (
        ("snowflake-arctic-embed", "query: "),
        ("e5-", "query: "),
    )

    async def embed(self, texts: List[str]) -> List[List[float]]:
        model = await self._get_model()
        result = await self._executor.run(
            lambda: model.encode(texts, normalize_embeddings=True).tolist()
        )
        return result

    async def embed_query(self, text: str) -> List[float]:
        name = self._model_name.lower()
        for marker, prefix in self._QUERY_PREFIX_FAMILIES:
            if marker in name:
                return await self.embed_one(prefix + text)
        if "bge" in name:
            # Average of the instructed and raw query embeddings, renormalized.
            # The instruction alone helps short queries but hurts verbose ones;
            # the average beats either endpoint on evidence-retrieval evals.
            both = await self.embed([self._BGE_QUERY_INSTRUCTION + text, text])
            merged = [(a + b) / 2.0 for a, b in zip(both[0], both[1])]
            norm = sum(x * x for x in merged) ** 0.5 or 1.0
            return [x / norm for x in merged]
        return await self.embed_one(text)

    async def embed_queries(self, texts: List[str]) -> List[List[float]]:
        """Apply the model-family query instruction in one inference batch."""
        name = self._model_name.lower()
        for marker, prefix in self._QUERY_PREFIX_FAMILIES:
            if marker in name:
                return await self.embed([prefix + text for text in texts])
        if "bge" in name:
            instructed = [self._BGE_QUERY_INSTRUCTION + text for text in texts]
            encoded = await self.embed(instructed + texts)
            out: List[List[float]] = []
            for first, second in zip(encoded[:len(texts)], encoded[len(texts):]):
                merged = [(a + b) / 2.0 for a, b in zip(first, second)]
                norm = sum(x * x for x in merged) ** 0.5 or 1.0
                out.append([x / norm for x in merged])
            return out
        return await self.embed(texts)


@dataclass
class _BgeOnnxRuntime:
    session: Any
    tokenizer: Any
    inference_lock: threading.Lock


class BgeOnnxProvider(EmbeddingProvider):
    """Exact, hash-pinned BGE v1.5 ONNX inference with no network fallback."""

    dim = 1024

    def __init__(self) -> None:
        settings = get_settings()
        self._artifact_dir = settings.bge_onnx_artifact_dir
        self._intra_op_threads = settings.bge_onnx_intra_op_threads
        self._configured_dimension = settings.embedding_dim
        self._runtime: _BgeOnnxRuntime | None = None
        self._load_lock = asyncio.Lock()
        self._executor = _inference_executor(settings)
        self._load_task: asyncio.Task | None = None

    def _load(self) -> _BgeOnnxRuntime:
        from .bge_onnx import (
            BGE_ONNX_EMBEDDING_DIMENSION,
            BGE_ONNX_MAX_SEQUENCE_LENGTH,
            validate_bge_onnx_artifact,
        )

        if self._configured_dimension != BGE_ONNX_EMBEDDING_DIMENSION:
            raise ValueError(
                "Pinned BGE ONNX requires EMBEDDING_DIM=1024; "
                f"configured value is {self._configured_dimension}"
            )
        if not self._artifact_dir.strip():
            raise ValueError(
                "BGE_ONNX_ARTIFACT_DIR is required when EMBEDDING_PROVIDER=bge-onnx"
            )

        import onnxruntime as ort
        from tokenizers import Tokenizer

        artifact = validate_bge_onnx_artifact(self._artifact_dir)
        tokenizer = Tokenizer.from_file(str(artifact.tokenizer))
        tokenizer.enable_truncation(max_length=BGE_ONNX_MAX_SEQUENCE_LENGTH)
        tokenizer.enable_padding()

        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.add_session_config_entry("session.disable_prepacking", "1")
        if self._intra_op_threads:
            options.intra_op_num_threads = self._intra_op_threads
            options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(artifact.model),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        artifact.assert_unchanged()

        inputs = {item.name: item for item in session.get_inputs()}
        expected_inputs = {"input_ids", "attention_mask", "token_type_ids"}
        if set(inputs) != expected_inputs or any(
            item.type != "tensor(int64)" for item in inputs.values()
        ):
            raise ValueError("Pinned BGE ONNX model has an incompatible input contract")
        outputs = session.get_outputs()
        if len(outputs) != 1 or outputs[0].name != "last_hidden_state":
            raise ValueError("Pinned BGE ONNX model has an incompatible output contract")
        output_shape = outputs[0].shape
        if len(output_shape) != 3 or output_shape[-1] != BGE_ONNX_EMBEDDING_DIMENSION:
            raise ValueError(
                "Pinned BGE ONNX model does not produce 1024-dimensional embeddings"
            )
        return _BgeOnnxRuntime(
            session=session,
            tokenizer=tokenizer,
            inference_lock=threading.Lock(),
        )

    async def _get_runtime(self) -> _BgeOnnxRuntime:
        if self._runtime is not None:
            return self._runtime
        async with self._load_lock:
            if self._runtime is None:
                if self._load_task is None:
                    self._load_task = asyncio.create_task(self._executor.run(self._load))
                self._runtime = await asyncio.shield(self._load_task)
            return self._runtime

    @staticmethod
    def _encode_sync(runtime: _BgeOnnxRuntime, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        with runtime.inference_lock:
            encoded = runtime.tokenizer.encode_batch(texts)
            feed = {
                "input_ids": np.asarray([row.ids for row in encoded], dtype=np.int64),
                "attention_mask": np.asarray(
                    [row.attention_mask for row in encoded], dtype=np.int64
                ),
                "token_type_ids": np.asarray(
                    [row.type_ids for row in encoded], dtype=np.int64
                ),
            }
            output = np.asarray(runtime.session.run(["last_hidden_state"], feed)[0])
        if output.ndim != 3 or output.shape != (
            len(texts),
            feed["input_ids"].shape[1],
            BgeOnnxProvider.dim,
        ):
            raise ValueError("Pinned BGE ONNX model returned an invalid embedding shape")
        cls = output[:, 0, :].astype(np.float32, copy=False)
        if not np.isfinite(cls).all():
            raise ValueError("Pinned BGE ONNX model returned non-finite embeddings")
        norms = np.linalg.norm(cls, axis=1, keepdims=True)
        if np.any(norms <= 0):
            raise ValueError("Pinned BGE ONNX model returned a zero-norm embedding")
        return (cls / norms).tolist()

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        runtime = await self._get_runtime()
        return await self._executor.run(lambda: self._encode_sync(runtime, texts))

    async def embed_query(self, text: str) -> List[float]:
        from .bge_onnx import BGE_ONNX_QUERY_INSTRUCTION

        instructed, raw = await self.embed([BGE_ONNX_QUERY_INSTRUCTION + text, text])
        merged = (np.asarray(instructed) + np.asarray(raw)) / 2.0
        norm = float(np.linalg.norm(merged))
        if not np.isfinite(merged).all() or norm <= 0:
            raise ValueError("Pinned BGE ONNX model returned an invalid query embedding")
        return (merged / norm).tolist()


class LocalProvider(EmbeddingProvider):
    """Deterministic word-projection for tests — zero API calls.

    Each token maps deterministically to a random unit vector; the text
    embedding is the L2-normalized sum of its token vectors.  Two texts
    sharing tokens will have meaningfully similar cosines, which is the
    minimal property needed for semantic recall tests to behave correctly.
    """
    dim = 1024

    @staticmethod
    def _token_vec(token: str, dim: int) -> np.ndarray:
        # Deterministic PRNG seeding only; MD5 is not a security primitive here.
        seed = int(
            hashlib.md5(token.encode(), usedforsecurity=False).hexdigest(), 16
        ) % (2**31)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(dim).astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    async def embed(self, texts: List[str]) -> List[List[float]]:
        results = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for token in text.lower().split():
                vec += self._token_vec(token, self.dim)
            norm = np.linalg.norm(vec)
            results.append((vec / (norm + 1e-9)).tolist())
        return results


def get_provider() -> EmbeddingProvider:
    settings = get_settings()
    match settings.embedding_provider:
        case "voyage":
            return VoyageProvider()
        case "openai":
            return OpenAIProvider()
        case "sentence-transformers":
            return SentenceTransformerProvider()
        case "bge-onnx":
            return BgeOnnxProvider()
        case _:
            # "local" is a deterministic token-hash stub for unit tests. On
            # LOCOMO it retrieves at 24% vs the real model's 82% — production
            # data behind it is silently getting test-grade recall, so say so
            # every time it is constructed.
            logging.getLogger("agentmem.embeddings").warning(
                "EMBEDDING_PROVIDER='local' is the deterministic TEST STUB — "
                "semantic recall will be test-grade (24% vs 82% evidence "
                "retrieval on LOCOMO). Install lians-sdk[local] and set "
                "EMBEDDING_PROVIDER=sentence-transformers for real recall."
            )
            return LocalProvider()


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider
