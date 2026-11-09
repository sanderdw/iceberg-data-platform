# Transcript of the recorded run

Recorded on 21 October 2026 in 4K against a fresh install of Iceberg Data Platform 0.9.9 without extensions, on WSL2. The persona is `sander`, Administrator of the team `analytics`, with the database `multiverse` (Development). Everything below really happened. The OpenMetadata shot is reused from `demo-e2e/recordings/h8-om` (20 October 2026).

## Off camera

| Step | What |
| --- | --- |
| Install | `ICEBERG_EXTENSIONS= sh -c 'curl … install.sh \| sh'` |
| Admin portal | Team `analytics`; Keycloak account `sander` (analytics · Administrator) |
| pgAdmin | 0.9.9 installs `pgadmin/servers.json` with mode 600, so pgAdmin could not read it: `chmod 644`, then `setup.py load-servers` (fixed in 0.9.10). Signed in as `admin@example.com`, connected to "Polaris metadata" |
| RustFS | Console signed in with the root keys from `.env` |
| Notebook | `notebook/iceberg_explained.py` copied to `/work`; `/work/.marimo.toml` got `auto_instantiate = false` and `on_cell_change = "lazy"`, so nothing runs on open |
| Spike | Database `spike` for the dry run, deleted before recording; `multiverse` deleted and recreated once, because marimo had kept the dry run's outputs (`/work/__marimo__/session/iceberg_explained.py.json`) |
| Token | The DuckDB connection keeps the Keycloak token of its `ATTACH`, which expires after about ten minutes (Polaris answered 401 on commit). A hidden cell at the bottom re-attaches with a fresh token; it ran off camera before each notebook clip |

## On camera

| Clip | Input | Result |
| --- | --- | --- |
| c2-database | User portal → Create database `multiverse`, "Rick and Morty: characters, locations and episodes" | Catalog `db-352c28f0…`, bucket `db-db-352c28f0…-2890f361ad53` |
| c2-bucket | RustFS → Buckets | The new, empty bucket |
| c3-notebook | Run cells: setup, `connect_duckdb`, create, look inside | Namespace `rickandmorty`, table `characters`: 826 rows in one Parquet file, snapshot 1 (append) |
| c3-portal | Catalog → multiverse → rickandmorty → characters: Overview, Schema, Preview | Location `s3://…/rickandmorty/characters`, 7 columns, 100 rows |
| c4-rustfs | Bucket → rickandmorty → characters → metadata, preview `00001-….metadata.json` | `00000`/`00001` metadata.json, `snap-…avro` (manifest list), `…-m0.avro` (manifest) |
| c4-rustfs-data | data/ | One Parquet file |
| c4-pgadmin | Query Tool on `polaris`: catalogs (`type_code = 4`), then `internal_properties->>'metadata-location'` of `characters` | `db-352c28f0…` = multiverse with its bucket; the path of `00001-….metadata.json` |
| c5-notebook | `DELETE … WHERE name = 'Jerry Smith'`, look inside, time travel to snapshot 1 | Snapshot 2 (delete), a position delete file with 3 rows; snapshot 1 still has 3 Jerry Smiths (ids 5, 175, 177) |
| c5-portal | Snapshots tab; Preview of the first snapshot | Two snapshots; Jerry Smith in row 5 |
| c5-rustfs | data/ | The original Parquet file and `…-deletes.parquet` |
| c6-notebook | PyIceberg `overwrite`, `expire_snapshots().older_than(now)`, remove unreferenced files, check | One snapshot left (4, append); the original Parquet file, 3 manifests and 4 manifest lists removed; time travel no longer possible, Jerry in 0 data files |
| c6-portal | Snapshots tab | One snapshot (the native snapshot dropdown does not render in the recording) |
| c6-rustfs | data/ | The new Parquet file and the delete file; the original is gone |
| c7-notebook | Species query with DuckDB | Human, Alien, Humanoid, Animal, Robot |

The delete file of snapshot 2 stays referenced after the PyIceberg overwrite, because PyIceberg does not drop position delete files that point at a removed data file. It holds only file paths and row positions, not the deleted rows.
