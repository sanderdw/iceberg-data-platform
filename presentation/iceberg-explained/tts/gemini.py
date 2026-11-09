"""Gemini TTS over REST: one request per text, returns 24 kHz mono 16-bit WAV bytes."""

import base64
import io
import os
import time
import wave
from pathlib import Path

import httpx

MODEL = "gemini-3.8-flash-tts"
VOICE = "Achird"
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# Calm and steady: a colleague showing their own work, not an announcer.
STYLE = ("Spreek Nederlands zoals in Nederland, in de ik-vorm: een data engineer die collega's rustig en "
         "direct uitlegt hoe iets werkt. Nuchter en ontspannen, geen presentator. Houd elke zin hetzelfde tempo, "
         "dezelfde toonhoogte en hetzelfde volume. Engelse vaktermen zoals Iceberg, Apache, snapshot, manifest, "
         "bucket, engine, multiverse, DuckDB en Snowflake spreek je Engels uit. Het woord Parquet spreek je Frans uit: "
         "par-kè, met de klemtoon op de laatste lettergreep en zonder t aan het eind.")
SETTINGS = {"style": STYLE}
# The English version (--voice gemini-en): Despina in American English, Sander's pick from tools/voices_en.py.
VOICE_EN = "Despina"
STYLE_EN = ("Speak neutral American English in the first person: a data engineer calmly and directly showing "
            "colleagues their own work and explaining how it works. Down to earth and relaxed, not a presenter. "
            "Keep every sentence at the same pace, pitch and volume.")


def api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        env = Path(__file__).resolve().parent.parent / ".env"
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        raise SystemExit("GEMINI_API_KEY is missing (.env)")
    return key


def find_audio(node):
    """The first base64 audio payload anywhere in the response."""
    if isinstance(node, dict):
        mime = str(node.get("mime_type") or node.get("mimeType") or "")
        if isinstance(node.get("data"), str) and (mime.startswith("audio") or node.get("type") == "audio"):
            return base64.b64decode(node["data"]), mime
        for value in node.values():
            found = find_audio(value)
            if found:
                return found
    if isinstance(node, list):
        for value in node:
            found = find_audio(value)
            if found:
                return found
    return None


def synthesize(text, voice=VOICE, previous_text=None, next_text=None, style=STYLE, attempts=4):
    """WAV bytes for one text. Gemini takes no neighbouring text; tts/generate.py passes it for the cache key."""
    body = {
        "model": MODEL,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": text,
            "annotations": [{"type": "speech_metadata", "style": style}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    }
    for attempt in range(attempts):
        response = httpx.post(URL, json=body, headers={"x-goog-api-key": api_key()}, timeout=180)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code >= 400:
            raise SystemExit(f"TTS failed ({response.status_code}): {response.text[:500]}")
        found = find_audio(response.json())
        if not found:
            raise SystemExit(f"No audio in response: {str(response.json())[:500]}")
        data, _ = found
        if data[:4] == b"RIFF":
            return data
        # Raw PCM (audio/l16): wrap it in a WAV header.
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(24000)
            out.writeframes(data)
        return buffer.getvalue()
    raise SystemExit("TTS failed after retries")
