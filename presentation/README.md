# Presentation

reveal.js deck about the platform, with screenshots captured from a fresh local stack on 2026-09-30.

- `index.html`, the deck. Styled after the [Nothing design skill](https://github.com/dominikmartn/nothing-design-skill): OLED black, Doto for hero moments, Space Grotesk body, Space Mono labels, no shadows or gradients. reveal.js 6.0 loads from cdnjs, so an internet connection is needed the first time.
- `fonts/`, Doto, Space Grotesk and Space Mono with their OFL licenses, copied from `public/fonts/` so the deck matches the portals and renders offline.
- `screenshots/`, PNG captures of the admin portal (including the create user form), the Getting started pages of both portals, the user portal (databases, the flights notebook, catalog, the flights semantic model and its diagram, data shares for another team and an external party, the receiving team's view), the workspace API docs, RustFS console and Keycloak, all 4000×2500 PNG: a 2000×1250 CSS pixel viewport at a 2× device pixel ratio. The access dialog uses 1600×1000 at 2.5× so it fills more of the same image.

Slides with a single screenshot show it as large as the stage allows. Slides with two screenshots have vertical slides below them (arrow down or swipe) that show each capture full screen; the `↓ full screen` hint at the top right marks those slides. Captions always sit at the bottom of the slide.

After the intro, one slide sets out the principles (clean UX, API first, AI native, open source, loosely coupled), followed by the first use: the installer output, the Getting started pages, connecting an MCP client and the bundled agent skills. The installer, MCP and skill slides are text; the skill transcript is illustrative. The AI agent slides in the demo show the tools of both MCP endpoints as text, without a screenshot.

The demo follows one data product. A notebook publishes synthetic flights as six Iceberg tables, checks them and stores their semantic model next to them. The deck then shows the metadata an AI needs, the model's diagram in the catalog, how an agent uses it, and a data share of the model with another team and a research partner.

The Iceberg introduction starts with a regular PostgreSQL table, then follows the references from the catalog to the flights table's rows and explains how a new batch becomes visible. The storage screenshots are optional vertical slides below the file walkthrough and JSON example; follow the `↓ see the files` and `↓ see the real file` hints. Speaker notes include technical details and sources, using the [Polaris 1.8.0 documentation](https://polaris.apache.org/releases/1.8.0/) for Polaris behavior.

Open `index.html` directly in a browser, or serve the folder:

```bash
npx --yes serve presentation
```

Keys: arrows to navigate, `S` for speaker notes, `F` for fullscreen, `ESC` for the overview.

## Recapture the screenshots

`scripts/presentation-screenshots.mjs` signs in through Keycloak and seeds the demo story: three teams of a fictional grid operator, five databases and four users with a role per team. Team administrator `mila` creates a sixth database, `ai-examples`, in the user portal. `sander` runs notebook **06 · Write an AI-ready flights product** there: it publishes six tables in `ai_flights` and the `flights` semantic model that the catalog, diagram and bucket captures show. `mila` then shares the model, with the tables it reads, with a research partner and with the team outage-response, where `noor` reads it without owning a database. The script writes every PNG in `screenshots/`. It needs both stacks running and the Playwright Chromium (`npx playwright install chromium`).

```bash
npm run presentation:screenshots                 # everything
npm run presentation:screenshots -- --only 03,24 # a few shots, by number
```

Seeding skips what already exists, and the passwords chosen at the forced first sign-in are kept in `.local/presentation/state.json` (git-ignored), so the script can run again on the same stack. The credential capture (`33`) shows a real client secret of the local stack; the script replaces that secret right after the data share captures, so the pictured one opens nothing. On a stack that already has the share, `33` comes from its **New secret** action. Start from empty volumes (`docker compose down -v`, delete that state file) for a bucket listing without leftover files of repeated notebook runs. The infrastructure page keeps fifteen minutes of probe history and scans the buckets every five, so capture it last on a stack that has been up that long: `-- --only 05`.

Export to PDF: open `index.html?print-pdf` in Chrome and print to PDF.

## Promo video

`promo/` holds a 60 second promo about AI-ready data products: the flights notebook, its quality checks, the semantic model and its diagram, an agent answering with the agreed metric, and a data share of the model. `promo/iceberg-data-platform-promo.mp4` is the video with sound, and `promo/index.html` plays the same timeline live in the browser (space pauses, arrows skip). The closing slide of the deck links to the video.

The page is rendered, not recorded. Every visual is a function of time, `timeline.json` holds the scene times and sound cues, and `data.json` holds the carrier numbers from notebook 07, so the agent scene shows real results. The soundtrack is synthesized from the same timeline by `scripts/promo-audio.py`, so the cuts and sounds stay in sync.

```bash
npm run presentation:screenshots -- --only 51,52,53   # diagram hover states, notebook 07 and data.json
npm run promo:render                                 # soundtrack, 1800 frames, MP4 and poster.png
node scripts/promo-render.mjs --stills 12,44         # single frames in .local/promo/stills
```

To swap in a produced music track, replace the audio stream: `ffmpeg -i promo/iceberg-data-platform-promo.mp4 -i music.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest out.mp4`.

The deck is published at https://sanderdw.github.io/iceberg-data-platform/ by the `Pages` workflow on every push that touches this folder. The folder is excluded from source releases.
