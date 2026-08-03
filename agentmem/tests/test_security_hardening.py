"""Regression coverage for fail-closed invariants and bounded degradation signals."""

from __future__ import annotations

import ast
import base64
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import lians.crypto as crypto
import lians.metrics as metrics
import lians.secret_storage as secret_storage
import lians.subject_key_loader as subject_key_loader
from lians.models import SubjectKey


_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "lians"
_INVARIANT_MODULES = (
    "crypto.py",
    "integration_service.py",
    "key_rotation.py",
    "scim_reconciliation_service.py",
    "secret_storage.py",
)


@pytest.mark.parametrize("filename", _INVARIANT_MODULES)
def test_security_invariants_do_not_depend_on_python_assertions(filename: str) -> None:
    source = (_SOURCE_ROOT / filename).read_text(encoding="utf-8")
    assertions = [
        node.lineno
        for node in ast.walk(ast.parse(source, filename=filename))
        if isinstance(node, ast.Assert)
    ]
    assert assertions == []


def test_empty_legacy_subject_keyring_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        crypto,
        "get_master_keyring",
        lambda: SimpleNamespace(candidates=()),
    )

    with pytest.raises(RuntimeError, match="no configured key candidates"):
        crypto.unwrap_subject_key(b"\x00" * 28)


def test_empty_legacy_sealed_value_keyring_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        secret_storage,
        "get_master_keyring",
        lambda: SimpleNamespace(candidates=()),
    )
    value = "lians-sealed:v1:" + base64.urlsafe_b64encode(b"\x00" * 28).decode()

    with pytest.raises(RuntimeError, match="no configured key candidates"):
        secret_storage.unseal_text(value, purpose="test", context="test")


def test_v2_sealed_value_without_classified_key_id_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(secret_storage, "sealed_text_version", lambda _value: (2, None))
    monkeypatch.setattr(
        secret_storage,
        "get_master_keyring",
        lambda: SimpleNamespace(),
    )

    with pytest.raises(
        secret_storage.SealedValueFormatError,
        match="omitted its key id",
    ):
        secret_storage.unseal_text("synthetic", purpose="test", context="test")


def test_best_effort_metric_collapses_untrusted_component_labels(monkeypatch) -> None:
    marker = "tenant-and-secret-controlled-label"
    observations: list[tuple[dict[str, str], int]] = []

    class MetricProbe:
        def labels(self, **labels):
            self.labels_value = labels
            return self

        def inc(self, count=1):
            observations.append((self.labels_value, count))

    monkeypatch.setattr(metrics, "_best_effort_failures", MetricProbe())
    metrics.record_best_effort_failure(marker)

    assert observations == [({"component": "other"}, 1)]
    assert marker not in repr(observations)


@pytest.mark.asyncio
async def test_subject_key_unwrap_failure_withholds_key_and_observes_safely(
    db,
    monkeypatch,
    caplog,
) -> None:
    namespace = "private-namespace-marker"
    subject_ref = "private-subject-marker"
    db.add(
        SubjectKey(
            namespace=namespace,
            subject_id=subject_ref,
            enc_key=b"\x00" * 28,
        )
    )
    await db.commit()
    observations: list[tuple[str, int]] = []

    def observe(component: str, *, count: int = 1) -> None:
        observations.append((component, count))

    monkeypatch.setattr(subject_key_loader, "record_best_effort_failure", observe)
    caplog.set_level(logging.WARNING, logger="lians.subject_key_loader")

    loaded = await subject_key_loader.load_subject_keys(
        db,
        namespace,
        [subject_ref],
    )

    assert loaded == {}
    assert observations == [("subject_key_unwrap", 1)]
    assert "affected content withheld" in caplog.text
    assert namespace not in caplog.text
    assert subject_ref not in caplog.text
