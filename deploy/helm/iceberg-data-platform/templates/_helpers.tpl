{{/* Browser origin of a host key (admin, users, auth, catalog, s3). */}}
{{- define "idp.origin" -}}
{{- $root := index . 0 -}}{{- $key := index . 1 -}}
{{- $explicit := index $root.Values.origins $key -}}
{{- if $explicit -}}{{ trimSuffix "/" $explicit }}
{{- else -}}
{{- if not $root.Values.baseDomain -}}{{ fail "Set baseDomain, or origins for every host" }}{{- end -}}
https://{{ index $root.Values.hosts $key }}.{{ $root.Values.baseDomain }}
{{- end -}}
{{- end -}}

{{- define "idp.hostname" -}}
{{- include "idp.origin" . | trimPrefix "https://" | trimPrefix "http://" | splitList ":" | first -}}
{{- end -}}

{{- define "idp.issuer" -}}{{ include "idp.origin" (list . "auth") }}/realms/iceberg{{- end -}}

{{- define "idp.tag" -}}{{ .Values.image.tag | default .Chart.AppVersion }}{{- end -}}

{{/* A platform image: registry/name:tag. */}}
{{- define "idp.image" -}}
{{- $root := index . 0 -}}{{ $root.Values.image.registry }}/{{ index $root.Values.images (index . 1) }}:{{ include "idp.tag" $root }}
{{- end -}}

{{- define "idp.secretName" -}}{{ .Values.existingSecret | default "iceberg-platform" }}{{- end -}}

{{- define "idp.notebooksNamespace" -}}{{ .Release.Namespace }}-notebooks{{- end -}}

{{- define "idp.labels" -}}
app.kubernetes.io/name: iceberg-data-platform
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Labels of one component; its selector uses the last two. */}}
{{- define "idp.component" -}}
{{- $root := index . 0 -}}
{{ include "idp.labels" $root }}
app.kubernetes.io/part-of: iceberg-data-platform
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{- define "idp.selector" -}}
app.kubernetes.io/part-of: iceberg-data-platform
app.kubernetes.io/component: {{ . }}
{{- end -}}

{{/* An environment variable from the platform Secret. */}}
{{- define "idp.secretEnv" -}}
{{- $root := index . 0 -}}
- name: {{ index . 1 }}
  valueFrom:
    secretKeyRef:
      name: {{ include "idp.secretName" $root }}
      key: {{ if gt (len .) 2 }}{{ index . 2 }}{{ else }}{{ index . 1 }}{{ end }}
{{- end -}}

{{- define "idp.pullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* PLATFORM_EXTENSIONS: id=origin,... */}}
{{- define "idp.extensions" -}}
{{- $root := . -}}{{- $pairs := list -}}
{{- range $id, $ext := .Values.extensions -}}
{{- $origin := $ext.origin | default (printf "https://%s.%s" $ext.host $root.Values.baseDomain) -}}
{{- $pairs = append $pairs (printf "%s=%s" $id (trimSuffix "/" $origin)) -}}
{{- end -}}
{{ join "," $pairs }}
{{- end -}}

{{- define "idp.listingExtensions" -}}
{{- $ids := list -}}
{{- range $id, $ext := .Values.extensions }}{{ if $ext.listing }}{{ $ids = append $ids $id }}{{ end }}{{ end -}}
{{ join "," $ids }}
{{- end -}}

{{/* The environment every portal process shares with compose.yaml. */}}
{{- define "idp.platformEnv" -}}
- name: OIDC_ISSUER
  value: {{ include "idp.issuer" . | quote }}
- name: OIDC_INTERNAL_ISSUER
  value: http://keycloak:8080/realms/iceberg
- name: POLARIS_PUBLIC_URL
  value: {{ include "idp.origin" (list . "catalog") | quote }}
- name: POLARIS_CLIENT_ID
  value: {{ .Values.polarisClientId | quote }}
{{ include "idp.secretEnv" (list . "POLARIS_CLIENT_SECRET") }}
- name: S3_ENDPOINT
  value: {{ include "idp.origin" (list . "s3") | quote }}
- name: S3_INTERNAL_ENDPOINT
  value: http://rustfs:9000
- name: AWS_REGION
  value: {{ .Values.awsRegion | quote }}
- name: PLATFORM_EXTENSIONS
  value: {{ include "idp.extensions" . | quote }}
- name: PLATFORM_LISTING_EXTENSIONS
  value: {{ include "idp.listingExtensions" . | quote }}
{{- end -}}

{{/* OpenTelemetry export of the platform's own services (server/entrypoints.py); none without an endpoint. */}}
{{- define "idp.otelEnv" -}}
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

{{/* The same for Polaris, through Quarkus's own settings. */}}
{{- define "idp.polarisOtelEnv" -}}
{{- if and .Values.otel.endpoint .Values.otel.polaris -}}
- {name: QUARKUS_OTEL_SDK_DISABLED, value: "false"}
- {name: QUARKUS_OTEL_SERVICE_NAME, value: iceberg-polaris}
- name: QUARKUS_OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ .Values.otel.endpoint | quote }}
- {name: QUARKUS_OTEL_EXPORTER_OTLP_PROTOCOL, value: http/protobuf}
{{- if .Values.otel.headersSecret }}
- name: QUARKUS_OTEL_EXPORTER_OTLP_HEADERS
  valueFrom:
    secretKeyRef: {name: {{ .Values.otel.headersSecret }}, key: OTEL_EXPORTER_OTLP_HEADERS}
{{- end }}
{{- if .Values.otel.resourceAttributes }}
- name: QUARKUS_OTEL_RESOURCE_ATTRIBUTES
  value: {{ .Values.otel.resourceAttributes | quote }}
{{- end }}
{{- else -}}
- {name: QUARKUS_OTEL_SDK_DISABLED, value: "true"}
{{- end }}
{{- end -}}

{{- define "idp.rustfsAdminEnv" -}}
- name: S3_ADMIN_ENDPOINT
  value: http://rustfs:9000
- name: RUSTFS_ACCESS_KEY
  value: {{ .Values.rustfsAccessKey | quote }}
{{ include "idp.secretEnv" (list . "RUSTFS_SECRET_KEY") }}
{{- end -}}

{{/* Container hardening for the platform's own images (uid 10001). */}}
{{- define "idp.restricted" -}}
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

{{/* Readiness and liveness from an HTTP health path. */}}
{{- define "idp.probes" -}}
readinessProbe:
  httpGet: {path: {{ index . 0 }}, port: {{ index . 1 }}}
  periodSeconds: 10
  failureThreshold: 3
livenessProbe:
  httpGet: {path: {{ index . 0 }}, port: {{ index . 1 }}}
  periodSeconds: 20
  failureThreshold: 6
  initialDelaySeconds: {{ if gt (len .) 2 }}{{ index . 2 }}{{ else }}20{{ end }}
{{- end -}}

{{/* Postgres: the bundled pod, or postgres.external. */}}
{{- define "idp.pgExternal" -}}
{{- with .Values.postgres.external }}{{ if .enabled }}{{ if not (and .host .existingSecret) }}{{ fail "postgres.external needs host and existingSecret" }}{{ end }}true{{ end }}{{ end -}}
{{- end -}}

{{- define "idp.pgHost" -}}{{ if include "idp.pgExternal" . }}{{ .Values.postgres.external.host }}{{ else }}postgres{{ end }}{{- end -}}
{{- define "idp.pgPort" -}}{{ if include "idp.pgExternal" . }}{{ .Values.postgres.external.port }}{{ else }}5432{{ end }}{{- end -}}

{{/* The database and user of "polaris" or "keycloak". */}}
{{- define "idp.pgDatabase" -}}{{ if include "idp.pgExternal" (index . 0) }}{{ (index (index . 0).Values.postgres.external (index . 1)).database }}{{ else }}{{ index . 1 }}{{ end }}{{- end -}}
{{- define "idp.pgUser" -}}{{ if include "idp.pgExternal" (index . 0) }}{{ (index (index . 0).Values.postgres.external (index . 1)).username }}{{ else }}{{ index . 1 }}{{ end }}{{- end -}}

{{/* The password of "polaris" or "keycloak" as an environment variable. */}}
{{- define "idp.pgPasswordEnv" -}}
{{- $root := index . 0 -}}{{- $which := index . 2 -}}
- name: {{ index . 1 }}
  valueFrom:
    secretKeyRef:
{{- if include "idp.pgExternal" $root }}
      name: {{ $root.Values.postgres.external.existingSecret }}
      key: {{ ternary "POLARIS_DB_PASSWORD" "KEYCLOAK_DB_PASSWORD" (eq $which "polaris") }}
{{- else }}
      name: {{ include "idp.secretName" $root }}
      key: {{ ternary "POSTGRES_PASSWORD" "KEYCLOAK_DB_PASSWORD" (eq $which "polaris") }}
{{- end }}
{{- end -}}

{{- define "idp.pgSslmode" -}}{{ if include "idp.pgExternal" . }}{{ .Values.postgres.external.sslmode }}{{ else }}disable{{ end }}{{- end -}}
