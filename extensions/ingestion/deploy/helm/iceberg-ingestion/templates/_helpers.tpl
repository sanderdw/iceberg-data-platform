{{- define "ing.tag" -}}{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}
{{- define "ing.image" -}}{{ .root.Values.image.registry }}/{{ index .root.Values.images .name }}:{{ include "ing.tag" .root }}{{- end -}}
{{- define "ing.runsNamespace" -}}{{ .Release.Namespace }}-ingestion-runs{{- end -}}

{{- define "ing.labels" -}}
app.kubernetes.io/name: iceberg-ingestion
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
io.iceberg-platform.extension: ingestion
{{- end -}}

{{- define "ing.selector" -}}
app.kubernetes.io/name: iceberg-ingestion
app.kubernetes.io/component: ingestion
{{- end -}}

{{/* The extension's browser host, from EXTENSION_ORIGIN in the platform's handshake Secret. */}}
{{- define "ing.host" -}}
{{- $handshake := lookup "v1" "Secret" .Release.Namespace .Values.bridgeSecret -}}
{{- $origin := "" -}}
{{- if $handshake }}{{ $origin = index $handshake.data "EXTENSION_ORIGIN" | b64dec }}{{ end -}}
{{- .Values.ingress.host | default ($origin | trimPrefix "https://" | trimPrefix "http://" | trimSuffix "/") -}}
{{- end -}}

{{- define "ing.pullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "ing.restricted" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  capabilities:
    drop: [ALL]
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{/* OpenTelemetry export (__main__.py); none without an endpoint. */}}
{{- define "ing.otelEnv" -}}
{{- if .Values.otel.endpoint -}}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
{{- if .Values.otel.headersSecret }}
- name: OTEL_EXPORTER_OTLP_HEADERS
  valueFrom:
    secretKeyRef: {name: {{ .Values.otel.headersSecret }}, key: OTEL_EXPORTER_OTLP_HEADERS}
{{- end }}
{{- if .Values.otel.resourceAttributes }}
- name: OTEL_RESOURCE_ATTRIBUTES
  value: {{ .Values.otel.resourceAttributes | quote }}
{{- end }}
{{- end }}
{{- end -}}
