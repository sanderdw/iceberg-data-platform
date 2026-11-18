# Changelog

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
