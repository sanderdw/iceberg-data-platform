# {{PROJECT}}

A dbt v2 project on the Iceberg Data Platform.

- `schema` is the Iceberg namespace; `+catalog_name` picks the database (display name with `-` as `_`).
- `profiles.yml` and `catalogs.yml` are generated for every run from your team's databases in the
  run's environment. Do not add them.
- Use `+materialized: replace_table` (the default here) or `incremental`. Iceberg views are not
  supported by DuckDB; `table` recreates the table and breaks data shares on it.
- Read another table of your team with a source:

  ```yaml
  sources:
    - name: raw
      database: {{CATALOG}}
      schema: synthetic
      tables:
        - name: neighborhood_electricity
  ```
