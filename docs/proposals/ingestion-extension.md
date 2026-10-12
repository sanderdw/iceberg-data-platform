# Ingestion extension (proposed, 2026-10-07)

**Superseded (2026-10-12).** The extension was built as `extensions/ingestion` (Ingestion (dlt), core
0.9.0) on dlt's native Iceberg destination: dlt reads and writes, with its bookkeeping kept inside
the run. See its [README](https://github.com/sanderdw/iceberg-data-platform/tree/main/extensions/ingestion#readme) and
[DECISIONS.md](https://github.com/sanderdw/iceberg-data-platform/blob/main/extensions/ingestion/DECISIONS.md). The DuckDB readers, per-endpoint forwarders,
the other-catalog source and OLake below remain ideas. The rest of this page is kept as history.

Not implemented. This records how a team loads external data into the platform on a schedule, as a
third extension next to Conversational BI and dbt. It covers four kinds of source:
- other Apache Iceberg catalogs
- PostgreSQL and MariaDB databases
- REST APIs
- files in object storage or on SFTP

## Why

The dbt extension transforms tables that are already in the platform. Its runs have no internet:
each one sits on a private network with only the catalog and storage, so model SQL cannot send data
out or pull anything in. dbt seeds load a CSV committed to the project, which works once but not
for a source that changes. Getting data in is a separate job, with its own credentials and network
rules.

## Decision: a separate extension, `ingest`

- **Not inside dbt.** Giving dbt runs network access to serve some sources weakens the guarantee
  for every model. A separate extension keeps dbt closed, lets a team use either one alone, and
  matches the "loosely coupled" principle.
- **No core change.** Bridge contract 0.1 already has what it needs: user context, an automation
  principal per team and environment with `writer` access, short-lived `write` tokens, the network
  contract's role labels for isolated jobs, and the table-property conventions for the **Produced
  by** card.
- **Copy, not federation.** The extension copies data into the team's databases, so shares,
  semantic models and dbt work on it like on any other table. Reading another catalog in place is a
  platform question (see alternatives).
- **Two readers, one writer.**
  - **DuckDB** reads what it reads natively, under a permissive licence: Iceberg catalogs and
    PostgreSQL.
  - **dlt** reads the rest: MariaDB, REST APIs and files.
  - **The extension's own writer** commits every source to Iceberg.
- **Identity:** extension id `ingest`, label **Sources** in the user portal, port 3008, MCP client
  `ext-ingest-mcp`. Own Compose project `iceberg-ingest`, own version and tag `ingest-vX.Y.Z`, like
  the other two.

## Connections and sources

Two objects, so credentials and what to load are managed apart:

- A **connection** says where a system is and how to sign in. Team administrators create it, per
  environment. Its credentials are write-only (see [Credentials](#credentials)).
- A **source** says what to load from a connection into which table, how, and when. Writers
  create sources in development; in acceptance and production only team administrators do, which
  mirrors dbt's rule that only `main` builds there.

```yaml
# Connections (team administrators)
- name: crm
  kind: mariadb                          # postgres | mariadb | iceberg-rest | rest-api | files
  host: crm-db.example.com
  port: 3306
  database: crm
  tls: verify-full
  credentials: {user: …, password: …}    # write-only
- name: partner-lake
  kind: iceberg-rest
  uri: https://catalog.partner.example/api/catalog
  warehouse: shared
  storage_hosts: [s3.eu-west-1.amazonaws.com]
  credentials: {client_id: …, client_secret: …, oauth2_server_uri: …}
- name: cbs
  kind: rest-api
  preset: cbs-statline                   # fills in base URL, paging and the version check

# Sources (writers)
- name: crm-customers
  connection: crm
  table: customers                       # or a read-only query
  target: {database: db-…, namespace: [raw_crm], name: customers}
  mode: merge                            # replace | merge
  key: [id]
  cursor: updated_at                     # load only rows changed since the last run
  schedule: "0 5 * * *"                  # cron in UTC
- name: energiebalans
  connection: cbs
  resource: 83140NED
  target: {database: db-…, namespace: [raw_cbs], name: energiebalans}
  mode: replace
  schedule: "0 5 * * *"
```

The extension keeps each source's state: the last cursor value, snapshot id, file date or version.
It passes that to the next run as the starting point and stores the new value after a successful
commit. Runs themselves are stateless, and dlt's own state store is not used.

## Connectors

No user code runs: connectors are code in the pinned runner image, versioned with the extension.

| Kind | Reader | Sign-in | Skip or load less |
| --- | --- | --- | --- |
| `iceberg-rest` | DuckDB `iceberg` extension: `ATTACH … (TYPE iceberg, READ_ONLY)` | OAuth2 client credentials or a bearer token; vended credentials, or storage keys on the connection | Skip when the table's current snapshot id equals the last loaded one |
| `postgres` | DuckDB `postgres` extension: `ATTACH … (TYPE postgres, READ_ONLY)` | User and password, TLS | `cursor`: only rows above the last value, merged on `key` |
| `mariadb` | dlt `sql_database` with SQLAlchemy and PyMySQL, Arrow backend | User and password, TLS | As `postgres` |
| `rest-api` | dlt `rest_api` | None, API key, bearer token, HTTP basic or OAuth2 client credentials | An incremental request parameter, or a preset's own check |
| `files` | dlt `filesystem`: CSV, Parquet or JSONL from S3-compatible storage, Azure, GCS or SFTP | Storage keys, SAS token or SSH key | Only files modified since the last run |

**Other Iceberg catalogs.** One DuckDB session attaches the external catalog read-only and the
platform catalog with the run's token, the same way the platform's notebooks attach Polaris. A
source copies one table, or every table of a namespace, with `INSERT … SELECT` per table. The
source revision is the external snapshot id.

**PostgreSQL.** A source names a table or a read-only query, and DuckDB pushes the cursor filter
down to the database. A cursor (an `updated_at` or an increasing id) does not see deleted rows, so
such a source also gets a periodic full refresh (for example weekly). The connection's account
should be read-only at the source as well.

**MariaDB.** dlt reflects the table's columns and reads it in chunks into Arrow, with the same
cursor and full-refresh rules as PostgreSQL. PyMySQL is pure Python, so it is the slower path;
ConnectorX is a faster dlt backend for MySQL-protocol databases, which the spike measures. MySQL
speaks the same protocol and will probably work, but only MariaDB is tested and documented.

**REST APIs.** dlt's `rest_api` source is declarative:
- base URL and sign-in
- a paginator: next link, `Link` header, offset, page number or cursor
- the path to the records
- nested endpoints, such as `/customers/{id}/orders`
- an incremental parameter
- retries on rate limits

dlt splits nested JSON into child tables (`<table>__<child>`). **Presets** fill this in for known
APIs.

The first preset is **CBS StatLine** (OData v4 at `datasets.cbs.nl`). Table `83140NED`
(*Energiebalans*, yearly, CC BY 4.0) has 312,314 observations, served 100,000 per page through
`@odata.nextLink`. The preset loads the observations and each dimension's code list as
`<name>__<dimension>`. It skips the run when `Properties.Version` has not changed.

**Candidate for databases: OLake.** [OLake](https://github.com/datazip-inc/olake) (Apache-2.0, Go
with a Java Iceberg writer, since 2024) copies PostgreSQL, MySQL and other databases into Iceberg
through REST catalogs, Polaris included. It does full loads, incremental loads and change data
capture (PostgreSQL logical replication, MySQL binlog). Each source runs as a container that takes
its configuration and state as files, which fits the launcher.

If it passes the spike, it replaces both database readers, and CDC catches deleted rows without a
periodic full refresh. The spike decides on these points:
- **It writes Iceberg itself.** Table identity on a full refresh and the Produced-by properties need
  checking. DuckDB in notebooks and dbt must read the delete files it writes for CDC.
- **Sign-in.** It must work with a short-lived Bridge token and vended credentials.
- **MariaDB.** It is not listed; only MySQL is.
- **The image.** A Go binary plus a JVM is much heavier than the DuckDB runner.
- **Maturity.** It is young and maintained by one company.

**Files.** A source names a path pattern and a format. Each run loads files modified since the last
run (`merge`) or all matching files (`replace`).

**How dlt is used.**
- dlt reads and normalises the data, and writes Parquet to the run's `/work`.
- Its own destinations, Iceberg output included, are not used. They would put dlt's bookkeeping
  tables (`_dlt_loads`, `_dlt_pipeline_state`, `_dlt_version`) in team namespaces.
- After the first load, a dlt schema contract freezes the columns, so a changed schema fails the run
  with a clear message, as for the DuckDB readers.
- The writer drops `_dlt_load_id`, because the run id is in the table properties. It keeps
  `_dlt_id` and `_dlt_parent_id` where child tables exist, as their join keys.
- dlt's telemetry is switched off (`RUNTIME__DLTHUB_TELEMETRY=false`); runs could not reach it
  anyway.

## Runs

Runs reuse the dbt design:
- a launcher that is the only container with the Docker socket
- fixed inputs
- one pinned runner image `iceberg-ingest-runner` per run: read-only, no capabilities, user
  `10001`, memory and CPU limits

Each run gets:
- a private network with the catalog and storage under their contract aliases
- a short-lived `write` token of the team's automation principal
- the same time limit, derived from `POLARIS_TOKEN_LIFETIME`

**Writing.** The writer commits with DuckDB in one Iceberg transaction, the way dbt's
`replace_table` does: `replace` deletes and inserts, and `merge` deletes the keys it loads and
inserts them again. The table keeps its identity, so data shares and properties survive. A changed
schema fails the run with a clear message; `full_refresh` recreates the table. The runner uses the
same DuckDB version and extension wheels as the dbt runner, plus the `postgres` extension wheel.

**Metadata** follows the [table-property conventions](../../contracts/bridge/v1/table-properties.md):
- producer `ingest` and the connector version
- a deep link to the source in the extension
- run id and time
- `source-revision`: the snapshot id, the last cursor value, the newest file date or the API's
  version
- `node`: the source name

`comment` names the connection and, for open data, the publisher and licence, which CC BY requires.

Built-in quality checks put their result in `quality.status`:
- the source returned rows
- the row count did not drop more than a configured share

### Network: one forwarder per endpoint

Databases speak their own protocols, and their clients cannot use an HTTP proxy, so an HTTPS proxy
alone is not enough. For each endpoint a run needs, the launcher starts a forwarder: a small process
in a container from the same pinned image.

**How it connects.**
- The forwarder sits on the run's internal network under the endpoint's own host name as alias
  (`crm-db.example.com`), and on an egress network.
- The run resolves that name to the forwarder, which passes TCP through to the real host on the
  same port.
- TLS stays end to end. Host-name checks (HTTPS, database `verify-full`) keep working because the
  client still uses the real name. dbt runs use the same alias technique for the catalog and
  storage.

**What it may reach.**
- **Where endpoints come from:** the connection names them (database host and port, catalog host
  and declared storage hosts, API host, file storage host). Names that are not aliases do not
  resolve, so nothing else is reachable.
- **Platform allowlist:** the platform administrator sets `INGEST_ALLOWED_ENDPOINTS`, for example
  `*.cbs.nl:443,crm-db.example.com:3306`. A connection outside it is refused when saved and when
  run.
- **The forwarder resolves the name itself:**
  - Public addresses are allowed.
  - Private addresses only when the allowlist names that host exactly, without a wildcard, because
    company databases often sit on private networks.
  - Loopback, link-local (including cloud metadata at `169.254.169.254`) and the platform's own
    Docker networks are always refused.
- **When it runs:** it writes bytes per endpoint to the run log and stops with the run.

## Credentials

- **Storage:** stored encrypted in the extension's SQLite database, with AES-GCM and a key
  (`INGEST_SECRET_KEY`) that its setup script generates in its `.env`.
- **Write-only:** a team administrator sets or replaces them on the Sources page or through the REST
  API. No response ever returns them.
- **MCP tools can neither read nor set credentials.** An agent can draft a connection and test it
  once a person has entered the credentials, so a secret never passes through a chat.
- **In a run:** they are passed only as environment variables, never in a file that becomes an
  artifact, and are scrubbed from logs.
- **Rotation:** replace them on the connection; the next run uses the new ones.

## Working with dbt

A loaded table is an ordinary Iceberg table, so a dbt project declares it as a source. At first,
schedules are offset (load at 05:00 UTC, dbt at 05:30).

Proposed for dbt 0.2.0: a schedule that builds when a source changes. dbt checks the current
snapshot of its source tables every few minutes and starts a build after a new one. That needs no
event channel between extensions and works for any producer: the ingest extension, a notebook or a
Spark job.

## API, MCP and portal

As in dbt, every capability is a service method exposed as a REST operation and an MCP tool, with
an OpenAPI contract in `contracts/ingest-api/v1`. Credentials are the one exception: they are REST
and page only.

| Area | Tools |
| --- | --- |
| Context | `whoami`, `list_environments`, `enable_environment` (team admin), `disable_environment` |
| Connections | `list_connections`, `get_connection` (never credentials), `create_connection` and `update_connection` (team admin, without credentials), `delete_connection` (with `confirm_name`), `test_connection` (reachability and sign-in) |
| Explore | `browse_connection`: schemas, tables and columns of a database, namespaces and tables of a catalog, or files under a path; `preview_source`: the first rows and the inferred schema, without writing |
| Sources | `list_sources`, `get_source`, `create_source`, `update_source` (with `expected_revision`), `delete_source` (with `confirm_name`) |
| Runs | `run_source` (optional `full_refresh`), `list_runs`, `get_run`, `get_run_logs`, `cancel_run` |
| Schedules | `set_schedule`, `delete_schedule` |

The **Sources** page lists a team's connections and sources with their last run and next schedule.
A new source is one form (connection, table, resource or path, target, mode, schedule) with a
preview before saving.

A skill, `ingest-sources`, guides an agent from browsing a connection to a first load. It then
hands over to the `dbt-platform` skill for a staging model and to `semantic-model` for the mart.

## Software

| Part | Software | Licence |
| --- | --- | --- |
| API, MCP server and Sources page | Python 3.14, FastAPI, Uvicorn, the `mcp` SDK, joserfc, httpx, SQLite (standard library), plain JavaScript and CSS | MIT, BSD |
| Scheduler and credentials | croniter; `cryptography` for AES-GCM | MIT; Apache-2.0 or BSD |
| Launcher | Docker SDK for Python | Apache-2.0 |
| Runner: DuckDB readers and the writer | DuckDB 1.5.5 with the `iceberg`, `httpfs`, `avro` and `postgres` extension wheels; PyIceberg for table properties | MIT; Apache-2.0 |
| Runner: dlt readers | dlt, SQLAlchemy, PyMySQL, PyArrow; ConnectorX if the spike shows it is needed | Apache-2.0; MIT |
| Forwarder | Own code on the Python standard library, like dbt's catalog proxy | Apache-2.0 |

- **Already in the dbt extension:** everything except dlt and its readers, `cryptography` and the
  `postgres` extension.
- **The `postgres` extension** contains libpq (PostgreSQL License, permissive).
- **No copyleft code.** MariaDB goes through PyMySQL, not through DuckDB's `mysql` extension, which
  contains MariaDB Connector/C (LGPL-2.1).

## Principles

| Principle | How this fits |
| --- | --- |
| Clean UX | One form per source with a preview; a run that found nothing new says so |
| API first | The page is a client of the REST API, described by an OpenAPI contract |
| AI native | Agents browse, draft, test, preview and load through MCP with the user's identity, plus a skill; only credentials stay with a person |
| Open source | Apache-2.0, MIT and BSD building blocks; no copyleft library |
| Loosely coupled | Bridge contract 0.1 and standard Iceberg; dbt sees only tables |

## Alternatives considered

- **Network access for dbt runs.** Rejected; see above.
- **Reading other catalogs in place (no copy).** Polaris has experimental catalog federation. It
  would be a core feature with its own access and governance questions, so it is not part of this
  extension.
- **A scheduled notebook.** Notebooks run as the person who opened them. A schedule needs the team's
  service account and a record of each run.
- **Airbyte or Meltano.** They bring their own scheduler, database and workers, which is far more to
  run than one launcher and its forwarders.
- **DuckDB's `mysql` extension for MariaDB.** Faster, but it contains MariaDB Connector/C under
  LGPL-2.1.
- **Everything through dlt, PostgreSQL included.** One reader for all databases, but slower, and
  the usual PostgreSQL driver (psycopg) is LGPL too. DuckDB reads PostgreSQL natively and pushes
  filters down.
- **Own REST and file readers instead of dlt.** We would maintain paging, sign-in, retries, nested
  JSON and incremental loading ourselves, for every new API.
- **Change data capture** (PostgreSQL logical replication, MariaDB binlog) with Debezium and Kafka.
  It catches deletes, but needs a broker and runs all the time. It is a later option for sources
  where a cursor plus periodic full refresh is not enough.

## Open points

- **dlt's weight.** It brings about 30 dependencies, among them requests, sqlglot, pendulum and
  GitPython. The runner image grows and Dependabot has more to update. dlt is pinned like DuckDB and
  updated deliberately.
- **More presets.** dlt's verified sources (Salesforce, HubSpot, Stripe, GitHub, Google Sheets and
  more) are code that `dlt init` copies. Each one we offer is reviewed and built into the image as a
  preset, never installed at run time.
- **Plain append.** `append` with a cursor needs the data and the new cursor in one commit to avoid
  duplicates after a failure. Version 0.1 offers `replace` and `merge` only.
- **Object storage hosts.** Aliases are exact names. An external catalog or file source with
  virtual-hosted S3 buckets (`bucket.s3.region.amazonaws.com`) needs those hosts declared on the
  connection, or a path-style endpoint.
- **Default allowlist.** Empty (the administrator adds endpoints) or a short list of public open-data
  hosts such as `datasets.cbs.nl:443`.
- **Size.** Iceberg and PostgreSQL copies stream through DuckDB; dlt sources go through Parquet in
  `/work`, whose size limit bounds one load.
- **Kubernetes.** The launcher gets the same backend switch as proposed for dbt. Forwarders and
  host aliases map to a per-run Service with egress allowed by NetworkPolicy; this needs its own
  check.
- **Shared code with dbt.** Start with a copy of the launcher; extract a shared package once both
  are stable.

## Order of work

1. **A spike** with one source of each kind, all in Docker:
   - PostgreSQL and MariaDB containers
   - a second Polaris as the external catalog
   - CBS `83140NED` through the REST preset
   - CSV files in a second RustFS bucket

   It checks:
   - the forwarders, including host-name checks under TLS
   - the refusal of private and platform addresses
   - the skip on an unchanged version or snapshot
   - PyMySQL against ConnectorX on a large MariaDB table
   - OLake against the DuckDB and dlt readers on the same PostgreSQL and MariaDB tables: speed,
     deleted rows through CDC, Polaris with a Bridge token, and DuckDB reading the result
2. **The extension:** API, MCP, connections with credentials, schedules, the Sources page, an
   installer option and a release workflow like dbt's, released as `ingest-v0.1.0`.
3. **dbt 0.2.0:** build when a source changes.
