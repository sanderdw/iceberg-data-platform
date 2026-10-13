import React from "react";
import { random } from "remotion";
import { bob } from "../lib/anim";
import { C, FONT, H, T, W } from "../lib/theme";

/** Shared SVG defs: glows and gradients. Include once per <svg>. */
export const Defs: React.FC = () => (
  <defs>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="14" />
    </filter>
    <filter id="softGlow" x="-100%" y="-100%" width="300%" height="300%">
      <feGaussianBlur stdDeviation="60" />
    </filter>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stopColor={C.night} />
      <stop offset="1" stopColor={C.night2} />
    </linearGradient>
    <linearGradient id="sea" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stopColor={C.seaTop} />
      <stop offset="0.45" stopColor={C.sea} />
      <stop offset="1" stopColor={C.seaDeep} />
    </linearGradient>
    <radialGradient id="spot" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stopColor={C.white} stopOpacity="0.35" />
      <stop offset="1" stopColor={C.white} stopOpacity="0" />
    </radialGradient>
  </defs>
);

/** Full-frame SVG in the fixed 1920×1080 coordinate system. */
export const Stage: React.FC<{ children: React.ReactNode; style?: React.CSSProperties }> = ({ children, style }) => (
  <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} style={{ position: "absolute", inset: 0, ...style }}>
    <Defs />
    {children}
  </svg>
);

/** Night sky: gradient, slow-drifting stars in three depths, and soft coloured glows. */
export const Sky: React.FC<{ frame: number; glow?: boolean }> = ({ frame, glow = true }) => (
  <g>
    <rect width={W} height={H} fill="url(#sky)" />
    {glow && (
      <g filter="url(#softGlow)" opacity={0.55}>
        <circle cx={300 + bob(frame, 40, 600)} cy={260} r={260} fill={C.violet} opacity={0.35} />
        <circle cx={1650} cy={820 + bob(frame, 30, 700, 0.5)} r={300} fill={C.teal} opacity={0.18} />
      </g>
    )}
    {Array.from({ length: 90 }, (_, i) => {
      const depth = 1 + (i % 3);
      const x = (random(`sx${i}`) * (W + 200) - frame * 0.06 * depth + W + 200) % (W + 200) - 100;
      const y = random(`sy${i}`) * H;
      const twinkle = 0.45 + 0.4 * Math.sin(frame / (20 + depth * 9) + i);
      return <circle key={i} cx={x} cy={y} r={depth * 1.3} fill={C.white} opacity={twinkle * 0.6} />;
    })}
  </g>
);

type TextProps = {
  x: number;
  y: number;
  size?: number;
  weight?: number;
  fill?: string;
  anchor?: "start" | "middle" | "end";
  opacity?: number;
  children: React.ReactNode;
  spacing?: number;
};

/** SVG text in the house font, vertically centred on y. */
export const Txt: React.FC<TextProps> = ({
  x, y, size = T.label, weight = 800, fill = C.white, anchor = "middle", opacity = 1, children, spacing = 0,
}) => (
  <text
    x={x}
    y={y}
    fontFamily={FONT}
    fontSize={size}
    fontWeight={weight}
    fill={fill}
    textAnchor={anchor}
    dominantBaseline="central"
    opacity={opacity}
    letterSpacing={spacing}
  >
    {children}
  </text>
);

/** Approximate width of a text in Nunito 800, for sizing pills and cards. */
export function textWidth(text: string, size: number): number {
  return text.length * size * 0.56;
}

/** Group that pops in: scales from its own centre (cx, cy) with springy `p` (0 → 1). */
export const Pop: React.FC<{ p: number; cx: number; cy: number; children: React.ReactNode; opacity?: number }> = ({
  p, cx, cy, children, opacity = 1,
}) =>
  p <= 0.001 ? null : (
    <g transform={`translate(${cx} ${cy}) scale(${p}) translate(${-cx} ${-cy})`} opacity={Math.min(1, p * 1.5) * opacity}>
      {children}
    </g>
  );

/** Rounded pill with a label: route chips, tool chips, tags. */
export const Chip: React.FC<{
  x: number; y: number; text: string; fill?: string; shade?: string; color?: string; size?: number; mono?: boolean;
}> = ({ x, y, text, fill = C.teal, shade = C.tealShade, color = C.ink, size = T.small }) => {
  const w = textWidth(text, size) + size * 1.4;
  const h = size * 1.9;
  return (
    <g>
      <rect x={x - w / 2} y={y - h / 2 + 6} width={w} height={h} rx={h / 2} fill={shade} />
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={h / 2} fill={fill} />
      <Txt x={x} y={y} size={size} fill={color} weight={900}>
        {text}
      </Txt>
    </g>
  );
};

/** A sheet of paper with a title and text lines: AGENTS.md, a skill, a design record. */
export const Doc: React.FC<{
  x: number; y: number; w?: number; h?: number; title: string; accent?: string; lines?: number; titleSize?: number;
}> = ({ x, y, w = 300, h = 380, title, accent = C.violet, lines = 5, titleSize = 34 }) => {
  const fold = 54;
  return (
    <g>
      <path
        d={`M ${x - w / 2} ${y - h / 2} H ${x + w / 2 - fold} L ${x + w / 2} ${y - h / 2 + fold} V ${y + h / 2} H ${x - w / 2} Z`}
        fill={C.paper}
      />
      <rect x={x + w / 2 - 34} y={y - h / 2 + fold} width={34} height={h - fold} fill={C.paperShade} opacity={0.6} />
      <path d={`M ${x + w / 2 - fold} ${y - h / 2} V ${y - h / 2 + fold} H ${x + w / 2} Z`} fill={C.paperShade} />
      <rect x={x - w / 2} y={y - h / 2} width={w - fold} height={16} fill={accent} />
      <Txt x={x - w / 2 + 30} y={y - h / 2 + 62} size={titleSize} fill={C.ink} anchor="start" weight={900}>
        {title}
      </Txt>
      {Array.from({ length: lines }, (_, i) => (
        <rect
          key={i}
          x={x - w / 2 + 30}
          y={y - h / 2 + 112 + i * 44}
          width={(w - 90) * (i % 3 === 2 ? 0.6 : i % 2 ? 0.85 : 1)}
          height={16}
          rx={8}
          fill={i === 0 ? accent : C.paperShade}
          opacity={i === 0 ? 0.8 : 1}
        />
      ))}
    </g>
  );
};

/** Source line in the lower-left corner. */
export const Source: React.FC<{ text: string; opacity?: number }> = ({ text, opacity = 1 }) => (
  <Txt x={70} y={1018} size={T.caption} fill={C.muted} anchor="start" weight={700} opacity={opacity}>
    {text}
  </Txt>
);

/** Rounded card: flat base with a darker shade underneath. */
export const Card: React.FC<{
  x: number; y: number; w: number; h: number; fill?: string; shade?: string; rx?: number; children?: React.ReactNode;
}> = ({ x, y, w, h, fill = C.night2, shade = C.ink, rx = 28, children }) => (
  <g>
    <rect x={x - w / 2} y={y - h / 2 + 10} width={w} height={h} rx={rx} fill={shade} opacity={0.7} />
    <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={rx} fill={fill} />
    {children}
  </g>
);

/** Small four-point sparkle. */
export const Sparkle: React.FC<{ x: number; y: number; r?: number; fill?: string; opacity?: number }> = ({
  x, y, r = 16, fill = C.sun, opacity = 1,
}) => (
  <path
    d={`M ${x} ${y - r} Q ${x + r * 0.2} ${y - r * 0.2} ${x + r} ${y} Q ${x + r * 0.2} ${y + r * 0.2} ${x} ${y + r} Q ${x - r * 0.2} ${y + r * 0.2} ${x - r} ${y} Q ${x - r * 0.2} ${y - r * 0.2} ${x} ${y - r} Z`}
    fill={fill}
    opacity={opacity}
  />
);

/** Check mark in a circle. */
export const Tick: React.FC<{ x: number; y: number; r?: number; fill?: string }> = ({ x, y, r = 30, fill = C.green }) => (
  <g>
    <circle cx={x} cy={y} r={r} fill={fill} />
    <path
      d={`M ${x - r * 0.45} ${y + r * 0.02} L ${x - r * 0.1} ${y + r * 0.38} L ${x + r * 0.5} ${y - r * 0.35}`}
      stroke={C.ink}
      strokeWidth={r * 0.22}
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </g>
);

/** Cross in a circle. */
export const Cross: React.FC<{ x: number; y: number; r?: number; fill?: string }> = ({ x, y, r = 30, fill = C.red }) => (
  <g>
    <circle cx={x} cy={y} r={r} fill={fill} />
    <path
      d={`M ${x - r * 0.38} ${y - r * 0.38} L ${x + r * 0.38} ${y + r * 0.38} M ${x + r * 0.38} ${y - r * 0.38} L ${x - r * 0.38} ${y + r * 0.38}`}
      stroke={C.ink}
      strokeWidth={r * 0.22}
      strokeLinecap="round"
    />
  </g>
);
