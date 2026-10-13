import React from "react";
import { bob, ease, fade, mix } from "../lib/anim";
import { C } from "../lib/theme";

/** The desk: window at dawn, a table and a mug. */
export const Desk: React.FC<{ frame: number }> = ({ frame }) => (
  <g>
    <rect x={0} y={800} width={1920} height={280} fill="#0D1B45" />
    {/* Window at dawn */}
    <rect x={150} y={250} width={330} height={400} rx={26} fill={C.night2} />
    <clipPath id="pane">
      <rect x={172} y={272} width={286} height={356} rx={16} />
    </clipPath>
    <g clipPath="url(#pane)">
      <rect x={172} y={272} width={286} height={356} fill={C.dawn} />
      <rect x={172} y={520} width={286} height={120} fill="#4B3F8E" />
      <circle cx={315} cy={640 - mix(0, 60, ease(frame, 0, 400))} r={78} fill={C.sun} opacity={0.9} />
      <circle cx={315} cy={640 - mix(0, 60, ease(frame, 0, 400))} r={120} fill={C.sun} opacity={0.25} filter="url(#glow)" />
    </g>
    <rect x={306} y={272} width={18} height={356} fill={C.night2} />
    <rect x={172} y={442} width={286} height={16} fill={C.night2} />
    {/* Desk */}
    <rect x={330} y={782} width={1260} height={34} rx={14} fill={C.dim} />
    <rect x={330} y={770} width={1260} height={30} rx={14} fill={C.muted} />
    <rect x={400} y={816} width={30} height={200} fill={C.dim} />
    <rect x={1490} y={816} width={30} height={200} fill={C.dim} />
    {/* Mug with steam */}
    <rect x={1290} y={690} width={74} height={84} rx={18} fill={C.coralShade} />
    <rect x={1290} y={690} width={60} height={84} rx={18} fill={C.coral} />
    <path d="M 1364 710 a 22 22 0 0 1 0 44" stroke={C.coralShade} strokeWidth={12} fill="none" />
    {[0, 1].map((i) => (
      <path
        key={i}
        d={`M ${1312 + i * 22} 670 q 14 -24 0 -48 q -14 -24 0 -48`}
        stroke={C.white}
        strokeWidth={7}
        strokeLinecap="round"
        fill="none"
        opacity={0.25 + 0.15 * Math.sin(frame / 12 + i)}
        transform={`translate(0 ${bob(frame, 6, 70, i)})`}
      />
    ))}
  </g>
);

/** Code lines streaming into the laptop screen. */
export const CodeScreen: React.FC<{ frame: number; from: number }> = ({ frame, from }) => {
  const colors = [C.teal, C.violet, C.sun, C.coral, C.muted];
  return (
    <g opacity={fade(frame, from, 8)}>
      <rect x={-194} y={-274} width={388} height={44} rx={12} fill={C.night2} />
      <circle cx={-168} cy={-252} r={8} fill={C.coral} />
      <circle cx={-144} cy={-252} r={8} fill={C.sun} />
      <circle cx={-120} cy={-252} r={8} fill={C.green} />
      {Array.from({ length: 9 }, (_, i) => {
        const w = 60 + ((i * 53) % 180);
        const indent = (i % 3) * 26;
        const shown = Math.max(0, Math.min(1, (frame - from - i * 4) / 6));
        return (
          <rect key={i} x={-170 + indent} y={-212 + i * 20} width={w * shown} height={11} rx={5.5} fill={colors[i % colors.length]} />
        );
      })}
    </g>
  );
};

