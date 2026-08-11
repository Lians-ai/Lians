from datetime import datetime, timezone

from src.lians.memory_compiler import (
    COMPILER_VERSION,
    METADATA_KEY,
    SCHEMA_VERSION,
    classify_memory,
    compile_memory_metadata,
    extract_entities,
)


def test_classifies_core_memory_kinds_deterministically():
    cases = {
        "preference": "Alice prefers dark mode and would rather use keyboard shortcuts.",
        "procedure": "First open the console, then select the evidence export workflow.",
        "policy": "Compliance policy requires human approval before production deployment.",
        "outcome": "The migration completed and reduced p95 latency by 32 percent.",
        "relationship": "Alice reports to Bob and works with the platform team.",
        "episode": "Yesterday we investigated the production timeout.",
        "reflection": "Lesson learned: next time validate the index before the cutover.",
        "fact": "The service uses PostgreSQL for durable state.",
    }
    for expected, text in cases.items():
        kind, confidence, method = classify_memory(text)
        assert kind == expected
        assert 0.0 < confidence <= 1.0
        assert method == "rules"


def test_caller_memory_type_is_authoritative():
    kind, confidence, method = classify_memory(
        "This sounds like an outcome but is intentionally procedural.",
        {"memory_type": "procedure"},
    )
    assert kind == "procedure"
    assert confidence == 1.0
    assert method == "caller"


def test_compilation_preserves_metadata_and_attaches_source_provenance():
    event_time = datetime(2026, 7, 29, 14, 0, tzinfo=timezone.utc)
    result = compile_memory_metadata(
        "Alice prefers Grafana and currently works with Acme Corp.",
        {"tenant_label": "regulated", "ticker": "ACME"},
        event_time=event_time,
        source="otel://trace/123",
    )

    assert result["tenant_label"] == "regulated"
    block = result[METADATA_KEY]
    assert block["schema"] == SCHEMA_VERSION
    assert block["compiler"] == COMPILER_VERSION
    assert block["kind"] == "preference"
    assert "ACME" in block["entities"]
    assert "Alice" in block["entities"]
    assert block["temporal_hints"] == ["currently"]
    assert block["source"]["event_time"] == event_time.isoformat()
    assert block["source"]["source"] == "otel://trace/123"
    assert len(block["source"]["content_sha256"]) == 64


def test_recompilation_replaces_only_reserved_projection():
    event_time = datetime.now(timezone.utc)
    first = compile_memory_metadata(
        "Alice likes blue.",
        {"customer": "alice"},
        event_time=event_time,
        source=None,
    )
    second = compile_memory_metadata(
        "Alice likes green.",
        first,
        event_time=event_time,
        source=None,
    )
    assert second["customer"] == "alice"
    assert second[METADATA_KEY]["source"]["content_sha256"] != first[METADATA_KEY]["source"]["content_sha256"]


def test_entity_extraction_is_bounded_and_deduplicated():
    entities = extract_entities(
        "Alice met Alice at Acme Corp with NVDA.",
        {"ticker": "NVDA"},
    )
    assert entities.count("Alice") == 1
    assert entities.count("NVDA") == 1


def test_incidental_like_is_not_misclassified_as_a_preference():
    kind, _confidence, _method = classify_memory(
        "It looks like the database migration completed successfully."
    )
    assert kind == "outcome"
