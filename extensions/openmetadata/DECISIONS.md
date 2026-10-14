# Decisions for OpenMetadata (2026-10-14)

Checked against the OpenMetadata 2.0.5 release: the JSON schemas in `openmetadata-spec` and the
REST resources of `openmetadata-service`. Only this extension depends on them.

## Push, not pull

OpenMetadata removed its standalone Iceberg connector in 1.13 (services were migrated to
`CustomDatabase`). From 2.0 it reaches Iceberg only through a query engine such as Trino or
Snowflake. Its connector never ingested semantic models either. So this extension pushes
through OpenMetadata's REST API what the platform's Bridge hands it for each listed data share.
A metadata-only Polaris credential for OpenMetadata to pull with would have needed list
privileges, and Polaris doesn't filter listings, so it would reveal every name in a namespace.

## Metadata only

The Bridge gives a listing extension names, schemas, descriptions, partition fields, producer
conventions and the normalized semantic models: no data, view SQL, storage locations, snapshots
or credentials. This extension publishes all of that except the row count, which a catalog
reader doesn't need. View SQL stays out because it names tables that are not listed and can hold
literal values. A semantic model's dimension expressions and metric SQL are published: the owning
team chose to list the model, and they are what makes a metric understandable.

## OpenMetadata 2.0.5 API, and where it differs from the plan

| Topic | 2.0.5 | Consequence |
| --- | --- | --- |
| Database service type | No `Iceberg` service type | `CustomDatabase`, without a connection |
| Table type | `Iceberg` and `View` exist | Tables are `Iceberg`, views `View` |
| Column types | `TIMESTAMPZ`, `UUID`, `VARIANT`, `FIXED`, `BYTES`, `STRUCT`, `ARRAY` (needs `arrayDataType`), `MAP` | Iceberg `binary` maps to `BYTES`, `fixed[n]` to `FIXED` with `dataLength` |
| Partition interval types | `TIME-UNIT`, `COLUMN-VALUE`, `OTHER`, … | `identity` → `COLUMN-VALUE`, year/month/day/hour → `TIME-UNIT`, bucket and truncate → `OTHER` with the transform as interval |
| Metric | `dimensions` (`CATEGORICAL`, `TIME`), `metricExpression`, `metricType`, `assets` | As planned; measures and filters are not set |
| Data product | `domains` and `description` required; `assets` in the create request is deprecated | Assets go through `PUT /v1/dataProducts/{name}/assets/add` and `/remove`, compared with `GET …/name/{fqn}/assets` |
| Data product delete | `DELETE /v1/dataProducts/name/{name}` always hard-deletes | An unlisted data product is deleted and created again when relisted; tables and metrics are soft-deleted |
| Restore | PUT of a create request restores a soft-deleted entity and its children with the same id | No separate restore call |
| Domain | `domainType` required | `Source-aligned`, set on the services and metrics so data products and their assets share it |
| Team | `teamType` required | `Group`. When the bot may not create teams, listings are published without owners |
| Version | `GET /v1/system/version` | Read on every full sync and shown in `/api/status` |

An asset may be in more than one data product (`dataProducts` is a list), so a table listed in
two shares belongs to both.

## ER diagram in the data product description

OpenMetadata's ER diagrams are a Collate feature; the open-source UI shows keys only in each
table's Table Constraints. Its description editor renders neither Mermaid nor HTML images, but it
renders a Markdown image with a data URI. So `diagram.py` draws an SVG of the listed tables that
take part in a foreign key, from the same keys as the table constraints, and the description
repeats the relationships as a Markdown table for readers and search.

## Descriptions follow the platform

A bot's PUT never replaces a description that is already set in OpenMetadata, for an entity or a
column. After each PUT the client compares the stored descriptions (OpenMetadata stores `+` and
`=` as HTML entities) with the platform's and sends a JSON Patch for the ones that differ. Where
the platform has no description, OpenMetadata keeps what people wrote there.

## Foreign keys need their tables first

OpenMetadata refuses a foreign key to a table it doesn't have yet. Tables that others refer to
are published first, and a self-reference or a cycle gets its foreign keys in a second PUT.

## Schemas are data product assets

A listing's schemas join its data product, so a schema page shows where its tables are listed. A
schema with tables in two listings belongs to both data products.

## Semantic models become glossary terms

A semantic model names the business concepts behind its tables, so each listed model is a term in
one glossary per installation (`OM_DOMAIN`), with its datasets and described fields as child terms.
Tables and columns are tagged with the terms of the datasets and fields that read them; a field
reads a column when its expression is just that column. Terms are named by the model's place
(`<prefix>__<database>__<namespace>__<model>`), so two models never share one, and child terms add
their dataset and field (`…__<model>__<dataset>__<field>`): OpenMetadata wants a term name unique in
the whole glossary, not only among its siblings, and fields such as `id` or `name` recur. Display
names stay readable. Bot-created terms are Approved at once. A PUT keeps tags it doesn't name, so terms of models that are no longer listed
are hard-deleted, which removes their tags too. Metrics stay Metric entities, not terms.

## Field labels become classification tags

Ossie gives a field an optional `label`, "a label for categorization". OpenMetadata's word for
that is a tag of a classification, not a glossary term: a label groups fields across models and
says nothing about one model. So each label is a tag of one classification per installation
(`<prefix>__<OM_DOMAIN> labels`, shown as `<OM_DOMAIN> labels`), on the column the field reads and
on the field's glossary term. The classification is the installation's own, so two installations
in one domain never remove each other's labels: a tag no listed model uses is hard-deleted, which
removes it from every column and term, and a label a field no longer has leaves that field's
column and term. Tags that people add in OpenMetadata stay. Ossie has no dictionary of its own; a
model that wants one uses its labels for the concepts of an ontology, as the platform's flights
example does.

Every database, schema and table carries the domain explicitly: OpenMetadata refuses a PATCH of a
data product's asset whose domain is only inherited from its service.
