import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import type { Beat } from "../lib/edl";
import { C, FONT } from "../lib/theme";

/** Which beat of the shot is playing, and how far it is (0..1). Before the first beat: 0, 0. */
export function useBeat(beats: Beat[], pieceFrom: number): { index: number; p: number; frame: number } {
  const frame = useCurrentFrame();
  const at = frame + pieceFrom;
  let index = 0;
  beats.forEach((b, i) => {
    if (b.from <= at) index = i;
  });
  const b = beats[index];
  const p = b ? Math.max(0, Math.min(1, (at - b.from) / Math.max(1, b.frames))) : 0;
  return { index, p, frame };
}

export const mono = (size: number, color: string = C.textMid): React.CSSProperties => ({
  fontFamily: FONT.mono,
  fontSize: size,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color,
  whiteSpace: "nowrap",
});

/** A figure card in the Nothing style: black, a hairline frame, corner labels, the figure in the middle. */
export const FigureCard: React.FC<{
  fig: string;
  title: string;
  hint: React.ReactNode;
  frames: number;
  children: React.ReactNode;
}> = ({ fig, title, hint, frames, children }) => {
  const f = useCurrentFrame();
  const opacity = interpolate(f, [0, 8, frames - 8, frames], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const inset = 56;
  return (
    <AbsoluteFill style={{ backgroundColor: C.black, opacity }}>
      <div style={{ position: "absolute", inset, border: `1px solid ${C.border}` }} />
      <div style={{ position: "absolute", left: inset + 28, top: inset + 24, ...mono(22, C.text) }}>{fig}</div>
      <div style={{ position: "absolute", right: inset + 28, top: inset + 24, ...mono(22, C.textMid) }}>{title}</div>
      <div style={{ position: "absolute", left: inset + 28, bottom: inset + 22, display: "flex", alignItems: "center", gap: 14, ...mono(22, C.white) }}>
        <div style={{ width: 10, height: 10, borderRadius: 5, background: C.textMid }} />
        {hint}
      </div>
      <AbsoluteFill style={{ padding: `${inset + 70}px ${inset + 40}px`, justifyContent: "center", alignItems: "center" }}>{children}</AbsoluteFill>
    </AbsoluteFill>
  );
};
