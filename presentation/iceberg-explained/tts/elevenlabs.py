"""ElevenLabs TTS over REST: one request per text, returns 48 kHz mono 16-bit WAV bytes."""

import io
import os
import time
import wave
from pathlib import Path

import httpx

MODEL = "eleven_multilingual_v2"
VOICE = "SgTSLbD1cct1Z3KhCdCJ"
RATE = 48000
URL = f"https://api.elevenlabs.io/v1/text-to-speech/{{voice}}?output_format=pcm_{RATE}"
# Calm and steady: a colleague showing his own work, not an announcer.
SETTINGS = {"stability": 0.55, "similarity_boost": 0.85, "style": 0.1, "use_speaker_boost": True, "speed": 1.0}


def api_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        env = Path(__file__).resolve().parent.parent / ".env"
        for line in env.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        raise SystemExit("ELEVENLABS_API_KEY is missing (.env)")
    return key


def synthesize(text, voice=VOICE, previous_text=None, next_text=None, settings=SETTINGS, attempts=4):
    """WAV bytes for one text; the neighbouring texts keep the intonation continuous."""
    body = {"text": text, "model_id": MODEL, "voice_settings": settings}
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text
    for attempt in range(attempts):
        response = httpx.post(URL.format(voice=voice), json=body, headers={"xi-api-key": api_key()}, timeout=180)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code >= 400:
            raise SystemExit(f"TTS failed ({response.status_code}): {response.text[:500]}")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(response.content)
        return buffer.getvalue()
    raise SystemExit("TTS failed after retries")
