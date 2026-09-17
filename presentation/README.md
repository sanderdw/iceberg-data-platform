# Presentation

reveal.js deck about the platform, with screenshots captured from a running local stack on 2026-09-16.

- `index.html` — the deck. Styled after the [Nothing design skill](https://github.com/dominikmartn/nothing-design-skill): OLED black, Doto for hero moments, Space Grotesk body, Space Mono labels, no shadows or gradients. reveal.js 5.1 loads from cdnjs, so an internet connection is needed the first time.
- `fonts/` — Doto, Space Grotesk and Space Mono with their OFL licenses, copied from `public/fonts/` so the deck matches the portals and renders offline.
- `screenshots/` — PNG captures of the admin portal, user portal, marimo notebooks, RustFS console and Keycloak, taken at 2000×1250 CSS pixels with a 2× device pixel ratio (4000×2500 PNG).

Slides with a single screenshot show it as large as the stage allows. Slides with two screenshots have vertical slides below them (arrow down or swipe) that show each capture full screen; the `↓ full screen` hint at the top right marks those slides. Captions always sit at the bottom of the slide.

Open `index.html` directly in a browser, or serve the folder:

```bash
npx --yes serve presentation
```

Keys: arrows to navigate, `S` for speaker notes, `F` for fullscreen, `ESC` for the overview.

Export to PDF: open `index.html?print-pdf` in Chrome and print to PDF.

The deck is published at https://sanderdw.github.io/iceberg-data-platform/ by the `Pages` workflow on every push that touches this folder. The folder is excluded from source releases.
