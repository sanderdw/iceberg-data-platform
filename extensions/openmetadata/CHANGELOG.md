# Changelog

## 0.1.0 - 2026-10-14

- First version of OpenMetadata for the Iceberg Data Platform, on Extension Bridge contract 0.1.1
  (capability `listings`, core 0.9.1).
- Publishes every data share that its owning team lists in the data catalog to OpenMetadata 2.0:
  a database service per environment, the database, its namespaces as schemas, and the listed
  tables and views with their columns, types, descriptions, partitioning and required columns.
- Semantic models become OpenMetadata metrics: description and synonyms, the SQL expression,
  the metric type from the outer aggregate (also with a FILTER clause or a constant
  scale, and `100 * … / …` as a percentage), and the model's fields as categorical or time
  dimensions. The tables a model reads are the metric's assets and its upstream lineage; primary
  and foreign keys of the model show on the tables when both ends are listed.
- Each listing is a data product in one domain, with the owning team as owner and the contact
  for access requests in its description. Lineage between listed tables comes from the
  producer conventions, such as dbt's.
- Metadata only: no data, view SQL, storage locations, snapshots or credentials leave the
  platform, and the extension never receives a catalog token.
- Unlisting, revoking or an expiry soft-deletes the tables and metrics and deletes the data
  product; listing it again restores the same entities. A full sync every 15 minutes publishes
  schema and semantic model edits and heals changes made in OpenMetadata.
- The user portal's **OpenMetadata** link opens the catalog (`OM_UI_URL`), or the sync status without it.
- The data product's description shows an ER diagram of the listed tables and their keys (an SVG, since open-source OpenMetadata draws none), with the same relationships as a table. Its schemas are assets too, so a schema page names the data product.
- Semantic models enrich the catalog: the data product describes each model (description, AI instructions, datasets, metrics); a glossary term per model, dataset and described field tags the tables and columns they read; tables add what their dataset means, and undocumented columns get their field's description, synonyms and AI context. Terms of models that are no longer listed are removed, with their tags.
- Field labels become tags of one classification (`<OM_DOMAIN> labels`), on the column and the field's glossary term; labels no listed model uses are removed. Dataset, field and metric AI context (instructions, examples) joins their descriptions.
- Descriptions follow the platform: a changed table, column, metric or data product description replaces the one in OpenMetadata; descriptions people add there where the platform has none stay.
- Tables that foreign keys refer to are published first, so a listing never fails on its own keys.
- Optional `compose.openmetadata.yaml` with a local OpenMetadata 2.0.5 server for demos, and
  `scripts.om_token` to write its bot token.
