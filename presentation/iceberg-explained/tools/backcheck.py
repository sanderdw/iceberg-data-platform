# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Transcribe every voice clip back with Gemini and compare it with the beat's text.

    uv run tools/backcheck.py            # all beats
    uv run tools/backcheck.py s1b2 s5d1  # only these
    uv run tools/backcheck.py --voice gemini-en   # the English version

Prints the similarity per beat, worst first, and the transcript of anything below 0.93.
"""

import base64
import difflib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tts"))
if "--voice" not in sys.argv:  # these tools check the Gemini voice-over unless told otherwise
    sys.argv += ["--voice", "gemini"]
from gemini import URL, api_key  # noqa: E402
from variant import LANG, cut_file, voice_dir  # noqa: E402

MODEL = "gemini-3.8-flash"
PROMPT = ("Write down literally what is said in this English audio. Only the text, no explanation." if LANG == "en" else
          "Schrijf letterlijk uit wat er in deze Nederlandse audio gezegd wordt. Alleen de tekst, zonder uitleg.")


def texts(node):
    if isinstance(node, dict):
        if node.get("type") == "text" and isinstance(node.get("text"), str):
            yield node["text"]
        for value in node.values():
            yield from texts(value)
    elif isinstance(node, list):
        for value in node:
            yield from texts(value)


def transcribe(path):
    body = {
        "model": MODEL,
        "input": [{"type": "user_input", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "audio", "mime_type": "audio/wav", "data": base64.b64encode(path.read_bytes()).decode()},
        ]}],
    }
    response = httpx.post(URL, json=body, headers={"x-goog-api-key": api_key()}, timeout=180)
    if response.status_code >= 400:
        raise SystemExit(f"{path.name}: {response.status_code} {response.text[:400]}")
    found = [t for t in texts(response.json()) if t.strip() and t != PROMPT]
    return found[-1] if found else ""


def norm(text):
    return re.sub(r"[^\w ]", " ", text.lower()).split()


def main():
    cut = json.loads(cut_file().read_text())
    beats = [b for c in cut["chapters"] for s in c["shots"] for b in s.get("beats", [])]
    wanted = {a for a in sys.argv[1:] if not a.startswith("-") and not a.startswith("gemini")}
    beats = [b for b in beats if not wanted or b["id"] in wanted]

    def check(beat):
        heard = transcribe(voice_dir() / f"{beat['id']}.wav")
        ratio = difflib.SequenceMatcher(None, norm(beat.get("say", beat["text"])), norm(heard)).ratio()
        return beat["id"], ratio, heard

    with ThreadPoolExecutor(6) as pool:
        results = sorted(pool.map(check, beats), key=lambda r: r[1])
    for beat_id, ratio, heard in results:
        print(f"{ratio:.3f}  {beat_id}" + (f"\n       {heard}" if ratio < 0.93 else ""))


if __name__ == "__main__":
    main()
