import timings from "../timings.json";

// All timing comes from timings.json (written by tts/generate.py), never from hard-coded seconds.

type Beat = {
  scene: string;
  start: number;
  duration: number;
  startFrame: number;
  durationFrames: number;
  text: string;
};
type Scene = {
  id: string;
  title: string;
  start: number;
  end: number;
  startFrame: number;
  endFrame: number;
  beats: string[];
};

export const FPS: number = timings.fps;
export const TOTAL: number = timings.durationFrames;
export const END_CARD: number = timings.endCard.startFrame;
export const beats = timings.beats as Record<string, Beat>;
export const scenes = timings.scenes as Scene[];

// A scene becomes visible PRE frames before its first beat, so its zoom-in happens in the gap,
// and stays OVERLAP frames into the next one for the cross-zoom.
export const PRE = 18;
export const OVERLAP = 16;

export function visualStart(id: string): number {
  const index = scenes.findIndex((s) => s.id === id);
  return index === 0 ? 0 : scenes[index].startFrame - PRE;
}

export function visualEnd(id: string): number {
  const index = scenes.findIndex((s) => s.id === id);
  const next = scenes[index + 1];
  return (next ? visualStart(next.id) : END_CARD - PRE) + OVERLAP;
}

/**
 * Frames of one scene, relative to the scene's own first visible frame (what useCurrentFrame()
 * returns inside it): `sceneTimes("s3").b.s3b4` is the start frame of beat s3b4.
 */
export function sceneTimes(id: string) {
  const origin = visualStart(id);
  const scene = scenes.find((s) => s.id === id);
  if (!scene) throw new Error(`Unknown scene ${id}`);
  const b: Record<string, number> = {};
  const d: Record<string, number> = {};
  const e: Record<string, number> = {};
  for (const beatId of scene.beats) {
    b[beatId] = beats[beatId].startFrame - origin;
    d[beatId] = beats[beatId].durationFrames;
    e[beatId] = b[beatId] + d[beatId];
  }
  return {
    origin,
    length: visualEnd(id) - origin,
    b,
    d,
    e,
    /** The frame at which `phrase` is spoken within a beat, estimated from its position in the text. */
    at(beatId: string, phrase: string, offset = 0): number {
      const beat = beats[beatId];
      const index = beat.text.indexOf(phrase);
      if (index < 0) throw new Error(`"${phrase}" is not in ${beatId}`);
      return b[beatId] + Math.round((beat.durationFrames * index) / beat.text.length) + offset;
    },
  };
}

/** Global frame of a beat start, for sound effects on the main timeline. */
export function beatFrame(beatId: string): number {
  return beats[beatId].startFrame;
}

/** Global frame at which a phrase is spoken. */
export function phraseFrame(beatId: string, phrase: string, offset = 0): number {
  const beat = beats[beatId];
  const index = beat.text.indexOf(phrase);
  if (index < 0) throw new Error(`"${phrase}" is not in ${beatId}`);
  return beat.startFrame + Math.round((beat.durationFrames * index) / beat.text.length) + offset;
}
