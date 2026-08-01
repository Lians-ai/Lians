"""Fast, daemon-free checks for the versioned homelab contract."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "homelab"


def expressions(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "expr" and isinstance(item, str):
                yield item
            yield from expressions(item)
    elif isinstance(value, list):
        for item in value:
            yield from expressions(item)


class HomelabStaticContract(unittest.TestCase):
    def test_dashboard_json_and_metric_names_are_real(self):
        dashboards = [
            LAB / "grafana/dashboards/lians-homelab.json",
            ROOT / "integrations/grafana-lians-app/src/dashboards/lians-operations.json",
        ]
        queries: list[str] = []
        for path in dashboards:
            queries.extend(expressions(json.loads(path.read_text(encoding="utf-8"))))
        rendered = "\n".join(queries)
        self.assertIn("agentmem_memory_writes_total", rendered)
        self.assertIn("agentmem_memory_recalls_total", rendered)
        self.assertNotIn("lians_http_requests_total", rendered)
        self.assertNotIn("lians_http_request_duration_seconds", rendered)

    def test_homelab_dashboard_surfaces_observed_activity_without_false_alarm_colors(self):
        dashboard = json.loads(
            (LAB / "grafana/dashboards/lians-homelab.json").read_text(encoding="utf-8")
        )
        panels = {panel["title"]: panel for panel in dashboard["panels"]}
        self.assertIn("Traces observed · session", panels)
        self.assertIn("Recall throughput by cache result", panels)
        self.assertNotIn("Memory writes · 1h", panels)
        self.assertNotIn("Write throughput by supersession outcome", panels)
        self.assertIn(
            "tempo_ingester_traces_created_total",
            panels["Traces observed · session"]["targets"][0]["expr"],
        )
        self.assertIn(
            "sum by (cache_hit)",
            panels["Recall throughput by cache result"]["targets"][0]["expr"],
        )
        self.assertEqual(
            panels["Recalls · 1h"]["fieldConfig"]["defaults"]["color"],
            {"mode": "fixed", "fixedColor": "blue"},
        )
        self.assertNotIn("continuous-GrYlRd", json.dumps(dashboard))

    def test_packaged_operations_rate_survives_an_absent_write_series(self):
        dashboard = json.loads(
            (
                ROOT / "integrations/grafana-lians-app/src/dashboards/lians-operations.json"
            ).read_text(encoding="utf-8")
        )
        operation_rate = dashboard["panels"][0]["targets"][0]["expr"]
        self.assertIn("agentmem_memory_writes_total", operation_rate)
        self.assertIn("agentmem_memory_recalls_total", operation_rate)
        self.assertEqual(operation_rate.count("or vector(0)"), 2)

    def test_trace_pipelines_are_split_to_prevent_feedback(self):
        alloy = (LAB / "alloy/config.alloy").read_text(encoding="utf-8")
        packaged_alloy = (
            ROOT / "integrations/grafana-lians-app/provisioning/alloy-lians.alloy"
        ).read_text(encoding="utf-8")
        plugin_source = (ROOT / "integrations/grafana-lians-app/src/module.tsx").read_text(
            encoding="utf-8"
        )
        compose = (LAB / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn('otelcol.receiver.otlp "lians_runtime"', alloy)
        self.assertIn('endpoint = "0.0.0.0:14317"', alloy)
        self.assertIn('otelcol.receiver.otlp "integration"', alloy)
        self.assertIn('endpoint = "0.0.0.0:4318"', alloy)
        self.assertIn("http://alloy:14317", compose)
        runtime_block = alloy.split('otelcol.processor.batch "lians_runtime"', 1)[1]
        runtime_block = runtime_block.split('otelcol.receiver.otlp "integration"', 1)[0]
        self.assertIn("otelcol.exporter.otlp.tempo.input", runtime_block)
        self.assertNotIn("otelcol.exporter.otlphttp.lians.input", runtime_block)
        for packaged in (packaged_alloy, plugin_source):
            self.assertIn('otelcol.receiver.otlp "integration"', packaged)
            self.assertIn('otelcol.receiver.otlp "lians_runtime"', packaged)
            packaged_runtime = packaged.split('otelcol.processor.batch "lians_runtime"', 1)[1]
            packaged_runtime = packaged_runtime.split('otelcol.exporter.otlphttp "lians"', 1)[0]
            self.assertNotIn("otelcol.exporter.otlphttp.lians.input", packaged_runtime)

    def test_tempo_cannot_silently_back_off_its_metrics_generator(self):
        tempo = (LAB / "tempo/tempo.yml").read_text(encoding="utf-8")
        verifier = (LAB / "workload/verify.py").read_text(encoding="utf-8")
        processor_lines = [
            line for line in tempo.splitlines() if line.strip().startswith("processors:")
        ]
        local_blocks_enabled = any("local-blocks" in line for line in processor_lines)
        traces_wal_configured = "traces_storage:" in tempo
        self.assertIn("processors: [service-graphs, span-metrics]", tempo)
        self.assertFalse(local_blocks_enabled and not traces_wal_configured)
        self.assertIn('endpoint(TEMPO_URL, "/status/config")', verifier)
        self.assertIn("tempo_metrics_generator_registry_active_series", verifier)
        for failure in (
            "local blocks processor requires traces wal",
            "could not initialize processors",
            "instance creation in backoff",
            "failed to forward request to metrics generator",
            "error tailing wal",
        ):
            self.assertIn(failure, verifier)

    def test_data_stores_are_not_published_to_host(self):
        compose = (LAB / "compose.yaml").read_text(encoding="utf-8")
        postgres_block = compose.split("  postgres:", 1)[1].split("\n  redis:", 1)[0]
        redis_block = compose.split("  redis:", 1)[1].split("\n  migrate:", 1)[0]
        self.assertNotIn("ports:", postgres_block)
        self.assertNotIn("ports:", redis_block)
        self.assertIn('"127.0.0.1:8001:8000"', compose)
        self.assertIn('"127.0.0.1:3000:3000"', compose)

    def test_workload_never_embeds_a_raw_api_key(self):
        alloy = (LAB / "alloy/config.alloy").read_text(encoding="utf-8")
        self.assertIn("local.file.lians_api_key.content", alloy)
        self.assertFalse(any("api-key" in path.name for path in LAB.rglob("*") if path.is_file()))

    def test_customer_sample_is_read_only_and_verifier_is_rebuilt(self):
        compose = (LAB / "compose.yaml").read_text(encoding="utf-8")
        powershell = (LAB / "lab.ps1").read_text(encoding="utf-8")
        shell = (LAB / "lab.sh").read_text(encoding="utf-8")
        scenario = (LAB / "workload/scenario.py").read_text(encoding="utf-8")
        verifier = (LAB / "workload/verify.py").read_text(encoding="utf-8")
        self.assertEqual(compose.count("source: ${LAB_SAMPLE_FILE:-./samples/default.json}"), 3)
        self.assertEqual(compose.count("target: /sample/input.json"), 3)
        self.assertGreaterEqual(compose.count("read_only: true"), 3)
        self.assertIn('@("--profile", "tools", "build", "verify")', powershell)
        self.assertIn("compose --profile tools build verify", shell)
        self.assertIn("dispose", powershell)
        self.assertIn("dispose|reset", shell)
        self.assertIn("--resolve-for-launch", powershell)
        self.assertIn("--resolve-for-launch", shell)
        self.assertIn(".local.json", scenario)
        self.assertIn("proof_sample == mounted_sample.manifest", verifier)

    def test_streaming_integration_lab_is_bounded_and_versioned(self):
        compose = (LAB / "compose.yaml").read_text(encoding="utf-8")
        powershell = (LAB / "lab.ps1").read_text(encoding="utf-8")
        shell = (LAB / "lab.sh").read_text(encoding="utf-8")
        dataset = (LAB / "workload/dataset.py").read_text(encoding="utf-8")
        bulk = (LAB / "workload/bulk_ingest.py").read_text(encoding="utf-8")
        workload_image = (LAB / "workload/Dockerfile").read_text(encoding="utf-8")
        catalog = json.loads((LAB / "integrations/catalog.json").read_text(encoding="utf-8"))
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        bulk_block = compose.split("  bulk-ingest:", 1)[1].split("\nnetworks:", 1)[0]
        self.assertIn("profiles: [bulk]", bulk_block)
        self.assertIn("source: ${LAB_DATASET_FILE:-./datasets/default.ndjson}", bulk_block)
        self.assertIn("target: /dataset/input.ndjson", bulk_block)
        self.assertIn("read_only: true", bulk_block)
        self.assertIn("lab-state:/state:ro", bulk_block)
        self.assertIn("LAB_DATASET_MAX_RECORDS", bulk_block)
        self.assertIn("LAB_BULK_CONCURRENCY", bulk_block)
        self.assertIn("RATE_LIMIT_PER_MINUTE: ${LAB_RATE_LIMIT_PER_MINUTE:-3000}", compose)

        for launcher in (powershell, shell):
            for command in (
                "list-integrations",
                "check-dataset",
                "generate-dataset",
                "ingest-dataset",
                "capacity-report",
            ):
                self.assertIn(command, launcher)
            self.assertIn("LAB_DATASET_POLICY_ACK", launcher)
            self.assertIn("latest-capacity-receipt.json", launcher)

        self.assertIn("preflight_dataset", bulk)
        self.assertIn("iter_dataset_records", bulk)
        self.assertIn("ThreadPoolExecutor", bulk)
        self.assertIn("LatencyHistogram", bulk)
        self.assertIn("dataset changed", dataset)
        self.assertIn("dataset.py", workload_image)
        self.assertIn("bulk_ingest.py", workload_image)
        self.assertIn("homelab/datasets/*.local.ndjson", gitignore)
        self.assertIn("homelab/datasets/*.local.ndjson", dockerignore)
        self.assertIn("homelab/samples/*.local.json", dockerignore)

        integrations = {item["id"]: item for item in catalog["integrations"]}
        self.assertEqual(
            set(integrations), {"grafana", "otlp", "scenario-json", "memory-ndjson"}
        )
        self.assertEqual(integrations["memory-ndjson"]["status"], "local-lab")
        for name in ("laptop", "workstation", "dedicated"):
            profile = (LAB / f"profiles/{name}.env").read_text(encoding="utf-8")
            self.assertIn(f"LAB_SCALE_PROFILE={name}", profile)
            self.assertIn("LAB_DATASET_MAX_RECORDS=", profile)
            self.assertIn("LAB_DATASET_MAX_BYTES=", profile)

    def test_homelab_requires_signed_offline_verifiable_evidence(self):
        compose = (LAB / "compose.yaml").read_text(encoding="utf-8")
        powershell = (LAB / "lab.ps1").read_text(encoding="utf-8")
        shell = (LAB / "lab.sh").read_text(encoding="utf-8")
        verifier = (LAB / "workload/verify.py").read_text(encoding="utf-8")
        workload_image = (LAB / "workload/Dockerfile").read_text(encoding="utf-8")
        example_env = (LAB / ".env.example").read_text(encoding="utf-8")
        self.assertIn("LIANS_EVIDENCE_SIGNING_PRIVATE_KEY=", example_env)
        self.assertIn(
            "LIANS_EVIDENCE_SIGNING_KEY_ID=lians-homelab-ed25519-v1", example_env
        )
        self.assertIn("env_bootstrap.py", powershell)
        self.assertIn("env_bootstrap.py", shell)
        self.assertIn("EVIDENCE_SIGNING_PRIVATE_KEY: ${LIANS_EVIDENCE_SIGNING_PRIVATE_KEY:?", compose)
        self.assertIn(
            "EVIDENCE_SIGNING_KEY_ID: ${LIANS_EVIDENCE_SIGNING_KEY_ID:-", compose
        )
        self.assertIn("EXPECTED_EVIDENCE_SIGNING_KEY_ID:", compose)
        verify_block = compose.split("  verify:", 1)[1].split("\nnetworks:", 1)[0]
        self.assertNotIn("LIANS_EVIDENCE_SIGNING_PRIVATE_KEY", verify_block)
        self.assertIn("Ed25519PublicKey.from_public_bytes", verifier)
        self.assertIn('signature.get("status") == "signed"', verifier)
        self.assertIn("cryptography==48.0.1", workload_image)

    def test_grafana_app_is_provisioned_and_dashboard_is_portable(self):
        app = (LAB / "grafana/provisioning/plugins/lians-app.yml").read_text(encoding="utf-8")
        self.assertIn("type: lians-lians-app", app)
        self.assertIn("disabled: false", app)
        dashboard = json.loads(
            (
                ROOT / "integrations/grafana-lians-app/src/dashboards/lians-operations.json"
            ).read_text(encoding="utf-8")
        )
        variable = dashboard["templating"]["list"][0]
        self.assertEqual(variable["name"], "prometheus")
        self.assertEqual(variable["type"], "datasource")
        self.assertEqual(variable["query"], "prometheus")
        self.assertIn("${prometheus}", json.dumps(dashboard))


if __name__ == "__main__":
    unittest.main()
