# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""One listening file of voice clips, each after a spoken number, to pick out the ones to record again.

    uv run tools/review.py s1b2 s3b3 ...   # out/review.m4a, and the numbers with their beats
    uv run tools/review.py tts/cache-takes-3/s1b6-a.wav ...   # takes kept aside: <beat>-<take>.wav
    uv run tools/review.py --voice gemini-en s1b2 ...   # the English version

Retake a clip by moving its raw file out of tts/cache/ (it is named <beat>-<key>.wav) and running generate.py again.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tts"))
if "--voice" not in sys.argv:  # these tools check the Gemini voice-over unless told otherwise
    sys.argv += ["--voice", "gemini"]
import gemini  # noqa: E402
from variant import LANG, cut_file, voice_dir  # noqa: E402

NUMBERS = ("een twee drie vier vijf zes zeven acht negen tien elf twaalf dertien veertien vijftien zestien "
           "zeventien achttien negentien twintig").split()
if LANG == "en":
    NUMBERS = ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
               "seventeen eighteen nineteen twenty").split()


def label(number):
    path = ROOT / "tts" / "cache" / ("labels-en" if LANG == "en" else "labels") / f"{number}.wav"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        voice, style = (gemini.VOICE_EN, gemini.STYLE_EN) if LANG == "en" else (gemini.VOICE, gemini.STYLE)
        path.write_bytes(gemini.synthesize(f"{NUMBERS[number - 1].capitalize()}.", voice=voice, style=style))
    return path


def main():
    ids = [a for a in sys.argv[1:] if not a.startswith("-") and not a.startswith("gemini")]
    if not ids or len(ids) > len(NUMBERS):
        raise SystemExit(f"give 1 to {len(NUMBERS)} beat ids")
    cut = json.loads(cut_file().read_text())
    texts = {b["id"]: b["text"] for c in cut["chapters"] for s in c["shots"] for b in s.get("beats", [])}
    parts = []
    for number, arg in enumerate(ids, 1):
        clip = Path(arg) if arg.endswith(".wav") else voice_dir() / f"{arg}.wav"
        beat = clip.stem.split("-")[0]
        parts += [label(number), clip]
        print(f"{number:2}  {clip.stem:8} {texts[beat]}")
    inputs = [arg for part in parts for arg in ("-i", str(part))]
    pads = ";".join(f"[{i}]aresample=48000,apad=pad_dur={0.4 if i % 2 == 0 else 1.2}[a{i}]" for i in range(len(parts)))
    concat = "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[o]"
    target = ROOT / "out" / ("review-en.m4a" if LANG == "en" else "review.m4a")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", f"{pads};{concat}",
                    "-map", "[o]", "-c:a", "aac", "-b:a", "160k", str(target)], check=True)
    print(target)


if __name__ == "__main__":
    main()
