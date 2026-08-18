from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _benchmark_module():
    path = Path(__file__).parents[1] / "benchmarks" / "temporal_continuity.py"
    spec = spec_from_file_location("lians_temporal_continuity_benchmark", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_temporal_continuity_benchmark_is_reproducible_and_bounded():
    result = _benchmark_module().run()

    assert result["schema"].endswith("temporal-continuity-benchmark/v0.1")
    assert "not an independent vendor benchmark" in result["claim_boundary"]
    assert result["systems"]["lians_temporal_continuity"]["passed"] == 6
    assert result["systems"]["lians_temporal_continuity"]["score_percent"] == 100.0
    assert result["systems"]["append_only_latest_match"]["passed"] < 6
