import React from 'react';
import { AppPlugin } from '@grafana/data';
import { Alert, CodeEditor, Stack } from '@grafana/ui';

const alloy = `// Partner application traces fan out unsampled to Grafana and Lians.
otelcol.receiver.otlp "integration" {
  grpc { endpoint = "0.0.0.0:4317" }
  http { endpoint = "0.0.0.0:4318" }
  output {
    traces = [otelcol.processor.batch.integration.input]
  }
}

otelcol.processor.batch "integration" {
  output {
    traces = [
      otelcol.exporter.otlp.grafana_cloud.input,
      otelcol.exporter.otlphttp.lians.input,
    ]
  }
}

// Point Lians' OTEL_EXPORTER_OTLP_ENDPOINT at :14317. Its own runtime spans
// must bypass the Lians exporter or /v1/traces instrumentation can recurse.
otelcol.receiver.otlp "lians_runtime" {
  grpc { endpoint = "0.0.0.0:14317" }
  http { endpoint = "0.0.0.0:14318" }
  output {
    traces = [otelcol.processor.batch.lians_runtime.input]
  }
}

otelcol.processor.batch "lians_runtime" {
  output {
    traces = [otelcol.exporter.otlp.grafana_cloud.input]
  }
}

otelcol.exporter.otlphttp "lians" {
  client {
    endpoint = sys.env("LIANS_OTLP_ENDPOINT")
    headers = { "X-API-Key" = sys.env("LIANS_API_KEY") }
  }
}

otelcol.auth.basic "grafana_cloud" {
  username = sys.env("GRAFANA_CLOUD_INSTANCE_ID")
  password = sys.env("GRAFANA_CLOUD_API_KEY")
}

otelcol.exporter.otlp "grafana_cloud" {
  client {
    endpoint = sys.env("GRAFANA_CLOUD_OTLP_ENDPOINT")
    auth = otelcol.auth.basic.grafana_cloud.handler
  }
}`;

const Configuration = () => (
  <Stack direction="column" gap={2}>
    <Alert title="Split Lians OTLP fan-out" severity="warning">
      Partner traces fan out to Grafana Cloud and Lians. Send Lians runtime spans
      through the separate Grafana-only receiver to prevent telemetry recursion.
    </Alert>
    <CodeEditor value={alloy} language="text" readOnly height="640px" />
  </Stack>
);

export const plugin = new AppPlugin().addConfigPage({
  title: 'Configuration',
  body: Configuration,
  id: 'configuration',
});
