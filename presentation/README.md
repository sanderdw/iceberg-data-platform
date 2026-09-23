# Presentation

reveal.js deck about the platform, with screenshots captured from a fresh local stack on 2026-09-23.

- `index.html`, the deck. Styled after the [Nothing design skill](https://github.com/dominikmartn/nothing-design-skill): OLED black, Doto for hero moments, Space Grotesk body, Space Mono labels, no shadows or gradients. reveal.js 5.1 loads from cdnjs, so an internet connection is needed the first time.
- `fonts/`, Doto, Space Grotesk and Space Mono with their OFL licenses, copied from `public/fonts/` so the deck matches the portals and renders offline.
- `screenshots/`, PNG captures of the admin portal, the user portal (databases, catalog, data shares for another team and an external party, the receiving team's view), the workspace API docs, marimo notebooks writing with PyIceberg, reading natively with DuckDB and building an AI-ready flights product, RustFS console and Keycloak, all 4000×2500 PNG: a 2000×1250 CSS pixel viewport at a 2× device pixel ratio. The access dialog uses 1600×1000 at 2.5× so it fills more of the same image.

Slides with a single screenshot show it as large as the stage allows. Slides with two screenshots have vertical slides below them (arrow down or swipe) that show each capture full screen; the `↓ full screen` hint at the top right marks those slides. Captions always sit at the bottom of the slide.

The AI agent slides show the `claude mcp add` commands and tools of both MCP endpoints as text, without a screenshot.

The Iceberg introduction starts with a regular PostgreSQL table, then follows the references from the catalog to the rows and explains how a new batch becomes visible. The storage screenshots are optional vertical slides below the file walkthrough and JSON example; follow the `↓ see the files` and `↓ see the real file` hints. Speaker notes include technical details and sources, using the [Polaris 1.7.0 documentation](https://polaris.apache.org/releases/1.7.0/) for Polaris behavior.

Open `index.html` directly in a browser, or serve the folder:

```bash
npx --yes serve presentation
```

Keys: arrows to navigate, `S` for speaker notes, `F` for fullscreen, `ESC` for the overview.

## Recapture the screenshots

`scripts/presentation-screenshots.mjs` signs in through Keycloak and seeds the demo story: three teams of a fictional grid operator, five databases and four users with a role per team. Team administrator `mila` creates a sixth database, `ai-examples`, in the user portal. `sander` runs example notebooks 01, 03 and 04 in `smart-meter-readings` (04 only creates the Iceberg v3 table that the catalog and bucket captures show) and the flights notebooks 06–07 in `ai-examples`. `mila` then shares one of the resulting tables with a municipality and with the team outage-response, where `noor` reads it without owning a database. The script writes every PNG in `screenshots/`. It needs both stacks running and the Playwright Chromium (`npx playwright install chromium`).

```bash
npm run presentation:screenshots                 # everything
npm run presentation:screenshots -- --only 03,24 # a few shots, by number
```

Seeding skips what already exists, and the passwords chosen at the forced first sign-in are kept in `.local/presentation/state.json` (git-ignored), so the script can run again on the same stack. The credential capture (`33`) shows a real client secret of the local stack; the script replaces that secret right after the data share captures, so the pictured one opens nothing. On a stack that already has the share, `33` comes from its **New secret** action. Start from empty volumes (`docker compose down -v`, delete that state file) for a bucket listing without leftover files of repeated notebook runs. The infrastructure page keeps fifteen minutes of probe history and scans the buckets every five, so capture it last on a stack that has been up that long: `-- --only 05`.

Export to PDF: open `index.html?print-pdf` in Chrome and print to PDF.

The deck is published at https://sanderdw.github.io/iceberg-data-platform/ by the `Pages` workflow on every push that touches this folder. The folder is excluded from source releases.
