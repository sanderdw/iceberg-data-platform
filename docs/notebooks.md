# Notebooks

Each team and environment has one shared `/work` filespace that holds all its notebooks, across databases. Teammates see each other's saved files. It is not a collaborative editor, though, so coordinate when you edit the same file.

Each session and database runs in its own container with your own credentials. Switching team or environment, signing out or letting the session expire stops your execution but keeps saved files, so save before you switch. The variables `catalog`, `table` and `df` are ready to use.

Notebooks have internet access. Packages you install with marimo's package installer (uv) go to `/tmp/packages` and disappear when the container stops. For permanent dependencies, add them to the `notebook` uv dependency group and rebuild the image ([run from source](develop/run-from-source.md)).

A native DuckDB attachment caches its credentials. Reconnect it, or call `refresh_table_credentials`, to renew them or to query another table.

Polars reads and writes Iceberg data files itself. Pass it the table's storage settings: `pl.scan_iceberg(table, storage_options=polars_storage_options(table))`, and the same for `sink_iceberg`, with `polars_storage_options` from `user_portal.notebook.connection`. Load the table again for fresh credentials.

## Examples

New filespaces receive these notebooks. Existing ones receive only the files they are missing, and nothing is overwritten. Each notebook runs from top to bottom.

| Notebook | Shows | Needs write access |
| --- | --- | --- |
| 01 · Neighborhood data with PyIceberg | Creating a synthetic energy table with PyIceberg | Yes |
| 02 · Visualize with DuckDB | Querying and charting that table | No |
| 03 · Native DuckDB on Iceberg | Attaching Polaris directly in DuckDB: snapshots, SQL | No |
| 04 · Write Iceberg v3 with DuckDB | Iceberg v3: variant, nanosecond timestamps, geometry, defaults, deletion vectors | Yes |
| 05 · Read Iceberg v3 with DuckDB | Reading those v3 features, row lineage and time travel | No |
| 06 · Write an AI-ready flights product | Publishing synthetic flight tables after quality checks, and their Apache Ossie semantic model in Polaris | Yes |
| 07 · Read an AI-ready flights product | Reading the semantic model from Polaris, checking the data against it, and answering questions with its joins and metrics; no local files | No |
| 08 · Load data with dlt | Loading paged API data into `raw_dlt_demo` with dlt, nested lists as child tables, merging on a key | Yes |
| 09 · Write an AI-ready grid forecast with Polars | Generating a synthetic MV load forecast and congestion outlook as lazy Polars queries, checking it with Polars SQL, writing it with Polars' Iceberg sink, and its semantic model in Polaris | Yes |
| 10 · Read an AI-ready grid forecast with Polars | Reading the semantic model from Polaris, scanning the tables lazily with Polars, and analysing congestion in an area of substations with the stored joins and metrics; no local files | No |

Run the write notebook before its reader.
