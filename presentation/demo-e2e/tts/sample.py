# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Two sample lines in the narrator voice, to approve the voice and its settings.

    uv run tts/sample.py      # tts/samples/sample-1.wav, sample-2.wav
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from elevenlabs import synthesize  # noqa: E402

LINES = [
    "Ik laat je zien hoe je van een lege laptop naar een antwoord op je eigen data gaat. "
    "Eén commando installeert het hele platform.",
    "Kort gezegd: de agent doet het werk, maar ik blijf eigenaar. "
    "Hij vraagt mij welke vragen het model moet beantwoorden, en publiceert pas als ik akkoord geef.",
]

out = Path(__file__).resolve().parent / "samples"
out.mkdir(exist_ok=True)
for index, line in enumerate(LINES, 1):
    path = out / f"sample-{index}.wav"
    path.write_bytes(synthesize(line))
    print(path)
