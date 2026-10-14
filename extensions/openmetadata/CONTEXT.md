# Context

The words this extension uses. It translates between three vocabularies: the platform's, Apache
Ossie's (semantic models) and OpenMetadata's. Where they use the same word for different things,
this file says which one is meant. Code, docs and commit messages use these words this way.

## The platform

**Data share**
: A team's grant of some tables, views and semantic models of one database. Its audiences are another
  team, an external party, or a listing in the data catalog, alone or combined.

**Listing**
: A data share whose owning team listed it in the data catalog. It publishes metadata only: no data,
  no credential, and no view SQL, location or snapshots. The Bridge serves it to listing extensions
  (capability `listings`). _Avoid:_ "catalog entry", "publication".

**Listed object**
: A table, view or semantic model in a listing. Objects that were dropped since the share was made
  are *unavailable* and not published.

**Owner**
: The team that owns the database and the share. It is the OpenMetadata owner of what this extension
  publishes, unless the bot may not create teams.

**Contact**
: Whom to ask for access, as the owning team wrote it on the share, such as a team mailbox.

**Full sync**
: Fetching and publishing every listing, every 15 minutes and at start. A *poll* (every minute) only
  publishes listings whose summary version changed. A summary version covers a listing's header and
  object names, not their schemas, so schema and semantic model edits arrive with the full sync.

## Semantic models (Ossie)

**Semantic model**
: An Ossie document stored in a namespace: datasets, relationships and metrics. One stored document
  (a *semantic model object* in a listing) can hold several models.

**Dataset**
: A business entity of a model that reads one table, such as `FLIGHT`. _Not_ a table: the dataset is
  the meaning, the table the storage.

**Field**
: A named expression of a dataset. When the expression is just a column name, the field *reads* that
  column, and what the model says about the field lands on the column.

**Label**
: Ossie's optional "label for categorization" of a field. It groups fields across models by what they
  hold, such as `Delay` or `AirportCode`. The flights example uses its ontology's concepts as labels.
  _Avoid:_ "tag" for the Ossie side.

**AI context**
: Ossie's `ai_context` of a model, dataset, field or metric: instructions, synonyms and examples for
  an AI agent. Synonyms of a field or metric travel separately; the rest are label and value rows.

**Metric**
: An agreed calculation in a model, with its SQL. In OpenMetadata it is a Metric entity, never a
  glossary term.

**Relationship**
: A join from one dataset's columns to another's. When both ends are listed tables, it becomes a
  foreign key, and the dataset's primary key a primary key.

## OpenMetadata

**Data product**
: One per listing, in the configured domain (`OM_DOMAIN`). Its assets are the listing's schemas,
  tables and metrics; its description holds the contact, a section per semantic model and the ER
  diagram.

**Glossary term**
: A business term in the extension's glossary (named after `OM_DOMAIN`). Each listed model is a term,
  with its datasets and described fields as child terms. Tables and columns are tagged with them.

**Classification tag**
: A tag of the installation's classification `<prefix>__<OM_DOMAIN> labels`. Each field label is one, on the
  field's column and its glossary term. _Avoid:_ "label" for the OpenMetadata side, except in
  "tag label", OpenMetadata's name for a tag applied to an entity.

**Dictionary**
: Ossie has none, and this extension makes none of its own. The glossary is OpenMetadata's data
  dictionary; labels that name ontology concepts tie it to an ontology.

**Prefix**
: `OM_SERVICE_PREFIX`, `iceberg` by default. Everything this extension owns carries it: services
  `<prefix>-<environment>`, data products `<prefix>-share-…`, metrics and glossary terms
  `<prefix>__…`, and the label classification `<prefix>__<OM_DOMAIN> labels`. Removal only touches those.

## How they map

| Platform or Ossie | OpenMetadata |
| --- | --- |
| Environment | Database service `<prefix>-<environment>` |
| Database | Database, named by its id |
| Namespace | Database schema |
| Listed table or view | Table |
| Listing | Data product |
| Owning team | Team, as owner |
| Semantic model, dataset, described field | Glossary term, with child terms |
| Field label | Classification tag |
| Metric | Metric |
| Relationship and primary key | Table constraints |
| AI context, synonyms | Descriptions and term synonyms |
