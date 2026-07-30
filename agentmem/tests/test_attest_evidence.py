import json

import pytest

from benchmarks.attest_evidence import attest


def test_attest_hashes_a_passed_artifact(tmp_path):
    artifact = tmp_path / "load.json"
    artifact.write_text(json.dumps({"passed": True}), encoding="utf-8")
    result = attest(
        "postgres_load_test",
        artifact,
        methodology="1000 requests against Postgres",
    )
    record = result["postgres_load_test"]
    assert len(record["artifact_sha256"]) == 64
    assert record["passed"] is True


def test_attest_refuses_failed_artifact(tmp_path):
    artifact = tmp_path / "failed.json"
    artifact.write_text(json.dumps({"passed": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="passed=true"):
        attest("postgres_load_test", artifact, methodology="load")


def test_competitive_attestation_requires_independent_source(tmp_path):
    artifact = tmp_path / "leaderboard.json"
    artifact.write_text(json.dumps({"passed": True}), encoding="utf-8")
    with pytest.raises(ValueError, match="independent party"):
        attest(
            "longmemeval_v2_lafs_submission",
            artifact,
            methodology="official protocol",
        )
