from benchmarks.formal_proof import run_benchmark


def test_formal_proof_benchmark_smoke() -> None:
    report = run_benchmark(scenarios=8, scale_domain=12)

    assert report["pass"] is True
    assert report["correctness"]["false_claim_detection_recall"] == 1.0
    assert report["correctness"]["vacuous_proof_rejected"] is True
    assert report["scale"]["enumeration_complete"] is True
    assert report["scale"]["project_code_executed"] is False
    assert report["correctness"]["python_source_counterexample_found"] is True
    assert report["correctness"]["unsafe_python_source_rejected"] is True
    assert report["python_scale"]["bounded_implementation_correctness_proven"] is True
    assert report["python_scale"]["project_code_executed"] is False
