---
name: semantic-model
description: Create, improve or update an Apache Ossie semantic model for tables in an Iceberg Data Platform database, and store it in Polaris next to the tables. It interviews the user about the business questions the model must answer, profiles the data, drafts the datasets, fields, relationships, metrics and AI instructions, proves every question can be answered, and publishes only after the user approves. Use when the user wants a semantic model, metric definitions, an AI-ready or documented data product, or asks why an agent cannot answer a business question about their tables.
---

# Semantic models for Iceberg Data Platform

A semantic model tells people and AI agents what tables mean: datasets mapped to Iceberg tables, their fields, the joins between them, agreed metrics with their SQL, and instructions for using them. It follows [Apache Ossie](https://github.com/apache/ossie). Polaris stores it in a namespace, next to the tables, and never runs it. The portal's Catalog shows it, the MCP tool `describe_semantic_model` returns it and Conversational BI answers from it.

A model is only as good as the questions it was built for. This skill therefore **starts with an interview** and does not publish until every question the user gave runs against the real data and the user has approved the result.

Work from the folder that holds `iceberg_connect.py` (the installation folder, by default `~/iceberg-data-platform`). This skill's files are in `.agents/skills/semantic-model`:
- [scripts/semantic_model.py](scripts/semantic_model.py): `profile`, `draft`, `check`, `show` and `publish`, signed in as the user
- [references/interview.md](references/interview.md): the question bank and the quality bar
- [references/model-format.md](references/model-format.md): the document format Polaris and the portal read, with an example

Below, `SM` means `uv run .agents/skills/semantic-model/scripts/semantic_model.py`.

## Rules

- **Never guess business meaning.** Grain, keys, time zones, metric definitions, exclusions and data sensitivity come from the user. The data can suggest an answer; the user confirms it.
- **Ask in rounds.** Use your agent's question tool if it has one (for example `ask_user`, `vscode/askQuestions` or `AskUserQuestion`), otherwise ask in chat and stop until the user replies. At most four questions per round, each with your suggested answer when the profile supports one.
- **Read before you write.** Writing needs the Writer role in the database's team. Publishing replaces a model of the same name, so show the user what changes first.
- **Never print or store tokens.** The script signs in by itself. If it reports that the user is not signed in, ask the user to run `uv run iceberg_connect.py login` and approve the code in their browser.
- **Keep work files.** Save the model as `semantic-models/<namespace>/<name>.json` and the questions as `semantic-models/<namespace>/<name>.questions.sql` in the working folder, so the model can be reviewed and rebuilt later.

## 1. Connect

1. The script signs in through `iceberg_connect.py`. When a command answers "You are not signed in" or "Your sign-in has expired", ask the user to run `uv run iceberg_connect.py login` themselves (it opens a browser to approve a code) and continue when they confirm. When `iceberg_connect.py` is missing, the user downloads it in the user portal: **Catalog › Connect from your computer**.
2. Check for the user portal's MCP server: call `list_databases` (your agent may show it with a prefix, usually `iceberg-user`). It gives the database id (`db-…`), the team role and the environment. Without the MCP server, ask the user for the database id; the portal's Catalog shows it.

If the MCP tools are missing and the user wants them, register the server. `<URL>` is the user portal address plus `/mcp`, by default `http://localhost:3002/mcp`.

| Agent | Register | Sign in |
|---|---|---|
| Codex (CLI and IDE extension) | In `~/.codex/config.toml`: `[mcp_servers.iceberg-user]` with `url = "<URL>"` and `scopes = ["openid", "profile", "offline_access"]`, plus `[mcp_servers.iceberg-user.oauth]` with `client_id = "iceberg-mcp"` | `codex mcp login iceberg-user` |
| GitHub Copilot in VS Code | In `.vscode/mcp.json`: `{"servers": {"iceberg-user": {"type": "http", "url": "<URL>", "oauth": {"clientId": "iceberg-mcp"}}}}` | Start the server from `mcp.json`; VS Code opens the sign-in |
| GitHub Copilot CLI | In `~/.copilot/mcp-config.json` under `mcpServers`: `"iceberg-user": {"type": "http", "url": "<URL>", "tools": ["*"], "oauthClientId": "iceberg-mcp", "oauthPublicClient": true}` | On first use; if asked for a client ID, enter `iceberg-mcp` |
| Claude Code | `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user <URL>` | On the first tool call |
| Any other agent | A streamable HTTP MCP server at `<URL>` with OAuth: public client ID `iceberg-mcp`, no client secret | Callback on `localhost` or `127.0.0.1` |

The tools load in a new agent session. The script works without them.

## 2. Scope

Ask together:
- **Which database, namespace and tables?** Offer what `list_namespaces` and `list_tables` show. Tables of one model must be in the namespace where the model is stored.
- **New model or an existing one?** `list_semantic_models` shows the existing models. To change one, run `SM show <db> <namespace> <name> > semantic-models/<namespace>/<name>.json` and continue at step 3 with the gaps the user reports.
- **Who will use it, and for what?** For example analysts in a BI tool, a chat assistant or a data share recipient. This decides how much to explain.
- **Model name.** Letters, digits, `-` and `_`; usually the subject, such as `flights` or `orders`.

## 3. Profile the data

Run `SM profile <db> <namespace> <table> [<table> …]`. It prints the row count and, per column, the type, NULL share, distinct values, minimum and maximum, and the values of low-cardinality text columns. Use `describe_table` for table comments and snapshots, and `preview_rows` for sample rows.

Note what the user must explain: candidate keys, columns that look like joins, time columns and their range, NULL shares, sentinel values such as `''`, `'Unknown'` or `'null'`, padded text, codes without labels, and columns that hold personal data.

## 4. Interview

Follow [references/interview.md](references/interview.md). In short:

1. **Questions first.** Ask for 5 to 10 real questions the model must answer, in the user's own words, including at least one about a trend over time and one comparison. For each, ask what a good answer looks like.
2. **Then the meaning behind them**, in rounds: grain and keys, joins, time, business terms and synonyms, metric definitions (formula, denominator, unit, exclusions), missing and odd values, ownership and refresh, sensitivity.
3. **Play it back.** Summarize the answers in a short table (term → definition → column or SQL) and ask the user to correct it before you draft.

Skip what the profile and the user already settled. Stop the interview when every question in the list can be mapped to fields and metrics.

## 5. Draft

1. `SM draft <db> <namespace> <table> [<table> …] --name <name> > semantic-models/<namespace>/<name>.json` writes a skeleton: one dataset per table, one field per column, time columns marked as time dimensions, and `TODO` where meaning is needed.
2. Fill it in from the interview, following [references/model-format.md](references/model-format.md):
   - model `description` with the grain ("One row in PURCHASE per …")
   - every dataset and field `description`; mark the columns people filter or group by as `dimension: {"is_time": false}` and time columns as `{"is_time": true}`
   - `primary_key` per dataset (leave empty only for event logs without a key, and say so in the instructions)
   - `relationships` for every join the questions need
   - `metrics` for every number a question asks for, written with dataset names (`PURCHASE.amount`), each description ending with its unit. Keep them timeless aggregates over one dataset's columns: governed queries refuse subqueries, so "last 7 days" is a filter at question time, not a metric
   - time columns as time dimensions, so questions can group them by day, week or month
   - `ai_context.instructions`: one line each for Time, Grain, Missing values, Owner and Refresh, Classification, then how to answer the user's typical questions and which pitfalls to avoid
3. Write `semantic-models/<namespace>/<name>.questions.sql`: one query per interview question, each after a `-- Q: <question>` line, using the dataset names as tables and the model's metric expressions.

## 6. Check against the quality bar

Run `SM check <db> <namespace> semantic-models/<namespace>/<name>.json --questions semantic-models/<namespace>/<name>.questions.sql`.

It validates the structure and that every field is a real column, then runs, read-only and in UTC, every field and metric, the primary-key and relationship checks, and every question, showing up to 10 result rows each. Fix every `error` and `FAIL` and every `TODO`. Then go through the quality bar in [references/interview.md](references/interview.md#quality-bar); a `warning` is acceptable only when the user agrees.

## 7. Review with the user

Show the user:
- the model summary: datasets, relationships, metrics with their definitions and units, and the instructions
- each question with its answer from the check, so they can judge whether the numbers are right
- for an update, what changed compared with the stored model

Ask for approval or corrections. Return to step 4 or 5 until the user approves. Never publish without approval.

## 8. Publish and verify

1. `SM publish <db> <namespace> semantic-models/<namespace>/<name>.json --name <name> --questions semantic-models/<namespace>/<name>.questions.sql` runs the checks again and creates or replaces the model. If someone changed the model since it was read, it stops; run `show`, merge, and publish again.
   - Through MCP instead: `publish_semantic_model` with the model, after `SM check` passed. To replace a model, pass the `entityVersion` that `describe_semantic_model` returned; a newer version is refused. Show the user the `warnings` it returns.
2. Verify with `describe_semantic_model` (or the portal's **Catalog**, where the **Diagram** tab draws the joins). Its `queryable` part lists, per metric, the dimensions it can be split by.
3. Ask two or three interview questions through `query_semantic_model` (metrics, dimensions with an optional time grain, filters) and compare the answers with the check's. This is how chat agents will answer them.
4. Finish with one line per question the model now answers, and where the work files are.

If publishing answers that semantic models are switched off, an administrator sets `POLARIS_SEMANTIC_MODELS=true` in `.env`. A role error means the user needs the Writer role in the database's team.
