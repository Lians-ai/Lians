"""Daemon-free regression tests for Tempo's metrics-generator safety gate."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB / "workload"))

from verify import (
    CheckFailure,
    prometheus_metric_sum,
    verify_tempo_metrics_generator,
    verify_tempo_runtime_config,
)


class TempoSafetyTests(unittest.TestCase):
    def test_prometheus_metric_sum_handles_labelled_samples(self):
        metrics = (
            'tempo_metric{tenant="one"} 2\n'
            'tempo_metric{tenant="two"} 3\n'
            "tempo_other 99"
        )
        self.assertEqual(prometheus_metric_sum(metrics, "tempo_metric"), 5)
        self.assertIsNone(prometheus_metric_sum(metrics, "missing"))

    def test_local_blocks_requires_traces_storage(self):
        with self.assertRaisesRegex(CheckFailure, "traces_storage"):
            verify_tempo_runtime_config("processors: [local-blocks]")
        detail = verify_tempo_runtime_config(
            "metrics_generator:\n  traces_storage:\n    path: /var/tempo/traces\n"
            "processors: [local-blocks]"
        )
        self.assertTrue(detail["local_blocks_enabled"])
        self.assertTrue(detail["traces_wal_configured"])

    def test_collections_with_zero_active_series_fail(self):
        metrics = (
            "tempo_metrics_generator_registry_collections_total 429890\n"
            "tempo_metrics_generator_registry_active_series 0"
        )
        with self.assertRaisesRegex(CheckFailure, "zero active series"):
            verify_tempo_metrics_generator(metrics)

    def test_active_generator_passes(self):
        metrics = (
            "tempo_metrics_generator_registry_collections_total 12\n"
            'tempo_metrics_generator_registry_active_series{tenant="single-tenant"} 4'
        )
        detail = verify_tempo_metrics_generator(metrics)
        self.assertEqual(detail["metrics_generator_collections"], 12)
        self.assertEqual(detail["metrics_generator_active_series"], 4)


if __name__ == "__main__":
    unittest.main()
