from __future__ import annotations
import asyncio
import hashlib
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

    async def embed(self, texts: List[str]) -> List[List[float]]:
        model = await self._get_model()
        loop = asyncio.get_event_loop()
        # Run blocking CPU inference off the event loop thread.
        result = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, normalize_embeddings=True).tolist(),
        )
        return result


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
            return LocalProvider()


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider
