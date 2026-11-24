# Decisions

## A lightweight extension, not a table-management service (0.0.1, 2026-11-18)

Apache Amoro and its fork OLake-Fusion are full table-management services: a Java management service,
Spark or Flink optimizers, their own UI and sign-in, and one catalog credential that writes every table.
That fits neither a platform that runs on a laptop nor its team ownership: every team enables an
extension per environment, and its runs act as that team's automation principal only. Maintenance is
therefore an extension like Ingestion and dbt: policies in its own store, runs in short-lived containers
with the team's tokens, and no service that holds a catalog-wide credential.

## The runner is Go on Apache iceberg-go (0.0.1, 2026-11-18)

The gateway stays Python like every other extension. The runner is a separate Go binary because of which
Iceberg library can do the work:

| Operation | PyIceberg 0.12 | iceberg-go `main` (pinned) | iceberg-rust 0.11 |
| --- | --- | --- | --- |
| Expire snapshots, per-ref retention | metadata only, no file cleanup | yes | yes |
| Orphan cleanup | no | yes (`table/maintenance`) | no |
| Rewrite data files (compaction), `REPLACE` commits, delete files applied | no (apache/iceberg-python#1092, #3131 open) | yes, with partial progress | no (apache/iceberg-rust#3046 open) |
| Rewrite manifests | no | yes | no (apache/iceberg-rust#2543 open) |
| Remove dangling deletes, rewrite position deletes | no | yes, on `main` only | no |
| Equality deletes | refuses them | reads, applies and removes dead ones | partial |

The runner pins iceberg-go to commit `e7a4d96` of 2026-11-18 on `main` (pseudo-version
`v0.7.1-0.20261118175253-e7a4d96b505d`): dangling-delete removal is not in a release yet. That is the
risk this beta accepts: `main` moves fast. Moving the pin is a deliberate change with the Go tests and the
spike below. The JSON job and result between gateway and runner is the only boundary, so the runner can
move to another library without changing the gateway.

## How a run applies a policy (0.0.1)

Per table, in this order: compaction, dangling deletes, manifests, orphan files, snapshot expiry.

- Expiry never deletes files at commit (`WithPostCommit(false)`): the files expired snapshots leave behind
  become orphans. Orphan cleanup runs **before** expiry, so what one run expires stays until the next run:
  the schedule's interval is the grace period, as `nonCurrentDays` is in S3 Tables. Orphan cleanup never
  removes files younger than a day, so writes in progress are safe.
- Branch and tag heads, each ref's `max-ref-age-ms`, `max-snapshot-age-ms` and `min-snapshots-to-keep`,
  and the table properties `history.expire.*` override the policy, as Iceberg defines. S3 Tables instead
  fails a table with its own branches, tags or retention properties.
- `gc.enabled=false` skips expiry and orphan cleanup.
- Compaction bin-packs per partition, and sorts when the table has a sort order (S3 Tables' `auto`). Its
  input per table and run is capped (`maxInputMbPerRun`): a backlog is worked off over several runs, and
  the runner's memory stays bounded. It commits `replace` snapshots stamped with the run id.
- A table whose commit conflicts with a writer fails on its own; the others continue and the run is
  `partial`. The next run tries again.
- Health checks and dry runs get a `read` catalog token; only maintenance gets `write`.

## The spike (0.0.1, 2026-11-18)

Against Apache Polaris 1.8.0 and RustFS 1.0.4, with a catalog configured as the platform creates them
(`endpoint` for hosts, `endpointInternal` for Polaris, path-style access, vended credentials) and the
runner reaching storage through the internal endpoint:

- Health check and dry run of two tables with 12 snapshots and 12 small files each, one partitioned by day.
- Compaction: 12 files into 1, and into 2 for the table partitioned by day; 1,200 rows and their ids
  intact; a `replace` snapshot with the run id; time travel to the first snapshot still worked.
- Manifest rewrite: 12 manifests into 1. iceberg-go's `RewriteManifests` takes no snapshot properties, so
  these snapshots carry no run id.
- Expiry without the policy's minimum age: 11 of 13 snapshots expired; the head and the tagged snapshot
  stayed. Orphan cleanup then listed and deleted the 30 freed files with the vended credentials. The
  current snapshot and the tag both still read (1,200 and 300 rows).

Not covered yet: tables with position or equality deletes and deletion vectors (dangling-delete removal),
sort compaction, a team's automation principal instead of the catalog administrator, and the platform's
Bridge and Keycloak end to end (the integration job of `maintenance-ci.yml`).

## Runs on Kubernetes (0.0.1)

The chart `deploy/helm/iceberg-maintenance` follows Ingestion's: the gateway and the launcher are two
containers of one pod sharing the artifacts volume, and the launcher (`LAUNCHER_BACKEND=kubernetes`)
creates one Job per run in the namespace `<namespace>-maintenance-runs`, which holds nothing but runs and
is labelled `io.iceberg-platform.isolated`. The run pod has the Docker container's isolation (user 10001,
read-only root, no capabilities, no service account token), and the catalog token and a one-run ticket
live in a Secret owned by the Job, never in its spec.

There is no `docker cp` on Kubernetes. The Go runner switches to remote mode when `RUN_URL` is set
(`runner/remote.go`, standard library only): it fetches its staged work directory from the launcher's
`GET /jobs/{id}/input`, runs as on Docker, and always returns `out/` with `PUT /jobs/{id}/output`, both
with the ticket; each works once. A failed fetch still reports a failed result. Unlike Ingestion's, the
runs' NetworkPolicy has no internet egress at all: DNS, the catalog, storage and the launcher only.

## The API follows the platform's conventions, with three choices of its own (2026-11-25)

The REST API and the MCP tools use the same rules as the other extensions: one run shape and status
vocabulary, lists as objects, one environment shape, RFC 3339 times, one error body. Three things are
Maintenance's own:

- **A schedule runs at most once an hour.** The other extensions allow every five minutes. A
  maintenance run rewrites files and expires snapshots, and the time between two runs is the grace
  period before orphan cleanup deletes what the previous run expired (see "How a run applies a
  policy"). A shorter interval adds commits that conflict with writers and gains nothing. A cron that
  runs more often is refused with `invalid_cron`.
- **The schedule is set on its own, and stored on the policy.** `PUT` and `DELETE
  /databases/{database}/schedule` (`set_schedule`, `delete_schedule`), as a pipeline has one schedule in
  Ingestion. A schedule applies the policy, so it needs one (`policy_required`), and deleting the policy
  removes it. Setting it does not change the policy's revision: someone changing when maintenance runs
  does not conflict with someone editing what it does. It has no `createdBy` or `createdAt`, because
  the policy row has no columns for them.
- **Two tools start a run.** `POST /databases/{database}/runs` keeps `mode`. For agents it is
  `inspect_database` (`inspect` or `dry-run`, which read with a `read` token) and `run_maintenance`
  (always `maintain`). Only the second is marked destructive, so an agent's client asks the user before
  maintaining and not before every health check. It is the one run tool on the platform that is
  destructive: it expires snapshots and deletes files.

`partial` keeps its meaning here: some tables failed and the others were maintained. `incomplete` is in
the shared vocabulary and Maintenance never reports it.

The table health inside `health` (`oldestSnapshotMs`, `currentSnapshotMs`) and the actions inside
`results` are what the runner wrote and stay as it wrote them; its snapshot times are Iceberg's own
milliseconds.

## Not in 0.0.1

- Record expiration (row TTL, as S3 Tables offers): it deletes data, so it needs its own opt-in per table.
- Table and partition statistics: DuckDB and PyIceberg do not use them.
- A pending-deletion list with its own grace period, independent of the schedule's interval.
