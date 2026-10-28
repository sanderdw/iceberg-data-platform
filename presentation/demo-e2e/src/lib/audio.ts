import { Edl } from "./edl";

// Music volume per frame: barely there under the voice, a little more in the gaps, faded in and out.
const OPEN = 0.16;
const UNDER = 0.05;
const RAMP = 12; // frames

function curve(edl: Edl): number[] {
  const total = edl.durationFrames;
  const level = new Array<number>(total).fill(OPEN);
  for (const beat of edl.beats) {
    const from = beat.from - 6;
    const to = beat.from + beat.frames + 6;
    for (let f = Math.max(0, from - RAMP); f < Math.min(total, to + RAMP); f++) {
      const into = f < from ? (f - (from - RAMP)) / RAMP : f > to ? 1 - (f - to) / RAMP : 1;
      level[f] = Math.min(level[f], OPEN + (UNDER - OPEN) * Math.max(0, Math.min(1, into)));
    }
  }
  return level.map((v, f) => v * Math.min(1, f / edl.fps) * Math.min(1, (total - f) / (4 * edl.fps)));
}

const curves = new Map<Edl, number[]>();

/** The ducked music volume of one timeline, for <Audio volume>. */
export function musicVolume(edl: Edl): (frame: number) => number {
  if (!curves.has(edl)) curves.set(edl, curve(edl));
  const level = curves.get(edl)!;
  return (frame) => level[Math.max(0, Math.min(level.length - 1, Math.round(frame)))];
}
