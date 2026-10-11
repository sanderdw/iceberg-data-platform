# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Voice samples: the same script line in several Gemini voices, to choose the narrator.

    uv run tts/voices.py            # writes tts/samples/<voice>.wav and tts/samples/all.wav
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gemini import synthesize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VOICES = ["Sadachbia", "Achird", "Sulafat", "Puck", "Iapetus", "Sadaltager"]
BEAT = "s2b5"


def main():
    script = json.loads((ROOT / "script" / "script.json").read_text())
    text = next(b["text"] for s in script["scenes"] for b in s["beats"] if b["id"] == BEAT)
    out = ROOT / "tts" / "samples"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for voice in VOICES:
        path = out / f"{voice}.wav"
        if not path.exists():
            path.write_bytes(synthesize(text, voice))
        files.append(path)
        print(f"{voice}: {path.relative_to(ROOT)}")
    # One file to listen through: each voice after a short gap, in the order above.
    inputs = []
    for path in files:
        inputs += ["-i", str(path)]
    parts = "".join(f"[{i}:a]aresample=48000,apad=pad_dur=1.2[a{i}];" for i in range(len(files)))
    concat = "".join(f"[a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[out]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", parts + concat,
                    "-map", "[out]", str(out / "all.wav")], check=True)
    print("all:", (out / "all.wav").relative_to(ROOT), "order:", ", ".join(VOICES))


if __name__ == "__main__":
    main()
