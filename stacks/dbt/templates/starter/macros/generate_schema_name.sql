{#- Iceberg Data Platform: a model's `schema` is its Iceberg namespace, used verbatim.
  Added to a run only when the project defines no generate_schema_name of its own. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
