# Transcript of the recorded run

Recorded on 20 October 2026 in 4K against a fresh install of Iceberg Data Platform 0.9.9 with Conversational BI 0.1.3, dbt 0.1.1 and Ingestion 0.1.1, plus the OpenMetadata extension 0.1.1 with its local OpenMetadata 2.0.5 (`--demo`, installed off camera), on WSL2. The agent is Claude Code 2.1.304 (Opus 5.5) in `~/iceberg-data-platform`, signed in as `sander`, Administrator of the team `analytics`, with auto-memory off for that folder. Everything below really happened. The full session, with timing, is in `recordings/session-*.cast` (asciinema, git-ignored).

## What was typed

| Where | Input |
| --- | --- |
| Terminal | `curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh \| sh` |
| Installer | `y` (Conversational BI), `y` (dbt), `y` (Ingestion), `1` (Google Gemini), Enter (`google:gemini-3.8-flash`), the API key (not echoed) |
| Off camera | `install.sh --demo` of `openmetadata-v0.1.1` |
| Admin portal | Team `analytics` ("Data products and analysis"); Keycloak account `sander` (Sander de Wildt, sander@example.com), analytics · Administrator |
| User portal | Database `multiverse` ("Rick and Morty: characters, locations and episodes"), Development |
| Terminal | `claude mcp add --transport http --client-id <client> --callback-port 3010 <name> <url>` for iceberg-user (`iceberg-mcp`, :3002), iceberg-ingestion (`ext-ingestion-mcp`, :3008) and iceberg-dbt (`ext-dbt-mcp`, :3004); `claude`; `/mcp` → Authenticate, three times |
| Claude Code | "Laad de Rick and Morty API (https://rickandmortyapi.com/api) in mijn database multiverse, in de namespace raw_rickandmorty: de personages, locaties en afleveringen. Laad hem daarna elk uur opnieuw." |
| | Ingestion aanzetten? → "Ja, doe maar." |
| Claude Code | "Maak hier met dbt een stermodel van: een feitentabel met welk personage in welke aflevering speelt, en dimensies voor personage, aflevering en locatie. Zet het daarna op main." |
| | dbt aanzetten? → "Ja, zet aan" |
| Claude Code | "Maak een semantisch model op het stermodel, zodat we er vragen over kunnen stellen in Conversational BI." |
| | `! uv run iceberg_connect.py login` (device code approved in the browser); interview: the 7 suggested questions and definitions; `is_first_appearance` → Nee; store in the dbt project; dbt schedule `15 * * * *` → Ja |
| | Akkoord? → "Akkoord, publiceer het maar." |
| Off camera | Conversational BI: Enable (analytics · development) |
| User portal → Conversational BI | Ask in Conversational BI; "Welke soorten personages treden het vaakst op?" |
| Claude Code | "Publiceer het semantisch model rickandmorty in de datacatalogus: alleen metadata, met analytics@example.com als contact." |
| Off camera | OpenMetadata sign-in (`admin@open-metadata.org`) |

A first take of the ingestion step is not in the video: the agent remembered the previous run from its auto-memory ("Het platform lijkt opnieuw geïnstalleerd"). It deleted the pipeline it had just created, the memory was set aside, auto-memory was turned off for the folder, and the step was recorded again.

## Results

- Ingestion: pipeline `rickandmorty` (REST API, `json_link` on `info.next`, replace), schedule `0 * * * *` (UTC), 4,340 rows in 22 s: `characters` 826, `locations` 126, `episodes` 51, plus the child tables `characters__episode` (1,267), `episodes__characters` (1,266) and `locations__residents` (804), linked to their parent by `_dlt_parent_id`.
- dbt: project `rickandmorty`, staging models in `staging_rickandmorty`, the star model in `marts_rickandmorty` (`fct_character_appearances`, `dim_character`, `dim_episode`, `dim_location`), 8 models and 32 tests (unique and not-null keys, relationships from the fact table to each dimension, accepted values for status and gender, one row per character per episode), merged to main; build schedule `15 * * * *`.
- Semantic model `rickandmorty` in the dbt project (`semantic_models/multiverse/marts_rickandmorty/rickandmorty.yml`), published by the build: datasets APPEARANCE, PERSONAGE, EPISODE, LOCATION; 7 metrics; all 7 questions pass the check and give the same answers through `query_semantic_model`.
- Conversational BI: a horizontal bar chart of appearances per species (Human first, then Alien and Humanoid), with the query card and SQL.
- OpenMetadata: data share `share-2f260ddd…` (listed, no external credential, contact analytics@example.com) with the model and its four tables; data product `rickandmorty` in the domain `Iceberg Data Platform` with the description, the semantic model section, the metrics, an ER diagram of the tables and keys, and 12 assets.

## English run, chapters 4 to 8

Recorded on 22 and 23 October 2026 in 4K for the English version, against a fresh install of the same releases (0.9.9, Conversational BI 0.1.3, dbt 0.1.1, Ingestion 0.1.1, OpenMetadata 0.1.1 `--demo`). Chapters 1 to 3 reuse the Dutch clips; their state (team `analytics`, account `sander`, database `multiverse`, the three MCP servers signed in) was set up off camera. Claude Code 2.1.308 (Opus 5.5). The clips have an `-en` suffix; `edit/cut-en.json` is the cut.

| Where | Input |
| --- | --- |
| Claude Code | "Load the Rick and Morty API (https://rickandmortyapi.com/api) into my database multiverse, in the namespace raw_rickandmorty: the characters, locations and episodes. Then reload it every hour." |
| | Enable Ingestion? → "Enable development (Recommended)" |
| Claude Code | "Build a star model on this with dbt: a fact table with which character appears in which episode, and dimensions for character, episode and location. Then put it on main." |
| | Enable dbt? → "Enable development (Recommended)" |
| Claude Code | "Create a semantic model on the star model, so we can ask questions about it in Conversational BI." |
| | Interview: all four question groups, the suggested metric definitions, origin vs current location, "recent" = latest season; `! uv run iceberg_connect.py login` (device code approved in the browser); Publish → "Publish (Recommended)"; "Yes, schedule main hourly." |
| Off camera | Conversational BI: Enable (analytics · development) |
| User portal → Conversational BI | Ask in Conversational BI; "Which kinds of characters appear most often?" |
| Claude Code | "Publish the semantic model rickandmorty to the data catalog: metadata only, with analytics@example.com as the contact." |

Results: pipeline `rickandmorty`, hourly at `0 * * * *`, 4,340 rows in six tables in `raw_rickandmorty`; dbt project `rickandmorty` with staging in `stg_rickandmorty` and the star model in `rickandmorty` (8 models, 34 tests), built hourly at `15 * * * *`; semantic model `rickandmorty` with 4 metrics (`appearances`, `characters`, `episodes`, `avg_cast_per_episode`), 10 questions checked; a Conversational BI bar chart of appearances per species (Human first, then Alien and Humanoid); data share `share-0fb71e74…`, listed, no external credential, in OpenMetadata as data product `rickandmorty` with 9 assets.
