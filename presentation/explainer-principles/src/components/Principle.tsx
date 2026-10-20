import React from "react";
import script from "../../script/script.json";
import { bob, ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { LAYERS } from "./Iceberg";
import { Tip } from "./Tip";
import { Pop, Txt } from "./ui";

export const PRINCIPLES = script.principles.map((p, i) => ({ ...p, fill: LAYERS[i].fill, shade: LAYERS[i].shade }));

/** Tip floating in a small strip of water in the lower-right corner, looking at the title. */
export const MiniTip: React.FC<{ frame: number; at: number; x?: number; y?: number }> = ({ frame, at, x = 1640, y = 960 }) => {
  const rise = pop(frame, at, 120, 12);
  const surface = (px: number) => y + Math.sin(px / 60 + frame / 14) * 4;
  let wave = `M ${x - 200} ${surface(x - 200)}`;
  for (let px = x - 180; px <= x + 200; px += 20) wave += ` L ${px} ${surface(px)}`;
  return (
    <g opacity={Math.min(1, rise)}>
      <clipPath id={`minitip-${at}`}>
        <rect x={x - 220} y={y - 400} width={440} height={406} />
      </clipPath>
      <g clipPath={`url(#minitip-${at})`}>
        <Tip x={x} y={y + 6 + mix(160, 0, rise) + bob(frame, 4, 70)} scale={0.62} frame={frame} expression="happy" look={-0.8} wave={fade(frame, at + 8, 8) * fadeOut(frame, at + 50, 12)} />
      </g>
      <path d={wave} stroke={C.foam} strokeWidth={5} fill="none" strokeLinecap="round" />
    </g>
  );
};

/**
 * A principle's title card during its first beat, which then docks as a small header in the
 * top-left corner for the rest of the scene.
 */
export const PrincipleTitle: React.FC<{ frame: number; index: number; at: number; dock: number }> = ({
  frame, index, at, dock,
}) => {
  const p = PRINCIPLES[index];
  const big = pop(frame, at) * fadeOut(frame, dock - 8, 10);
  const small = fade(frame, dock, 10);
  const tagline = fade(frame, at + 12, 10);
  return (
    <g>
      <Pop p={big} cx={960} cy={480}>
        <rect x={960 - 150} y={262} width={300} height={70} rx={35} fill={p.fill} />
        <Txt x={960} y={297} size={T.small} weight={900} fill={C.ink}>
          {`Principle ${index + 1}`}
        </Txt>
        <Txt x={960} y={450} size={136} weight={900}>
          {p.name}
        </Txt>
        <Txt x={960} y={590} size={T.h2 + 4} weight={900} fill={p.fill} opacity={tagline}>
          {p.tagline}
        </Txt>
      </Pop>
      <g opacity={small} transform={`translate(${mix(-40, 0, ease(frame, dock, dock + 12))} 0)`}>
        <circle cx={96} cy={86} r={34} fill={p.fill} />
        <Txt x={96} y={88} size={T.small} weight={900} fill={C.ink}>
          {index + 1}
        </Txt>
        <Txt x={148} y={72} size={T.small + 4} weight={900} anchor="start">
          {p.name}
        </Txt>
        <Txt x={148} y={112} size={T.caption} weight={800} anchor="start" fill={p.fill}>
          {p.tagline}
        </Txt>
      </g>
      {frame < dock && <MiniTip frame={frame} at={at + 6} />}
    </g>
  );
};

/** A labelled card that pops in: used for lists of things in spoken order. */
export const ItemCard: React.FC<{
  frame: number; at: number; x: number; y: number; text: string; fill?: string; shade?: string; w?: number; color?: string; dim?: number;
}> = ({ frame, at, x, y, text, fill = C.night2, shade = C.ink, w = 360, color = C.white, dim = 0 }) => (
  <Pop p={pop(frame, at)} cx={x} cy={y}>
    <g opacity={1 - dim * 0.6}>
      <rect x={x - w / 2} y={y - 40 + 8} width={w} height={80} rx={24} fill={shade} />
      <rect x={x - w / 2} y={y - 40} width={w} height={80} rx={24} fill={fill} />
      <Txt x={x} y={y} size={T.small + 2} weight={900} fill={color}>
        {text}
      </Txt>
    </g>
  </Pop>
);
