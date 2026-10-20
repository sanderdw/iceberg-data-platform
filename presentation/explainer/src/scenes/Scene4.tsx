import React from "react";
import { random, useCurrentFrame } from "remotion";
import { count, ease, fade, fadeOut, formatNumber, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent, Badge, Person } from "../components/Characters";
import { Plug } from "../components/Icons";
import { Chip, Cross, Doc, Pop, Source, Stage, Tick, Txt } from "../components/ui";

const t = sceneTimes("s4");

export const PRINCIPLES = [
  { lines: ["Clean", "UX"], fill: C.sun, shade: C.sunShade },
  { lines: ["API", "first"], fill: C.teal, shade: C.tealShade },
  { lines: ["AI", "native"], fill: C.violet, shade: C.violetShade },
  { lines: ["Open", "source"], fill: C.green, shade: C.greenShade },
  { lines: ["Loosely", "coupled"], fill: C.coral, shade: C.coralShade },
];

/** The icon on each principle tile. */
const PrincipleIcon: React.FC<{ i: number; x: number; y: number; frame: number }> = ({ i, x, y, frame }) => {
  switch (i) {
    case 0: // Clean UX: a tidy window
      return (
        <g>
          <rect x={x - 54} y={y - 40} width={108} height={80} rx={14} fill={C.ink} />
          <rect x={x - 54} y={y - 40} width={108} height={22} rx={11} fill={C.white} opacity={0.85} />
          <rect x={x - 36} y={y - 4} width={72} height={10} rx={5} fill={C.white} opacity={0.7} />
          <rect x={x - 36} y={y + 16} width={44} height={10} rx={5} fill={C.white} opacity={0.5} />
        </g>
      );
    case 1:
      return (
        <Txt x={x} y={y} size={72} weight={900} fill={C.ink}>
          {"{ }"}
        </Txt>
      );
    case 2:
      return <Agent x={x} y={y + 12} scale={0.5} frame={frame} />;
    case 3:
      return (
        <Txt x={x} y={y} size={64} weight={900} fill={C.ink}>
          {"</>"}
        </Txt>
      );
    default: // Loosely coupled: two blocks joined by a link
      return (
        <g>
          <rect x={x - 66} y={y - 30} width={52} height={60} rx={14} fill={C.ink} />
          <rect x={x + 14} y={y - 30} width={52} height={60} rx={14} fill={C.ink} />
          <rect x={x - 18} y={y - 6} width={36} height={12} rx={6} fill={C.white} opacity={0.85} />
        </g>
      );
  }
};

export const PrincipleTiles: React.FC<{
  frame: number; at: number; y: number; size?: number; ticks?: number[]; spacing?: number;
}> = ({ frame, at, y, size = 270, ticks = [], spacing = 320 }) => (
  <g>
    {PRINCIPLES.map((p, i) => {
      const x = 960 + (i - 2) * spacing;
      const s = size / 270;
      return (
        <Pop key={i} p={pop(frame, at + i * 5)} cx={x} cy={y}>
          <g transform={`translate(${x} ${y}) scale(${s}) translate(${-x} ${-y})`}>
            <rect x={x - 125} y={y - 145 + 12} width={250} height={290} rx={36} fill={p.shade} />
            <rect x={x - 125} y={y - 145} width={250} height={290} rx={36} fill={p.fill} />
            <PrincipleIcon i={i} x={x} y={y - 55} frame={frame} />
            {p.lines.map((line, j) => (
              <Txt key={line} x={x} y={y + 50 + j * 46} size={42} weight={900} fill={C.ink}>
                {line}
              </Txt>
            ))}
            {(ticks[i] ?? 0) > 0 && (
              <Pop p={ticks[i]} cx={x + 110} cy={y - 130}>
                <Tick x={x + 110} y={y - 130} r={34} />
              </Pop>
            )}
          </g>
        </Pop>
      );
    })}
  </g>
);

/** A dashed connection that draws itself from a to b. */
const Wire: React.FC<{ x1: number; y1: number; x2: number; y2: number; p: number; color?: string }> = ({
  x1, y1, x2, y2, p, color = C.muted,
}) => (
  <line
    x1={x1}
    y1={y1}
    x2={mix(x1, x2, p)}
    y2={mix(y1, y2, p)}
    stroke={color}
    strokeWidth={7}
    strokeLinecap="round"
    strokeDasharray="4 16"
    opacity={p > 0 ? 1 : 0}
  />
);

export const Scene4: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s4b1
  const checked = t.at("s4b1", "every change");
  const ticks = [0, 1, 2, 3, 4].map((i) => pop(frame, checked + 8 + i * 5));
  // s4b2
  const script = t.at("s4b2", "So a script");
  // s4b3
  const tool = t.at("s4b3", "Every API call");
  const identity = t.at("s4b3", "with the person");
  // s4b4
  const drift = t.at("s4b4", "If a tool");
  const fails = t.at("s4b4", "a test fails");
  // s4b5
  const servers = t.at("s4b5", "with more than");

  return (
    <Stage>
      {/* s4b1: five principles */}
      <g opacity={vis(t.b.s4b1, t.b.s4b2)}>
        <Txt x={960} y={180} size={T.h1} weight={900}>
          Five principles
        </Txt>
        <PrincipleTiles frame={frame} at={t.b.s4b1 + 10} y={560} ticks={ticks} />
        <Pop p={pop(frame, checked)} cx={960} cy={860}>
          <Chip x={mix(320, 1600, ease(frame, checked + 4, checked + 34))} y={860} text="a change" fill={C.white} shade={C.paperShade} />
        </Pop>
      </g>

      {/* s4b2 and s4b3: buttons, scripts and agents use the same routes */}
      <g opacity={vis(t.b.s4b2, t.b.s4b4)}>
        <Txt x={960} y={150} size={T.h1} weight={900} opacity={fadeOut(frame, t.b.s4b3 - 6, 8)}>
          API first
        </Txt>
        <Txt x={960} y={150} size={T.h1} weight={900} fill={C.violet} opacity={fade(frame, t.b.s4b3, 8)}>
          AI native
        </Txt>
        {/* A person and their button */}
        <Pop p={pop(frame, t.b.s4b2 + 2)} cx={330} cy={760}>
          <Person x={330} y={930} scale={0.95} frame={frame} />
          <Chip x={330} y={480} text="Run pipeline" fill={C.coral} shade={C.coralShade} />
        </Pop>
        {frame > identity && (
          <Pop p={pop(frame, identity + 4)} cx={350} cy={850}>
            <Badge x={350} y={850} scale={1.3} />
          </Pop>
        )}
        <Wire x1={480} y1={480} x2={800} y2={480} p={ease(frame, t.b.s4b2 + 12, t.b.s4b2 + 30)} />
        {/* The route */}
        <Pop p={pop(frame, t.b.s4b2 + 22)} cx={960} cy={480}>
          <Chip x={960} y={480} text="POST /runs" fill={C.teal} shade={C.tealShade} size={T.label} />
        </Pop>
        {/* A script presses the same route */}
        <g opacity={fadeOut(frame, t.b.s4b3 - 4, 10)}>
          <Pop p={pop(frame, script)} cx={1590} cy={480}>
            <Doc x={1590} y={480} w={250} h={300} title="script.py" accent={C.teal} lines={4} />
          </Pop>
          <Wire x1={1460} y1={480} x2={1120} y2={480} p={ease(frame, script + 8, script + 24)} />
        </g>
        {/* The same operation as an MCP tool, used by an agent */}
        <g opacity={fade(frame, tool, 8)}>
          <Wire x1={960} y1={530} x2={960} y2={720} p={ease(frame, tool, tool + 14)} color={C.violet} />
          <Pop p={pop(frame, tool + 10)} cx={960} cy={780}>
            <Chip x={960} y={780} text="MCP tool · run" fill={C.violet} shade={C.violetShade} color={C.white} size={T.label} />
          </Pop>
          <Pop p={pop(frame, tool + 18)} cx={1560} cy={760}>
            <Agent x={1560} y={760} scale={1.2} frame={frame} />
          </Pop>
          <Wire x1={1440} y1={780} x2={1150} y2={780} p={ease(frame, tool + 22, tool + 36)} color={C.violet} />
          {frame > identity && (
            <Pop p={pop(frame, identity + 10)} cx={1560} cy={800}>
              <Badge x={1560} y={800} scale={1.3} />
            </Pop>
          )}
        </g>
      </g>

      {/* s4b4: thirty tools, each paired with its route; one drifts and a test fails */}
      <g opacity={vis(t.b.s4b4, t.b.s4b5)}>
        <Txt x={960} y={170} size={T.h1} weight={900} fill={C.violet}>
          {`${count(frame, t.b.s4b4 + 4, 30, 30)} tools`}
        </Txt>
        {Array.from({ length: 30 }, (_, i) => {
          const col = i % 6;
          const row = Math.floor(i / 6);
          const x = 960 + (col - 2.5) * 250;
          const y = 360 + row * 120;
          const isDrift = i === 15;
          const apart = isDrift ? ease(frame, drift, drift + 20) : 0;
          return (
            <Pop key={i} p={pop(frame, t.b.s4b4 + 6 + i)} cx={x} cy={y}>
              <rect x={x - 96} y={y - 26} width={92} height={52} rx={26} fill={C.teal} />
              <rect x={x + 4 + apart * 50} y={y - 26 + apart * 30} width={92} height={52} rx={26} fill={C.violet} transform={`rotate(${apart * 16} ${x + 50} ${y})`} />
            </Pop>
          );
        })}
        <Pop p={pop(frame, fails)} cx={960 + (3 - 2.5) * 250 + 130} cy={600}>
          <Cross x={960 + (3 - 2.5) * 250 + 130} y={600} r={44} />
        </Pop>
        <Pop p={pop(frame, fails + 4)} cx={960} cy={960}>
          <Chip x={960} y={960} text="test fails" fill={C.red} shade={C.coralShade} />
        </Pop>
      </g>

      {/* s4b5: the open protocol */}
      <g opacity={vis(t.b.s4b5, t.length)}>
        {Array.from({ length: 140 }, (_, i) => {
          const angle = random(`a${i}`) * Math.PI * 2;
          const r = 230 + random(`r${i}`) * 300;
          const appear = pop(frame, servers + Math.floor(random(`d${i}`) * 30));
          return (
            <circle
              key={i}
              cx={960 + Math.cos(angle) * r * appear}
              cy={600 + Math.sin(angle) * r * 0.62 * appear}
              r={7 + (i % 3) * 3}
              fill={[C.teal, C.violet, C.sun, C.coral][i % 4]}
              opacity={Math.min(1, appear)}
            />
          );
        })}
        <Pop p={pop(frame, t.b.s4b5 + 4)} cx={960} cy={600}>
          <Plug x={960} y={600} scale={1.6} />
        </Pop>
        <Txt x={960} y={170} size={T.h1} weight={900} fill={C.teal} opacity={fade(frame, servers, 8)}>
          {`${formatNumber(count(frame, servers, 10000, 40))}+`}
        </Txt>
        <Txt x={960} y={255} size={T.label} weight={800} opacity={fade(frame, servers, 8)}>
          public MCP servers
        </Txt>
        <Pop p={pop(frame, t.at("s4b5", "it had moved"))} cx={960} cy={940}>
          <Chip x={960} y={940} text="Linux Foundation · Agentic AI Foundation" fill={C.white} shade={C.paperShade} size={T.small} />
        </Pop>
        <Source text="Source: Anthropic, December 2025" opacity={fade(frame, t.b.s4b5, 10)} />
      </g>
    </Stage>
  );
};
