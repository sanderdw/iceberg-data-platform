# Build a semantic model

A [semantic model](semantic-models.md) lets people and agents answer questions from agreed metrics. This guide builds one for tables you can write. You need the **Read & write** role in the team that owns the database.

## Build it with an agent

The installation's [`semantic-model` skill](agent-skills.md) builds a model with you. Start your coding agent in the installation folder and ask, for example, "Build a semantic model for the flights tables in my sandbox database".

The skill:

1. signs in through `iceberg_connect.py` as you; run `uv run iceberg_connect.py login` when it asks;
2. interviews you about the business questions the model must answer;
3. profiles the tables and drafts datasets, joins, metrics and AI instructions;
4. runs every question, field and metric against the real tables;
5. publishes to Polaris only after you approve.

It keeps the model and its questions in `semantic-models/<namespace>/` in the working folder, so you can review and rebuild it later.

## Build it in a notebook

Example notebook **06 · Write an AI-ready flights product** publishes synthetic flight tables and then their model; **09** does the same with Polars for a grid forecast. Copy one of them as a starting point:

- `flights.product.yaml` and `grid_forecast.product.yaml`, next to the notebooks, hold the descriptions, labels and metrics.
- Notebook **07** or **10** reads the model back with any role and answers questions with its joins and metrics.

See [notebooks](notebooks.md).

## Build it in a dbt project

Put an Apache Ossie document at `semantic_models/<database>/<namespace>/<name>.yml` in the project. Every successful build or run publishes it to its environment, next to the tables. Descriptions the model leaves out come from the dbt models it reads. See [project conventions](dbt.md#project-conventions).

## Publish it through MCP

An agent connected to the [user portal's MCP server](ai-agents.md) publishes a model with `publish_semantic_model`. Replacing a model needs the `entityVersion` you read, so an update is refused if someone else changed it since. The tool returns warnings for metrics a governed query would refuse, and refuses fields that are not columns of their table.

## Check the result

1. In the user portal, open the namespace in **Catalog**. The model is listed after the tables and views.
2. **Details →** shows its datasets, fields, metrics and relationships. The **Diagram** tab draws each relationship between its join columns.
3. Ask a question with `query_semantic_model`, or in [Conversational BI](conversational-bi.md) when it is installed. The answer shows the compiled SQL and the metric definitions it used.

## Write it so agents answer well

These rules come from building models with the [MCP tools](mcp.md); [why the tools work this way](mcp-design.md) gives the reasons:

- **Units.** End each metric description with `Unit: …`.
- **Dataset names.** Write metric expressions with the dataset name, such as `sum(SALES.amount)` or
  `count(SALES.id)`. A metric that names no dataset, such as a bare `count(*)`, cannot be queried on
  its own, because the portal cannot tell which dataset it counts.
- **Fields.** Describe each field, and give it synonyms and examples in its `ai_context`. A `label`
  groups fields by what they hold across models, such as `Delay`; a data catalog shows it as a tag.
- **Dimensions.** Mark every field people group or filter by as a dimension, identifiers included.
  Once a dataset marks any field, only marked fields can be used.
- **Data gaps.** Say in each metric's description which years it covers, and in the instructions what
  NULL means. An agent then picks the newest year that has data instead of reporting NULL.
- **Spellings and levels.** Explain suffixes and levels in the instructions, such as `(PV)` for
  provinces, and the filter on the region level that keeps sums from counting a region twice.
- **Ratios.** Build ratios from sums, such as `100.0 * sum(a) / nullif(sum(b), 0)`. They then weigh
  correctly over several rows. `avg` of a stored percentage does not.

## Share it

Add the model to a [data share](data-shares.md). Selecting a model also selects the tables it reads; recipients can't query it without them.
