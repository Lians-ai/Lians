from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def test_latency_benchmark_reports_lians_owned_work_without_provider_claims() -> None:
    source = Path(__file__).resolve().parents[1] / "benchmarks" / "continuity_latency.py"
    spec = spec_from_file_location("lians_continuity_latency_benchmark", source)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    result = module.run(memory_count=20, iterations=5)

    assert result["fixture"] == {"synthetic": True, "memory_count": 20, "iterations": 5}
    assert result["results"]["bounded_recall_and_signed_receipt"]["p95_ms"] >= 0
    assert result["results"]["graph_nodes"] >= 20
    assert "do not measure or accelerate provider model inference" in result["claim_boundary"]
