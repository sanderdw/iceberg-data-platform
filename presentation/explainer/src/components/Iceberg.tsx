import React from "react";
import { random } from "remotion";
import { mix } from "../lib/anim";
import { C, H, T, W } from "../lib/theme";
import { Expression, Tip } from "./Tip";
import { Txt } from "./ui";

// The four layers under the water, top to bottom, in the order the voice-over names them.
export const LAYERS = [
  { label: "Principles", fill: C.sun, shade: C.sunShade },
  { label: "Shared interfaces", fill: C.teal, shade: C.tealShade },
  { label: "Written context", fill: C.violet, shade: C.violetShade },
  { label: "Checks", fill: C.coral, shade: C.coralShade },
];
const BANDS = [
  [16, 160],
  [160, 300],
  [300, 440],
  [440, 610],
];
const MASS = "M -150 2 C -270 70 -370 250 -310 420 C -250 590 240 600 330 440 C 400 300 310 90 150 2 Z";
const MASS_SHADE = "M 150 2 C 310 90 400 300 330 440 C 290 520 200 570 90 586 C 200 470 250 240 150 2 Z";

/** Screen y of the middle of layer `i`, for scenes that point at a layer. */
export function layerY(waterY: number, scale: number, i: number): number {
  return waterY + ((BANDS[i][0] + BANDS[i][1]) / 2) * scale;
}

/**
 * The sea with Tip on the surface and the mass below the waterline.
 * (cx, waterY) is the middle of the waterline on screen; `scale` sizes the whole iceberg.
 */
export const Iceberg: React.FC<{
  frame: number;
  cx: number;
  waterY: number;
  scale: number;
  reveal?: number; // 0: murky water, 1: clear water showing the mass
  layers?: number[]; // per layer 0..1: lit in its colour
  labels?: number[]; // per layer 0..1: label visible
  expression?: Expression;
  wave?: number;
  tipY?: number; // extra offset for Tip popping up (positive = lower)
  look?: number;
  id: string;
}> = ({ frame, cx, waterY, scale, reveal = 0, layers = [], labels = [], expression = "smile", wave = 0, tipY = 0, look = 0, id }) => {
  const surface = (x: number) => waterY + Math.sin(x / 140 + frame / 18) * 7;
  let wavePath = `M -20 ${surface(-20)}`;
  for (let x = 0; x <= W + 40; x += 40) wavePath += ` L ${x} ${surface(x)}`;
  const body = `${wavePath} L ${W + 40} ${H + 40} L -20 ${H + 40} Z`;
  return (
    <g>
      {/* Mass under the water */}
      <g transform={`translate(${cx} ${waterY}) scale(${scale})`}>
        <clipPath id={`mass-${id}`}>
          <path d={MASS} />
        </clipPath>
        <path d={MASS} fill={C.iceDeep} />
        <g clipPath={`url(#mass-${id})`}>
          {BANDS.map(([a, b], i) => (
            <g key={i}>
              <rect x={-400} y={a} width={800} height={b - a} fill={i % 2 ? C.iceDeepShade : C.iceDeep} opacity={0.35} />
              <rect x={-400} y={a} width={800} height={b - a} fill={LAYERS[i].fill} opacity={0.9 * (layers[i] ?? 0)} />
              <rect x={-400} y={b - 6} width={800} height={6} fill={C.seaDeep} opacity={0.25} />
            </g>
          ))}
          <path d={MASS_SHADE} fill={C.seaDeep} opacity={0.22} />
        </g>
      </g>
      {/* The water, murky or clear */}
      <path d={body} fill="url(#sea)" opacity={mix(0.93, 0.5, reveal)} />
      {/* Lit layers glow through the water */}
      <g transform={`translate(${cx} ${waterY}) scale(${scale})`} clipPath={`url(#mass-${id})`}>
        {BANDS.map(([a, b], i) => (
          <rect key={i} x={-400} y={a} width={800} height={b - a - 6} fill={LAYERS[i].fill} opacity={0.82 * (layers[i] ?? 0)} />
        ))}
        <path d={MASS_SHADE} fill={C.seaDeep} opacity={0.25 * Math.max(0, ...layers.map((v) => v ?? 0))} />
      </g>
      {Array.from({ length: 26 }, (_, i) => {
        const x = random(`bx${i}`) * W;
        const speed = 0.4 + random(`bs${i}`) * 0.8;
        const span = H - waterY + 60;
        const y = H + 30 - (((frame * speed + random(`by${i}`) * span) % span) || 0);
        return y > waterY + 20 ? <circle key={i} cx={x + Math.sin(frame / 25 + i) * 8} cy={y} r={3 + (i % 4) * 2} fill={C.foam} opacity={0.18} /> : null;
      })}
      {/* Labels to the right of the mass */}
      {LAYERS.map((layer, i) => {
        const o = labels[i] ?? 0;
        if (o <= 0.01) return null;
        const y = layerY(waterY, scale, i);
        const edge = cx + (i === 3 ? 330 : 360) * scale;
        const lx = cx + 420 * scale + 40;
        return (
          <g key={layer.label} opacity={o}>
            <line x1={edge} y1={y} x2={lx - 24} y2={y} stroke={layer.fill} strokeWidth={5} strokeLinecap="round" strokeDasharray="2 12" />
            <circle cx={lx - 24} cy={y} r={9} fill={layer.fill} />
            <Txt x={lx} y={y} size={T.label} anchor="start" fill={C.white} weight={900}>
              {layer.label}
            </Txt>
          </g>
        );
      })}
      {/* Tip above the water, then the surface line over its base */}
      <clipPath id={`above-${id}`}>
        <rect x={-100} y={-2000} width={W + 200} height={2000 + waterY + 10} />
      </clipPath>
      <g clipPath={`url(#above-${id})`}>
        <Tip x={cx} y={waterY + 6 + tipY} scale={scale} frame={frame} expression={expression} wave={wave} look={look} />
      </g>
      <path d={wavePath} stroke={C.foam} strokeWidth={5} fill="none" opacity={0.75} />
    </g>
  );
};
