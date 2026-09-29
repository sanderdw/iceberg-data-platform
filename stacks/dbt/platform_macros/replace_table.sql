{#- Iceberg Data Platform: replace a table's rows in one Iceberg transaction.

  dbt's `table` materialization drops and recreates the table, which gives it a new identity:
  data shares on it break and its properties and column docs are lost on every run. This one
  keeps the table: `DELETE` and `INSERT ... BY NAME` commit together. When the columns change,
  run with --full-refresh to recreate the table (shares on it must then be saved again).
-#}
{% materialization replace_table, adapter='duckdb' %}
  {%- set target_relation = this.incorporate(type='table') -%}
  {%- set existing = load_cached_relation(this) -%}
  {{ run_hooks(pre_hooks) }}
  {%- if existing is not none and should_full_refresh() -%}
    {% call statement('drop_for_full_refresh') -%}
      drop table {{ target_relation }}
    {%- endcall %}
    {%- set existing = none -%}
  {%- endif -%}
  {%- if existing is none -%}
    {% call statement('main') -%}
      create table {{ target_relation }} as {{ sql }}
    {%- endcall %}
  {%- else -%}
    {% call statement('main') -%}
      begin transaction;
      delete from {{ target_relation }};
      insert into {{ target_relation }} by name {{ sql }};
      commit;
    {%- endcall %}
  {%- endif -%}
  {{ run_hooks(post_hooks) }}
  {{ return({'relations': [target_relation]}) }}
{% endmaterialization %}
