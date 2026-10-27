# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.2", "scipy>=1.15"]
# ///
"""A quiet music bed, synthesized with code (no samples, no licences).

    uv run audio/make_music.py                  # public/music/music.wav, as long as src/edl.json
    uv run audio/make_music.py --voice gemini   # public/music/music-gemini.wav, as long as src/edl-gemini.json

Soft pad chords that change with each chapter, a slow pulse of low notes and a long reverb tail.
It stays in the background: the video ducks it under the voice (src/lib/audio.ts). Deterministic.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tts"))
from variant import variant_file  # noqa: E402
RATE = 48000
SEED = 20261020
# One chord per chapter (MIDI notes), calm and slightly open: Fmaj9 Am7 Cmaj7 Dm9 Fmaj7 G6 Am9 Cadd9 Fmaj9.
CHORDS = [[53, 60, 64, 67], [57, 60, 64, 67], [48, 55, 59, 64], [50, 57, 60, 64], [53, 57, 60, 64],
          [55, 59, 62, 64], [57, 60, 64, 71], [48, 55, 62, 64], [53, 60, 64, 67]]


def hz(note):
    return 440.0 * 2 ** ((note - 69) / 12)


def pad(chord, seconds, rng):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros(n)
    for note in chord:
        for detune in (-0.06, 0.0, 0.07):
            f = hz(note) * 2 ** (detune / 12)
            phase = rng.uniform(0, 2 * np.pi)
            out += np.sin(2 * np.pi * f * t + phase) + 0.25 * np.sin(4 * np.pi * f * t + phase)
    # Slow swell so chord changes breathe instead of cut.
    fade = min(n // 2, int(2.5 * RATE))
    env = np.ones(n)
    env[:fade] = np.linspace(0, 1, fade) ** 1.5
    env[-fade:] = np.linspace(1, 0, fade) ** 1.5
    return out * env / (len(chord) * 3)


def pulse(chord, seconds):
    """A soft low note every two seconds, the root of the chord."""
    n = int(seconds * RATE)
    out = np.zeros(n)
    f = hz(chord[0] - 12)
    length = int(1.8 * RATE)
    t = np.arange(length) / RATE
    note = np.sin(2 * np.pi * f * t) * np.exp(-t * 2.2) * (1 - np.exp(-t * 60))
    for start in range(0, n - length, 2 * RATE):
        out[start:start + length] += note
    return out * 0.35


def reverb(x, rng):
    tail = int(3.0 * RATE)
    impulse = rng.standard_normal(tail) * np.exp(-np.arange(tail) / RATE * 2.0)
    wet = signal.fftconvolve(x, impulse)[: len(x)]
    return 0.7 * x + 0.3 * wet / np.max(np.abs(wet) + 1e-9) * np.max(np.abs(x))


def main():
    edl = json.loads(variant_file(ROOT / "src" / "edl.json").read_text())
    fps, total = edl["fps"], edl["durationFrames"] / edl["fps"]
    rng = np.random.default_rng(SEED)
    music = np.zeros(int(total * RATE) + RATE)
    for i, chapter in enumerate(edl["chapters"]):
        start = chapter["from"] / fps
        # Overlap into the next chapter so the swells cross-fade.
        length = chapter["frames"] / fps + 2.5
        chord = CHORDS[i % len(CHORDS)]
        part = pad(chord, length, rng) + pulse(chord, length)
        a = int(start * RATE)
        music[a:a + len(part)] += part[: len(music) - a]
    music = music[: int(total * RATE)]
    sos = signal.butter(4, 2400, "low", fs=RATE, output="sos")
    music = reverb(signal.sosfilt(sos, music), rng)
    music = music / np.max(np.abs(music)) * 0.5
    stereo = np.stack([music, np.roll(music, 240)], axis=1)
    out = variant_file(ROOT / "public" / "music" / "music.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((stereo * 32767).astype(np.int16).tobytes())
    print(f"{out.relative_to(ROOT)}: {total:.1f} s")


if __name__ == "__main__":
    main()
