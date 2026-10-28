# From API to answer

Voice-over script, per chapter. Source: `edit/cut-en.json` (edit it there). Brackets show what is on screen.


## Title

- *[title]* Welcome to a user demo of the Iceberg Data Platform. I'll show you how to install the platform on your own laptop, and how to build a pipeline end to end, turn it into a semantic model, and ask questions about it.
- *[title]* Then I publish that data product in a data catalog. I use OpenMetadata for that, but it's just an example: this works with other data catalogs too. And everything is headless and agent-driven.

## 01 · Installation

- *[h1-install]* It starts with one command. The script fetches the latest release and installs it.
- *[h1-install]* Then it asks which extensions I want. I pick all three: Conversational BI, dbt, and Ingestion with dlt. For the chat I pick Gemini for now, but you're flexible here.
- *[h1-install]* Then it pulls the images and starts the platform: the catalog, Keycloak, the portals and the extensions. That takes a few minutes.
- *[h1-summary]* At the end it shows exactly what to do next: sign in as platform administrator, with a temporary password.

## 02 · First steps

- *[h2-admin]* I sign in as platform administrator, and I choose my own password right away.
- *[h2-admin]* I create one team as an example: analytics.
- *[h2-admin]* And one account: myself, as Administrator of that team. You see the temporary password only once.
- *[h2-admin]* That's it for the platform administrator. Now I switch to the user role.
- *[h2-user]* I sign in to the user portal, now as team admin of analytics.
- *[h2-user]* And I create the database multiverse. That is its own Iceberg catalog, with its own bucket.

## 03 · Connecting the agent

- *[h3-connect]* Now the agent. I work with Claude Code in the install folder, because the skills are already there. GitHub Copilot, Codex, or any other agent with MCP works just as well. I add three MCP servers: the user portal, Ingestion and dbt.
- *[h3-connect]* Each MCP server signs in under my name. I'm already signed in in the browser, so that's easy, without a password.
- *[h3-connect]* Conversational BI has an MCP server too. With it, your agent asks your semantic models questions itself, with the same governed queries, for example for a report or a notebook.
- *[h3-connect]* Today I only show it briefly in the browser. And the agent can only do what I'm allowed to do myself.

## 04 · Loading data

- *[h4-ingest-en]* I just ask in plain English: load the Rick and Morty API into my database multiverse, and reload it every hour. That API is a well-known sample project. You can just as well load a table from PostgreSQL, for example, or other APIs.
- *[h4-ingest-en]* The agent picks up the ingestion skill, puts a pipeline together and validates it. Then it sees that Ingestion isn't enabled yet, so we turn that on first.
- *[h4-ingest-en]* Pipelines run on behalf of the whole team. I accept.
- *[h4-ingest-en]* It enables Ingestion, runs the pipeline and sets the schedule: for this project, every hour.
- *[h4-ingest-en]* In about a minute and a half there are six tables, while the source API has only three kinds of entities: characters, locations and episodes. dlt also creates the link tables it needs right away, such as the episodes of a character.
- *[h4-web-en]* In the Ingestion extension I now see the pipeline the agent created. Here I see when it ran, and what the schedule is: every hour, on the hour.
- *[h4-cat-en]* The catalog has the six tables, each with a link back to the pipeline that created it.
- *[h4-cat-en]* A link table points to its parent with a key, so everything can still be joined.

## 05 · A star model with dbt

- *[h5-dbt-en]* Now I turn it into a star model, with dbt: a fact table with which character appears in which episode, and dimensions for character, episode and location.
- *[h5-dbt-en]* The agent picks up the dbt skill and looks at the raw tables first.
- *[h5-dbt-en]* dbt isn't enabled yet either, so we enable it again.
- *[h5-dbt-en]* It writes the sources, the staging models and the star model, and builds them.
- *[h5-dbt-en]* With every model it also writes tests: unique and non-empty keys, links from the fact table to each dimension, and valid values for status and gender, for example. It derives them from the data, and dbt runs them on every build.
- *[h5-web-en]* In the pipeline viewer you see the lineage, from raw table to fact table.
- *[h5-web-en]* And for each model, which tests run. Green means the data meets what we expect of it.

## 06 · Semantic model

- *[h6-semantic-en]* Then the next step: a semantic model. With it I tie definitions to the data: what a metric means, and how the tables relate.
- *[h6-semantic-en]* The skill starts with an interview. That helps me think about what a user might ask later, so the right definitions get recorded.
- *[h6-semantic-en]* Ideally the producer of the data provides as much of this as possible. Here the agent suggests the questions and definitions itself.
- *[h6-semantic-en]* It drafts the model, and turns the questions from the interview into tests.
- *[h6-semantic-en]* To check them against the real data, it needs my own sign-in. I approve that in the browser.
- *[h6-semantic-en]* Then it runs every question from the interview against the real data. That way it knows for sure that the model can answer them.
- *[h6-semantic-en]* Only then does it ask whether it may publish, with the answers alongside, so I can see whether they're right.
- *[h6-semantic-en]* It stores the model in the dbt project, so every build publishes it again. After publishing, it asks all the questions once more as a final test.
- *[h6-semantic-en]* The raw data reloads every hour, so I let it build the star model every hour too, at a quarter past.
- *[h6-web-en]* In the catalog there's now the model, with its description and instructions for AI.
- *[h6-web-en]* In the diagram you see the appearances on the left, joined to character, episode and location.
- *[h6-web-en]* And the metrics, each with a definition. That lets a question be steered better later on.

## 07 · Conversational BI

- *[h7-ask-en]* From the model I go to Conversational BI, to show how a user could work with it.
- *[h7-ask-en]* The chart is Generative UI. The language model only chooses which component to show, such as a chart, a table or a KPI, and with which columns. The extension fetches the numbers from the result itself. So they never come from the language model.
- *[h7-sql-en]* And I see exactly which query ran. The language model doesn't write free SQL. It asks the model for a metric, and the platform builds the query.

## 08 · Data catalog

- *[h8-publish-en]* Finally I make the data product findable. I ask the agent to publish it in the data catalog.
- *[h8-publish-en]* It does that with a data share that publishes only metadata, so no data. Anyone who wants to use the data asks the team for access.
- *[h8-om-en]* Within a minute it's in OpenMetadata, as a data product of team analytics.
- *[h8-om-en]* With all the metadata: the description, the semantic model with its instructions, and the metrics with their definitions.
- *[h8-om-en]* And an ER diagram of the tables and their keys.
- *[h8-om-en]* The tables and the metrics are the assets of the data product, but definitions differ per data catalog.
- *[h8-om-en]* For each table you see the columns with their description. OpenMetadata isn't part of the installer. How to install that extension is in the documentation.

## End

- *[end]* I hope this shows you how an agentic data platform can work end to end. Try it yourself, with one install command.
