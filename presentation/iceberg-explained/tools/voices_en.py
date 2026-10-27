# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""English voice samples: one paragraph per voice, each after a spoken number, in one listening file.

    uv run tools/voices_en.py          # out/voice-samples-en.m4a, and the numbers with their voices
    uv run tools/voices_en.py female   # out/voice-samples-en-female.m4a
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tts"))
import gemini  # noqa: E402

STYLE = ("Speak {accent} English in the first person: a data engineer calmly and directly explaining to colleagues "
         "how something works. Down to earth and relaxed, not a presenter. Keep every sentence at the same pace, "
         "pitch and volume.")
AMERICAN = STYLE.format(accent="neutral American")
BRITISH = STYLE.format(accent="British")

TEXT = ("Apache Iceberg is not a data platform. It is an open-source table format for large datasets. "
        "It describes how files and metadata together form one table, so that different engines can read and write "
        "that table reliably. The data itself sits in Parquet files in object storage. "
        "I work with Claude Code as my agent. It builds a star model with dbt and connects to the platform over MCP.")

SAMPLES = [
    ("Achird", "American", AMERICAN),
    ("Achird", "British", BRITISH),
    ("Charon", "American", AMERICAN),
    ("Iapetus", "American", AMERICAN),
    ("Alnilam", "American", AMERICAN),
    ("Schedar", "American", AMERICAN),
]
FEMALE = [
    ("Kore", "American", AMERICAN),
    ("Sulafat", "American", AMERICAN),
    ("Despina", "American", AMERICAN),
    ("Erinome", "American", AMERICAN),
    ("Gacrux", "American", AMERICAN),
    ("Aoede", "American", AMERICAN),
]
NUMBERS = "One Two Three Four Five Six".split()


def main():
    folder = ROOT / "tts" / "samples-en"
    folder.mkdir(parents=True, exist_ok=True)
    female = "female" in sys.argv[1:]
    parts = []
    for number, (voice, accent, style) in enumerate(FEMALE if female else SAMPLES, 1):
        label = folder / f"label-{number}.wav"
        if not label.exists():
            label.write_bytes(gemini.synthesize(f"{NUMBERS[number - 1]}.", voice="Achird", style=AMERICAN))
        sample = folder / f"{number}-{voice}-{accent}.wav"
        if not sample.exists():
            sample.write_bytes(gemini.synthesize(TEXT, voice=voice, style=style))
        parts += [label, sample]
        print(f"{number}  {voice} ({accent})")
    inputs = [arg for part in parts for arg in ("-i", str(part))]
    pads = ";".join(f"[{i}]aresample=48000,apad=pad_dur={0.4 if i % 2 == 0 else 1.2}[a{i}]" for i in range(len(parts)))
    concat = "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[o]"
    target = ROOT / "out" / ("voice-samples-en-female.m4a" if female else "voice-samples-en.m4a")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", f"{pads};{concat}",
                    "-map", "[o]", "-c:a", "aac", "-b:a", "160k", str(target)], check=True)
    print(target)


if __name__ == "__main__":
    main()
