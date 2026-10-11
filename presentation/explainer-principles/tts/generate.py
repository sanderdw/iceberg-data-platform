# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Voice-over: one clip per beat, timings for the animation, and subtitles.

    uv run tts/generate.py

- Each beat is synthesized once and cached on a hash of model + voice + style + text, so editing
  one sentence re-records only that sentence (tts/cache/).
- Clips are trimmed of leading and trailing silence and normalised to -16 LUFS / -1.5 dBTP, 48 kHz
  mono (public/voice/<beat>.wav).
- src/timings.json holds the start and duration of every beat and scene, in seconds and frames.
- public/voice/voiceover.wav is the whole voice-over on that timeline; out/<slug>.srt the subtitles.
"""

import hashlib
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini import MODEL, STYLE, synthesize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VOICE = "Sulafat"
FPS = 30
RATE = 48000
LEAD_IN = 1.0      # seconds before the first beat
BEAT_GAP = 0.45    # between beats of one scene
SCENE_GAP = 1.4    # between scenes (room for the zoom transition)
TAIL = 7.0         # after the last beat: time to read the end card
MAX_CUE = 84       # characters per subtitle cue (two lines of 42)
# Subtitles show numbers as they are written, the voice-over as they are spoken.
WRITTEN = {
    "Thirteen releases": "13 releases",
}


def spoken(text):
    """The text without TTS tags, as it appears in subtitles."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def duration(path):
    return float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path).stdout)


def record(beat):
    key = hashlib.sha256(json.dumps([MODEL, VOICE, STYLE, beat["text"]]).encode()).hexdigest()[:16]
    raw = ROOT / "tts" / "cache" / f"{beat['id']}-{key}.wav"
    if not raw.exists():
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(synthesize(beat["text"], VOICE))
        print(f"recorded {beat['id']}")
    out = ROOT / "public" / "voice" / f"{beat['id']}.wav"
    stamp = out.with_suffix(".key")
    if not out.exists() or not stamp.exists() or stamp.read_text() != key:
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


def pauses(path):
    """Midpoints (seconds) of the silences inside a clip."""
    log = run("ffmpeg", "-hide_banner", "-i", str(path), "-af", "silencedetect=noise=-40dB:d=0.2", "-f", "null", "-").stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", log)]
    return [(a + b) / 2 for a, b in zip(starts, ends)]


def cues(text, start, length, gaps=()):
    """Subtitle cues for one beat: sentences (split further at commas when long), timed by length
    and moved to the nearest pause in the clip."""
    parts = []
    for sentence in re.split(r"(?<=[.?!])\s+", text):
        while len(sentence) > MAX_CUE:
            cut = sentence.rfind(", ", 0, MAX_CUE)
            cut = cut + 1 if cut > 20 else sentence.rfind(" ", 0, MAX_CUE)
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        parts.append(sentence)
    # Merge short neighbours so cues do not flash by.
    merged = []
    for part in parts:
        if merged and len(merged[-1]) + 1 + len(part) <= MAX_CUE and len(part) < 30:
            merged[-1] += " " + part
        else:
            merged.append(part)
    total = sum(len(p) for p in merged)
    bounds, at = [0.0], 0.0
    for part in merged[:-1]:
        at += length * len(part) / total
        near = min(gaps, key=lambda g: abs(g - at), default=None)
        bounds.append(near if near is not None and abs(near - at) < 0.9 and near > bounds[-1] + 0.4 else at)
    bounds.append(length)
    return [(start + a, start + b, part) for a, b, part in zip(bounds, bounds[1:], merged)]


def written(text):
    for spoken_form, written_form in WRITTEN.items():
        text = text.replace(spoken_form, written_form)
    return text


def wrap(text, width=42):
    """Two balanced lines: the word break that keeps the longer line shortest."""
    if len(text) <= width:
        return text
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: max(len(" ".join(words[:i])), len(" ".join(words[i:]))))
    return f"{' '.join(words[:best])}\n{' '.join(words[best:])}"


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def main():
    script = json.loads((ROOT / "script" / "script.json").read_text())
    beats = [b for s in script["scenes"] for b in s["beats"]]
    with ThreadPoolExecutor(4) as pool:
        files = dict(zip([b["id"] for b in beats], pool.map(record, beats)))

    at = LEAD_IN
    timings = {"fps": FPS, "voice": VOICE, "model": MODEL, "leadIn": LEAD_IN, "tail": TAIL, "scenes": [], "beats": {}}
    for index, scene in enumerate(script["scenes"]):
        if index:
            at += SCENE_GAP - BEAT_GAP
        scene_start = at
        for beat in scene["beats"]:
            length = duration(files[beat["id"]])
            timings["beats"][beat["id"]] = {
                "scene": scene["id"], "start": round(at, 3), "duration": round(length, 3),
                "startFrame": round(at * FPS), "durationFrames": round(length * FPS), "text": spoken(beat["text"])}
            at += length + BEAT_GAP
        timings["scenes"].append({
            "id": scene["id"], "title": scene["title"], "start": round(scene_start, 3),
            "end": round(at - BEAT_GAP, 3), "startFrame": round(scene_start * FPS),
            "beats": [b["id"] for b in scene["beats"]]})
    voice_end = at - BEAT_GAP
    timings["voiceEnd"] = round(voice_end, 3)
    timings["endCard"] = {"start": round(voice_end + 1.0, 3), "startFrame": round((voice_end + 1.0) * FPS)}
    timings["duration"] = round(voice_end + TAIL, 3)
    timings["durationFrames"] = round((voice_end + TAIL) * FPS)
    # Scenes run until the next one starts; the last one until the end card.
    for scene, following in zip(timings["scenes"], timings["scenes"][1:] + [None]):
        scene["endFrame"] = following["startFrame"] if following else timings["endCard"]["startFrame"]
    (ROOT / "src" / "timings.json").write_text(json.dumps(timings, indent=2) + "\n")

    # The whole voice-over on the same timeline.
    inputs, filters = [], []
    for i, beat in enumerate(beats):
        inputs += ["-i", str(files[beat["id"]])]
        delay = round(timings["beats"][beat["id"]]["start"] * 1000)
        filters.append(f"[{i}:a]adelay={delay}:all=1[d{i}]")
    mix = "".join(f"[d{i}]" for i in range(len(beats)))
    run("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
        ";".join(filters) + f";{mix}amix=inputs={len(beats)}:normalize=0,apad,atrim=0:{timings['duration']}[out]",
        "-map", "[out]", "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(ROOT / "public" / "voice" / "voiceover.wav"))

    lines = []
    for beat in beats:
        info = timings["beats"][beat["id"]]
        for start, end, text in cues(info["text"], info["start"], info["duration"], pauses(files[beat["id"]])):
            lines.append(f"{len(lines) + 1}\n{stamp(start)} --> {stamp(end)}\n{wrap(written(text))}\n")
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    (out / f"{script['slug']}.srt").write_text("\n".join(lines))
    print(f"{len(beats)} beats, {timings['duration']:.1f} s, {len(lines)} subtitle cues")


if __name__ == "__main__":
    main()
