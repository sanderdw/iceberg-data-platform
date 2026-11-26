# Changelog

## Unreleased

The REST API and the MCP tools follow the platform's one set of conventions, and policies moved into
Apache Polaris. Nothing of the old shapes or the old state is kept: set the policies again, and update
scripts and agent instructions.

- **Account menu.** The header ends the same way as in the administration and user portals: **Read-only** when
  you have the read role in each of your teams, then Settings, Apps and your name. Your name opens your teams and
  roles, **Getting started** (this extension's documentation), the theme switch and **Sign out**. Settings says what
  turning it on and off does and who can change it, and looks the same in every extension.
- **Settings.** Team administrators enable and disable Maintenance per environment under **Settings** ›
  **Environments** (the header link), the page every extension shares, which also shows who enabled each one. Disabling was only possible
  through MCP or REST. A database, policy or table in an environment where Maintenance is off says so, with
  Enable for administrators; the start page's "Where maintenance can run" card is gone.
- **Bridge contract 1.0.** Needs platform 1.0.0 (contract `>=1.0,<2`). A Bridge error keeps its own
  status and code (a Bridge 503 was answered as 502). The Apps menu no longer adds page-wide listeners
  each time it opens.
- **Policies are Apache Polaris policies.** A database's policy is one Polaris policy per system type,
  attached to its catalog: `system.snapshot-expiry`, `system.data-compaction` (with dangling deletes),
  `system.metadata-compaction` (manifests) and `system.orphan-file-removal`. Maintenance creates its own in
  the namespace `_maintenance` and updates one another tool attached in place. `settings` has the keys
  `snapshotExpiry` `{enabled, maxSnapshotAgeDays, minSnapshotsToKeep}`, `dataCompaction` `{enabled,
  targetFileSizeBytes, minInputFiles, deleteRatioThreshold, removeDanglingDeletes, maxInputBytesPerRun}`,
  `metadataCompaction` `{enabled, minCountToMerge}` and `orphanFileRemoval` `{enabled,
  maxOrphanFileAgeInDays}` (were `expireSnapshots`, `compaction`, `danglingDeletes`, `rewriteManifests` and
  `orphanFiles`, with hours and megabytes). `whoami` returns `policyTypes` (was `operations`).
- **Versions, not revisions.** The policy returns `policies` with each type's Polaris `name`, `namespace`,
  `version`, `managed` and `problem`; `PATCH` takes `expectedVersions` (was `expectedRevision`, tool
  parameter `expected_versions`). A stale version is still 409 `revision_conflict`. `revision`, `createdAt`,
  `createdBy`, `updatedAt` and `updatedBy` are gone: Polaris keeps none of them.
- **Exclusions are overrides.** `exclusions` `[{target, level, types}]` (was `exclude`, tables only)
  switches policy types off for a namespace or a table, with disabled Polaris policies attached there.
  New codes: 422 `invalid_exclusions`, 409 `exclusion_conflict`.
- **Runs ask Polaris.** A run resolves the policies that apply to each table, so policies other tools
  attached to the catalog, a namespace or a table count. Each action has `policy` (`name`, `namespace`,
  `version`, `appliedAt`); table health has `policies`. A type without a policy is not maintained, also in a
  dry run.
- **Reading and writing policies needs Maintenance enabled** in the environment (409
  `environment_not_enabled`). `list_databases` returns `policy: null` where it cannot read them.
- **Commit conflicts are retried.** A commit Polaris refuses as a conflict, and that iceberg-go does not
  replay itself, is planned again against the new snapshot and retried with backoff, up to 4 tries, before
  the table fails. Actions report `retries`, the totals `commitRetries`.
- **State.** The SQLite store keeps schedules, exclusions, runs and table health only. A state file from
  before this change is replaced on start.

- **A schedule of its own.** `PUT /databases/{database}/schedule` with `{cron, enabled}` and
  `DELETE /databases/{database}/schedule`, as the tools `set_schedule` and `delete_schedule`. It needs a
  policy (409 `policy_required`) and is no longer set through the policy (`cron`, `scheduleEnabled`). A
  schedule is `{cron, enabled, nextRunAt, lastRun}`, read with the policy and the database. Changing it
  changes no policy version. The minimum stays once an hour.
- **Policy.** `PATCH /databases/{database}/policy` (was `PUT`) with `{settings, exclusions,
  expectedVersions}`; the tool is `update_policy` (was `set_policy`). Without a policy it creates one from
  the defaults.
- **Two tools start a run.** `inspect_database` (`mode` `inspect` or `dry-run`) changes no table and is
  no longer marked destructive; `run_maintenance` always maintains and takes no `mode`. The REST
  operation keeps `mode`. Maintaining without a policy is 409 `policy_required` (was `no_policy`, which
  now only means there is no policy to delete).
- **Runs.** Starting a run returns 202 with the run as `get_run` returns it, `results` included.
  `GET /runs/{run}` and `get_run` take `wait` (0 to 600 seconds). A run has `scheduled` (was
  `schedule`). Run logs take `limit` (was `tail`). The statuses are the platform's eight: `queued`,
  `running`, `success`, `incomplete`, `partial`, `failed`, `error`, `cancelled`.
- **Table health.** The tools are `list_table_health` and `get_table_health` (were `list_tables` and
  `get_table`; operation ids `listTableHealth`, `getTableHealth`). The list takes `limit` (500, at most
  5000) and returns `{tables, total}`.
- **Lists are objects.** `GET /databases` returns `{databases}`, `GET /databases/{database}/runs`
  `{runs}`, `GET /teams/{team}/environments` `{environments}`; the tools return the same.
- **Environments.** Enabling and disabling return the environment `{team, environment, enabled,
  enabledBy, databases}`, not the platform's service account. Disabling one that is not enabled is 409
  `environment_not_enabled` (was 404). `whoami` is built field by field and no longer passes on
  `sharedDatabases`.
- **Times** are RFC 3339 strings in UTC with milliseconds, such as `2026-11-24T09:15:02.123Z` (were
  seconds as a number).
- **Errors.** Every error has `{error, code}`: an unknown path is 404 `not_found`, a wrong method 405
  `method_not_allowed`, an unexpected failure 500 `internal_error` (was `error`). A `team` filter that
  names a team you are not in is 403 `not_a_member` (was an empty list).
- The OpenAPI contract describes every response and the error body.
- The page sets the schedule in its own form on the database's page, and the policy editor edits the
  policy only.

## 0.0.1 - 2026-11-18

- First version of Maintenance for the Iceberg Data Platform, a **beta**, on Extension Bridge contract 0.1.
- Table health per database: snapshots and their age, data files and small files with a size histogram,
  position and equality delete files and deletion vectors, manifests, branches and tags, and why a table
  needs attention. A health check reads every table with a read-only token and changes nothing.
- One policy per database, with the defaults of Apache Iceberg and Amazon S3 Tables: expire snapshots
  older than 5 days, keeping the last one; compact files below 75% of a 128 MB target, applying their
  deletes, sorted when the table has a sort order; remove dangling delete files; merge manifests above
  100; remove orphan files older than 3 days (off by default). Tables can be excluded.
- Branch and tag heads, each ref's own retention and the table properties `history.expire.*` always win.
  Tables with `gc.enabled=false` keep their history. Expired snapshots' files become orphans that orphan
  cleanup deletes in a later run, never in the same run, and never younger than a day.
- Dry runs plan the policy without changing anything. Compaction and manifest rewrites commit `replace`
  snapshots; compaction stamps them with the run (`iceberg-data-platform.maintenance.run-id`).
- Runs in isolated containers started by a launcher, the only component with the Docker socket:
  read-only, no capabilities, memory, CPU and process limits, and a private network that holds only the
  platform's catalog and storage. The runner is one static Go binary on Apache iceberg-go, pinned to the
  `main` commit `e7a4d96` of 2026-11-18. A run ends 15 minutes before its token expires.
- Team administrators set policies and schedules (cron in UTC, at most hourly) and enable environments;
  writers check health, dry-run and run maintenance and stop runs; readers view.
- REST API and MCP server with the same capabilities, and a table health page.
- **Runs on Kubernetes.** The chart `deploy/helm/iceberg-maintenance`, published as
  `oci://ghcr.io/sanderdw/charts/iceberg-maintenance`, installs Maintenance next to the platform's chart.
  Each run is a Kubernetes Job in a namespace of its own (`<namespace>-maintenance-runs`) that reaches only
  the catalog, storage and the launcher, no internet; the runner fetches its job and returns its results
  through the launcher (`LAUNCHER_BACKEND=kubernetes`).
- **The platform installer adds it** (from platform 0.9.12): answer yes to its question, or set
  `ICEBERG_EXTENSIONS=maintenance`.
