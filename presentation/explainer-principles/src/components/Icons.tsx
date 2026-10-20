import React from "react";
import { C, T } from "../lib/theme";
import { Txt } from "./ui";

/** Laptop seen from the front; origin is the middle of its base. */
export const Laptop: React.FC<{ x: number; y: number; scale?: number; screen?: React.ReactNode }> = ({ x, y, scale = 1, screen }) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={-210} y={-290} width={420} height={270} rx={22} fill={C.dim} />
    <rect x={-194} y={-274} width={388} height={238} rx={12} fill={C.ink} />
    {screen}
    <path d="M -260 -20 H 260 L 240 6 H -240 Z" fill={C.muted} />
    <rect x={-240} y={0} width={480} height={10} rx={5} fill={C.dim} />
  </g>
);

/** Speech bubble with up to three lines of text; (x, y) is its centre, the tail points down-left. */
export const ChatBubble: React.FC<{
  x: number; y: number; w: number; lines: string[]; size?: number; fill?: string; color?: string; tail?: "left" | "right";
}> = ({ x, y, w, lines, size = T.small, fill = C.white, color = C.ink, tail = "left" }) => {
  const h = lines.length * size * 1.3 + size * 1.4;
  const tx = tail === "left" ? x - w / 2 + 70 : x + w / 2 - 70;
  return (
    <g>
      <rect x={x - w / 2} y={y - h / 2 + 8} width={w} height={h} rx={34} fill={C.paperShade} />
      <rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={34} fill={fill} />
      <path d={`M ${tx - 24} ${y + h / 2 - 2} L ${tx - (tail === "left" ? 40 : -40)} ${y + h / 2 + 44} L ${tx + 30} ${y + h / 2 - 2} Z`} fill={fill} />
      {lines.map((line, i) => (
        <Txt key={i} x={x - w / 2 + 44} y={y - h / 2 + size * 1.35 + i * size * 1.3} size={size} fill={color} anchor="start" weight={800}>
          {line}
        </Txt>
      ))}
    </g>
  );
};

/** Magnifying glass; origin is the centre of the lens. */
export const Magnifier: React.FC<{ x: number; y: number; scale?: number }> = ({ x, y, scale = 1 }) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={44} y={34} width={30} height={96} rx={15} fill={C.sunShade} transform="rotate(-40 59 82)" />
    <circle cx={0} cy={0} r={64} fill={C.sun} />
    <circle cx={0} cy={0} r={46} fill={C.ice} opacity={0.9} />
    <path d="M -26 -18 A 30 30 0 0 1 -6 -32" stroke={C.white} strokeWidth={8} strokeLinecap="round" fill="none" />
  </g>
);

/** Padlock; origin is the centre of its body. */
export const Padlock: React.FC<{ x: number; y: number; scale?: number; fill?: string; shade?: string }> = ({
  x, y, scale = 1, fill = C.amber, shade = C.sunShade,
}) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <path d="M -34 -20 V -46 A 34 34 0 0 1 34 -46 V -20" stroke={C.muted} strokeWidth={14} fill="none" />
    <rect x={-52} y={-24} width={104} height={84} rx={18} fill={shade} />
    <rect x={-52} y={-24} width={88} height={84} rx={18} fill={fill} />
    <circle cx={-4} cy={12} r={10} fill={C.ink} />
    <rect x={-8} y={14} width={8} height={22} rx={4} fill={C.ink} />
  </g>
);

/** Round decision stamp; origin is its centre. */
export const Stamp: React.FC<{ x: number; y: number; scale?: number; text: string; fill?: string }> = ({
  x, y, scale = 1, text, fill = C.coral,
}) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={-22} y={-150} width={44} height={70} rx={14} fill={C.sunShade} />
    <ellipse cx={0} cy={-150} rx={46} ry={22} fill={C.sun} />
    <rect x={-86} y={-84} width={172} height={44} rx={14} fill={C.dim} />
    <rect x={-100} y={-44} width={200} height={70} rx={16} fill={fill} />
    <Txt x={0} y={-9} size={34} fill={C.ink} weight={900}>
      {text}
    </Txt>
  </g>
);

/** Price tag hanging from a string; origin is the hole. */
export const PriceTag: React.FC<{ x: number; y: number; scale?: number; text: string; angle?: number }> = ({
  x, y, scale = 1, text, angle = 0,
}) => (
  <g transform={`translate(${x} ${y}) rotate(${angle}) scale(${scale})`}>
    <path d="M 0 0 L 40 -40 H 230 A 20 20 0 0 1 250 -20 V 20 A 20 20 0 0 1 230 40 H 40 Z" fill={C.sunShade} transform="translate(0 7)" />
    <path d="M 0 0 L 40 -40 H 230 A 20 20 0 0 1 250 -20 V 20 A 20 20 0 0 1 230 40 H 40 Z" fill={C.sun} />
    <circle cx={34} cy={0} r={10} fill={C.ink} />
    <Txt x={145} y={0} size={46} fill={C.ink} weight={900}>
      {text}
    </Txt>
  </g>
);

/** Stopwatch; `hand` is the angle of the hand in degrees (0 = up). Origin is the dial centre. */
export const Stopwatch: React.FC<{ x: number; y: number; scale?: number; hand: number; accent: string; goal?: number }> = ({
  x, y, scale = 1, hand, accent, goal = 0,
}) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={-22} y={-150} width={44} height={36} rx={10} fill={C.muted} />
    <rect x={-40} y={-168} width={80} height={24} rx={12} fill={C.muted} />
    <circle cx={0} cy={0} r={130} fill={C.dim} />
    <circle cx={0} cy={0} r={112} fill={C.white} />
    {Array.from({ length: 12 }, (_, i) => (
      <rect key={i} x={-4} y={-104} width={8} height={18} rx={4} fill={C.paperShade} transform={`rotate(${i * 30})`} />
    ))}
    {/* Goal mark */}
    <rect x={-6} y={-112} width={12} height={30} rx={6} fill={C.ink} transform={`rotate(${goal})`} />
    <path d={arc(hand)} fill={accent} opacity={0.35} />
    <rect x={-6} y={-92} width={12} height={96} rx={6} fill={accent} transform={`rotate(${hand})`} />
    <circle cx={0} cy={0} r={14} fill={C.ink} />
  </g>
);

function arc(angle: number): string {
  const r = 96;
  const a = Math.max(0.01, Math.min(359.9, angle));
  const rad = ((a - 90) * Math.PI) / 180;
  const large = a > 180 ? 1 : 0;
  return `M 0 0 L 0 ${-r} A ${r} ${r} 0 ${large} 1 ${Math.cos(rad) * r} ${Math.sin(rad) * r} Z`;
}

/** Plug with a cable going down; origin is the plug body centre. */
export const Plug: React.FC<{ x: number; y: number; scale?: number; fill?: string; shade?: string }> = ({
  x, y, scale = 1, fill = C.teal, shade = C.tealShade,
}) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={-26} y={-90} width={14} height={40} rx={6} fill={C.muted} />
    <rect x={12} y={-90} width={14} height={40} rx={6} fill={C.muted} />
    <rect x={-60} y={-58} width={120} height={100} rx={26} fill={shade} />
    <rect x={-60} y={-58} width={100} height={100} rx={26} fill={fill} />
    <rect x={-14} y={42} width={28} height={50} rx={10} fill={shade} />
  </g>
);

/** Calendar card for "a meeting"; origin is the centre. */
export const MeetingCard: React.FC<{ x: number; y: number; scale?: number }> = ({ x, y, scale = 1 }) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <rect x={-170} y={-120} width={340} height={250} rx={28} fill={C.paperShade} transform="translate(0 10)" />
    <rect x={-170} y={-120} width={340} height={250} rx={28} fill={C.paper} />
    <rect x={-170} y={-120} width={340} height={70} rx={28} fill={C.coral} />
    <rect x={-170} y={-80} width={340} height={30} fill={C.coral} />
    <Txt x={0} y={-84} size={36} fill={C.ink} weight={900}>
      Meeting
    </Txt>
    {[-100, -40, 20, 80].map((cx, i) => (
      <g key={i}>
        <circle cx={cx} cy={-4} r={20} fill={[C.violet, C.teal, C.sun, C.coral][i]} />
        <rect x={cx - 26} y={22} width={52} height={36} rx={18} fill={[C.violet, C.teal, C.sun, C.coral][i]} opacity={0.7} />
      </g>
    ))}
    <Txt x={0} y={92} size={30} fill={C.dim} weight={800}>
      Let's discuss…
    </Txt>
  </g>
);
