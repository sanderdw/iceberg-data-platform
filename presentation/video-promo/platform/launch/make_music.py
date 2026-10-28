"""Synthesises the launch video's ambient bed: pad chords, a soft pulse and a plucked arpeggio."""
import math, random, struct, wave

SR, DUR = 44100, 89.0
N = int(SR * DUR)
L, R = [0.0] * N, [0.0] * N
BEAT = 60 / 96

CHORDS = [  # Am9, Fmaj7, Cmaj7, G6
    [110.0, 164.81, 196.0, 246.94, 261.63],
    [87.31, 130.81, 164.81, 220.0, 261.63],
    [65.41, 130.81, 196.0, 246.94, 329.63],
    [98.0, 146.83, 196.0, 246.94, 329.63],
]
BAR2 = 8 * BEAT  # two bars per chord

def add_tone(t0, t1, f, amp, attack, release, detune=0.0025):
    a, b = int(t0 * SR), min(N, int((t1 + release) * SR))
    for ch, d in ((L, 1 - detune), (R, 1 + detune)):
        w = 2 * math.pi * f * d / SR
        for i in range(a, b):
            t = (i - a) / SR
            env = min(1.0, t / attack) * (1.0 if i < t1 * SR else max(0.0, 1 - (i / SR - t1) / release))
            s = math.sin(w * (i - a))
            ch[i] += amp * env * (s + 0.18 * math.sin(2 * w * (i - a)))

# Pad: chord changes every two bars until the end card, then Am9 rings out.
t, k = 0.0, 0
END = 82.0
while t < END:
    t1 = min(t + BAR2, END)
    for f in CHORDS[k % 4]:
        add_tone(t, t1, f, 0.05, 1.6, 1.8)
    t, k = t1, k + 1
for f in CHORDS[0] + [329.63, 440.0]:
    add_tone(END, DUR - 2.5, f, 0.055, 0.6, 2.4)

def add_kick(t0, amp):
    a = int(t0 * SR)
    ph = 0.0
    for i in range(a, min(N, a + int(0.35 * SR))):
        t = (i - a) / SR
        f = 42 + 60 * math.exp(-t * 30)
        ph += 2 * math.pi * f / SR
        v = amp * math.sin(ph) * math.exp(-t * 11)
        L[i] += v; R[i] += v

def add_pluck(t0, f, amp, pan):
    a = int(t0 * SR)
    w = 2 * math.pi * f / SR
    for i in range(a, min(N, a + int(0.9 * SR))):
        t = (i - a) / SR
        v = amp * math.exp(-t * 6) * (math.sin(w * (i - a)) + 0.3 * math.sin(2 * w * (i - a)) * math.exp(-t * 14))
        L[i] += v * (1 - pan); R[i] += v * pan

def add_tick(t0, amp):
    a = int(t0 * SR)
    rnd = random.Random(int(t0 * 1000))
    for i in range(a, min(N, a + int(0.03 * SR))):
        t = (i - a) / SR
        v = amp * rnd.uniform(-1, 1) * math.exp(-t * 180)
        L[i] += v * 0.6; R[i] += v

# Pulse from the problem scene until the end card; accents on section starts.
SECTIONS = [5, 14, 19, 30, 55, 66, 74, 82]
b = 5.0
while b < END - 0.01:
    add_kick(b, 0.22)
    b += BEAT
for s in SECTIONS:
    add_kick(s, 0.32)

# Ticks on off-beats from the stack scene onwards.
b = 19.0 + BEAT / 2
while b < END:
    add_tick(b, 0.05)
    b += BEAT

# Arpeggio during the product tour, agents and install scenes.
b, n = 30.0, 0
while b < END - 0.01:
    chord = CHORDS[int(b // BAR2) % 4]
    notes = [f * 2 for f in chord[1:]]
    if not (66 <= b < 74 and n % 2):
        add_pluck(b, notes[n % len(notes)], 0.045, 0.3 + 0.4 * ((n * 37) % 10) / 10)
    b += BEAT / 2; n += 1

peak = max(max(abs(x) for x in L), max(abs(x) for x in R))
g = 0.85 / peak
with wave.open('public/music.wav', 'wb') as out:
    out.setnchannels(2); out.setsampwidth(2); out.setframerate(SR)
    out.writeframes(b''.join(struct.pack('<hh', int(math.tanh(L[i] * g) * 32767), int(math.tanh(R[i] * g) * 32767)) for i in range(N)))
print('peak', peak)
