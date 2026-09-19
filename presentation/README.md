# Presentation

reveal.js deck about the platform, with screenshots captured from a running local stack on 2026-09-19.

- `index.html` — the deck. Styled after the [Nothing design skill](https://github.com/dominikmartn/nothing-design-skill): OLED black, Doto for hero moments, Space Grotesk body, Space Mono labels, no shadows or gradients. reveal.js 5.1 loads from cdnjs, so an internet connection is needed the first time.
- `fonts/` — Doto, Space Grotesk and Space Mono with their OFL licenses, copied from `public/fonts/` so the deck matches the portals and renders offline.
- `screenshots/` — PNG captures of the admin portal, user portal, the five marimo example notebooks (PyIceberg, DuckDB, Iceberg v3), RustFS console and Keycloak, all 4000×2500 PNG: a 2000×1250 CSS pixel viewport at a 2× device pixel ratio. The access dialog uses 1600×1000 at 2.5× so it fills more of the same image.

Slides with a single screenshot show it as large as the stage allows. Slides with two screenshots have vertical slides below them (arrow down or swipe) that show each capture full screen; the `↓ full screen` hint at the top right marks those slides. Captions always sit at the bottom of the slide.

Open `index.html` directly in a browser, or serve the folder:

```bash
npx --yes serve presentation
```

Keys: arrows to navigate, `S` for speaker notes, `F` for fullscreen, `ESC` for the overview.

## Recapture the screenshots

`scripts/presentation-screenshots.mjs` signs in through Keycloak, seeds the demo story (three teams of a fictional grid operator, five databases, three users with a role per team), runs the five example notebooks as `sander` and writes every PNG in `screenshots/`. It needs both stacks running and the Playwright Chromium (`npx playwright install chromium`).

```bash
npm run presentation:screenshots                 # everything
npm run presentation:screenshots -- --only 03,24 # a few shots, by number
```

Seeding skips what already exists, and the passwords chosen at the forced first sign-in are kept in `.local/presentation/state.json` (git-ignored), so the script can run again on the same stack. Start from empty volumes (`docker compose down -v`, delete that state file) for a bucket listing without leftover files of repeated notebook runs. The infrastructure page keeps fifteen minutes of probe history and scans the buckets every five, so capture it last on a stack that has been up that long: `-- --only 05`.

Export to PDF: open `index.html?print-pdf` in Chrome and print to PDF.

The deck is published at https://sanderdw.github.io/iceberg-data-platform/ by the `Pages` workflow on every push that touches this folder. The folder is excluded from source releases.
