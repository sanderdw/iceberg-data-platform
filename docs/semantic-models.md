# Semantic models

A semantic model says what the tables in a namespace mean: datasets that map to tables, their fields, the relationships between datasets and agreed metrics with their SQL. It follows the [Apache Ossie](https://github.com/apache/ossie) specification. To make one, see [build a semantic model](build-a-semantic-model.md).

## Why they matter

A table tells an agent which columns exist, not what "on-time arrival" means or how flights join to airports. Without a model, every agent writes its own SQL, and two agents can answer the same question with different numbers.

With a model, the owners agree on the meaning once. Governed queries take metric and field names, never SQL: the portal compiles the model's own expressions and joins only along its relationships. A metric then means the same in every answer, whoever asks. [Why the MCP tools work this way](mcp-design.md) explains the rules behind that.

## Where they live

Polaris 1.8 stores a model in the namespace, next to the tables it describes. It never runs a metric. Because the model sits in the catalog, the same grants cover it as the tables: readers read it, writers create, replace and remove it. An update is refused if someone else changed the model since you read it.

Storing it beside the data, instead of in a BI tool, means every consumer reads the same definition:

- **The user portal** lists models in **Catalog** after the tables and views. **Details →** shows the datasets with their AI context and fields, each with its label, description, synonyms and examples, the metrics and relationships, and the stored definition as YAML or JSON. The **Diagram** tab draws each dataset as a table and each relationship as a line between the join columns.
- **AI agents** read it with `list_semantic_models` and `describe_semantic_model`, find exact filter values with `list_dimension_values`, and answer from the agreed metrics with `query_semantic_model`. See [MCP tools](mcp.md#semantic-models).
- **[Conversational BI](conversational-bi.md)** answers chat questions with the same governed queries.
- **[Data shares](data-shares.md)** can include models. Recipients see exactly the shared models and can't list or change any others. A listed share publishes them, with their descriptions, labels and AI context, to a data catalog such as [OpenMetadata](openmetadata.md).
- **[dbt pipelines](dbt.md)** publish the models in a project's `semantic_models/` folder after every successful build.

## Labels

A field's `label` names the concept it holds, such as `Delay` or `AirportCode`. Labels group fields by meaning across models and tables, so a data catalog can find every delay column with one tag. The example notebooks take them from an ontology.

## A beta feature of Polaris

Semantic models are a beta feature of Polaris. The platform switches them on; an administrator can turn them off ([semantic models](admin-guide.md#semantic-models)). The portal then lists none, and the notebooks explain that they are switched off.
