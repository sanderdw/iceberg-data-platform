# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Voice-over clips: one per beat in edit/cut.json, in the narrator's ElevenLabs voice or in Gemini TTS.

    uv run tts/generate.py                  # every beat, ElevenLabs
    uv run tts/generate.py s1b2 s4b4        # only these (others must be cached already)
    uv run tts/generate.py --voice gemini   # every beat, Gemini (public/voice/gemini/, tts/durations-gemini.json)
    uv run tts/generate.py --voice gemini-en   # the English version, from edit/cut-en.json

- Each beat is synthesized once and cached on a hash of model + voice + settings + text + its
  neighbours (tts/cache/), so editing one sentence re-records only that sentence and the two around it.
- A beat's optional `say` is what is spoken when it differs from its `text` (the subtitle), and its optional
  `style` is added to the Gemini style prompt for that beat only, for example to fix one word's pronunciation.
- Clips are trimmed of leading and trailing silence and normalised to -16 LUFS / -1.5 dBTP, 48 kHz
  mono (public/voice/<beat>.wav). tts/durations.json holds their lengths for edit/build.py.
"""

import hashlib
import importlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from variant import LANG, VARIANT, cut_file, voice_dir, variant_file  # noqa: E402

TTS = importlib.import_module("gemini" if VARIANT.startswith("gemini") else "elevenlabs")
MODEL, SETTINGS, VOICE, synthesize = TTS.MODEL, TTS.SETTINGS, TTS.VOICE, TTS.synthesize
if LANG == "en":
    VOICE, SETTINGS = TTS.VOICE_EN, {"style": TTS.STYLE_EN}
ROOT = Path(__file__).resolve().parent.parent
RATE = 48000


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def duration(path):
    return float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path).stdout)


def beats():
    cut = json.loads(cut_file().read_text())
    return [b for c in cut["chapters"] for s in c["shots"] for b in s.get("beats", [])]


def record(beat, before, after):
    spoken = beat.get("say", beat["text"])
    parts = [MODEL, VOICE, SETTINGS, spoken, before, after] + ([beat["style"]] if "style" in beat else [])
    key = hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]
    raw = ROOT / "tts" / "cache" / f"{beat['id']}-{key}.wav"
    if not raw.exists():
        raw.parent.mkdir(parents=True, exist_ok=True)
        style = SETTINGS["style"] if LANG == "en" else TTS.STYLE
        extra = {"voice": VOICE, "style": style} if LANG == "en" else {}
        if "style" in beat:
            extra["style"] = style + " " + beat["style"]
        raw.write_bytes(synthesize(spoken, previous_text=before, next_text=after, **extra))
        print(f"recorded {beat['id']}")
    out = voice_dir() / f"{beat['id']}.wav"
    stamp = out.with_suffix(".key")
    # A new take of the same text has the same key, so a newer raw clip also counts as changed.
    if not out.exists() or not stamp.exists() or stamp.read_text() != key or raw.stat().st_mtime > out.stat().st_mtime:
        normalise(raw, out)
        stamp.write_text(key)
    return out


def normalise(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    trim = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
            "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse")
    # Two-pass loudnorm: measure, then apply linearly so the voice keeps its dynamics.
    measured = run("ffmpeg", "-hide_banner", "-i", str(source), "-af",
                   f"{trim},loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-").stderr
    stats = json.loads(measured[measured.rindex("{"):measured.rindex("}") + 1])
    loudnorm = ("loudnorm=I=-16:TP=-1.5:LRA=11:linear=true:"
                f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
                f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
                f"offset={stats['target_offset']}")
    run("ffmpeg", "-y", "-loglevel", "error", "-i", str(source), "-af", f"{trim},{loudnorm},aresample={RATE}",
        "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(target))


def main():
    all_beats = beats()
    only = {a for a in sys.argv[1:] if not a.startswith("-") and a != VARIANT}
    jobs = []
    for i, beat in enumerate(all_beats):
        if only and beat["id"] not in only:
            continue
        before = all_beats[i - 1]["text"] if i else None
        after = all_beats[i + 1]["text"] if i + 1 < len(all_beats) else None
        jobs.append((beat, before, after))
    with ThreadPoolExecutor(3) as pool:
        files = dict(zip([b["id"] for b, _, _ in jobs], pool.map(lambda j: record(*j), jobs)))
    path = variant_file(ROOT / "tts" / "durations.json")
    durations = json.loads(path.read_text()) if path.exists() else {}
    durations.update({k: round(duration(v), 3) for k, v in files.items()})
    path.write_text(json.dumps(durations, indent=2) + "\n")
    print(f"{len(files)} clips, {sum(durations.get(b['id'], 0) for b in all_beats):.1f} s of voice")


if __name__ == "__main__":
    main()
