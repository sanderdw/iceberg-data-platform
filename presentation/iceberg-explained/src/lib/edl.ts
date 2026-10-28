import geminiJson from "../edl-gemini.json";
import geminiEnJson from "../edl-gemini-en.json";
import type { Lang } from "./i18n";

export type Piece =
  | { kind: "video"; clip: string; startFrom: number; rate: number; from: number; frames: number; fast?: boolean; shot: string }
  | { kind: "hold"; clip: string; startFrom: number; from: number; frames: number; shot: string }
  | { kind: "card"; card: "title" | "chapter" | "end" | "diagram"; diagram?: string; shot?: string; number?: string; title?: string; from: number; frames: number };
export type Chapter = { id: string; number: string | null; title: string | null; from: number; frames: number };
export type Beat = { id: string; from: number; frames: number; text: string; shot: string };
export type Edl = {
  fps: number;
  durationFrames: number;
  pieces: Piece[];
  chapters: Chapter[];
  beats: Beat[];
  estimated: string[];
  voice: boolean;
  music: boolean;
  lang?: Lang;
};

/** One timeline per voice-over: the beats differ in length, so the cut does too (edit/build.py --voice gemini). */
export const EDLS = {
  gemini: geminiJson as unknown as Edl,
  "gemini-en": geminiEnJson as unknown as Edl,
};
export type Voice = keyof typeof EDLS;

/** Where a voice's clips and music bed live in public/, as written by the Python scripts. */
export const voiceFile = (voice: Voice) => `voice/${voice}/voiceover.wav`;
export const musicFile = (voice: Voice) => `music/music-${voice}.wav`;

export const EDL = EDLS.gemini;
export const FPS = EDL.fps;
export const TOTAL = EDL.durationFrames;
