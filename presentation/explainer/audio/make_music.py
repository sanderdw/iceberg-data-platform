# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2.2", "scipy>=1.15"]
# ///
"""Music and sound effects, synthesized with code (no samples, no licences).

    uv run audio/make_music.py

- public/music/music.wav: ambient music per scene (pad, bass, light arpeggio, reverb), as long as
  the video in src/timings.json. Chords change on scene starts. Deterministic (fixed seed).
- public/sfx/{whoosh,click,pop,sparkle,gong}.wav: short effects.
Ducking under the voice happens in the video (src/lib/audio.ts), not here.
"""

import json
import wave
from pathlib import Path

import numpy as np
from scipy import signal

ROOT = Path(__file__).resolve().parent.parent
RATE = 48000
SEED = 20261007
BPM = 84
BAR = 4 * 60 / BPM  # seconds

# Chords per scene as MIDI notes (root first). The mood follows the story: curious, tense, wonder,
# building, gentle, decisive, resolved.
PROGRESSIONS = {
    "s1": [[53, 57, 60, 64], [57, 60, 64, 67], [50, 57, 60, 64], [48, 55, 62, 64]],  # Fmaj7 Am7 Dm9 Cadd9
    "s2": [[57, 60, 64, 67], [53, 57, 60, 64], [48, 55, 60, 64], [55, 59, 62, 67]],  # Am7 Fmaj7 C G
    "s3": [[50, 57, 60, 64], [46, 53, 57, 62], [53, 57, 60, 64], [48, 55, 60, 62]],  # Dm9 Bbmaj7 Fmaj7 Csus2
    "s4": [[48, 55, 60, 64], [47, 55, 62, 67], [45, 55, 60, 64], [53, 57, 60, 64]],  # C G/B Am7 Fmaj7
    "s5": [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]],  # Fmaj7 Em7 Dm7 Cmaj7
    "s6": [[57, 60, 64, 69], [55, 59, 62, 67], [53, 57, 60, 65], [55, 59, 62, 65]],  # Am G F G7
    "s7": [[53, 57, 60, 64], [55, 59, 62, 67], [57, 60, 64, 67], [48, 55, 59, 64]],  # Fmaj7 G Am7 Cmaj7
    "end": [[48, 55, 59, 62, 64]],                                                     # Cmaj9
}


def hz(note):
    return 440.0 * 2 ** ((note - 69) / 12)


def envelope(n, attack, release):
    env = np.ones(n)
    a = min(n, int(attack * RATE))
    r = min(n - a, int(release * RATE))
    if a:
        env[:a] = np.linspace(0, 1, a) ** 2
    if r:
        env[n - r:] *= np.linspace(1, 0, r) ** 2
    return env


def pad(notes, seconds, rng):
    """Warm pad: detuned saw-ish stacks, low-passed, slow attack."""
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    out = np.zeros(n)
    for note in notes:
        for detune in (-0.07, 0.0, 0.06):
            f = hz(note + detune)
            phase = rng.uniform(0, 2 * np.pi)
            # A few harmonics with falling amplitude: a soft saw.
            for k in range(1, 6):
                out += np.sin(2 * np.pi * f * k * t + phase * k) / (k ** 1.6)
    b, a = signal.butter(2, 1400 / (RATE / 2))
    out = signal.lfilter(b, a, out)
    wobble = 1 + 0.08 * np.sin(2 * np.pi * 0.11 * t)
    return out * wobble * envelope(n, 1.2, 1.4) / (len(notes) * 6)


def bass(root, seconds):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    f = hz(root - 12)
    tone = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    # A soft pulse on every bar.
    beat = np.exp(-((t % BAR) / 1.6))
    return tone * (0.55 + 0.45 * beat) * envelope(n, 0.3, 0.8) * 0.35


def pluck(f, seconds, rng):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    mod = np.sin(2 * np.pi * f * 2 * t) * np.exp(-t * 18) * 1.2
    tone = np.sin(2 * np.pi * f * t + mod) * np.exp(-t * 5.5)
    return tone * envelope(n, 0.004, 0.05)


def arpeggio(notes, seconds, rng, density):
    """Light eighth-note arpeggio in the upper octave; the pattern varies per bar but is seeded."""
    n = int(seconds * RATE)
    out = np.zeros(n)
    step = BAR / 8
    upper = [x + 12 for x in notes] + [notes[0] + 24]
    pattern = [0, 2, 1, 3, 2, 4, 1, 3]
    count = int(seconds / step)
    for i in range(count):
        if rng.random() > density:
            continue
        note = upper[pattern[(i + int(rng.integers(0, 2))) % len(pattern)] % len(upper)]
        start = int(i * step * RATE)
        tone = pluck(hz(note), min(1.2, seconds - i * step), rng)
        end = min(n, start + len(tone))
        out[start:end] += tone[: end - start] * (0.6 + 0.4 * rng.random())
    return out * 0.11


def reverb(x, seconds=2.8, mix=0.32, seed=SEED):
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    ir = rng.standard_normal(n) * np.exp(-t * 3.2)
    b, a = signal.butter(1, 5000 / (RATE / 2))
    ir = signal.lfilter(b, a, ir)
    ir /= np.sqrt(np.sum(ir ** 2))
    wet = signal.fftconvolve(x, ir)[: len(x)]
    return (1 - mix) * x + mix * wet * 0.9


def write(path, data, channels=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(data, -1, 1)
    pcm = (data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())


def music(timings):
    rng = np.random.default_rng(SEED)
    total = timings["duration"]
    n = int(total * RATE)
    left = np.zeros(n + RATE * 4)
    right = np.zeros(n + RATE * 4)
    # Sections: each scene from its start (minus the zoom-in), then the end card.
    starts = [max(0.0, s["start"] - 0.6) for s in timings["scenes"]]
    starts[0] = 0.0
    sections = list(zip([s["id"] for s in timings["scenes"]], starts))
    sections.append(("end", timings["endCard"]["start"] - 0.6))
    density = {"s1": 0.45, "s2": 0.7, "s3": 0.55, "s4": 0.8, "s5": 0.5, "s6": 0.85, "s7": 0.7, "end": 0.35}
    for index, (scene, start) in enumerate(sections):
        stop = sections[index + 1][1] if index + 1 < len(sections) else total
        chords = PROGRESSIONS[scene]
        at = start
        k = 0
        while at < stop - 0.05:
            length = min(BAR * (2 if scene in ("s1", "end") else 1), stop - at)
            chord = chords[k % len(chords)]
            overlap = 1.2  # chords cross-fade
            seconds = length + overlap
            layer_pad = pad(chord, seconds, rng)
            layer_bass = bass(chord[0], seconds)
            layer_arp = arpeggio(chord, length, rng, density[scene])
            s = int(at * RATE)
            pan = 0.5 + 0.12 * np.sin(k)
            for buf, gain in ((left, 1 - pan), (right, pan)):
                buf[s : s + len(layer_pad)] += layer_pad * 2 * gain + layer_bass * 0.9
                buf[s : s + len(layer_arp)] += layer_arp * 2 * (1 - gain if k % 2 else gain)
            at += length
            k += 1
    left, right = reverb(left[:n]), reverb(right[:n], seed=SEED + 1)
    # Fade in over 1.5 s and out over the last 4 s.
    fade = np.ones(n)
    fade[: int(1.5 * RATE)] = np.linspace(0, 1, int(1.5 * RATE))
    fade[-int(4 * RATE):] = np.linspace(1, 0, int(4 * RATE)) ** 1.5
    stereo = np.stack([left * fade, right * fade], axis=1)
    stereo /= np.max(np.abs(stereo)) / 0.7  # peak about -3 dBFS
    write(ROOT / "public" / "music" / "music.wav", stereo.reshape(-1), channels=2)


def sfx():
    rng = np.random.default_rng(SEED + 7)
    out = ROOT / "public" / "sfx"

    # Whoosh: band-passed noise sweeping up then down.
    n = int(0.75 * RATE)
    t = np.arange(n) / RATE
    noise = rng.standard_normal(n)
    sweep = np.zeros(n)
    chunk = 512
    for i in range(0, n, chunk):
        centre = 300 + 2200 * np.sin(np.pi * i / n)
        b, a = signal.butter(2, [centre * 0.6 / (RATE / 2), min(0.99, centre * 1.6 / (RATE / 2))], btype="band")
        sweep[i : i + chunk] = signal.lfilter(b, a, noise[max(0, i - 2048) : i + chunk])[-len(noise[i : i + chunk]):]
    whoosh = sweep * np.sin(np.pi * t / t[-1]) ** 2
    write(out / "whoosh.wav", reverb(whoosh / np.max(np.abs(whoosh)) * 0.5, 1.2, 0.25))

    # Click: tiny tick plus a short high blip.
    n = int(0.08 * RATE)
    t = np.arange(n) / RATE
    click = np.sin(2 * np.pi * 2200 * t) * np.exp(-t * 90) + rng.standard_normal(n) * np.exp(-t * 400) * 0.3
    write(out / "click.wav", click / np.max(np.abs(click)) * 0.45)

    # Pop: a round "bloop" with a falling pitch.
    n = int(0.16 * RATE)
    t = np.arange(n) / RATE
    freq = 520 * np.exp(-t * 9) + 180
    pop = np.sin(2 * np.pi * np.cumsum(freq) / RATE) * np.exp(-t * 22)
    write(out / "pop.wav", pop / np.max(np.abs(pop)) * 0.55)

    # Sparkle: a few high pings.
    n = int(0.9 * RATE)
    sparkle = np.zeros(n)
    for i, note in enumerate([88, 91, 95, 100]):
        s = int(i * 0.07 * RATE)
        tone = pluck(hz(note), 0.6, rng) * (0.9 - i * 0.15)
        sparkle[s : s + len(tone)] += tone[: n - s]
    write(out / "sparkle.wav", reverb(sparkle / np.max(np.abs(sparkle)) * 0.35, 1.5, 0.4))

    # Gong: low inharmonic partials with a long decay.
    n = int(3.5 * RATE)
    t = np.arange(n) / RATE
    gong = sum(np.sin(2 * np.pi * 98 * r * t) * np.exp(-t * d) * g
               for r, d, g in ((1, 1.1, 1), (2.76, 1.6, 0.5), (5.4, 2.4, 0.3), (8.93, 3.5, 0.15)))
    gong *= envelope(n, 0.01, 0.5)
    write(out / "gong.wav", reverb(gong / np.max(np.abs(gong)) * 0.5, 2.5, 0.35))


def main():
    timings = json.loads((ROOT / "src" / "timings.json").read_text())
    music(timings)
    sfx()
    print(f"music.wav {timings['duration']:.1f} s and 5 sound effects")


if __name__ == "__main__":
    main()
