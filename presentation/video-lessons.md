# Lessons learned: recorded demo videos

What we learned making `demo-e2e/` and `iceberg-explained/` (October 2026), for the next video of this kind: a real run, recorded on screen, with a voice-over, and for an explainer, animated diagrams in between. Read it before planning one. `demo-e2e/README.md` documents the tools; this file is about how to use them well.

## How the work goes

1. **Plan the story and the run together.** The chapters follow what really happens on screen. Agree on persona, team, database and data source up front.
2. **Record everything for real, chapter by chapter.** Each step is a separate clip with markers (`ctx.mark`), so the cut can find moments without guessing.
3. **Write the script after recording**, from what the agent actually said and did. `transcript.md` holds the prompts and results.
4. **Voice samples first**, then the full voice-over, then a check of every clip.
5. **Cut, check stills, render.** Then the review round with Sander, which always brings script changes, so keep the voice and the render cheap to redo.

## Content: what Sander wants in the voice-over

- **Explain, don't count.** No row counts, test counts or answers ("826 personages", "35 tests", "Human 706"). Say what happens and why instead: why 3 API entities become 6 tables, what the dbt tests check and where they come from, what the semantic model check does.
- **No internals that don't matter to the viewer**: branches, merges, diffs, "zero errors".
- **Name alternatives.** Other agents (GitHub Copilot, Codex, any agent with MCP) and other sources (a PostgreSQL table, other APIs). Present a tool as an example, not as the only option.
- **No absolute claims.** "Works with every data catalog" is only true with an extension per catalog.
- **Don't narrate off-camera steps.** If a step isn't worth explaining (enabling an extension, a sign-in), do it off camera. Refer to the docs for what the installer doesn't do (the OpenMetadata extension).
- **No meta talk.** No "everything you see is real, I only shortened the waiting", and no "VERSNELD ×N" labels.
- **Use his wording.** "Definities koppelen aan de data", not "everyone asks questions with the same definitions". The interview "helps think about what a user could ask"; ideally the data producer provides this.
- Sander edits `edit/cut.json` himself. When asked, check the Dutch (compound words, comma splices, `ermee`, typos) and fix clear errors. Propose style changes and leave them for him to decide.
- He reads the copy: in promo text (YouTube description) he is "you" or nobody, never "sander".

## Before recording

- **Start clean.** A fresh `~/iceberg-data-platform` and no `iceberg-platform_*` volumes. Never `down -v` without asking.
- **The agent's memory leaks earlier runs.** Claude Code's auto-memory for the install folder (`~/.claude/projects/-home-<user>-iceberg-data-platform/memory`) made the agent say "the platform seems reinstalled since last time" on camera. Move it aside and set `"autoMemoryEnabled": false` in the folder's `.claude/settings.local.json`.
- **Clean the agent setup** in that folder:
  - Remove stale local-scope MCP entries (`claude mcp remove <name> -s local`).
  - Allow the demo's MCP tools and `Bash(uv run:*)`, so no permission prompts appear.
  - Disable unrelated plugins.
  - Put unrelated connectors (`claude.ai …`, chrome-devtools) in the project's `disabledMcpServers`, because they show up in the `/mcp` menu.
- **Catch sign-in links.** Export `BROWSER=record/open-url.sh` in the demo shell, so OAuth and device-code URLs open in the recorded browser, already signed in to Keycloak.
- **Keys go in without showing.** Read them from env files and send them with `tmux send-keys`. Never echo or print them.
- **Off-camera prep:** extensions that aren't in the installer (OpenMetadata `--demo`, which takes a few minutes and about 4 GB), sign-ins to third-party UIs, and enabling an extension the voice-over doesn't mention.

## Recording

- **4K** is the 1536×864 viewport at device scale 2.5 (3840×2160). It keeps the same layout as 1080p at 1.25. Typing and scrolling come out at 12–22 unique frames per second, limited by the input, not by the capture. Measure with `mpdecimate` on a test clip before the real run.
- **ttyd:** bind with `-i 127.0.0.1` (in WSL `-i lo` binds elsewhere), and use `rendererType=dom` (WebGL drops repaints headless). Never `pkill -f` a pattern that is also in your own command line: it kills your shell. Use pid files.
- **`wait.py` matches per line.** A prompt that wraps over two lines never matches `foo.*\[y/N\]`, so the wait runs to its timeout and the screen sits still on camera. Match one line.
- **Claude Code repaints in place,** so tmux history and the xterm buffer are short. Read what the agent said from its session log (`~/.claude/projects/…/<session>.jsonl`) or with `cast_find.py --dump`. That cast is flushed late, so the last minute may not be in it yet.
- **Rename a failed take's JSON together with its `clip` field.** Otherwise `cast_find.py` maps times to the wrong clip.
- **Combine follow-ups in one prompt** ("… en zet het daarna op main"), so there is no extra turn to film. The agent asks before enabling an extension, sometimes as text and sometimes as a question menu. Answer on camera.
- **Browser pages:**
  - A link with `target=_blank` opens a new page. Catch it with `context.waitForEvent('page')` and start recording it immediately.
  - Some content only appears after a click, such as "Load preview" or "more". Check with a screenshot before recording.
  - Chats scroll to the bottom when the answer arrives, so scroll back to the chart yourself.
  - In OpenMetadata, Playwright called the "more" link not visible: click it by position. Content scrolls inside an inner pane, so measure offsets with the content expanded, and remember the visible part is smaller than the pane.
- **Take screenshots between steps.** Check every step's frame before moving on. A retake now is cheap; one after the cut is not.
- **Notebooks (marimo):**
  - Switch autorun off with a `.marimo.toml` next to the notebook, so opening it on camera runs nothing until you click.
  - marimo restores the last outputs from `/work/__marimo__/session/<notebook>.json`. Delete that file before a take, or the notebook opens with outputs from a previous run.
  - The DuckDB token from `connect_duckdb` expires after about 10 minutes (Polaris answers 401 on the commit). For a long recording, re-attach with a fresh token in a hidden cell; a shipped example doesn't need that.
- **pgAdmin in 0.9.9** shows no server: the installer leaves `pgadmin/servers.json` at mode 600, and pgAdmin runs as uid 5050. Off camera, `chmod 644` it and reload the servers. Fixed in the installer after 0.9.9.

## Voice

- **Gemini TTS** (`gemini-3.8-flash-tts`) with a Dutch style prompt. Sander picked the voice `Achird` from five samples, after Charon, which was the closest in pitch to his own voice. Every video uses Achird now, so the channel has one voice.
- **Record the whole voice-over in one run with one `STYLE`.** Clips recorded later or with a different prompt sound like a different day: tempo and pitch drift.
- **Level with a fixed gain and a limiter**, not linear `loudnorm`. Linear loudnorm stops short of the target on peaky clips, up to 3 dB quieter than the rest (`tts/generate.py`).
- **Check every clip by transcribing it back** with `gemini-3.8-flash`, and redo anything below a similarity of about 0.93. Gemini cannot judge pronunciation, though: it called a Dutch-sounding "agent" English, heard an English sentence as Dutch, and its IPA transcriptions were no better. Sander has to listen.
- **Pronunciation fixes, in this order:**
  1. Reword: "Runs draaien" came back as "Rens draaide" and became "Pipelines draaien".
  2. Respell for the voice only: "agent" is spoken as "eedzjent" (a per-beat `say`), and "Parquet" as "Parkee" everywhere (the `SPOKEN` map in `iceberg-explained/tts/generate.py`). The subtitles keep the real word.
  3. Add a per-beat `style` hint. "Agentic" needed the stress on the second syllable; spelling it out didn't work.

  What didn't work in Gemini: IPA in the text (still "parket"), and "Parqué". Another TTS service that takes IPA would mean a different voice from the other videos.
- **A respelling still varies per take.** Gemini gives a different take every run, so let Sander choose: keep the takes aside as `<beat>-<take>.wav`, build one listening file with `iceberg-explained/tools/review.py` (a spoken number before each clip), and he names the numbers that are wrong. Record those again and repeat. Put the chosen take in `tts/cache` under its key.
- Write numbers out as they are spoken.
- **Re-recording the same text keeps the same cache key.** `generate.py` renormalises when the raw clip is newer. Delete a stale `.key` if in doubt.

## Editing

- `freezedetect` shrinks the waits, but a **blinking caret counts as change**: a wait on a page with a focused text field plays at full length. Cut it by hand by moving the shot's `from`.
- **Render a still at every beat start + 1.5 s for the shots that matter.** What the voice describes must be on screen when it is said. In this video, the Generative UI beat started on the answer text, not on the chart.
- **Check the build for long holds** (the last frame held more than a few seconds). Split a long beat over the shot before it instead.
- **Open with a title card without voice, about 3 seconds** of music, before the first chapter.
- **Render 4K** with `--scale=2` on 1920×1080 compositions. The cards stay sharp and a 9-minute video takes about 30 minutes. Check resolution, codec and loudness (about −16 LUFS) afterwards.

## Diagrams (explainers)

- **A classic diagram with real names beats a clever metaphor.** A tree of catalog → metadata.json → manifest list → manifests → Parquet, with the real file names from the recording, replaced an "archive" metaphor that Sander rejected.
- **One caption, bottom left,** that follows the beat. No extra readouts or counters around the diagram.
- **Straight arrows.** A reference between columns runs through the gap between them, never across a box.
- One red accent per frame (Nothing design); everything else in greys.

## English versions

- **English is a parallel variant, not a replacement.** `--voice gemini-en` reads `edit/cut-en.json` and writes its own files (`tts/variant.py`); the Dutch build stays byte for byte the same. Card and diagram text goes through `t(nl, en)` (`src/lib/i18n.ts`), driven by the EDL's `lang`.
- **The voice is `Despina`** in neutral American English, picked from six female samples (`iceberg-explained/tools/voices_en.py female`) after a set of male ones. The style prompt is gender-neutral ("showing colleagues their own work"). English needs no respellings: "Parquet", "agent" and "dbt" come out right as written.
- **Re-record what shows Dutch on screen.** In the e2e demo, chapters 4 to 8 (prompts, agent answers, the Conversational BI question) were recorded again in English on a fresh install; chapters 1 to 3 and the explainer recordings were already English.
- **A second run is a different run.** In English, the agent asked to enable dbt before writing any model, the semantic model got four metrics instead of seven, and the diagram laid out differently. Write the English script from the English recording, not by translating the Dutch one.
- **The transcriber can mishear a clean take.** One explainer clip scored 0.83 three times on the same words while sounding right. Put it in the listening file instead of re-recording it again and again.

## Deliverables

- The 4K MP4, the SRT and a YouTube title and description, with chapters taken from the build (`src/edl-*.json`, starting at 0:00). No 1080p copy: Sander reviews the 4K file.
- A file sent with SendUserFile can be at most 30 MiB, so a 4K render goes by path.
- Nothing committed unless asked. Leave the stack and the rig running until Sander approves the video, then ask before stopping them.
