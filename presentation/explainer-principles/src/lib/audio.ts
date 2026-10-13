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
  // Each principle's title card lands with a soft gong.
  ...["s2b1", "s3b1", "s4b1", "s5b1"].map((id) => ({ frame: beatFrame(id) + 2, sound: "gong" as Sound, volume: 0.22 })),
  // s1
  { frame: phraseFrame("s1b1", "And then Monday"), sound: "pop", volume: 0.45 },
  { frame: phraseFrame("s1b2", "in a repository"), sound: "click", volume: 0.4 },
  { frame: beatFrame("s1b3") - 10, sound: "whoosh", volume: 0.35 },
  { frame: phraseFrame("s1b3", "Meet Tip"), sound: "pop", volume: 0.5 },
  { frame: phraseFrame("s1b5", "Four places"), sound: "sparkle", volume: 0.4 },
  // s2
  { frame: phraseFrame("s2b4", "Each answer"), sound: "pop", volume: 0.45 },
  { frame: phraseFrame("s2b5", "when a person tags it"), sound: "click", volume: 0.45 },
  { frame: phraseFrame("s2b5", "the full test run passes", 30), sound: "sparkle", volume: 0.4 },
  // s3
  ...["a wiki page", "a console click", "somebody's head"].map((p) => ({ frame: phraseFrame("s3b2", p), sound: "click" as Sound, volume: 0.35 })),
  { frame: phraseFrame("s3b4", "a test fails"), sound: "pop", volume: 0.5 },
  { frame: phraseFrame("s3b5", "And so does this video"), sound: "sparkle", volume: 0.4 },
  // s4
  { frame: phraseFrame("s4b2", "tomorrow's cage", 6), sound: "click", volume: 0.4 },
  { frame: phraseFrame("s4b4", "The record says why", 6), sound: "pop", volume: 0.45 },
  { frame: phraseFrame("s4b6", "So one part", 24), sound: "whoosh", volume: 0.3 },
  // s5
  { frame: phraseFrame("s5b2", "an exit date", 4), sound: "click", volume: 0.4 },
  { frame: phraseFrame("s5b3", "each tool can be swapped", 4), sound: "whoosh", volume: 0.3 },
  { frame: phraseFrame("s5b4", "Thirteen releases"), sound: "sparkle", volume: 0.35 },
  { frame: phraseFrame("s5b6", "We set the exit date", 2), sound: "click", volume: 0.45 },
  // s6
  ...["AI operates", "If it matters", "Decide in the open", "keep moving"].map((p) => ({ frame: phraseFrame("s6b1", p), sound: "pop" as Sound, volume: 0.35 })),
  { frame: beatFrame("s6b4") - 10, sound: "whoosh", volume: 0.35 },
  { frame: phraseFrame("s6b4", "they're under the water"), sound: "gong", volume: 0.3 },
];
