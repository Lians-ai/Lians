from __future__ import annotations

import pytest
from lians.telemetry import instrument_sqlalchemy


def test_sqlalchemy_instrumentation_uses_async_engine_sync_facade(monkeypatch):
    sqlalchemy_instrumentation = pytest.importorskip("opentelemetry.instrumentation.sqlalchemy")
    sync_engine = object()
    async_engine = type("AsyncEngineStub", (), {"sync_engine": sync_engine})()
    captured = {}

    def capture(_self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setattr(
        sqlalchemy_instrumentation.SQLAlchemyInstrumentor,
        "instrument",
        capture,
    )

    instrument_sqlalchemy(async_engine)

    assert captured["engine"] is sync_engine
