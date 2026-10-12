# Sources

Checked on 2026-10-07 against the Iceberg Data Platform repository (`main` plus the 0.8.0 working
tree). The principles are quoted from the team's engineering culture principles, without naming the
organisation. Spoken values are rounded for the ear; the table shows how each follows from the source.

## The principles (quoted)

| Principle | Tagline (spoken) | Explanation (spoken, shortened) |
| --- | --- | --- |
| AI Native | "AI operates. You architect." | "You put AI to work on the things AI is good at: code, tests, analysis, first-draft docs. You oversee the project and stay accountable for the framing, the trade-offs and everything that goes live." |
| Everything as code | "If it matters, it lives in the repo." | "Docs, deployments and architecture are versioned, reviewed and diffable. Nothing that matters lives in a wiki page, a console click, or somebody's head." |
| Own the decision | "Decide in the open. Own the outcome." | "You write down why, not just what. You build it and you run it. You design for composability and portability, so today's choice never becomes tomorrow's cage." |
| Stay relevant | "The market moves. So do we." | "We own the capability roadmap, not the tool roadmap. Every tool we run has a successor and an exit date we set, not one the market sets for us." |

## Claims about the project

| # | Scene | Claim (spoken) | Source | Exact value in the source | From exact to spoken |
| --- | --- | --- | --- | --- | --- |
| 1 | s1b4 | "An open data platform that I build together with an AI agent." | [README](https://github.com/sanderdw/iceberg-data-platform#readme); the author's way of working | README: "An open lakehouse you actually own." | "Open data platform" for "open lakehouse", for a general audience. **The author confirms the way of working.** |
| 2 | s2b3 | "An AI agent writes most of the code, the tests and the first drafts of the designs." | The author's way of working; for example `docs/proposals/ingestion-extension.md` (git history, commit 96c75f7) and `extensions/dbt/DECISIONS.md`, drafted by an agent on 2026-10-07 | — | **The author confirms "most".** |
| 3 | s2b4 | "Kubernetes now, or only a design? A two-hour token, or a setting?" | Decisions made on 2026-10-07: the Kubernetes record (`extensions/dbt/DECISIONS.md`, "Run jobs on Kubernetes (proposed for dbt 0.2.0)") and `POLARIS_TOKEN_LIFETIME` (`.env.example`, `compose.yaml`) | Kubernetes: "Only a design doc" chosen. Token: "1 hour, configurable" chosen over a fixed two-hour token | The two questions as they were asked |
| 4 | s2b5 | "A release only ships when a person tags it, and only after the full test run passes." | `.github/workflows/release.yml` | Triggered by `tags: ['v*']`; job `checks` runs `ci.yml`, and the release jobs `need` it. Stable releases come only "from vX.Y.Z tags on main" | Same |
| 5 | s3b3 | "The docs sit next to the code. The installer, the deployment and the release pipeline are files in the same repository." | Repository: `README.md`, `docs/`, `install.sh`, `install.ps1`, `compose.yaml`, `.github/workflows/` | — | Same |
| 6 | s3b4 | "If the code and its contract drift apart, a test fails." | `contracts/bridge/COMPATIBILITY.md`; `extensions/dbt/tests/test_api.py` | "`test/test_bridge.py` fails when they differ"; `test_every_operation_has_an_mcp_tool_and_the_contract_is_current` | Same |
| 7 | s3b5 | "The agent's instructions live there too, in an agents file and a set of skills." | `extensions/dbt/AGENTS.md`, `extensions/conversationalbi/AGENTS.md`, `user_portal/client/AGENTS.md`; `.agents/skills/` (demo-company, quick-share, semantic-model) and `extensions/dbt/skills/dbt-platform` | — | Same |
| 8 | s3b5 | "This video: its script, its sources and its music are code." | `presentation/explainer-principles/` (`script/script.json`, `script/BRONNEN.md`, `audio/make_music.py`, `src/`) | — | Same |
| 9 | s4b3 | "Big choices get a decision record: what we chose, what we didn't choose, and why." | `extensions/dbt/DECISIONS.md`; `docs/proposals/ingestion-extension.md` (git history, commit 96c75f7) (sections "Decision" and "Alternatives considered") | — | Summarised |
| 10 | s4b4 | "Pipeline runs get no internet, on purpose. The record says why." | `extensions/dbt/README.md` (Isolation); `docs/proposals/ingestion-extension.md` (git history, commit 96c75f7) ("Why") | "The container has a read-only root, no capabilities and no internet." | Same |
| 11 | s4b5 | "An infrastructure page shows the health of every container, extensions included." | `docs/admin-guide.md` (Infrastructure); `contracts/bridge/v1/network.md` (Extension labels) | "service health, per-container resources…"; "The core monitoring shows these containers on the Infrastructure page without further configuration." | Same |
| 12 | s4b6 | "Iceberg tables, S3 storage, OpenID Connect for sign-in, and MCP for agents. So one part can be replaced without touching the others." | README, principle 5 | "Open interfaces between the components: Iceberg REST, S3, OIDC and MCP, so you can replace one block without touching the others." | "Iceberg tables" for Iceberg REST; "OpenID Connect" for OIDC |
| 13 | s5b3 | "A catalog, storage, sign-in, an engine, and a place to work … each tool can be swapped." | README, introduction | "You need a catalog, object storage, identity, an engine and a place to work … Apache Polaris, Keycloak, RustFS, PostgreSQL, marimo and DuckDB … You can replace one block without touching the others." | PostgreSQL (the catalog's database) is left out for the ear |
| 14 | s5b4 | "Bots check the dependencies every week." | `.github/dependabot.yml` | `interval: weekly` for GitHub Actions, npm, uv and Docker, in the core and both extensions | Same |
| 15 | s5b4 | "Before this release, an agent swept the repository for anything that had gone stale." | The 0.8.0 release preparation on 2026-10-07 | A sweep of versions, pins, wording, links and leftovers; its findings were fixed | Same |
| 16 | s5b4 | "Thirteen releases in just over three weeks." | `CHANGELOG.md` | 13 releases from 0.2.0 (2026-09-15) to 0.8.0 (2026-10-07), 22 days | 22 days = just over three weeks |
| 17 | s5b5 | "Pipeline runs use Docker today, and a Kubernetes design is ready for later." | `extensions/dbt/DECISIONS.md`, "Run jobs on Kubernetes (proposed for dbt 0.2.0)" | "Not implemented. This records how runs can move from the platform's Docker host to a Kubernetes cluster" | "Ready" means designed, not built |
| 18 | s5b6 | "From version one, an old version keeps running next to the new one for at least a release." | `contracts/bridge/COMPATIBILITY.md` | "1.0.0 is the first stable contract. From then on … a major version is served under a new prefix (`/bridge/v2`) alongside the previous one for at least one core minor release." | "At least a release" for "at least one core minor release" |

## Illustrative visuals (not measured data)

- The poster, the Monday desk and the calendar (s1, s6).
- Tip and the four layers; the layers are the video's framing of the principles.
- The question cards (s2b4) show the two decisions, not a screenshot.
- The infrastructure dashboard (s4b5) is drawn, not a screenshot; its rows and bars are generic.
- The capability slots (s5b3) use the real block names; the swap animation is illustrative.
- The broom and the weekly bot (s5b4), and the road with milestones (s5b2, s5b5).
- Tool shapes in the background of s5b1 carry no real logos.
