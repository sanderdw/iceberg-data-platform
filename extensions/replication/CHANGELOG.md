# Changelog

## 0.1.0 - unreleased

- First version of Replication (OLake) for the Iceberg Data Platform, on Extension Bridge contract 0.1.
- PostgreSQL sources: change data capture through a replication slot and publication, incremental
  on a cursor column, or full copies, per table. The first run copies the existing rows (or only
  follows new changes), then each run reads the changes since the last one.
- Updates and deletes are written as positional deletes (Iceberg v2, the default) or deletion
  vectors (Iceberg v3), which DuckDB, PyIceberg and Polars all read. Equality deletes are never used.
- Source checks: OLake's prerequisites (logical WAL, replication privilege, slot, publication) and
  the tables it can replicate, with the SQL the database owner runs once.
- Runs execute OLake v1.2.0 in an isolated container as the team's service account, with a
  short-lived write token and vended storage credentials, and stop before the token expires; a large
  first copy continues in the next run. Every table shows **Produced by olake** in the platform
  catalog, with the change log position it was read up to.
- Sources on private networks run only inside the networks a platform administrator allows
  (`REP_ALLOWED_SOURCE_NETWORKS`). The source address is resolved and checked before every run and
  pinned in the run container.
- A page, a REST API and an MCP server with the same capabilities; the `replication` agent skill.
