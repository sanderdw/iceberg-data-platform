"""Gemini TTS over REST: one request per text, returns 24 kHz mono 16-bit PCM samples."""

import base64
import io
import os
import time
import wave
from pathlib import Path

import httpx

MODEL = "gemini-3.8-flash-tts"
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
STYLE = ("A popular-science narrator: curious, warm and lively, with a smile in the voice. "
         "Clear and unhurried, like explaining something fascinating to a friend.")


def api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        env = Path(__file__).resolve().parent.parent / ".env"
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                key = line.split("=", 1)[1].strip()
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


def synthesize(text, voice, style=STYLE, attempts=4):
    """WAV bytes for one text."""
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
        data, mime = found
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
