# Send logs and traces to OpenTelemetry

The platform's own services and Polaris can send their logs and traces to any backend that speaks OTLP: an OpenTelemetry Collector, Grafana with Loki and Tempo, Elastic, Pydantic Logfire, Honeycomb or Datadog. The platform does not ship a backend. You point it at the one you already run.

Export is off until you set an endpoint. The container logs (`docker compose logs`, `kubectl logs`) stay as they are, also with export on.

## Turn it on

### Compose

Add the endpoint, and the header your backend needs for its API key, to `.env` in the installation directory:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp.example.com
OTEL_EXPORTER_OTLP_HEADERS='Authorization=<your API key>'
OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=production
# Polaris's own traces too.
POLARIS_OTEL_DISABLED=false
```

Then restart the platform:

```bash
docker compose up -d --wait
docker compose -f compose.users.yaml up -d --wait users
```

The endpoint is the base address of an OTLP/HTTP receiver, usually port 4318. Each service appends `/v1/logs` and `/v1/traces` itself. OTLP over gRPC is not supported.

For an extension, add the same lines without `POLARIS_OTEL_DISABLED` to its own `.env`, such as `dbt/.env` in the installation directory. Then run `docker compose up -d --wait` in that folder.

### Kubernetes

Put the header in a Secret, then set the endpoint with `helm upgrade ... --reset-then-reuse-values`:

```bash
kubectl -n iceberg create secret generic otel \
  --from-literal=OTEL_EXPORTER_OTLP_HEADERS='Authorization=<your API key>'
helm upgrade iceberg oci://ghcr.io/sanderdw/charts/iceberg-data-platform -n iceberg --reset-then-reuse-values \
  --set otel.endpoint=http://otel-collector.observability:4318 --set otel.headersSecret=otel
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `otel.endpoint` | (none) | The OTLP/HTTP receiver. Without it, nothing is exported |
| `otel.headersSecret` | (none) | A Secret with the key `OTEL_EXPORTER_OTLP_HEADERS`, for a backend that needs an API key |
| `otel.resourceAttributes` | (none) | Added to every record, such as `deployment.environment.name=production` |
| `otel.polaris` | `true` | Polaris's own traces too |

The extension charts have the same `otel.endpoint`, `otel.headersSecret` and `otel.resourceAttributes`.

## What each service sends

| `service.name` | Logs | Traces |
| --- | --- | --- |
| `iceberg-portal` | The administration portal's records | Requests to its API, and its calls to Polaris, Keycloak and RustFS |
| `iceberg-users` | The user portal's records | The same. Notebook traffic through the portal is left out, including the access checks it makes |
| `iceberg-bridge` | The [audit trail](#audit-events) as events | Extension requests and the Bridge's calls to Polaris and Keycloak |
| `iceberg-monitor` | Collection failures | None: it polls every service, and those polls would be most of the traces |
| `iceberg-polaris` | None | Catalog requests, joined to the trace of the portal or extension that made them |
| `iceberg-conversationalbi` | The extension's records | Its API and MCP requests, and each question as an agent run: every LLM call with its model, tokens and messages, and every tool call with its arguments and result |
| `iceberg-dbt`, `iceberg-ingestion`, `iceberg-openmetadata` | The extension's records | Its API and MCP requests and its outgoing HTTP calls, such as to the Bridge |

Health checks are not traced. The records are the same as on stderr: our own loggers from INFO, libraries from WARNING. A trace links a request in the portal to the Polaris calls it made and to the records logged along the way.

Not covered:

- **Queries.** DuckDB and PyIceberg run in notebooks, runs and your own tools, which talk to Polaris and storage directly. The platform never sees the SQL. Polaris traces show which tables were loaded.
- **dbt and ingestion runs.** Their logs stay files that you read in the extension or with `get_run_logs`. The launchers and runs sit on internal networks without a route to your backend.
- **Sign-ins.** Keycloak has its own event log.
- **The CopilotKit runtime** of Conversational BI.

## Audit events

Every Bridge audit record is a log event named `iceberg.bridge.<event>`, with its fields as attributes. Filter on them in your backend instead of parsing the message.

| Event | Attributes |
| --- | --- |
| `automation_principal.created`, `automation_principal.exists` | `iceberg.automation_principal.id`, `iceberg.extension`, `iceberg.team`, `iceberg.environment`, `user.id`, `user.name` |
| `automation_principal.revoked` | `iceberg.automation_principal.id`, `iceberg.extension`, `iceberg.team`, `user.id`, `user.name` (both absent when the extension revoked it) |
| `automation_token.issued` | `iceberg.automation_principal.id`, `iceberg.extension`, `iceberg.access`, `iceberg.purpose` |
| `listing.read` | `iceberg.share.id`, `iceberg.extension` |

`user.id` is the platform user (`portal-…`), `user.name` the name they sign in with.

## What leaves the platform

Your backend receives:

- the log records, including the stack traces of unexpected errors;
- per request: the method, route, status, duration, URL path, client address and user agent. Paths hold IDs of teams, databases and principals;
- from Conversational BI: what its LLM provider receives, so the questions, model names and descriptions, the compiled SQL and up to `BI_LLM_ROWS` rows per result, plus the answers. Logfire and other backends with AI views show them as agent runs.

Apart from that, it never receives request or response bodies, headers, tokens or secrets. The sign-in callback, which carries a one-time code in its URL, is not traced. Treat the backend as you treat the container logs: whoever can read it sees who did what.

## In production

Send to an OpenTelemetry Collector next to the platform rather than straight to a SaaS backend. A service drops a batch it cannot deliver within 10 seconds (`OTEL_EXPORTER_OTLP_TIMEOUT`). A Collector buffers and retries, filters and samples, and holds the backend's API key in one place. A minimal configuration:

```yaml
receivers:
  otlp:
    protocols:
      http: {endpoint: 0.0.0.0:4318}
processors:
  batch: {}
exporters:
  otlphttp:
    endpoint: https://otlp.example.com
    headers: {Authorization: "${env:BACKEND_API_KEY}"}
service:
  pipelines:
    logs: {receivers: [otlp], processors: [batch], exporters: [otlphttp]}
    traces: {receivers: [otlp], processors: [batch], exporters: [otlphttp]}
```

Any other standard `OTEL_*` variable reaches the Python services as well, such as `OTEL_EXPORTER_OTLP_COMPRESSION=gzip`, `OTEL_TRACES_SAMPLER=parentbased_traceidratio` with `OTEL_TRACES_SAMPLER_ARG=0.1`, or `OTEL_SDK_DISABLED=true` to switch export off again. On Compose, add it to a service's `environment` first.

## Try it locally

Grafana's all-in-one image runs a Collector, Loki, Tempo and Grafana on the platform's network:

```bash
docker run -d --name otel-lgtm --network iceberg-platform_default -p 127.0.0.1:3030:3000 grafana/otel-lgtm:0.36.0
```

Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-lgtm:4318` and `POLARIS_OTEL_DISABLED=false` in `.env`, then restart the platform. Open Grafana at http://localhost:3030 (`admin`/`admin`). Logs are under **Explore › Loki**, traces under **Explore › Tempo**. Remove it with `docker rm -f otel-lgtm`.
