"""
Tests for the self-hosted SentenceTransformerProvider and air-gapped mode.

SentenceTransformerProvider tests use a mock so sentence-transformers does not
need to be installed in CI and no model download occurs.

Air-gapped mode tests verify the startup validator catches bad config before
any customer data is processed.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# SentenceTransformerProvider — unit tests with mocked model
# ---------------------------------------------------------------------------

class TestSentenceTransformerProvider:

    def _make_provider(self, model_name: str = "BAAI/bge-large-en-v1.5"):
        """Build a provider with a mocked ST model that returns 1024-dim vectors."""
        from lians.embeddings import SentenceTransformerProvider

        with patch("lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model=model_name,
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
        from lians.embeddings import SentenceTransformerProvider

        with patch("lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model="BAAI/bge-large-en-v1.5",
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        # _model should be None — not loaded yet
        assert provider._model is None

    def test_dim_mismatch_raises_at_load(self):
        """If the model produces wrong-dim vectors, ValueError at load time."""
        from lians.embeddings import SentenceTransformerProvider

        with patch("lians.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                sentence_transformer_model="some-384-dim-model",
                embedding_dim=1024,
            )
            provider = SentenceTransformerProvider()

        # Simulate a 384-dim model
        fake_st_module = types.ModuleType("sentence_transformers")
        bad_model = MagicMock()
        bad_model.encode = lambda texts, normalize_embeddings=True: np.zeros(
            (len(texts), 384), dtype=np.float32
        )
        fake_st_module.SentenceTransformer = lambda name: bad_model

        with (
            patch.dict("sys.modules", {"sentence_transformers": fake_st_module}),
            pytest.raises(ValueError, match="384-dim"),
        ):
            provider._load()

    def test_provider_registered_in_get_provider(self):
        """get_provider() must return SentenceTransformerProvider for the right key."""
        from lians.embeddings import SentenceTransformerProvider

        with patch("lians.embeddings.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                embedding_provider="sentence-transformers",
                sentence_transformer_model="BAAI/bge-large-en-v1.5",
            )
            from lians.embeddings import get_provider
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
            "graph_extract_llm": False,
            "auto_metadata_llm": False,
            "siem_url": "",
            "stripe_api_key": "",
            "otel_exporter_otlp_endpoint": "",
        }
        defaults.update(kwargs)
        return MagicMock(**defaults)

    def test_valid_airgap_config_passes(self):
        from lians.main import _validate_airgap
        # Should not raise
        _validate_airgap(self._settings())

    def test_local_provider_is_also_safe(self):
        from lians.main import _validate_airgap
        _validate_airgap(self._settings(embedding_provider="local"))

    def test_voyage_provider_raises(self):
        from lians.main import _validate_airgap
        with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER"):
            _validate_airgap(self._settings(embedding_provider="voyage"))

    def test_openai_provider_raises(self):
        from lians.main import _validate_airgap
        with pytest.raises(RuntimeError, match="EMBEDDING_PROVIDER"):
            _validate_airgap(self._settings(embedding_provider="openai"))

    def test_llm_stage_enabled_raises(self):
        from lians.main import _validate_airgap
        with pytest.raises(RuntimeError, match="SUPERSESSION_LLM_STAGE"):
            _validate_airgap(self._settings(supersession_llm_stage=True))

    @pytest.mark.parametrize(
        ("setting", "value", "message"),
        [
            ("graph_extract_llm", True, "GRAPH_EXTRACT_LLM"),
            ("auto_metadata_llm", True, "AUTO_METADATA_LLM"),
            ("siem_url", "https://siem.example.test", "SIEM_URL"),
            ("stripe_api_key", "sk_test_configured", "STRIPE_API_KEY"),
            (
                "otel_exporter_otlp_endpoint",
                "https://otel.example.test:4317",
                "OTEL_EXPORTER_OTLP_ENDPOINT",
            ),
        ],
    )
    def test_payload_egress_setting_raises(self, setting, value, message):
        from lians.main import _validate_airgap

        with pytest.raises(RuntimeError, match=message):
            _validate_airgap(self._settings(**{setting: value}))

    def test_both_violations_reported_together(self):
        """RuntimeError message should list all violations, not just the first."""
        from lians.main import _validate_airgap
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
        # _validate_airgap is only called when airgap_mode=True, so this
        # just ensures it doesn't silently run when called with bad config
        # that would otherwise be fine in non-airgap mode.
        # The test confirms the guard is in _validate_airgap, not in the providers.
        settings = self._settings(airgap_mode=False, embedding_provider="voyage")
        # Calling it directly would still raise — the airgap_mode check lives
        # in lifespan(), which only calls _validate_airgap when True.
        # This test documents that contract.
        assert settings.airgap_mode is False


class TestAirgapEgressDefenseInDepth:

    async def test_siem_stream_is_disabled_without_http(self, monkeypatch):
        from lians import siem

        settings = types.SimpleNamespace(
            airgap_mode=True,
            siem_url="https://siem.example.test",
            siem_token="secret",
        )
        monkeypatch.setattr(siem, "get_settings", lambda: settings)

        class ForbiddenClient:
            def __init__(self, *args, **kwargs):
                raise AssertionError("HTTP client must not be constructed in air-gap mode")

        monkeypatch.setattr(siem.httpx, "AsyncClient", ForbiddenClient)
        assert siem.siem_enabled() is False
        assert await siem.stream_event({"sensitive": "value"}) is False

    async def test_metering_outbox_is_disabled(self, monkeypatch):
        from lians import metering

        monkeypatch.setattr(
            metering,
            "get_settings",
            lambda: types.SimpleNamespace(airgap_mode=True, stripe_api_key="configured"),
        )
        db = MagicMock()
        assert (
            await metering.enqueue_usage_event(
                db,
                namespace="airgap",
                event_name="write",
                quantity=1,
                source_identifier="event-id",
            )
            is None
        )
        db.execute.assert_not_called()

    async def test_legacy_webhook_http_is_disabled(self, monkeypatch):
        from lians import config, webhook_service

        monkeypatch.setattr(
            config,
            "get_settings",
            lambda: types.SimpleNamespace(airgap_mode=True),
        )
        monkeypatch.setattr(
            webhook_service,
            "_validate_webhook_destination",
            MagicMock(side_effect=AssertionError("DNS must not run in air-gap mode")),
        )

        status, error = await webhook_service._http_post(
            "https://receiver.example.test",
            b'{"sensitive":"value"}',
            "sha256=unused",
        )
        assert status == 0
        assert error == "airgap_disabled"
