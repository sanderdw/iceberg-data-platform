"""Synthesizes the promo video's soundtrack from presentation/promo/timeline.json.

A quiet A-minor pad, a low pulse once the demo starts, blips for the dot-matrix type, key clicks while
text types in, a tick on every cut, and four rising notes that resolve on the end card. Deterministic: the same
timeline always gives the same file.

Usage: uv run --group data python scripts/promo-audio.py [out.wav]
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np

RATE = 48_000
TIMELINE = json.loads(Path("presentation/promo/timeline.json").read_text())
DURATION = TIMELINE["duration"]
BEAT = 60 / TIMELINE["bpm"]
rng = np.random.default_rng(6)
mix = np.zeros((2, int(DURATION * RATE)))


def note(name):
    """Frequency of a note such as A2 or C#3, with A4 at 440 Hz."""
    steps = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
    semitone = steps[name[0]] + (1 if "#" in name else 0) + 12 * (int(name[-1]) - 4)
    return 440 * 2 ** (semitone / 12)


def add(start, signal, gain=1.0, pan=0.0):
    """Mixes a mono signal in at `start` seconds; pan runs from -1 (left) to 1 (right)."""
    at = int(start * RATE)
    end = min(len(mix[0]), at + len(signal))
    if at >= end:
        return
    part = signal[: end - at] * gain
    mix[0, at:end] += part * np.sqrt((1 - pan) / 2)
    mix[1, at:end] += part * np.sqrt((1 + pan) / 2)


def seconds(length):
    return np.arange(int(length * RATE)) / RATE


def pad(at, freqs, length, gain):
    """A soft chord: slightly detuned sines with a slow attack and release."""
    t = seconds(length)
    env = np.minimum(1, t / 0.9) * np.minimum(1, (length - t) / 1.1)
    for i, f in enumerate(freqs):
        pan = (i / max(1, len(freqs) - 1)) * 1.2 - 0.6
        tone = (
            np.sin(2 * np.pi * f * t)
            + 0.5 * np.sin(2 * np.pi * f * 1.004 * t)
            + 0.18 * np.sin(4 * np.pi * f * t)
        )
        add(at, tone * env * gain / len(freqs), pan=pan)


def kick(gain):
    t = seconds(0.35)
    phase = 2 * np.pi * np.cumsum(46 + 70 * np.exp(-t * 28)) / RATE
    return np.sin(phase) * np.exp(-t * 9) * gain


def noise_burst(length, decay):
    t = seconds(length)
    return np.diff(rng.standard_normal(len(t) + 1)) * np.exp(-t * decay)


def blip(freq):
    t = seconds(0.16)
    return (np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(2 * np.pi * freq * 1.5 * t)) * np.exp(-t * 32)


scenes = TIMELINE["scenes"]
cues = TIMELINE["cues"]
end_card = cues["resolve"]
demo_start = next(s["start"] for s in scenes if s["id"] == "notebook")

# Pad: Am, F, C, G, two bars each, until the end card; quieter under the hook and the problem.
progression = [
    ["A2", "C3", "E3", "A3"],
    ["F2", "A2", "C3", "F3"],
    ["C3", "E3", "G3", "C4"],
    ["G2", "B2", "D3", "G3"],
]
bars = 2 * 4 * BEAT
chord = 0
start = 0.0
while start < end_card:
    length = min(bars + 1.0, end_card - start + 0.6)
    pad(start, [note(n) for n in progression[chord % 4]], length, 0.22 if start < demo_start else 0.3)
    start += bars
    chord += 1

# Pulse on the beat from the first demo scene until the end card, with a quiet off-beat tick.
evidence = next(s["start"] for s in scenes if s["id"] == "evidence")
beat = demo_start
while beat < end_card - 1e-6:
    add(beat, kick(0.4))
    if beat >= evidence:
        add(beat + BEAT / 2, noise_burst(0.05, 70), 0.05, pan=0.3)
    beat += BEAT

# A tick on every cut.
for scene in scenes[1:]:
    t = seconds(0.06)
    add(scene["start"], noise_burst(0.06, 180) * 0.6 + np.sin(2 * np.pi * 2400 * t) * np.exp(-t * 90), 0.25)

# Blips where the dot-matrix type appears.
for i, at in enumerate(cues["blips"]):
    add(at, blip(note("A6") if i % 2 == 0 else note("E6")), 0.22, pan=-0.3 + 0.2 * i)

# Key clicks while text types in, with a slightly irregular rhythm.
for window in cues["typing"]:
    at = window["start"]
    while at < window["end"]:
        add(at, noise_burst(0.012, 400), 0.09, pan=float(rng.uniform(-0.4, 0.4)))
        at += float(rng.uniform(0.045, 0.085))

# Lead-in: four plucked eighth notes climb into the end card's chord.
for i, name in enumerate(["E5", "G5", "A5", "C6"]):
    t = seconds(0.5)
    f = note(name)
    pluck = (
        (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t))
        * np.exp(-t * 7)
        * np.minimum(1, t / 0.004)
    )
    add(cues["lead_in"] + i * BEAT / 2, pluck, 0.1 + 0.02 * i, pan=-0.3 + 0.2 * i)

# Resolve: a low hit and an open A-minor chord that rings out.
t = seconds(DURATION - end_card)
add(end_card, kick(0.9))
add(end_card, np.sin(2 * np.pi * note("A1") * t) * np.exp(-t * 0.9), 0.35)
for i, name in enumerate(["A2", "E3", "A3", "B3", "C4", "E4"]):
    f = note(name)
    tone = np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * f * 1.003 * t)
    add(end_card, tone * np.exp(-t * 0.45) * np.minimum(1, t / 0.02), 0.07, pan=-0.5 + 0.2 * i)

# Master: soft clip, fade out over the last second, peaks at -1 dBFS.
out = np.tanh(mix * 1.2)
fade = np.ones(out.shape[1])
fade[-RATE:] = np.linspace(1, 0, RATE)
out *= fade
out *= 10 ** (-1 / 20) / np.max(np.abs(out))

path = Path(sys.argv[1] if len(sys.argv) > 1 else ".local/promo/soundtrack.wav")
path.parent.mkdir(parents=True, exist_ok=True)
with wave.open(str(path), "wb") as wav:
    wav.setnchannels(2)
    wav.setsampwidth(2)
    wav.setframerate(RATE)
    wav.writeframes((out.T * 32767).astype("<i2").tobytes())
rms = 20 * np.log10(np.sqrt(np.mean(out**2)))
print(f"{path} · {DURATION} s · RMS {rms:.1f} dBFS")
