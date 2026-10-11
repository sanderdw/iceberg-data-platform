import { Easing, interpolate, spring } from "remotion";
import { FPS } from "./time";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** Springy 0 → 1 (with a little overshoot) starting at frame `at`. */
export function pop(frame: number, at: number, stiffness = 170, damping = 11): number {
  if (frame < at) return 0;
  return spring({ frame: frame - at, fps: FPS, config: { stiffness, damping, mass: 0.7 } });
}

/** Calm spring without overshoot. */
export function settle(frame: number, at: number, stiffness = 90): number {
  if (frame < at) return 0;
  return spring({ frame: frame - at, fps: FPS, config: { stiffness, damping: 20, mass: 1 } });
}

/** Linear-ish fade 0 → 1 over `duration` frames from `at`. */
export function fade(frame: number, at: number, duration = 10): number {
  return interpolate(frame, [at, at + duration], [0, 1], { ...clamp, easing: Easing.out(Easing.quad) });
}

/** 1 → 0 over `duration` frames from `at`. */
export function fadeOut(frame: number, at: number, duration = 10): number {
  return 1 - fade(frame, at, duration);
}

/** Smooth 0 → 1 between two frames. */
export function ease(frame: number, from: number, to: number): number {
  return interpolate(frame, [from, to], [0, 1], { ...clamp, easing: Easing.inOut(Easing.cubic) });
}

export function mix(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** A counter that ticks up to `to`, easing out, over `duration` frames. */
export function count(frame: number, at: number, to: number, duration = 36): number {
  const t = interpolate(frame, [at, at + duration], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  return Math.round(to * t);
}

/** Slow sine bob for floating things. */
export function bob(frame: number, amplitude = 8, period = 90, phase = 0): number {
  return Math.sin(((frame / period) * 2 + phase) * Math.PI) * amplitude;
}

export function formatNumber(n: number): string {
  return n.toLocaleString("en-US");
}
