# Principles You Can See

A five-minute animated explainer for building teams: four engineering principles (AI Native,
Everything as code, Own the decision, Stay relevant), each explained and then shown on one real
project, the Iceberg Data Platform. Tip, the small iceberg from "The Part Under the Water"
(`../explainer`), is the guide; the four principles are the layers under the water. The video says
"our principles" and names no organisation.

Everything is built as code: Remotion for the animation, Gemini TTS for the voice-over, and
numpy/scipy for the music and sound effects. No samples, no stock footage, no licensed music.

## Build

Needs Node, `uv` and `ffmpeg` (all via Homebrew), and a Gemini API key in `.env`:

```bash
echo "GEMINI_API_KEY=…" > .env
npm install
npm run voice     # uv run tts/generate.py: voice clips, src/timings.json, out/principles-you-can-see.srt
npm run music     # uv run audio/make_music.py: public/music/music.wav and public/sfx/*.wav
npm run studio    # preview in the browser; one composition per scene (S1…S6, EndCard)
npm run render    # out/principles-you-can-see.mp4 (1080p)
npm run render:4k # out/principles-you-can-see-4k.mp4 (--scale=2; 1080p takes about 40 minutes here, 4K several times that)
node scripts/stills.mjs        # stills at the middle of every beat and a contact sheet per scene
node scripts/stills.mjs s3 end # only some scenes
```

Run `npm run music` after `npm run voice` whenever the length changes: the music is sized from
`src/timings.json`.

## Change a sentence and re-render

1. Edit the beat's `text` in `script/script.json`. Spell numbers as they are spoken; `<short pause>`
   and `<long pause>` are Gemini TTS tags and are left out of the subtitles.
2. `npm run voice`. Only the changed beat is synthesized again (cache in `tts/cache/`, keyed on
   model, voice, style and text). Timings, the full voice-over and the subtitles are rewritten.
3. `npm run music`, so the music fits the new length.
4. If the animation of that beat refers to a phrase (`t.at("s3b4", "AI made")`), keep that phrase in
   the new text, or change the scene file; a missing phrase throws a clear error.
5. `node scripts/stills.mjs <scene>` to check, then `npm run render`.

Every animation is keyed to beat start frames or to phrases in `timings.json`, never to
hard-coded seconds, so the picture follows the voice when a sentence changes.

## Structure

| Path | What it is |
| --- | --- |
| `script/script.json` | Scenes and beats: id, spoken text, visual note; end card text |
| `script/BRONNEN.md` | Sources: per claim the source, the exact value and how the spoken value follows |
| `tts/gemini.py` | Gemini TTS over REST (`gemini-3.8-flash-tts`) with the narrator style prompt |
| `tts/voices.py` | Voice samples to choose a narrator (`tts/samples/`) |
| `tts/generate.py` | Voice clips (trimmed, −16 LUFS / −1.5 dBTP, 48 kHz), `src/timings.json`, the `.srt` |
| `audio/make_music.py` | Deterministic ambient music per scene (pad, bass, arpeggio, reverb) and five sound effects |
| `src/lib/theme.ts` | Palette tokens, font, type scale |
| `src/lib/time.ts` | Timing helpers: `sceneTimes("s3").b.s3b4`, `.at(beat, phrase)`, scene windows |
| `src/lib/anim.ts` | Springy pop-ins, fades, counters |
| `src/lib/audio.ts` | Music ducking curve per frame, and the sound-effect cues |
| `src/components/` | Tip (the mascot), the iceberg world, the agent and people, the desk, icons, UI primitives |
| `src/components/Principle.tsx` | A principle's title card that docks as a header, Tip in the corner, item cards |
| `src/scenes/SceneN.tsx` | One file per scene; `EndCard.tsx` |
| `src/Explainer.tsx` | Places scenes, voice-over, music and sound effects on the timeline |
| `scripts/stills.mjs` | Contact sheets for review |
| `public/fonts/` | Nunito (SIL Open Font License) |

Generated and ignored by git: `public/voice/`, `public/music/`, `public/sfx/`, `tts/cache/`,
`tts/samples/`, `out/`, `node_modules/`, `.env`.

## Settings

- Voice: Gemini `Sulafat`, style "a popular-science narrator: curious, warm and lively".
- 1920×1080 at 30 fps, all vector; `--scale=2` gives a sharp 4K render.
- Lead-in 1 s, 0.45 s between beats, 1.4 s between scenes, 7 s tail for the end card.
- Music under the voice at 0.11, in the gaps at 0.34, faded in over 1 s and out over 4 s.
