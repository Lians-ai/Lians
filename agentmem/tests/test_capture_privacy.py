"""Privacy invariants shared by OTLP and Universal Recorder capture."""

from lians.capture_privacy import canonical_json, sanitize_capture


def test_secret_key_variants_are_redacted_even_in_full_capture():
    captured = sanitize_capture(
        {
            "X-API-Key": "first-secret",
            "OPENAI_API_KEY": "second-secret",
            "clientSecret": "third-secret",
            "proxy.authorization": "Bearer fourth-secret",
            "credentials": {"value": "fifth-secret"},
            "set-cookie": "session=sixth-secret",
            "webhook_signature": "seventh-secret",
        },
        mode="full",
    )

    rendered = canonical_json(captured)
    assert "first-secret" not in rendered
    assert "second-secret" not in rendered
    assert "third-secret" not in rendered
    assert "fourth-secret" not in rendered
    assert "fifth-secret" not in rendered
    assert "sixth-secret" not in rendered
    assert "seventh-secret" not in rendered
    assert rendered.count('"$captured":"redacted"') == 7


def test_hash_only_preserves_safe_protocol_metadata_but_hashes_unknown_values():
    captured = sanitize_capture(
        {
            "model_id": "open-model/v1",
            "max_tokens": 512,
            "input_tokens": 21,
            "vendor_blob": {"customer_note": "must never persist"},
        },
        mode="hash_only",
    )

    assert captured["model_id"] == "open-model/v1"
    assert captured["max_tokens"] == 512
    assert captured["input_tokens"] == 21
    assert captured["vendor_blob"]["$captured"] == "hash_only"
    assert "must never persist" not in canonical_json(captured)


def test_metadata_only_omits_unknown_vendor_metadata():
    captured = sanitize_capture(
        {"status": "ok", "vendor_request": {"patient": "private"}},
        mode="metadata_only",
    )

    assert captured["status"] == "ok"
    assert captured["vendor_request"] == {"$captured": "omitted"}


def test_content_is_hashed_after_nested_secrets_are_redacted():
    captured = sanitize_capture(
        {
            "messages": [
                {
                    "content": "private prompt",
                    "authorization": "Bearer top-secret-token",
                }
            ]
        },
        mode="hash_only",
    )

    assert captured["messages"]["$captured"] == "hash_only"
    rendered = canonical_json(captured)
    assert "private prompt" not in rendered
    assert "top-secret-token" not in rendered


def test_secret_shaped_scalar_values_are_redacted_under_generic_keys():
    captured = sanitize_capture(
        {
            "status": "ok",
            "name": "Bearer abcdefghijklmnopqrstuvwxyz",
            "provider": "postgresql://alice:password@example.test/db",
        },
        mode="full",
    )

    assert captured["status"] == "ok"
    assert captured["name"] == {"$captured": "redacted"}
    assert captured["provider"] == {"$captured": "redacted"}
