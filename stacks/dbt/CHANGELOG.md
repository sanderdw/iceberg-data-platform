# Changelog

## 0.1.0 (unreleased)

- First version of the dbt stack for the Iceberg Data Platform, on Extension Bridge contract 1.x.
- dbt v2 OSS 2.0.5 with DuckDB 1.5.5 on Iceberg REST, installed without run-time downloads.
- Git-backed projects per team with protected `main`, branches, commits, diffs and merges.
- Runs and schedules as the team's automation principal per environment, in isolated containers
  with one-hour read or write tokens. Acceptance and production build only `main`.
- `replace_table` materialization that keeps Iceberg table identity, data shares and docs.
- Producer, lineage and quality table properties and column docs after every build.
- REST API and MCP server with the same capabilities; pipeline graph and lineage from dbt's
  Parquet information schema.
