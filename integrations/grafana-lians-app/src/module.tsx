import React, { useMemo, useState } from 'react';
import { AppPlugin } from '@grafana/data';
import { Alert, Button, CodeEditor, Field, Input, Stack } from '@grafana/ui';

const Configuration = () => {
  const [liansEndpoint, setLiansEndpoint] = useState('https://api.lians.ai');
  const [grafanaEndpoint, setGrafanaEndpoint] = useState(
    'https://otlp-gateway-prod-us-central-0.grafana.net/otlp'
  );

  const alloy = useMemo(
    () => `// Environment
// LIANS_API_KEY=<write-scoped key>
// GRAFANA_CLOUD_INSTANCE_ID=<stack id>
// GRAFANA_CLOUD_API_KEY=<metrics-publish token>

otelcol.receiver.otlp "lians" {
  grpc {}
  http {}
  output {
    traces = [otelcol.processor.memory_limiter.lians.input]
  }
}

otelcol.processor.memory_limiter "lians" {
  check_interval = "1s"
  limit = "512MiB"
  spike_limit = "128MiB"
  output { traces = [otelcol.processor.batch.lians.input] }
}

otelcol.processor.batch "lians" {
  timeout = "2s"
  send_batch_size = 512
  output {
    traces = [
      otelcol.exporter.otlphttp.lians.input,
      otelcol.exporter.otlp.grafana_cloud.input,
    ]
  }
}

otelcol.exporter.otlphttp "lians" {
  client {
    endpoint = "${liansEndpoint.replace(/\/$/, '')}"
    headers = { "X-API-Key" = sys.env("LIANS_API_KEY") }
  }
  retry_on_failure { enabled = true }
  sending_queue { enabled = true, queue_size = 5000 }
}

otelcol.auth.basic "grafana_cloud" {
  username = sys.env("GRAFANA_CLOUD_INSTANCE_ID")
  password = sys.env("GRAFANA_CLOUD_API_KEY")
}

otelcol.exporter.otlp "grafana_cloud" {
  client {
    endpoint = "${grafanaEndpoint.replace(/\/$/, '')}"
    auth = otelcol.auth.basic.grafana_cloud.handler
  }
  retry_on_failure { enabled = true }
  sending_queue { enabled = true, queue_size = 5000 }
}`,
    [liansEndpoint, grafanaEndpoint]
  );

  return (
    <Stack direction="column" gap={3}>
      <Alert title="One trace stream, two connected views" severity="info">
        Grafana keeps operational telemetry. Lians receives the unsampled evidence stream,
        correlates GenAI spans into decisions, and links them back by trace ID.
      </Alert>
      <Stack direction="row" gap={2}>
        <Field label="Lians endpoint" description="Origin only; Alloy adds /v1/traces.">
          <Input
            width={48}
            value={liansEndpoint}
            onChange={(event) => setLiansEndpoint(event.currentTarget.value)}
          />
        </Field>
        <Field label="Grafana Cloud OTLP endpoint">
          <Input
            width={56}
            value={grafanaEndpoint}
            onChange={(event) => setGrafanaEndpoint(event.currentTarget.value)}
          />
        </Field>
      </Stack>
      <Alert title="Privacy default" severity="warning">
        Do not emit prompt, completion, memory, or tool-result content unless the customer
        explicitly opts in. Send IDs, hashes, versions, provenance, and capture status by default.
      </Alert>
      <CodeEditor value={alloy} language="text" readOnly height="560px" />
      <Stack direction="row" gap={1}>
        <Button
          icon="copy"
          onClick={() => navigator.clipboard.writeText(alloy)}
        >
          Copy Alloy configuration
        </Button>
        <Button
          variant="secondary"
          icon="external-link-alt"
          onClick={() => window.open(`${liansEndpoint.replace(/\/$/, '')}/health`, '_blank')}
        >
          Check Lians endpoint
        </Button>
      </Stack>
      <Alert title="Verification" severity="success">
        Send one GenAI span, then confirm that the Lians Operations dashboard shows an
        accepted span and a correlated decision. Add a Grafana data link using
        <code> trace_id </code> to open the corresponding Lians decision.
      </Alert>
    </Stack>
  );
};

export const plugin = new AppPlugin().addConfigPage({
  title: 'Connect Lians',
  body: Configuration,
  id: 'configuration',
});
