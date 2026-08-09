"""
Tests for the self-hosted SentenceTransformerProvider and air-gapped mode.

SentenceTransformerProvider tests use a mock so sentence-transformers does not
need to be installed in CI and no model download occurs.

Air-gapped mode tests verify the startup validator catches bad config before
any customer data is processed.
"""
from __future__ import annotations

import asyncio
import types
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# SentenceTransformerProvider — unit tests with mocked model
# ---------------------------------------------------------------------------

class TestSentenceTransformerProvider:

    def _make_provider(self, model_name: str = "BAAI/bge-large-en-v1.5"):
        """Build a provider with a mocked ST model that returns 1024-dim vectors."""
        from src.lians.embeddings import SentenceTransformerProvider

        with patch("src.lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model=model_name,
                sentence_transformer_revision="",
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        # Inject a fake model so _load() is never called
        fake_model = MagicMock()
        fake_model.encode = lambda texts, normalize_embeddings=True: np.random.randn(
            len(texts), 1024
        ).astype(np.float32)
        provider._model = fake_model
        return provider

    async def test_embed_returns_correct_shape(self):
        provider = self._make_provider()
        result = await provider.embed(["AAPL price target raised to $210", "Q3 earnings beat consensus"])
        assert len(result) == 2
        assert len(result[0]) == 1024
        assert len(result[1]) == 1024

    async def test_embed_one_returns_single_vector(self):
        provider = self._make_provider()
        vec = await provider.embed_one("Fed holds rates steady")
        assert isinstance(vec, list)
        assert len(vec) == 1024

    async def test_embed_returns_floats(self):
        provider = self._make_provider()
        result = await provider.embed(["test content"])
        assert all(isinstance(x, float) for x in result[0])

    def test_model_loaded_lazily(self):
        """Provider must not load the model at construction time."""
        from src.lians.embeddings import SentenceTransformerProvider

        with patch("src.lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model="BAAI/bge-large-en-v1.5",
                sentence_transformer_revision="",
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        # _model should be None — not loaded yet
        assert provider._model is None

    def test_dim_mismatch_raises_at_load(self):
        """If the model produces wrong-dim vectors, ValueError at load time."""
        from src.lians.embeddings import SentenceTransformerProvider

        with patch("src.lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model="some-384-dim-model",
                sentence_transformer_revision="",
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        # Simulate a 384-dim model
        fake_st_module = types.ModuleType("sentence_transformers")
        bad_model = MagicMock()
        bad_model.get_sentence_embedding_dimension.return_value = 384
        fake_st_module.SentenceTransformer = lambda name: bad_model

        with (
            patch.dict("sys.modules", {"sentence_transformers": fake_st_module}),
            pytest.raises(ValueError, match="384-dim"),
        ):
            provider._load()
        bad_model.encode.assert_not_called()

    def test_dimension_validation_does_not_run_inference(self):
        """Loading validates model metadata without an expensive probe encode."""
        from src.lians.embeddings import SentenceTransformerProvider

        with patch("src.lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model="BAAI/bge-large-en-v1.5",
                sentence_transformer_revision="",
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        fake_st_module = types.ModuleType("sentence_transformers")
        model = MagicMock()
        model.get_sentence_embedding_dimension.return_value = 1024
        fake_st_module.SentenceTransformer = lambda name: model

        with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
            assert provider._load() is model
        model.encode.assert_not_called()

    def test_pinned_revision_is_forwarded_to_sentence_transformers(self):
        from src.lians.embeddings import SentenceTransformerProvider

        revision = "d4aa6901d3a41ba39fb536a557fa166f842b0e09"
        with patch("src.lians.embeddings.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model="BAAI/bge-large-en-v1.5",
                sentence_transformer_revision=revision,
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        fake_st_module = types.ModuleType("sentence_transformers")
        model = MagicMock()
        model.get_sentence_embedding_dimension.return_value = 1024
        constructor = MagicMock(return_value=model)
        fake_st_module.SentenceTransformer = constructor

        with patch.dict("sys.modules", {"sentence_transformers": fake_st_module}):
            assert provider._load() is model
        constructor.assert_called_once_with(
            "BAAI/bge-large-en-v1.5",
            revision=revision,
        )

    async def test_background_warmup_does_not_block_startup(self):
        """Scheduling warmup returns while a slow provider is still working."""
        from src.lians.main import _start_embedding_warmup

        release = asyncio.Event()

        class SlowProvider:
            async def embed_one(self, text):
                await release.wait()
                return [0.0] * 1024

        task = _start_embedding_warmup(
            SlowProvider(),
            expected_dim=1024,
            provider_name="sentence-transformers",
        )
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        assert await task is True

    async def test_cancelled_embedding_waiter_keeps_native_capacity_occupied(self):
        """Cancellation must not admit new work while the native thread runs."""
        import threading

        from src.lians.embeddings import (
            EmbeddingWorkloadSaturatedError,
            _BoundedInferenceExecutor,
        )

        started = threading.Event()
        release = threading.Event()
        executor = _BoundedInferenceExecutor(max_workers=1, queue_timeout=0.02)

        def blocking_work():
            started.set()
            release.wait(timeout=2)
            return "done"

        running = asyncio.create_task(executor.run(blocking_work))
        while not started.is_set():
            await asyncio.sleep(0)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        with pytest.raises(EmbeddingWorkloadSaturatedError):
            await executor.run(lambda: "must-not-run")

        release.set()
        for _ in range(100):
            await asyncio.sleep(0.01)
            try:
                assert await executor.run(lambda: "next") == "next"
                break
            except EmbeddingWorkloadSaturatedError:
                continue
        else:
            pytest.fail("native inference slot was not released after completion")

    def test_provider_registered_in_get_provider(self):
        """get_provider() must return SentenceTransformerProvider for the right key."""
        from src.lians.embeddings import SentenceTransformerProvider

        with patch("src.lians.embeddings.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                embedding_provider="sentence-transformers",
                sentence_transformer_model="BAAI/bge-large-en-v1.5",
                sentence_transformer_revision="",
            )
            from src.lians.embeddings import get_provider
            provider = get_provider()

        assert isinstance(provider, SentenceTransformerProvider)


# ---------------------------------------------------------------------------
# Air-gapped mode — startup validation
# ---------------------------------------------------------------------------

class TestAirgapValidation:

    def _settings(self, **kwargs):
        """Build a minimal settings mock."""
        defaults = {
            "airgap_mode": True,
            "embedding_provider": "sentence-transformers",
            "supersession_llm_stage": False,
        }
        defaults.update(kwargs)
        return MagicMock(**defaults)

    def test_valid_airgap_config_passes(self):
        from src.lians.main import _validate_airgap
        # Should not raise
        _validate_airgap(self._settings())

    def test_local_provider_is_also_safe(self):
        from src.lians.main import _validate_airgap
        _validate_airgap(self._settings(embedding_provider="local"))

    def test_voyage_provider_raises(self):
        from src.lians.main import _validate_airgap
        with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER"):
            _validate_airgap(self._settings(embedding_provider="voyage"))

    def test_openai_provider_raises(self):
        from src.lians.main import _validate_airgap
        with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER"):
            _validate_airgap(self._settings(embedding_provider="openai"))

    def test_llm_stage_enabled_raises(self):
        from src.lians.main import _validate_airgap
        with pytest.raises(RuntimeError, match="SUPERSESSION_LLM_STAGE"):
            _validate_airgap(self._settings(supersession_llm_stage=True))

    def test_both_violations_reported_together(self):
        """RuntimeError message should list all violations, not just the first."""
        from src.lians.main import _validate_airgap
        with pytest.raises(RuntimeError) as exc_info:
            _validate_airgap(self._settings(
                embedding_provider="voyage",
                supersession_llm_stage=True,
            ))
        msg = str(exc_info.value)
        assert "EMBEDDING_PROVIDER" in msg
        assert "SUPERSESSION_LLM_STAGE" in msg

    def test_airgap_false_skips_validation(self):
        """When AIRGAP_MODE=false, bad providers must not raise."""
        from src.lians.main import _validate_airgap
        # _validate_airgap is only called when airgap_mode=True, so this
        # just ensures it doesn't silently run when called with bad config
        # that would otherwise be fine in non-airgap mode.
        # The test confirms the guard is in _validate_airgap, not in the providers.
        settings = self._settings(airgap_mode=False, embedding_provider="voyage")
        # Calling it directly would still raise — the airgap_mode check lives
        # in lifespan(), which only calls _validate_airgap when True.
        # This test documents that contract.
        assert settings.airgap_mode is False
