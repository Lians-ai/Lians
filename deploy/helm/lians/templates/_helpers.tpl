{{/* Chart and workload names. */}}
{{- define "lians.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "lians.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name (include "lians.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{- define "lians.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "lians.labels" -}}
helm.sh/chart: {{ include "lians.chart" . }}
app.kubernetes.io/name: {{ include "lians.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: lians
{{- end }}

{{- define "lians.selectorLabels" -}}
app.kubernetes.io/name: {{ include "lians.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: api
{{- end }}

{{- define "lians.otelSelectorLabels" -}}
app.kubernetes.io/name: {{ include "lians.name" . }}-otel-collector
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: recorder-gateway
{{- end }}

{{- define "lians.otelLabels" -}}
helm.sh/chart: {{ include "lians.chart" . }}
app.kubernetes.io/name: {{ include "lians.name" . }}-otel-collector
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: lians
app.kubernetes.io/component: recorder-gateway
{{- end }}

{{- define "lians.image" -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest }}
{{- end }}

{{- define "lians.otelImage" -}}
{{- printf "%s@%s" .Values.otelCollector.image.repository .Values.otelCollector.image.digest }}
{{- end }}

{{- define "lians.backupImage" -}}
{{- printf "%s@%s" .Values.backup.image.repository .Values.backup.image.digest }}
{{- end }}

{{- define "lians.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "lians.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "lians.backupServiceAccountName" -}}
{{- if .Values.backup.serviceAccount.create }}
{{- default (printf "%s-backup" (include "lians.fullname" .)) .Values.backup.serviceAccount.name }}
{{- else }}
{{- required "backup.serviceAccount.name is required when backup.serviceAccount.create=false" .Values.backup.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "lians.monitoringNamespace" -}}
{{- default .Release.Namespace .Values.monitoring.serviceMonitor.namespace }}
{{- end }}

{{- define "lians.rulesNamespace" -}}
{{- default .Release.Namespace .Values.monitoring.prometheusRule.namespace }}
{{- end }}

{{- define "lians.matchLabels" -}}
matchLabels:
{{- toYaml . | nindent 2 }}
{{- end }}
