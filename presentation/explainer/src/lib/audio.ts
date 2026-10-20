import { beatFrame, beats, FPS, phraseFrame, scenes, TOTAL, visualStart } from "./time";

// Music volume per frame: lower under the voice, higher in the gaps, faded in and out.
const OPEN = 0.34; // no voice
const UNDER = 0.11; // under the voice
const RAMP = 10; // frames

const curve: number[] = (() => {
  const level = new Array<number>(TOTAL).fill(OPEN);
  for (const beat of Object.values(beats)) {
    const from = beat.startFrame - 4;
    const to = beat.startFrame + beat.durationFrames + 4;
    for (let f = Math.max(0, from - RAMP); f < Math.min(TOTAL, to + RAMP); f++) {
      const into = f < from ? (f - (from - RAMP)) / RAMP : f > to ? 1 - (f - to) / RAMP : 1;
      const v = OPEN + (UNDER - OPEN) * Math.max(0, Math.min(1, into));
      level[f] = Math.min(level[f], v);
    }
  }
  const fadeIn = FPS;
  const fadeOut = 4 * FPS;
  return level.map((v, f) => v * Math.min(1, f / fadeIn) * Math.min(1, (TOTAL - f) / fadeOut));
})();

export function musicVolume(frame: number): number {
  return curve[Math.max(0, Math.min(TOTAL - 1, Math.round(frame)))];
}

export type Sound = "whoosh" | "click" | "pop" | "sparkle" | "gong";
export type Cue = { frame: number; sound: Sound; volume: number };

/** Sound effects, used sparingly and only on moments that are already in the picture. */
export const CUES: Cue[] = [
  // Scene transitions (each a zoom), except the very first scene.
  ...scenes.slice(1).map((s) => ({ frame: visualStart(s.id) + 2, sound: "whoosh" as Sound, volume: 0.35 })),
  // s1
  { frame: phraseFrame("s1b1", "A message"), sound: "pop", volume: 0.5 },
  { frame: phraseFrame("s1b2", "Today", 4), sound: "click", volume: 0.4 },
  { frame: beatFrame("s1b3") - 10, sound: "whoosh", volume: 0.35 },
  { frame: phraseFrame("s1b3", "Meet Tip"), sound: "pop", volume: 0.5 },
  { frame: beatFrame("s1b4") + 8, sound: "gong", volume: 0.3 },
  // s2
  { frame: phraseFrame("s2b1", "Nine out of ten"), sound: "click", volume: 0.35 },
  { frame: phraseFrame("s2b2", "Almost three"), sound: "click", volume: 0.35 },
  { frame: phraseFrame("s2b5", "It amplifies"), sound: "sparkle", volume: 0.4 },
  // s3
  { frame: beatFrame("s3b2"), sound: "whoosh", volume: 0.3 },
  ...["Principles", "Interfaces", "Context", "checks"].map((p) => ({ frame: phraseFrame("s3b3", p), sound: "click" as Sound, volume: 0.3 })),
  { frame: phraseFrame("s3b4", "AI made", 16), sound: "pop", volume: 0.45 },
  // s4
  { frame: phraseFrame("s4b1", "every change", 8), sound: "sparkle", volume: 0.35 },
  { frame: phraseFrame("s4b4", "a test fails"), sound: "pop", volume: 0.5 },
  { frame: phraseFrame("s4b5", "with more than"), sound: "sparkle", volume: 0.35 },
  // s5
  { frame: phraseFrame("s5b2", "It has to live", 18), sound: "pop", volume: 0.4 },
  { frame: phraseFrame("s5b4", "And design records", 14), sound: "click", volume: 0.45 },
  { frame: phraseFrame("s5b5", "connect AI", 18), sound: "sparkle", volume: 0.35 },
  // s6
  { frame: phraseFrame("s6b2", "no internet"), sound: "click", volume: 0.4 },
  { frame: phraseFrame("s6b3", "swaps it", 6), sound: "pop", volume: 0.45 },
  { frame: phraseFrame("s6b4", "Then I decide", 6), sound: "click", volume: 0.4 },
  { frame: phraseFrame("s6b5", "before it says done", 10), sound: "sparkle", volume: 0.4 },
  // s7
  ...["a design record", "a principles check", "a plan for a spike"].map((p) => ({
    frame: phraseFrame("s7b1", p),
    sound: "pop" as Sound,
    volume: 0.4,
  })),
  { frame: phraseFrame("s7b2", "But when it comes", 10), sound: "sparkle", volume: 0.35 },
  { frame: phraseFrame("s7b3", "So build"), sound: "gong", volume: 0.3 },
];
