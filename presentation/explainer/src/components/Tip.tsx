import React from "react";
import { C } from "../lib/theme";

export type Expression = "smile" | "wink" | "wow" | "happy";

/**
 * Tip, the mascot: the visible part of a small iceberg, with a face.
 * Origin (0, 0) is the middle of the waterline; the peak is about 190 units above it.
 */
export const Tip: React.FC<{
  x: number;
  y: number;
  scale?: number;
  frame: number;
  expression?: Expression;
  wave?: number; // 0..1: arm raised and waving
  look?: number; // -1..1: pupils left/right
}> = ({ x, y, scale = 1, frame, expression = "smile", wave = 0, look = 0 }) => {
  // Blink for 5 frames every ~3.3 s.
  const blink = frame % 100 > 94 ? 0.12 : 1;
  const armAngle = -20 - wave * (40 + Math.sin(frame / 4) * 22);
  const px = look * 5;
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`}>
      {/* Waving arm, behind the body */}
      {wave > 0.01 && (
        <g transform={`translate(98 -70) rotate(${armAngle})`} opacity={Math.min(1, wave * 3)}>
          <rect x={-14} y={-78} width={30} height={84} rx={15} fill={C.iceShade} />
          <rect x={-14} y={-78} width={20} height={84} rx={10} fill={C.ice} />
        </g>
      )}
      <path d="M -132 2 C -122 -50 -96 -122 -52 -166 C -26 -192 16 -196 42 -170 C 86 -126 116 -60 132 2 Z" fill={C.ice} />
      <path d="M 42 -170 C 86 -126 116 -60 132 2 L 34 2 C 52 -60 58 -118 42 -170 Z" fill={C.iceShade} />
      <path d="M -52 -166 C -60 -150 -66 -136 -70 -120 C -54 -140 -34 -156 -10 -168 C -26 -176 -40 -174 -52 -166 Z" fill={C.white} opacity={0.8} />
      {/* Cheeks */}
      <ellipse cx={-52} cy={-66} rx={16} ry={9} fill={C.coral} opacity={0.45} />
      <ellipse cx={46} cy={-66} rx={16} ry={9} fill={C.coral} opacity={0.45} />
      {/* Eyes */}
      <g>
        <ellipse cx={-28} cy={-96} rx={15} ry={18 * blink} fill={C.white} />
        <circle cx={-28 + px} cy={-94} r={8 * Math.min(1, blink * 2)} fill={C.ink} />
        {expression === "wink" ? (
          <path d="M 8 -94 Q 22 -106 36 -94" stroke={C.ink} strokeWidth={6} fill="none" strokeLinecap="round" />
        ) : (
          <>
            <ellipse cx={22} cy={-96} rx={15} ry={18 * blink} fill={C.white} />
            <circle cx={22 + px} cy={-94} r={8 * Math.min(1, blink * 2)} fill={C.ink} />
          </>
        )}
      </g>
      {/* Mouth */}
      {expression === "wow" ? (
        <ellipse cx={-3} cy={-56} rx={10} ry={13} fill={C.ink} />
      ) : expression === "happy" ? (
        <path d="M -24 -64 Q -3 -36 18 -64 Z" fill={C.ink} />
      ) : (
        <path d="M -22 -62 Q -3 -46 16 -62" stroke={C.ink} strokeWidth={6} fill="none" strokeLinecap="round" />
      )}
    </g>
  );
};
