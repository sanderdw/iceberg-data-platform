# Sources

Checked on 2026-10-07. Spoken values are rounded for the ear; the table shows how each follows from
the source. Where a newer figure exists, the newer one is used (METR's 2026 update, Stack Overflow's
2026 survey).

## Claims

| # | Scene | Claim (spoken) | Source | Exact value in the source | From exact to spoken |
| --- | --- | --- | --- | --- | --- |
| 1 | s2b1 | "Nine out of ten technology professionals now use AI at work." | Google Cloud, [Announcing the 2025 DORA report](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report) (2025-09-23); report: [dora.dev/dora-report-2025](https://dora.dev/dora-report-2025/) | "90% of survey respondents report using AI at work"; nearly 5,000 technology professionals surveyed | 90% = nine out of ten |
| 2 | s2b2 | "Almost three quarters of developers use coding assistants or agents every single day." | Stack Overflow, [2026 Developer Survey, AI](https://survey.stackoverflow.co/2026/ai) | "The overwhelming majority of respondents now use coding assistants and agents daily (73%)"; n = 10,926 for that chart | 73% ≈ almost three quarters (75%) |
| 3 | s2b3 | "Only about one in six developers trusts AI with most everyday tasks. About half trust it when they can easily check the answer." | Stack Overflow, [2026 Developer Survey, AI](https://survey.stackoverflow.co/2026/ai) | "48% indicate they trust AI when they can easily validate the answers and only 16% extend that trust to most tasks that are not important work decisions." | 16% ≈ one in six (16.7%); 48% ≈ about half. "Most everyday tasks" stands for "most tasks that are not important work decisions" |
| 4 | s2b4 | "Experienced developers slower with early 2025 tools, and a little faster with later ones." | METR, [Measuring the impact of early-2025 AI on experienced open-source developer productivity](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) (2025-07-10) and [We are changing our developer productivity experiment design](https://metr.org/blog/2026-02-24-uplift-update/) (2026-02-24) | Early 2025: tasks took 19% longer (CI +2% to +39%), 16 developers, 246 tasks. Late 2025: returning developers "speedup of -18%" (CI -38% to +9%), new developers -4% (CI -15% to +9%); 57 developers, 800+ tasks. METR: "only very weak evidence for the size of this increase" | "Slower" for 19% longer; "a little faster" for an estimated 4–18% speedup with wide intervals. No percentage is spoken because METR itself calls the new numbers weak evidence |
| 5 | s2b5, s5b5 | "AI doesn't fix a team. It amplifies what's already there." / "connect AI to your internal context" is one of the capabilities that make AI pay off | Google Cloud, [Announcing the 2025 DORA report](https://cloud.google.com/blog/products/ai-machine-learning/announcing-the-2025-dora-report); [DORA research page](https://research.google/pubs/dora-2025-state-of-ai-assisted-software-development-report/) | "AI doesn't fix a team; it amplifies what's already there." The DORA AI Capabilities Model has seven capabilities that magnify AI's positive impact, among them "Connect AI to your internal context" | Quoted; "capabilities that make AI pay off" paraphrases "magnify the positive impact of AI" |
| 6 | s4b5 | "By the end of 2025 the protocol had moved to the Linux Foundation, with more than ten thousand public servers." | Anthropic, [Donating the Model Context Protocol and establishing the Agentic AI Foundation](https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation) (2025-12-09) | MCP donated to the Agentic AI Foundation, "a directed fund under the Linux Foundation"; "more than 10,000 active public MCP servers"; introduced "one year ago" (late 2024) | "Moved to the Linux Foundation" for the foundation under the Linux Foundation; 10,000+ = more than ten thousand |
| 7 | s5b3 | "More than sixty thousand open-source projects already have an agents file." | [agents.md](https://agents.md/) | "used by over 60k open-source projects"; "now stewarded by the Agentic AI Foundation under the Linux Foundation" | 60k = sixty thousand |
| 8 | s4b1 | "Five principles, and every change is checked against all five." | Iceberg Data Platform, [README, Principles](https://github.com/sanderdw/iceberg-data-platform#principles) and `CONTRIBUTING.md` (working tree, release 0.8.0) | "Five principles behind every choice. Check a change against all five." Clean UX, API first, AI native, Open source, Loosely coupled | Same |
| 9 | s4b2, s4b3 | "Every button is an API call … Every API call is also a tool an agent can use, with the person's own identity." | Iceberg Data Platform, README Principles 2 and 3 | "Every button is a documented route with an OpenAPI schema, so a script can do everything a person can." "Agents are first-class users. Both portals serve MCP with the user's own identity" | Same, shortened |
| 10 | s4b4 | "In the pipelines extension, that's thirty tools. If a tool and its API call drift apart, a test fails." | Iceberg Data Platform, `extensions/dbt/dbt_portal/mcp_server.py` and `extensions/dbt/tests/test_api.py` (working tree, dbt extension 0.1.0) | 30 MCP tools; `test_every_operation_has_an_mcp_tool_and_the_contract_is_current` fails when they diverge | Same |
| 11 | s6b2–s6b4 | Worked example: pipeline runs have no internet on purpose; a separate loading extension behind the same contract; a copyleft library swapped; a newer tool kept as a candidate for a spike | Iceberg Data Platform, `extensions/dbt/README.md` ("no internet") and `docs/proposals/ingestion-extension.md` (working tree, 2026-10-07) | dbt runs: "no capabilities and no internet". The record: separate `ingest` extension behind Bridge contract 0.1; MariaDB via PyMySQL instead of DuckDB's `mysql` extension, which contains MariaDB Connector/C (LGPL-2.1); OLake as a candidate for the spike | Simplified to the steps; "copyleft terms" stands for LGPL-2.1 |
| 12 | s1b1 | "Load this table from Statistics Netherlands, every day" | CBS StatLine table [83140NED](https://opendata.cbs.nl/#/CBS/nl/dataset/83140NED/table) | The request that started the ingestion design | Named only, no figures |

## Illustrative visuals (not measured data)

- The desk, the chat bubbles, the meeting card and the streaming code (s1, s6b1, s7b1).
- Tip and the iceberg proportions. The four underwater layers are the video's framing, not a
  measured split.
- The ten people icons (s2b1) and the calendar strip (s2b2) only picture the percentages.
- The stopwatches (s2b4) show direction only, not the measured percentages.
- The amplifier with the neat and messy structures (s2b5).
- The "price tag" on the tip (s3b4) is a metaphor for cheaper code generation, not a cost figure.
- The server dots around the plug (s4b5) and the 30-chip grid (s4b4) are drawn, not real names.
- The agent arriving each morning with an empty thought bubble (s5b1): a metaphor for sessions
  without memory.
- Library and tool cards in s6 are generic and carry no product names or logos.
