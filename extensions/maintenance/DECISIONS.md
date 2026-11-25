# Decisions

## A lightweight extension, not a table-management service (0.0.1, 2026-11-18)

Apache Amoro and its fork OLake-Fusion are full table-management services: a Java management service,
Spark or Flink optimizers, their own UI and sign-in, and one catalog credential that writes every table.
That fits neither a platform that runs on a laptop nor its team ownership: every team enables an
extension per environment, and its runs act as that team's automation principal only. Maintenance is
therefore an extension like Ingestion and dbt: runs in short-lived containers with the team's tokens, and no
service that holds a catalog-wide credential. Its policies live in Apache Polaris (see "Policies are Apache
Polaris policies").

## Policies are Apache Polaris policies (2026-11-25)

Polaris 1.9 has a Policy API with four system types for table maintenance, stores and resolves them, and
runs none of them: an engine does. Maintenance is that engine. It keeps no policy of its own; it reads and
writes Polaris's, so a Spark administrator, the Polaris CLI and Maintenance see and change the same policies.
The platform's rule is to prefer what Polaris does over a store of our own.

- **Types.** `system.snapshot-expiry` (snapshot expiry), `system.data-compaction` (compaction and dangling
  deletes, as Iceberg's `rewrite_data_files` has `remove-dangling-deletes`), `system.metadata-compaction`
  (rewriting manifests) and `system.orphan-file-removal` (orphan files). The API names them
  `snapshotExpiry`, `dataCompaction`, `metadataCompaction` and `orphanFileRemoval`.
- **Keys.** Inside the content, the keys Polaris documents (`max_snapshot_age_days`,
  `min_snapshot_to_keep`, `max_orphan_file_age_in_days`) and Iceberg's `rewrite_data_files` options
  (`target-file-size-bytes`, `min-input-files`, `delete-ratio-threshold`, `remove-dangling-deletes`). Two
  are Maintenance's own because neither has one: `max-input-bytes-per-run` and `min-count-to-merge` (after
  Iceberg's `commit.manifest.min-count-to-merge`). Polaris's documentation also writes
  `target_file_size_bytes`; Maintenance reads that too. Keys it does not know are kept when it writes.
  Ages are in days, as Polaris has them, where they were hours; the bounds stay: orphan files at least a
  day, expiry at least a day and at least one snapshot kept.
- **Where they live.** Polaris keeps every policy in a namespace. Maintenance creates its own,
  `_maintenance`, in each database it manages, with no tables, and removes it with the policies. A user's
  namespace would be the wrong home: Polaris refuses to drop a namespace that holds policies, so a policy
  there would stop a team from dropping its namespace. The cost is one empty namespace that engines list;
  its leading underscore and its `comment` property say what it is.
- **The database's policy** is the policies attached to the catalog, one per type, whoever created them.
  Maintenance creates the missing ones as `snapshot-expiry`, `data-compaction`, `metadata-compaction` and
  `orphan-file-removal` in `_maintenance` and attaches them to the catalog; one another tool attached is
  updated in place. Removing the policy detaches all of them and deletes Maintenance's own.
- **Exclusions are overrides.** Polaris resolves policies table over namespace over catalog. An exclusion
  attaches a disabled policy (`enable: false`) of each excluded type to a namespace or a table: one shared
  policy per type, `excluded-snapshot-expiry` and so on. Excluding only some types is the same mechanism.
  Polaris can resolve attachments but not list them, so the exclusions Maintenance made are also in its
  SQLite store, to show and change them. A table that already has its own policy of a type from another
  tool cannot also be excluded for it (`exclusion_conflict`): Polaris allows one per type and target.
  Policies with other settings on a namespace or a table are not edited here; set them in Polaris and runs
  honour them.
- **Runs resolve.** Before a run starts, the gateway lists the tables with the run's token and asks
  Polaris, per table, which policies apply (`applicable-policies`). The job carries them, each action in the
  result names the policy behind it (name, namespace, version, attached to the catalog, a namespace or the
  table), and the table's health keeps what applied. A type with no policy is not maintained; a policy with
  a value outside Maintenance's bounds is not applied, and says why.
- **Conflicts.** Each type is one Polaris policy with its own version. An update sends the version it read
  as `current-policy-version`, and Polaris refuses it when someone changed the policy since:
  `revision_conflict`. The API returns the versions and takes `expectedVersions`. There is no revision of
  Maintenance's own.
- **Tokens.** Policies are read and written with the team's automation principal's tokens from the
  Bridge, like runs: `read` for reading and resolving, `write` (the catalog role `writer`, whose
  `CATALOG_MANAGE_CONTENT` includes the policy privileges in Polaris 1.9) for changing. So a policy needs
  Maintenance to be enabled in the environment (`environment_not_enabled`). Who may change a policy is still
  the team role in `Services`.
- **What stays in SQLite:** schedules, the exclusions Maintenance attached, runs and table health.

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

Per table, in this order, each by the policy of its type that applies to the table: compaction, dangling
deletes, manifests, orphan files, snapshot expiry.

- Expiry never deletes files at commit (`WithPostCommit(false)`): the files expired snapshots leave behind
  become orphans. Orphan cleanup runs **before** expiry, so what one run expires stays until the next run:
  the schedule's interval is the grace period, as `nonCurrentDays` is in S3 Tables. Orphan cleanup never
  removes files younger than a day, so writes in progress are safe.
- Branch and tag heads, each ref's `max-ref-age-ms`, `max-snapshot-age-ms` and `min-snapshots-to-keep`,
  and the table properties `history.expire.*` override the policy, as Iceberg defines. S3 Tables instead
  fails a table with its own branches, tags or retention properties.
- `gc.enabled=false` skips expiry and orphan cleanup.
- Compaction bin-packs per partition, and sorts when the table has a sort order (S3 Tables' `auto`). Its
  input per table and run is capped (`max-input-bytes-per-run`): a backlog is worked off over several runs, and
  the runner's memory stays bounded. It commits `replace` snapshots stamped with the run id.
- A table whose commit conflicts with a writer is planned and committed again; if that keeps failing, the
  table fails on its own, the others continue and the run is `partial`. The next run tries again (see
  "Commit conflicts are retried").
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

## Commit conflicts are retried (2026-11-25)

Since Polaris 1.8, a commit that loses to a concurrent writer is a retryable 409 (`CommitFailedException`),
where it was a 400. iceberg-go maps it to `table.ErrCommitFailed`, and already retries most commits itself:
it reloads the table and replays the change, with exponential backoff (the table properties
`commit.retry.*`, 4 retries by default). The runner does not repeat that. It catches what iceberg-go gives
back: commits it will not replay because they remove delete files (compaction that applies deletes,
dangling-delete removal), and plans that no longer hold, such as a rewrite of files another commit removed.

For those, the runner retries per table and operation (`runner/retry.go`): it waits, with exponential
backoff from 2 seconds to at most 30 and jitter on the upper half, reloads the table, plans the operation
again against the new snapshot (compaction reads the new files) and commits. It tries 4 times in all, never
past the run's deadline. Only then does the table fail. Other errors, and an unknown commit state (5xx), are
not retried. Each action reports its `retries`; the run's totals count `commitRetries`.

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
- **The schedule is set on its own.** `PUT` and `DELETE /databases/{database}/schedule` (`set_schedule`,
  `delete_schedule`), as a pipeline has one schedule in Ingestion. It is Maintenance's own, in SQLite: Polaris
  has no schedules. A schedule applies the policy, so it needs one (`policy_required`), and deleting the
  policy removes it. Setting it changes no policy version: someone changing when maintenance runs does not
  conflict with someone editing what it does.
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
