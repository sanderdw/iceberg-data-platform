{{- define "dbt.tag" -}}{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}
{{- define "dbt.image" -}}{{ .root.Values.image.registry }}/{{ index .root.Values.images .name }}:{{ include "dbt.tag" .root }}{{- end -}}
{{- define "dbt.runsNamespace" -}}{{ .Release.Namespace }}-dbt-runs{{- end -}}

{{- define "dbt.labels" -}}
app.kubernetes.io/name: iceberg-dbt
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
io.iceberg-platform.extension: dbt
{{- end -}}

{{- define "dbt.selector" -}}
app.kubernetes.io/name: iceberg-dbt
app.kubernetes.io/component: dbt
{{- end -}}

{{/* The extension's browser host, from EXTENSION_ORIGIN in the platform's handshake Secret. */}}
{{- define "dbt.host" -}}
{{- $handshake := lookup "v1" "Secret" .Release.Namespace .Values.bridgeSecret -}}
{{- $origin := "" -}}
{{- if $handshake }}{{ $origin = index $handshake.data "EXTENSION_ORIGIN" | b64dec }}{{ end -}}
{{- .Values.ingress.host | default ($origin | trimPrefix "https://" | trimPrefix "http://" | trimSuffix "/") -}}
{{- end -}}

{{- define "dbt.pullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "dbt.restricted" -}}
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
