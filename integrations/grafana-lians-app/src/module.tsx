import React from 'react';
import { AppPlugin } from '@grafana/data';
import { Alert, CodeEditor, Stack } from '@grafana/ui';

const alloy = `otelcol.receiver.otlp "default" {
  grpc {}
  http {}
  output {
    traces = [
      otelcol.exporter.otlphttp.lians.input,
      otelcol.exporter.otlp.grafana_cloud.input,
    ]
  }
}`;

const Configuration = () => (
  <Stack direction="column" gap={2}>
    <Alert title="Lians OTLP fan-out" severity="info">
      Configure Grafana Alloy to send the same unsampled trace stream to Grafana
      Cloud and the authenticated Lians OTLP/HTTP endpoint.
    </Alert>
    <CodeEditor value={alloy} language="text" readOnly height="360px" />
  </Stack>
);

export const plugin = new AppPlugin().addConfigPage({
  title: 'Configuration',
  body: Configuration,
  id: 'configuration',
});
