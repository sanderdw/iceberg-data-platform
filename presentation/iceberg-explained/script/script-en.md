# How does Apache Iceberg work?

Voice-over script, per chapter. Source: `edit/cut-en.json` (edit it there). Brackets show what is on screen.


## 01 · A table format

- *[diagram: layers]* Apache Iceberg is not a data platform. It is an open-source table format for large datasets. It describes how files and metadata together form one table, so that different engines can read and write that table reliably.
- *[diagram: layers]* The data itself sits in files, usually Parquet, in S3-compatible object storage such as RustFS. Next to that, Iceberg keeps metadata: which files belong to the table, with which schema, and at which moment.
- *[diagram: layers]* That metadata is a tree. The metadata file holds the table's schema and the list of snapshots: every version of the table. Each snapshot has a manifest list, which says which manifests belong to that version. And a manifest lists the Parquet files, with the smallest and largest value of each column. That lets an engine skip files without opening them.
- *[diagram: layers]* At the top sits the catalog. That is a technical catalog for Iceberg tables, not a data catalog where you search for data products. For each table it knows only one thing: where the current metadata file is.
- *[diagram: layers]* A client, such as DuckDB or Spark, talks to the catalog over the Iceberg REST API. To read, it asks for the table's metadata. With that, it quickly works out which Parquet files it needs, and it fetches only those from the bucket.
- *[diagram: layers]* To write, the client first puts new Parquet files in the bucket. Existing files are never changed. Then it asks the catalog, over the REST API, to commit the change. The catalog writes a new metadata file and moves its pointer in one step. That way every change becomes a new snapshot.
- *[diagram: layers]* Compare that with Postgres, where all of this sits in one server. Data and metadata are tightly coupled, and every query runs on that same server. With Iceberg they are separate parts: a catalog, a bucket, and an engine of your choice.

## 02 · The building blocks

- *[diagram: functions]* Let me line up those building blocks next to Postgres. For each function you see what Postgres does itself, what this platform uses, and what the alternatives are.
- *[diagram: functions]* Storage. Postgres writes to the disk of its own server. With Iceberg it is object storage: RustFS here, but Amazon S3 or Azure Blob Storage work just as well.
- *[diagram: functions]* File format. Postgres keeps its data in PGDATA, as binary blobs in its own format. Iceberg usually uses Parquet. That is columnar, so an engine reads only the columns it needs.
- *[diagram: functions]* Table format. What Postgres handles internally, Iceberg defines as an open table format. Delta Lake and Apache Hudi do the same, each in their own way.
- *[diagram: functions]* Metadata catalog. Postgres has its system catalog. Here that is Apache Polaris, which in turn keeps its own data in Postgres. Lakekeeper or AWS Glue can fill that role too.
- *[diagram: functions]* Catalog API. You talk to Postgres over its own protocol, or over ODBC. An Iceberg catalog speaks the Iceberg REST API, so any client that knows that API can work with it.
- *[diagram: functions]* And the query engine. In Postgres it is built in. With Iceberg you choose it yourself: DuckDB, PyIceberg, Polars, Spark or Snowflake.
- *[diagram: functions]* In Postgres, all these functions sit in one system. With Iceberg, each function is its own building block, which you can replace on its own.
- *[diagram: platform]* To keep it simple, one database here is one catalog in Polaris, with its own bucket in RustFS.
- *[c2-database]* In the portal I create the database multiverse.
- *[c2-bucket]* And RustFS right away has a new, empty bucket.

## 03 · Creating a table

- *[c3-notebook]* I create the table in a notebook, with DuckDB. PyIceberg, Polars or Spark can do exactly the same.
- *[c3-notebook]* DuckDB reads all characters from the Rick and Morty API and writes them to the bucket as Parquet. Polaris records the new metadata file.
- *[c3-notebook]* Every write is a snapshot. This first snapshot consists of one Parquet file.
- *[c3-portal]* The table now also shows up in the portal's catalog.
- *[c3-portal]* With its location in the bucket: the namespace and the table as folders. And the schema from the metadata.
- *[c3-portal]* The portal reads a preview of the data with my own permissions.

## 04 · Under the hood

- *[c4-rustfs]* In RustFS you see exactly that structure: for each table, a folder with metadata and a folder with data.
- *[c4-rustfs]* The metadata folder holds the metadata files, the snapshot's manifest list, and the manifest.
- *[c4-rustfs]* A metadata file is plain JSON: the schema, the location and the snapshots, each with a pointer to its manifest list.
- *[c4-rustfs-data]* The data folder holds the Parquet file with the characters themselves.
- *[c4-pgadmin]* To show what Polaris stores, the platform includes pgAdmin. Polaris needs only a few tables: entities for catalogs, namespaces and tables, and grant_records for the permissions.
- *[c4-pgadmin]* Here the database multiverse is a catalog, with the bucket as its base location.
- *[c4-pgadmin]* And for the table, Polaris stores only the path to the current metadata file. No data, just a pointer. The rest lives in the bucket.

## 05 · A delete

- *[c5-notebook]* Now I delete Jerry Smith from the table.
- *[c5-notebook]* DuckDB doesn't rewrite the Parquet file. It writes a small delete file that marks the rows, and creates a new snapshot.
- *[c5-notebook]* The first snapshot still exists. With time travel I read the table as it was, and Jerry is simply still in it.
- *[c5-portal]* The portal now shows two snapshots: the append and the delete.
- *[c5-portal]* And in a preview of the first snapshot, Jerry is still there.
- *[c5-rustfs]* In the bucket, the original Parquet file is still there, with the delete file next to it.
- *[diagram: deletes]* What that marking looks like depends on the version and the engine. In format version 2, as here, it is a delete file with the positions of the deleted rows.
- *[diagram: deletes]* In version 3 it is a deletion vector: a compact bitmap in a Puffin file, one per data file.
- *[diagram: deletes]* Other engines choose copy-on-write. They rewrite the data file right away, without the deleted rows.
- *[diagram: deletes]* But in all three cases the old file stays as long as an older snapshot points to it. That is useful for time travel, and for readers still working with an older snapshot. But the data is still there.

## 06 · Really gone

- *[diagram: permanent]* If a delete has to be really gone, for example for the GDPR, there are three steps.
- *[diagram: permanent]* One: rewrite the data without the deleted rows. Two: expire the old snapshots. Three: remove the files that no snapshot points to anymore.
- *[c6-notebook]* DuckDB can't expire snapshots yet, so I do these steps with PyIceberg, on the same table. First the rewrite: a new Parquet file without Jerry.
- *[c6-notebook]* Then I expire all snapshots except the current one.
- *[c6-notebook]* Expiring only removes the snapshots from the metadata. The files stay. So I look up the files that nothing points to anymore, and delete them.
- *[c6-notebook]* The check: time travel to the first snapshot no longer works, and Jerry is in none of the Parquet files.
- *[c6-portal]* The portal now shows just one snapshot.
- *[c6-rustfs]* And in the bucket, the original file is gone.
- *[c6-rustfs]* Snowflake handles this itself, with a fixed retention period. With Iceberg you schedule this maintenance yourself, with the engine you choose.

## 07 · Your own engine

- *[diagram: compare]* Technically, Snowflake works the same way. The data sits in separate storage, and the metadata is kept in another place. Only, Snowflake manages all of that itself.
- *[diagram: compare]* The difference with Apache Iceberg is that with open source you choose your own engine. And you do that on both sides.
- *[diagram: engines]* On the producer side, you write to Iceberg with the framework of your choice. That can be Python, Spark, or even Snowflake.
- *[diagram: engines]* Once the data is there, that engine is gone again. The data isn't tied to any engine.
- *[diagram: engines]* On the consumer side, you choose again. I often use DuckDB or Polars, but Snowflake can connect to such a catalog as well.
- *[c7-notebook]* As a consumer, I read the table here with DuckDB. The engine fetches only the files it needs, and computes in my own environment.

## 08 · Exchange

- *[diagram: combine]* The strength of Snowflake, or any other integrated data platform, is that all data sits on the same platform. There you combine data from different producers quickly, on one large compute platform.
- *[diagram: combine]* With Iceberg, that doesn't work the same way. When several producers each offer their data product through Iceberg, you as a consumer pull in the data you need.
- *[diagram: combine]* That is efficient, because you fetch only what you really need. But you process it in your own engine, so there is always a data transfer.
- *[diagram: combine]* You can see Apache Iceberg as a mechanism to exchange data, not as a replacement for a data platform like Snowflake. They complement each other.

## 09 · The data product

- *[h8-om]* An Iceberg table like this is the foundation of a data product. You can put a semantic model on top of it, with definitions and metrics.
- *[h8-om]* And you can publish the data product in a data catalog, like OpenMetadata here. I show how that works in the end-to-end demo.

## End

- *[end]* Try it yourself. Install the platform with one command, and create your own Iceberg table.
