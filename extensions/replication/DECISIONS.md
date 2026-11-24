# Decisions for Replication (OLake)

Spike 2026-11-18 and build 2026-11-19, with OLake v1.2.0 (`olakego/source-postgres:v1.2.0`) against
Apache Polaris and RustFS of platform 0.9.11, PostgreSQL 17 as the source, and the notebook image's
DuckDB 2.0.0, PyIceberg 0.12.0 and Polars 2.1.0 as readers. The choices hold for Bridge contract 0.1.

## Ingestion (dlt) or Replication (OLake)

| Use | When |
| --- | --- |
| Ingestion (dlt) | REST APIs; CSV, JSONL or Parquet files; one-time or occasional loads of a database table |
| Replication (OLake) | Keeping an operational database in step: inserts, updates and deletes as they happen, large tables, frequent schedules |

dlt's `sql_database` source stays for one-time loads. It sees no deletes, and its PyIceberg merge
commits every 1,000 rows, which does not suit large tables or frequent runs.

## Why OLake

Compared on 2026-11-19 (licences read from the repositories):

| Tool | Licence | Runs as | Writes updates as | Fit |
| --- | --- | --- | --- | --- |
| **OLake** | Apache-2.0 | A command that ends | Positional deletes or deletion vectors | Chosen |
| Debezium Server + debezium-server-iceberg | Apache-2.0 | A long-running server | Equality deletes in upsert mode | Unreadable for PyIceberg and Polars |
| Apache Flink CDC 3.x | Apache-2.0 | A Flink cluster | Equality deletes | Too heavy, same reader problem |
| Apache SeaTunnel | Apache-2.0 | Its own engine | Equality deletes (not checked) | The earlier candidate in Ingestion's decisions; heavier |
| RisingWave | Apache-2.0 | A streaming database | Equality deletes in upsert mode | Too heavy |
| Supabase ETL | Apache-2.0 | PostgreSQL only | Into Supabase's own storage | No external catalog |
| Airbyte, Estuary, PeerDB, pg_mooncake, Redpanda Connect (CDC parts), Sling | ELv2, BSL, AGPL, BSL, paid licence, GPL | | | Licence |

OLake is the only permissive option that runs as a command that ends, which is what the launcher
needs, and writes no equality deletes. The extension uses only its command line, so another engine
would replace the run image, not the extension.

## Delete modes

| `update_type` | DuckDB | PyIceberg | Polars |
| --- | --- | --- | --- |
| `eq` (equality deletes, OLake's default) | correct | fails: "does not yet support equality deletes" | fails |
| `pos` (positional deletes, Iceberg v2) | correct | correct | correct |
| `dv` (deletion vectors, Iceberg v3) | correct | correct | correct |

`pos` is the default and `dv` an option; `eq` is never offered. OLake keeps deleted rows: a row
deleted in the source stays with `_op_type = 'd'`, so the current rows are `_op_type <> 'd'`. It adds
`_olake_id`, `_olake_timestamp`, `_op_type`, `_cdc_lsn` and `_cdc_timestamp`.

`_current` views were considered and dropped: PyIceberg (and so Polars) can load an Iceberg view's
definition but not run it.

### Partitioned tables need REPLICA IDENTITY FULL

With PostgreSQL's default replica identity, a delete event carries only the primary key. OLake then
puts the positional delete file in the partition of the delete event's (null) values, not in the
partition of the row it deletes. The Iceberg spec scopes positional deletes to their partition, so
DuckDB rightly ignores the file and keeps showing the deleted rows; PyIceberg matches by file path
and does not. Verified: 50 deletes on a table partitioned by `month(created_at)` stayed visible in
DuckDB; after `ALTER TABLE … REPLICA IDENTITY FULL` the next deletes landed in the right partition.
The setup SQL adds `REPLICA IDENTITY FULL` for every partitioned change-capture table, and the API
warns. To report upstream: OLake should place the delete in the partition of the row it replaces.

## The run

The run image is OLake's own (`FROM olakego/source-postgres`, so Dependabot proposes its updates),
with a small start-up program in Python's standard library (`runner/`):

1. writes OLake's `source.json`, `destination.json` and `selected_streams.json` from the checked job
   and removes the first two (password, token) when it ends;
2. runs `discover` and checks that every chosen table exists, then `sync` with the previous
   `state.json`;
3. reads the snapshots each table got in this run for the counts, and commits the Bridge
   table-property conventions (`producer = olake`, run id, `source-revision = LSN …`).

A source check is a run of kind `check`: OLake's `check` returns its prerequisites (wal_level,
replication privilege, slot, publication, wal_sender_timeout) with current and recommended values,
and `discover` the tables with their keys and cursor columns.

### Settings that matter

| Setting | Why |
| --- | --- |
| `TELEMETRY_DISABLED=true` | OLake otherwise looks up the run's public IP address and location and sends them to analytics.olake.io on every command |
| `ssl.server_ca` for `verify-ca` and `verify-full` | OLake refuses these modes without a CA. The source's own `caCertificate`, or else the image's system bundle (`/etc/ssl/certs/ca-certificates.crt`) for public CAs. Verified against a PostgreSQL with its own CA: connects with it, refuses without |
| `preserve_toast_values` | Without `REPLICA IDENTITY FULL`, an update leaves unchanged large (TOAST) values out of the change and OLake would write `__olake_unavailable_value__`; with this it reads them back from the row an upsert replaces (positional deletes and deletion vectors only, our modes). Verified: 20 rows of 12,800 characters, 5 titles updated, every body intact. OLake then allows only upserts, so the run turns it on only when it appends to none of the chosen tables (no primary key, or `appendOnly`); otherwise the setup SQL asks for `REPLICA IDENTITY FULL` on every change-capture table of the replication |
| `sink_jar_path=/home/olake-iceberg-java-writer.jar` | OLake looks for its Java writer relative to the working directory |
| `rest_auth_type=oauth2`, `token`, `rest_access_delegation=vended-credentials` | The run's write token from the Bridge; Polaris vends storage credentials per table |
| A catalog proxy on 127.0.0.1 (a copy of the dbt runner's) | OLake's writer uses the vended `s3.endpoint` over its own `s3_endpoint`; the proxy rewrites the public endpoint to the internal one, as the Bridge network contract allows |
| `OLAKE_INDEX_DB_DIR=/work/index`, `java.io.tmpdir=/work/tmp` | The root file system is read-only and /tmp is a small tmpfs |

### Time limit and resuming

A run must end before its catalog token expires (`POLARIS_TOKEN_LIFETIME`, one hour by default). The
start-up program stops OLake with SIGTERM 3 minutes before the run's limit. Verified:

- **During a first copy**: OLake records the chunks still to copy in its state. An 8,000,000-row
  copy stopped after 20 seconds with 2 chunks left; the next run copied the rest, and DuckDB and
  PyIceberg counted exactly 8,000,000 rows and as many distinct ids. The run is `incomplete`.
- **During change capture**: the position does not advance, so the next run reads those changes
  again. OLake's upserts make that safe.

OLake does not checkpoint the change log position during a run. A burst of changes that one run
cannot finish within its limit or its memory is read again in full by every run. Replaying 8,000,000
upserts after a bulk insert needed more than 4 GiB (killed) and finished in 11 minutes with 10 GiB.
The runner reports a killed OLake as "probably out of memory, see RUN_MEMORY". To raise upstream: a
periodic checkpoint of the LSN. A longer-lived token would also help large first copies.

### Costs per run

- **Row index**: positional deletes need the position of the row they replace. OLake keeps an index
  of `_olake_id` per table on local disk; a fresh run container rebuilds it by scanning the table.
  Cheap for small tables, a scan of the id column for large ones. A persistent volume per
  replication would avoid it.
- **Check table**: each sync writes one row to the table `test_olake.test_olake` in the target
  database, to check the destination. OLake has no switch to skip it.
- **Delete files**: every run with updates or deletes adds delete files; the Maintenance extension
  compacts them.

## Known OLake issues (v1.2.0, checked 2026-11-19)

| Issue | Status | Effect here |
| --- | --- | --- |
| [#1257](https://github.com/datazip-inc/olake-go/issues/1257) Positional delete goes to the incoming record's partition | Open, no fix; describes only updates that move a row between partitions | Also every delete of a partitioned table without `REPLICA IDENTITY FULL` (above) |
| [#801](https://github.com/datazip-inc/olake-go/issues/801) Large PostgreSQL transactions are processed in memory | Open feature request, no PR | A run killed for memory does not advance the position, so it repeats (Time limit and resuming). Not reported: a periodic checkpoint during a run |
| [#1286](https://github.com/datazip-inc/olake-go/issues/1286) A re-inserted primary key leaves the `d` row live | Open, fix in [#1303](https://github.com/datazip-inc/olake-go/pull/1303) | Two rows per key without the `_op_type <> 'd'` filter |
| [#1363](https://github.com/datazip-inc/olake-go/issues/1363) Updating a primary key leaves the old row | Open, no fix | The old row stays live, even with the filter |

The user documentation lists them under "Known issues", with what to do.

## Sources and network

Operational databases usually live on private networks. The gateway resolves the source host before
every run and checks every address: loopback, link-local, multicast and reserved addresses are always
refused, private addresses only pass inside `REP_ALLOWED_SOURCE_NETWORKS`, and
`REP_ALLOW_PRIVATE_SOURCES=true` lifts the rules for laptop demos. The launcher pins the checked
address in the run (`extra_hosts` on Docker, `hostAliases` on Kubernetes), so OLake (Go and Java, where
no in-process guard reaches) never resolves the name itself, and DNS cannot point it elsewhere
afterwards. The run's networks hold only the catalog and storage, plus its own egress network.

Deleting a replication does not drop its replication slot: the run has no PostgreSQL client and the
account may not be allowed to. The API and the page give the database owner
`SELECT pg_drop_replication_slot('…')`, because a forgotten slot keeps the source's change log.

## Licences

OLake (olake-go, its Java writer and olake-ui) is Apache-2.0. Its direct Go dependencies are
Apache-2.0, MIT, BSD and two MPL-2.0 modules (`go-sql-driver/mysql`, `hashicorp/go-multierror`,
unmodified); its Java writer's are Apache-2.0, BSD-2 (PostgreSQL JDBC) and EPL for some Jersey,
Jakarta and Eclipse Collections libraries. A Trivy scan of `olakego/source-postgres:v1.2.0` found
GPL and LGPL only in Ubuntu 24.04's base packages and the Temurin JRE (GPL-2.0 with the Classpath
exception), as in any Ubuntu or Debian image. DB2 is not offered: OLake's DB2 driver links IBM's
`clidriver`, which has its own redistribution terms.
