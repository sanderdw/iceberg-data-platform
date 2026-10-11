# {{PROJECT}}

A dbt v2 project on the Iceberg Data Platform. It starts empty: add sources, models and tests,
then build.

- `schema` is the Iceberg namespace; `+catalog_name` picks the database (display name with `-` as `_`).
  Without a `schema`, a model lands in the namespace `main`.
- `profiles.yml` and `catalogs.yml` are generated for every run from your team's databases in the
  run's environment. Do not add them.
- Use `+materialized: replace_table` (the default here) or `incremental`. Iceberg views are not
  supported by DuckDB; `table` recreates the table and breaks data shares on it.

A first model reads a table of your team through a source and lands in the namespace `marts`:

```yaml
# models/sources.yml
sources:
  - name: raw
    database: {{CATALOG}}
    schema: synthetic
    tables:
      - name: neighborhood_electricity
```

```sql
-- models/marts/usage_per_neighborhood.sql
{{ config(schema='marts') }}
select neighborhood, sum(kwh) as kwh
from {{ source('raw', 'neighborhood_electricity') }}
group by neighborhood
```

Describe models and columns in YAML next to the SQL: descriptions become the table comment and
column docs in the catalog, and tests become the table's quality status.
