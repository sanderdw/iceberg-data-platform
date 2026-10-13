import React from "react";
import { C } from "../lib/theme";

/**
 * The agent: a small rounded helper with a screen for a face and a glowing antenna.
 * Origin is the centre of its body; it stands about 150 units tall.
 */
export const Agent: React.FC<{
  x: number; y: number; scale?: number; frame: number; mood?: "happy" | "blank" | "focus"; glow?: number;
}> = ({ x, y, scale = 1, frame, mood = "happy", glow = 1 }) => {
  const blink = frame % 120 > 115 ? 0.15 : 1;
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`}>
      <ellipse cx={-28} cy={46} rx={22} ry={12} fill={C.violetShade} />
      <ellipse cx={28} cy={46} rx={22} ry={12} fill={C.violetShade} />
      <line x1={0} y1={-66} x2={0} y2={-98} stroke={C.violetShade} strokeWidth={8} strokeLinecap="round" />
      <circle cx={0} cy={-104} r={20} fill={C.sun} opacity={0.35 * glow} filter="url(#glow)" />
      <circle cx={0} cy={-104} r={11} fill={C.sun} />
      <rect x={-64} y={-72} width={128} height={118} rx={38} fill={C.violetShade} />
      <rect x={-64} y={-72} width={104} height={118} rx={38} fill={C.violet} />
      <rect x={-44} y={-50} width={88} height={60} rx={20} fill={C.ink} />
      {mood === "blank" ? (
        <>
          <rect x={-22} y={-24} width={14} height={4} rx={2} fill={C.teal} />
          <rect x={8} y={-24} width={14} height={4} rx={2} fill={C.teal} />
        </>
      ) : (
        <>
          <rect x={-24} y={-38} width={13} height={22 * blink} rx={6.5} fill={C.teal} />
          <rect x={10} y={-38} width={13} height={22 * blink} rx={6.5} fill={C.teal} />
          {mood === "happy" && (
            <path d="M -12 -6 Q 0 4 12 -6" stroke={C.teal} strokeWidth={4} fill="none" strokeLinecap="round" />
          )}
        </>
      )}
    </g>
  );
};

/**
 * A person: round head, rounded body, two-tone. Origin is between the feet; about 260 units tall.
 */
export const Person: React.FC<{
  x: number; y: number; scale?: number; body?: string; bodyShade?: string; hair?: string; frame?: number;
}> = ({ x, y, scale = 1, body = C.coral, bodyShade = C.coralShade, hair = C.ink, frame = 0 }) => {
  const blink = frame % 140 > 135 ? 0.15 : 1;
  return (
    <g transform={`translate(${x} ${y}) scale(${scale})`}>
      <rect x={-60} y={-160} width={120} height={160} rx={56} fill={bodyShade} />
      <rect x={-60} y={-160} width={92} height={160} rx={46} fill={body} />
      <circle cx={0} cy={-206} r={46} fill={C.skinShade} />
      <circle cx={-6} cy={-206} r={40} fill={C.skin} />
      <path d="M -46 -214 C -44 -262 44 -262 46 -214 C 30 -236 -24 -240 -46 -214 Z" fill={hair} />
      <ellipse cx={-20} cy={-204} rx={5} ry={6 * blink} fill={C.ink} />
      <ellipse cx={10} cy={-204} rx={5} ry={6 * blink} fill={C.ink} />
      <path d="M -16 -186 Q -5 -178 6 -186" stroke={C.ink} strokeWidth={4} fill="none" strokeLinecap="round" />
    </g>
  );
};

/** An ID badge, worn by both the person and the agent. */
export const Badge: React.FC<{ x: number; y: number; scale?: number }> = ({ x, y, scale = 1 }) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={-34} y={-24} width={68} height={48} rx={10} fill={C.sunShade} />
    <rect x={-34} y={-24} width={60} height={48} rx={10} fill={C.sun} />
    <circle cx={-14} cy={-4} r={9} fill={C.ink} opacity={0.75} />
    <rect x={-2} y={-10} width={22} height={6} rx={3} fill={C.ink} opacity={0.75} />
    <rect x={-2} y={2} width={16} height={6} rx={3} fill={C.ink} opacity={0.75} />
    <rect x={-24} y={12} width={44} height={5} rx={2.5} fill={C.ink} opacity={0.5} />
  </g>
);
