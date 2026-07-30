from benchmarks.locomo_eval import _normalize_evidence


def test_normalize_evidence_expands_space_and_semicolon_packed_dialogue_ids():
    assert _normalize_evidence([
        "D8:6; D9:17",
        "D22:1 D22:2 D9:10 D9:11",
    ]) == ["D8:6", "D9:17", "D22:1", "D22:2", "D9:10", "D9:11"]


def test_normalize_evidence_preserves_normal_ids_and_removes_duplicates():
    assert _normalize_evidence(["D1:2", "D1:2", "D1:3"]) == ["D1:2", "D1:3"]
