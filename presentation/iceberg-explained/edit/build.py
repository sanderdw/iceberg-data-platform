"""The edit: edit/cut.json plus the recordings and the voice clips become src/edl-<voice>.json, the
voice-over on that timeline and the subtitles.

    python3 edit/build.py                  # src/edl-gemini.json; estimates beats that have no clip yet
    python3 edit/build.py --voice gemini-en   # the English version, from edit/cut-en.json

Rules:
- A shot plays its source range. In a normal shot, stretches where the screen does not change for
  longer than FREEZE_MIN (edit/freezes.json, from edit/freezes.sh) shrink to FREEZE_KEEP: that is
  waiting, not action. Everything else plays at 1x.
- A "fast" shot plays at its rate, unlabelled.
- The voice of a shot starts LEAD after the shot starts. When the voice needs more time than the
  picture, a fast shot slows down and a normal shot gives its waits back, then holds its last frame.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tts"))
from variant import LANG, cut_file, variant_file, voice_dir  # noqa: E402

FPS = 30
RATE = 48000
CARD = 1.8          # chapter card
LEAD = 0.35         # shot start to its first beat
BEAT_GAP = 0.35     # between beats of one shot
TAIL = 0.7          # after the last beat of a shot
FREEZE_MIN = 0.9    # waits longer than this shrink
FREEZE_KEEP = 0.45  # to this
MAX_REAL = 1.6      # fastest unlabelled playback of a normal shot
TITLE_EXTRA = 3.0  # the opening card has no voice, only music
END_EXTRA = 5.0
DIAGRAM_EXTRA = 0.8  # a diagram holds its last state a moment after the voice
MAX_CUE = 84
WRITTEN = {  # spoken forms to write as digits in the subtitles
}
_UNUSED = {
    "achthonderdzesentwintig": "826",
    "honderdzesentwintig": "126",
    "eenenvijftig": "51",
    "vijfendertig": "35",
    "zevenhonderdzes": "706",
    "tweehonderdvierenvijftig": "254",
    "acht voorgestelde": "8 voorgestelde",
    "alle acht": "alle 8",
    "bijna tachtig": "bijna 80",
    "Vier servers": "4 servers",
    "vier MCP-servers": "4 MCP-servers",
    "zes tabellen": "6 tabellen",
}


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def estimate(text):
    return len(text) / 15.0


def shot_pieces(shot, freezes):
    """The shot's pieces at their natural length: (kind, src_from, src_to, rate, out_len, grow)."""
    a, b = shot["from"], shot["to"]
    if shot.get("mode") == "fast":
        return [["video", a, b, shot["rate"], (b - a) / shot["rate"], 0.0]]
    pieces, t = [], a
    for fs, fe in freezes:
        fs, fe = max(fs, a), min(fe, b)
        if fe - fs <= FREEZE_MIN or fs < t:
            continue
        if fs > t:
            pieces.append(["video", t, fs, 1.0, fs - t, 0.0])
        # A wait: shown as a short hold of its first frame, able to grow back to its real length.
        pieces.append(["hold", fs, fs, 0.0, FREEZE_KEEP, fe - fs - FREEZE_KEEP])
        t = fe
    if b > t:
        pieces.append(["video", t, b, 1.0, b - t, 0.0])
    return pieces


def main():
    cut = json.loads(cut_file().read_text())
    freezes = json.loads((ROOT / "edit" / "freezes.json").read_text())
    durations_file = variant_file(ROOT / "tts" / "durations.json")
    durations = json.loads(durations_file.read_text()) if durations_file.exists() else {}
    estimated = []

    def voice(beat):
        if beat["id"] in durations:
            return durations[beat["id"]]
        if beat["id"] not in estimated:
            estimated.append(beat["id"])
        return estimate(beat["text"])

    out, beats, chapters, t = [], [], [], 0.0
    for chapter in cut["chapters"]:
        start = t
        if chapter.get("number"):
            out.append({"kind": "card", "card": "chapter", "number": chapter["number"], "title": chapter["title"],
                        "t": t, "len": CARD})
            t += CARD
        for shot in chapter["shots"]:
            need = 0.0
            if shot.get("beats"):
                need = LEAD + sum(voice(b) for b in shot["beats"]) + BEAT_GAP * (len(shot["beats"]) - 1) + TAIL
            # Voice of the shot, placed from its start.
            at = t + LEAD
            for b in shot.get("beats", []):
                beats.append({"id": b["id"], "t": at, "len": voice(b), "text": b["text"], "shot": shot["id"]})
                at += voice(b) + BEAT_GAP
            if shot.get("card"):
                extra = {"title": TITLE_EXTRA, "end": END_EXTRA}.get(shot["card"], DIAGRAM_EXTRA)
                length = need + extra
                card = {"kind": "card", "card": shot["card"], "t": t, "len": length, "shot": shot["id"]}
                if shot.get("diagram"):
                    card["diagram"] = shot["diagram"]
                out.append(card)
                t += length
                continue
            pieces = shot_pieces(shot, freezes.get(shot["clip"], []))
            natural = sum(p[4] for p in pieces)
            if shot.get("mode") != "fast" and natural > need:
                # Action that outlasts its voice plays a little faster, never so fast that typing looks unreal.
                active = sum(p[4] for p in pieces if p[0] == "video")
                if active > 0:
                    target = max(need, active / shot.get("max", MAX_REAL) + (natural - active))
                    rate = active / (target - (natural - active))
                    for p in pieces:
                        if p[0] == "video":
                            p[3], p[4] = rate, p[4] / rate
                natural = sum(p[4] for p in pieces)
            if need > natural:
                extra = need - natural
                if shot.get("mode") == "fast":
                    p = pieces[0]
                    p[3] = max(1.0, (p[2] - p[1]) / need)
                    p[4] = (p[2] - p[1]) / p[3]
                else:
                    room = sum(p[5] for p in pieces)
                    if room > 0:
                        share = min(1.0, extra / room)
                        for p in pieces:
                            p[4] += p[5] * share
                        extra -= room * share
                    if extra > 1e-3:
                        pieces.append(["hold", shot["to"] - 0.04, shot["to"] - 0.04, 0.0, extra, 0.0])
            for kind, a, b, rate, length, _ in pieces:
                piece = {"kind": kind, "clip": shot["clip"], "src": a, "t": t, "len": length, "shot": shot["id"]}
                if kind == "video":
                    piece["rate"] = rate
                    if shot.get("mode") == "fast":
                        piece["fast"] = True
                out.append(piece)
                t += length
        chapters.append({"id": chapter["id"], "number": chapter.get("number"), "title": chapter.get("title"),
                         "t": start, "len": t - start})

    # Seconds to frames, rounded on the running total so nothing drifts.
    def frames(x):
        return round(x * FPS)

    for item in out + chapters + beats:
        item["from"] = frames(item["t"])
        item["frames"] = frames(item["t"] + item["len"]) - item["from"]
    for piece in out:
        if piece["kind"] in ("video", "hold"):
            piece["startFrom"] = frames(piece["src"])
    total = frames(t)
    voiced = mix_voice(beats, t, estimated)
    edl = {"fps": FPS, "durationFrames": total, "pieces": out, "chapters": chapters, "beats": beats,
           "estimated": estimated, "voice": voiced, "music": variant_file(ROOT / "public" / "music" / "music.wav").exists()}
    if LANG == "en":
        edl["lang"] = "en"
    variant_file(ROOT / "src" / "edl.json").write_text(json.dumps(edl, indent=1) + "\n")
    write_srt(cut["slug"], beats)
    fast = sum(p["len"] for p in out if p.get("fast"))
    print(f"{len(out)} pieces, {t:.1f} s ({t / 60:.1f} min), {len(beats)} beats, {fast:.0f} s sped up"
          + (f", {len(estimated)} beats estimated" if estimated else ""))
    for c in chapters:
        print(f"  {c['id']} {c['title'] or '':20} {c['len']:6.1f} s")


def mix_voice(beats, total, estimated):
    clips = voice_dir()
    ready = [b for b in beats if b["id"] not in estimated and (clips / f"{b['id']}.wav").exists()]
    if not ready:
        return False
    inputs, filters = [], []
    for i, beat in enumerate(ready):
        inputs += ["-i", str(clips / f"{beat['id']}.wav")]
        filters.append(f"[{i}:a]adelay={round(beat['t'] * 1000)}:all=1[d{i}]")
    mix = "".join(f"[d{i}]" for i in range(len(ready)))
    run("ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex",
        ";".join(filters) + f";{mix}amix=inputs={len(ready)}:normalize=0,apad,atrim=0:{total:.3f}[out]",
        "-map", "[out]", "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(clips / "voiceover.wav"))
    return True


def written(text):
    for spoken_form, written_form in WRITTEN.items():
        text = text.replace(spoken_form, written_form)
    return text


def cues(text, start, length):
    """Subtitle cues for one beat: sentences, split at commas when long, timed by their length."""
    parts = []
    for sentence in re.split(r"(?<=[.?!:])\s+", text):
        while len(sentence) > MAX_CUE:
            cut = sentence.rfind(", ", 0, MAX_CUE)
            cut = cut + 1 if cut > 20 else sentence.rfind(" ", 0, MAX_CUE)
            parts.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        parts.append(sentence)
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
        bounds.append(at)
    bounds.append(length)
    return [(start + a, start + b, part) for a, b, part in zip(bounds, bounds[1:], merged)]


def wrap(text, width=42):
    if len(text) <= width:
        return text
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: max(len(" ".join(words[:i])), len(" ".join(words[i:]))))
    return f"{' '.join(words[:best])}\n{' '.join(words[best:])}"


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def write_srt(slug, beats):
    lines = []
    for beat in beats:
        for start, end, text in cues(written(beat["text"]), beat["t"], beat["len"]):
            lines.append(f"{len(lines) + 1}\n{stamp(start)} --> {stamp(end)}\n{wrap(text)}\n")
    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    # The English cut's slug already says what it is (demo-e2e-4k-en); the Dutch files keep their variant name.
    (out / f"{slug}.srt" if LANG == "en" else variant_file(out / f"{slug}.srt")).write_text("\n".join(lines))


if __name__ == "__main__":
    main()
