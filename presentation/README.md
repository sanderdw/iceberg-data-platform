# Presentation

reveal.js deck about the platform, with screenshots captured from a fresh local stack on 2026-10-05; the OpenMetadata captures (58–62) on 2026-10-14.

- `index.html`, the deck. Styled after the [Nothing design skill](https://github.com/dominikmartn/nothing-design-skill): OLED black, Doto for hero moments, Space Grotesk body, Space Mono labels, no shadows or gradients. reveal.js 6.0 loads from cdnjs, so an internet connection is needed the first time.
- `grid/`, the demo data and the agent session: `grid_sensors.py` writes a synthetic SCADA export (Parquet), `load.sql` loads it with the DuckDB CLI, `semantic-models/grid_sensors/` holds the semantic-model skill's work files, and `transcript.md` the session. The deck no longer shows this agent run; the semantic-models promo and the capture script's grid sensor shots still use it.
- `fonts/`, Doto, Space Grotesk and Space Mono with their OFL licenses, copied from `public/fonts/` so the deck matches the portals and renders offline.
- `screenshots/`, PNG captures of the admin portal (including the create user form and the extension service accounts), the Getting started pages of both portals, the user portal (databases, a notebook on the grid sensor data, example notebook 09 writing the MV load forecast with Polars and checking it, catalog, the feeder_load and mv_load_forecast semantic models with their diagrams, AI context and metrics, data shares for another team, an external party and a listing in the data catalog, the receiving team's view), OpenMetadata (the listed data products, their ER diagrams, a table and a metric of each), Conversational BI (enabling it, answers on both models and their SQL), the workspace API docs, RustFS console and Keycloak, all 4000×2500 PNG: a 2000×1250 CSS pixel viewport at a 2× device pixel ratio. The access dialog uses 1600×1000 at 2.5× so it fills more of the same image. The listing form and the OpenMetadata data product (58, 59) share a slide side by side, so they are 4000×4050 from a 1600×1620 viewport at 2.5×: a near-square half that fits the whole form and the data product down to its ER diagram. The two notebook 09 shots (63, 66) and the Polars listing form and data product (72, 73) share slides the same way.

The deck is built for a 30 minute slot for a mixed audience: 25 slides. After the intro, the principles and the one-liner come three parts. **01 How Apache Iceberg works**: a specification rather than a product, the comparison with a PostgreSQL table, and the open source building blocks. **02 The platform**: the architecture, Polaris RBAC per team, team administrators running their own databases, the MCP tools for AI agents, extensions behind the Extension Bridge and how team administrators enable them, and the security model. **03 From raw data to answers**: the demo below. Speaker notes carry the details and sources.

Slides with a single screenshot show it as large as the stage allows. Three slides have full-screen views below them (arrow down or swipe): the model diagram (with its overview and metrics), the OpenMetadata listing (with a table and a metric) and the Conversational BI answer (with its SQL). Slides carry no footer captions; only the full-screen views have a short caption.

The demo follows one data product: a synthetic medium-voltage load forecast of a fictional grid operator. sander runs example notebook 09 in `grid-sensors`: the notebook generates the forecast and its congestion outlook, checks them against the contract `grid_forecast.product.yaml` and writes eight Iceberg tables, and the notebook stores the `mv_load_forecast` semantic model next to them. The deck then shows the model's diagram, AI context and metrics, a data share of the model with outage-response, its listing in OpenMetadata, and noor asking the shared model a question in Conversational BI.

The capture script takes more screenshots than the deck shows (sign-in, users and teams, catalog details, notebooks, external shares and more), so they are at hand when a slide is added back.

Open `index.html` directly in a browser, or serve the folder:

```bash
npx --yes serve presentation
```

Keys: arrows to navigate, `S` for speaker notes, `F` for fullscreen, `ESC` for the overview.

## Recapture the screenshots

`scripts/presentation-screenshots.mjs` signs in through Keycloak and seeds the demo story: three teams of a fictional grid operator, five databases and four users with a role per team. Team administrator `mila` creates a sixth database, `grid-sensors`, in the user portal. When `grid_sensors` is missing there, the script signs `sander` in to `iceberg_connect.py` (the device code is approved in sander's browser session), writes the SCADA export and loads it with the DuckDB CLI, then publishes the `feeder_load` model from `grid/semantic-models` with the skill's script. `mila` shares the model, with the tables it reads, with a research partner and with the team outage-response, and lists it in the data catalog, which the OpenMetadata extension publishes. mila and `noor` (administrator of outage-response) enable Conversational BI, and noor asks it about the shared model. The same steps then run for the deck's demo: sander runs example notebook 09 (63, 66) and opens the `mv_load_forecast` model (67–69), mila shares it with outage-response (70, 71) and lists it (72, 73 with the ER diagram, 74, 75), and noor asks Conversational BI about it (76, 77). The script writes every PNG in `screenshots/`. It needs both stacks, the Conversational BI extension running with a working `LLM_MODEL`, the OpenMetadata extension with its local server (`compose.openmetadata.yaml`, signed in as its default administrator) for captures 58–62 and 72–75, and the Playwright Chromium (`npx playwright install chromium`).

To run the agent session yourself instead, stop after the database with `--seed-only`, load and model the data with your agent and the semantic-model skill, and run the script again: it skips what exists.

```bash
npm run presentation:screenshots                 # everything
npm run presentation:screenshots -- --only 03,24 # a few shots, by number
npm run presentation:screenshots -- --seed-only  # stop once grid-sensors exists
```

Seeding skips what already exists, and the passwords chosen at the forced first sign-in are kept in `.local/presentation/state.json` (git-ignored), so the script can run again on the same stack. sander's `iceberg_connect.py` sign-in is kept in `.local/presentation/config`, not in your own `~/.config`. The credential capture (`33`) shows a real client secret of the local stack; the script replaces that secret right after the data share captures, so the pictured one opens nothing. On a stack that already has the share, `33` comes from its **New secret** action. Start from empty volumes (`docker compose down -v`, delete that state file) for a bucket listing without leftover files of repeated notebook runs. The infrastructure page keeps fifteen minutes of probe history and scans the buckets every five, so capture it last on a stack that has been up that long: `-- --only 05`.

Export to PDF: open `index.html?print-pdf` in Chrome and print to PDF.

## Promo videos

`video-promo/` holds the promo videos. Each one keeps its source next to the rendered MP4.

### Semantic models

`video-promo/semantic-models/` holds a 60 second promo that follows the grid sensor story the deck told before its current demo: a coding agent loads the SCADA export with the DuckDB CLI, the semantic-model skill interviews the team and tests every question, the catalog draws the model, noor asks Conversational BI, grid-planning shares the model, and the Extension Bridge keeps the core small. `iceberg-data-platform-promo.mp4` is the video with sound, and `index.html` plays the same timeline live in the browser (space pauses, arrows skip). The closing slide of the deck links to this video and to the launch video.

The page is rendered, not recorded. Every visual is a function of time, `timeline.json` holds the scene times and sound cues, and `data.json` holds the real numbers: the row counts and the overload answer from `query_semantic_model`, the same question noor asks in captures 55 and 56. The soundtrack is synthesized from the same timeline by `scripts/promo-audio.py`, so the cuts and sounds stay in sync.

```bash
npm run presentation:screenshots -- --only 44,51,52,55   # the diagram, its hover states and the chat answer
npm run promo:render                                     # soundtrack, 1800 frames, MP4 and poster.png
node scripts/promo-render.mjs --stills 12,44             # single frames in .local/promo/stills
```

To swap in a produced music track, replace the audio stream: `ffmpeg -i video-promo/semantic-models/iceberg-data-platform-promo.mp4 -i music.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest out.mp4`.

### Platform launch

`video-promo/platform/launch/` is a 90 second launch video built with [Remotion](https://www.remotion.dev/) (1920×1080, 30 fps). `src/Launch.tsx` holds the scenes and their frame timeline, `public/shots/` and `public/fonts/` are copies of the deck's screenshots and fonts (Remotion only serves files from `public/`), and `out/iceberg-data-platform-launch.mp4` is the rendered video, linked from the closing slide of the deck next to the semantic-models promo. The ambient music bed is synthesized by `make_music.py` into `public/music.wav`, which is git-ignored, so generate it before the first render. Remotion is free for individuals and small companies; larger companies need a [company license](https://www.remotion.dev/license).

```bash
cd video-promo/platform/launch
npm install
python3 make_music.py            # public/music.wav
npm run studio                   # preview and scrub in the browser
npm run render                   # out/iceberg-data-platform-launch.mp4
```

Stills for review go in `stills/`, which is git-ignored.

The deck is published at https://sanderdw.github.io/iceberg-data-platform/presentation/ by the `Pages` workflow, together with the documentation site, on every push to main that touches this folder or the documentation. The folder is excluded from source releases.
