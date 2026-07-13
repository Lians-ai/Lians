from __future__ import annotations
import asyncio
import hashlib
import logging
import numpy as np
from abc import ABC, abstractmethod
from typing import List
from .config import get_settings


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
        self._model = None
        self._load_lock = asyncio.Lock()

    def _load(self):
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(self._model_name)
        # Cap the sequence length: long-context models (arctic: 8192) accept
        # pasted-document-sized inputs whose attention buffers OOM commodity
        # machines (one 8k-token text = ~1GB). 512 tokens is the standard
        # retrieval cap — embeddings truncate; stored content is unaffected.
        msl = getattr(model, "max_seq_length", None)
        if isinstance(msl, int) and msl > 512:
            model.max_seq_length = 512
        # Validate dimension before the first real request, not on every call.
        probe = model.encode(["probe"], normalize_embeddings=True)
        actual_dim = probe.shape[1]
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
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(None, self._load)
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
        loop = asyncio.get_event_loop()
        # Run blocking CPU inference off the event loop thread.
        result = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, normalize_embeddings=True).tolist(),
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
        seed = int(hashlib.md5(token.encode()).hexdigest(), 16) % (2**31)
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
