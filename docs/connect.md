# Connect from your computer

Use your own Python, DuckDB CLI or DBeaver instead of a notebook. You sign in as yourself, so your team role decides what you can read and write.

In **Catalog**, select a database and open **Connect from your computer**:

1. Download `iceberg_connect.py`. The portal fills in its Keycloak and catalog addresses. It holds no secret. The installer also puts a copy with the default local addresses next to `.env`.
2. Run `uv run iceberg_connect.py login`, then approve the code in your browser. The refresh token is stored in `~/.config/iceberg-platform` with owner-only permissions.
3. Copy the snippet for your tool. `db-…` is the database's catalog name.

| Tool | How |
| --- | --- |
| Python | `from iceberg_connect import catalog, duckdb_connection`. `catalog("db-…")` returns a PyIceberg catalog that renews its token itself. `duckdb_connection("db-…")` returns DuckDB with the database attached as `lakehouse`. |
| DuckDB CLI | `uv run iceberg_connect.py shell db-…` opens the DuckDB CLI with the database attached, on macOS, Linux and Windows. It needs `duckdb` on your `PATH`. |
| DBeaver | Create a DuckDB connection. Run `uv run iceberg_connect.py duckdb db-…` and add each printed line under **Connection settings › Initialization › Bootstrap queries**. |

Sessions run in UTC, so dates cast from timestamps are the same on every computer; `SET TimeZone = '…'` changes that for one session. DuckDB attaches read-only. Add `--write` to write with your own permissions. DuckDB keeps the token it started with, and that token lasts one hour. After that, run the command again (or replace the `CREATE SECRET` line in DBeaver). `uv run iceberg_connect.py logout` revokes the sign-in.

The helper turns off DuckDB's metadata-log lookup (`SET iceberg_use_metadata_log = false`). With the lookup on, a catalog clock that runs ahead of your computer makes the next write to a table fail with "Metadata-log exists but none of the entries were valid". Keep the platform host's clock in sync with NTP anyway.

With Compose, Keycloak, Polaris and RustFS listen on `127.0.0.1`, so out of the box this works only on the machine that runs the platform. On [Kubernetes](install-kubernetes.md) the catalog and S3 have public HTTPS hosts, so it works from any computer; download the helper from the portal. The [quick-share skill](agent-skills.md) makes it work from anywhere: it publishes the catalog and S3 API on the user portal's address. The Getting started guide warns when the catalog address only works on the platform's own machine. The helper names the address it could not reach and tells the user to download it again.
