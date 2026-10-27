# Van API tot antwoord

An end-to-end demo of about nine and a half minutes, in Dutch, in 4K, with a first-person voice-over in Gemini TTS. It shows a fresh install with `curl … | sh`, the first steps from `docs/first-steps.md`, and then a team administrator working with Claude Code and three MCP servers. The administrator loads https://rickandmortyapi.com every hour with the Ingestion (dlt) extension and has the agent build a star model with dbt and a semantic model with the skill. The administrator then asks Conversational BI a question in the browser and has the agent publish the data product to OpenMetadata.

Everything on screen is a real run, recorded on 20 October 2026. The waits are shortened: a frozen screen shrinks to a short pause, and agent and install time play faster, without a label. `transcript.md` lists what was typed and what came back.

Lessons learned for the next video of this kind are in `../video-lessons.md`.

## Pipeline

| Step | Command | Output |
| --- | --- | --- |
| 1. Record | see below | `recordings/*.mp4` (3840×2160) + `.json` (clip start on the wall clock), `markers.jsonl`, `session-*.cast` |
| 2. Find the waits | `npm run freezes` | `edit/freezes.json` |
| 3. Cut | edit `edit/cut.json` | chapters → shots (clip, source range, `fast` + rate, `max`) → beats (spoken text, optional `say`) |
| 4. Voice, timeline, music | `npm run voice:gemini` | `public/voice/gemini/`, `tts/durations-gemini.json`, `src/edl-gemini.json`, `out/demo-e2e-4k-gemini.srt`, `public/music/music-gemini.wav` |
| 5. Render | `npm run render:gemini` | `out/demo-e2e-4k-gemini.mp4` (3840×2160) |

The compositions are 1920×1080; `--scale=2` renders them at 3840×2160, so the cards are drawn sharp and the 4K clips play at full resolution. `npm run studio` previews it, with one composition per chapter (S1…S8) of the ElevenLabs timeline. Without voice clips, `build` estimates the beats from their length, so the cut can be checked before any synthesis.

How `edit/build.py` times a shot:

1. In a normal shot, waits over 0.9 s (no pixel change) shrink to 0.45 s. Action that outlasts its voice plays up to 1.6× faster, or up to the shot's `max`. A blinking caret counts as a change, so waits on a page with a focused text field have to be cut out by hand.
2. A `fast` shot plays at its rate.
3. When the voice needs more time than the picture, a fast shot slows down. A normal shot gives its waits back first, then holds its last frame.

## Voice

`tts/gemini.py` calls `gemini-3.8-flash-tts` with the voice `Achird`, chosen from five samples, and a Dutch style prompt; the key is `GEMINI_API_KEY` in `.env`, which is git-ignored. `tts/elevenlabs.py` is the narrator's own ElevenLabs voice (`ELEVENLABS_API_KEY`), used by the first version. Every voice-dependent step takes `--voice gemini` and writes its own files next to the ElevenLabs ones (`tts/variant.py`). Clips are cached on model, voice, settings, text and neighbours, so a changed sentence re-records only itself and the two around it.

Each clip was checked by transcribing it back with `gemini-3.8-flash` and comparing it with the script. A word that is read wrongly gets a different wording or a `say`; "Runs draaien" came back as "Rens draaide" and became "Pipelines draaien". "Agent" came out Dutch, so every beat with it has a `say` that spells it "eedzjent" (and "agentic" as "eedzjentik"), and the style prompt asks for the American pronunciation.

## English version

`npm run voice:en` and `npm run render:en` make `out/demo-e2e-4k-en.mp4`, with the voice `Despina` in American English and the cut in `edit/cut-en.json`. Chapters 4 to 8 were recorded again in English on 22 October 2026, as the `recordings/*-en.mp4` clips (see the English run in `transcript.md`); the other chapters use the same clips as the Dutch version. `tools/backcheck.py --voice gemini-en` and `tools/review.py --voice gemini-en` check the clips and make a listening file.

## Recording

`record/` holds the rig that recorded the run:

- **Browser:** `director.mjs` keeps one headless Chromium on 127.0.0.1:7777. `record/step` sends it JS steps (`ctx.page`, `ctx.ui.click/type`, `ctx.rec.start/stop`, `ctx.mark`), and it records a page as a 30 fps 3840×2160 MP4 (1536×864 at 2.5×) from the DevTools screencast, with a visible pointer.
- **Terminal:** `term.sh` serves a tmux session (socket `demo`) through ttyd on :7681, recorded by the director like any page and by asciinema. `type.py` types into it at a human pace, `wait.py` and `idle.py` wait for text or for Claude Code to finish, and `cast_find.py` / `cast_screens.py` replay the cast to find when text appeared.
- **Sign-in:** `BROWSER=record/open-url.sh` sends Claude Code's and `iceberg_connect.py`'s sign-in links to the director, which opens them in the recorded browser (`mcp_auth.sh`).

```bash
record/term.sh &                    # terminal
node record/director.mjs &          # browser and recorder
record/step <<'EOF'
await ctx.page('term', 'http://127.0.0.1:7681/');
await ctx.rec.start('h1-install', 'term');
EOF
python3 record/type.py --enter "curl -fsSL …/install.sh | sh"
```

Passwords chosen during the run are kept in `recordings/state.json` (mode 600). `recordings/` is git-ignored.

Gotchas:

- In WSL, ttyd's `-i lo` binds to 10.255.255.254. Use `-i 127.0.0.1`.
- Headless xterm drops repaints with the WebGL renderer, so use `rendererType=dom`.
- Never `pkill -f` a pattern that also appears in your own command line.
- Old local-scope MCP entries for the install folder must be removed first (`claude mcp remove <name> -s local`).
- The CBI chart of a question grouped by two fields (id and name) draws mostly empty bars, so ask single-dimension questions.
- Claude Code's auto-memory for the install folder remembers earlier runs. Move `~/.claude/projects/-home-<user>-iceberg-data-platform/memory` aside and set `"autoMemoryEnabled": false` in the folder's `.claude/settings.local.json` before recording.
- Claude Code repaints in place, so the tmux history is short. Read what the agent said from its session log in `~/.claude/projects/…/<session>.jsonl`, or replay the cast with `cast_find.py --dump`.
