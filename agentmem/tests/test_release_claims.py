from benchmarks.release_claims import evaluate_claims, valid_external_evidence


POLICY = {
    "claim_levels": {
        "foundation_verified": {"required_local_gates": ["a", "b"]},
        "production_validated": {
            "requires": ["foundation_verified", "load", "restore"],
        },
        "competitive_leader": {
            "requires": ["production_validated", "leaderboard", "independent"],
        },
    },
}


def _suite(a=True, b=True):
    return {
        "gates": [
            {"name": "a", "status": "passed" if a else "failed"},
            {"name": "b", "status": "passed" if b else "failed"},
        ],
    }


def _evidence(*, competitive=False, passed=True):
    record = {
        "schema": "lians.evidence.v1",
        "passed": passed,
        "artifact_sha256": "a" * 64,
        "generated_at": "2026-07-29T12:00:00+00:00",
        "methodology": "frozen protocol",
    }
    if competitive:
        record.update({
            "independent_party": "Independent Lab",
            "source_url": "https://example.org/result",
        })
    return record


def test_local_suite_only_permits_foundation_claim():
    result = evaluate_claims(POLICY, _suite())
    assert result["achieved_level"] == "foundation_verified"
    assert result["states"]["foundation_verified"]
    assert not result["best_claim_permitted"]


def test_production_evidence_does_not_imply_competitive_leadership():
    result = evaluate_claims(
        POLICY,
        _suite(),
        {"load": _evidence(), "restore": _evidence()},
    )
    assert result["achieved_level"] == "production_validated"
    assert not result["states"]["competitive_leader"]


def test_best_claim_requires_independent_competitive_evidence():
    result = evaluate_claims(
        POLICY,
        _suite(),
        {
            "load": _evidence(),
            "restore": _evidence(),
            "leaderboard": _evidence(competitive=True),
            "independent": _evidence(competitive=True),
        },
    )
    assert result["achieved_level"] == "competitive_leader"
    assert result["best_claim_permitted"]


def test_failed_local_gate_blocks_all_claims():
    result = evaluate_claims(POLICY, _suite(b=False))
    assert result["achieved_level"] == "unverified"
    assert "b" in result["missing"]["foundation_verified"]


def test_bare_booleans_cannot_unlock_external_claims():
    result = evaluate_claims(
        POLICY,
        _suite(),
        {"load": True, "restore": True},
    )
    assert result["achieved_level"] == "foundation_verified"
    assert result["invalid_external_evidence"] == ["load", "restore"]


def test_competitive_evidence_requires_independent_source():
    assert valid_external_evidence("load", _evidence())
    assert not valid_external_evidence("leaderboard", _evidence())
    assert valid_external_evidence("leaderboard", _evidence(competitive=True))
