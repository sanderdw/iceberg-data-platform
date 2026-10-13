import React from "react";
import { random, useCurrentFrame } from "remotion";
import { count, ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent } from "../components/Characters";
import { PrincipleTitle } from "../components/Principle";
import { Card, Chip, Pop, Stage, Tick, Txt } from "../components/ui";

const t = sceneTimes("s5");

const CAPABILITIES = [
  { capability: "Catalog", tool: "Polaris", fill: C.sun },
  { capability: "Storage", tool: "RustFS", fill: C.teal },
  { capability: "Sign-in", tool: "Keycloak", fill: C.violet },
  { capability: "Engine", tool: "DuckDB", fill: C.coral },
  { capability: "Workspace", tool: "marimo", fill: C.green },
];

/** A small calendar with a pin on the exit date. Origin is its centre. */
const ExitCalendar: React.FC<{ x: number; y: number; pin: number; label?: string }> = ({ x, y, pin, label = "Exit date" }) => (
  <g>
    <rect x={x - 130} y={y - 120 + 8} width={260} height={240} rx={24} fill={C.paperShade} />
    <rect x={x - 130} y={y - 120} width={260} height={240} rx={24} fill={C.paper} />
    <rect x={x - 130} y={y - 120} width={260} height={56} rx={24} fill={C.coral} />
    <rect x={x - 130} y={y - 90} width={260} height={26} fill={C.coral} />
    <Txt x={x} y={y - 90} size={26} weight={900} fill={C.ink}>
      {label}
    </Txt>
    {Array.from({ length: 12 }, (_, i) => (
      <rect key={i} x={x - 104 + (i % 4) * 54} y={y - 42 + Math.floor(i / 4) * 50} width={38} height={34} rx={8} fill={i === 9 ? C.coral : C.paperShade} opacity={i === 9 ? pin : 1} />
    ))}
    <g transform={`translate(0 ${mix(-160, 0, pin)})`} opacity={Math.min(1, pin * 2)}>
      <circle cx={x + 4} cy={y + 46} r={16} fill={C.red} />
      <rect x={x + 1} y={y + 46} width={6} height={30} rx={3} fill={C.dim} />
    </g>
  </g>
);

export const Scene5: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s5b2
  const capabilityRoad = t.at("s5b2", "Own the capability roadmap");
  const toolRoad = t.at("s5b2", "not the tool roadmap");
  const successor = t.at("s5b2", "has a successor");
  const exit = t.at("s5b2", "an exit date");
  // s5b3
  const filled = t.at("s5b3", "Each one is filled");
  const swapped = t.at("s5b3", "each tool can be swapped");
  // s5b4
  const bots = t.b.s5b4;
  const sweep = t.at("s5b4", "an agent swept");
  const releases = t.at("s5b4", "Thirteen releases");
  // s5b5
  const today = t.at("s5b5", "Docker today");
  const later = t.at("s5b5", "a Kubernetes design");
  // s5b6
  const policy = t.at("s5b6", "from version one");
  const exitDate = t.at("s5b6", "We set the exit date");

  return (
    <Stage>
      {/* s5b1: the market moves, in the background */}
      <g opacity={vis(t.b.s5b1, t.b.s5b2) * 0.5}>
        {Array.from({ length: 14 }, (_, i) => {
          const y = 140 + (i % 7) * 130;
          const speed = 3 + random(`ms${i}`) * 4;
          const x = ((random(`mx${i}`) * 2200 + (frame - t.b.s5b1) * speed) % 2300) - 200;
          return <rect key={i} x={x} y={y} width={90 + (i % 3) * 30} height={50} rx={14} fill={[C.dim, C.night2, C.violetShade][i % 3]} />;
        })}
      </g>
      <PrincipleTitle frame={frame} index={3} at={t.b.s5b1} dock={t.b.s5b2} />

      {/* s5b2: capability roadmap, successors and exit dates */}
      <g opacity={vis(t.b.s5b2, t.b.s5b3)}>
        <Pop p={pop(frame, capabilityRoad)} cx={760} cy={380}>
          <rect x={260} y={350} width={1000} height={60} rx={30} fill={C.coral} />
          {[0, 1, 2, 3].map((i) => (
            <circle key={i} cx={380 + i * 260} cy={380} r={18} fill={C.white} />
          ))}
          <Txt x={260} y={300} size={T.label} weight={900} anchor="start">
            Capability roadmap: ours
          </Txt>
        </Pop>
        <Pop p={pop(frame, toolRoad)} cx={760} cy={600}>
          <g opacity={0.45}>
            <rect x={260} y={570} width={1000} height={60} rx={30} fill={C.dim} />
            <Txt x={260} y={520} size={T.label} weight={900} anchor="start" fill={C.muted}>
              Tool roadmap: the market’s
            </Txt>
          </g>
          <line x1={250} y1={600} x2={1270} y2={600} stroke={C.red} strokeWidth={10} strokeLinecap="round" opacity={fade(frame, toolRoad + 12, 8)} />
        </Pop>
        <Pop p={pop(frame, successor)} cx={760} cy={820}>
          <Chip x={520} y={820} text="Tool today" fill={C.white} shade={C.paperShade} size={T.small} />
          <path d="M 680 820 H 820" stroke={C.muted} strokeWidth={10} strokeLinecap="round" />
          <path d="M 800 800 L 830 820 L 800 840" stroke={C.muted} strokeWidth={10} fill="none" strokeLinecap="round" strokeLinejoin="round" />
          <Chip x={990} y={820} text="Successor" fill={C.coral} shade={C.coralShade} size={T.small} />
        </Pop>
        <Pop p={pop(frame, exit - 4)} cx={1560} cy={560}>
          <ExitCalendar x={1560} y={560} pin={ease(frame, exit + 4, exit + 18)} />
          <Txt x={1560} y={740} size={T.small} weight={900}>
            set by us
          </Txt>
        </Pop>
      </g>

      {/* s5b3: a map of capabilities, each filled by a swappable tool */}
      <g opacity={vis(t.b.s5b3, t.b.s5b4)}>
        {CAPABILITIES.map((c, i) => {
          const x = 960 + (i - 2) * 330;
          const isSwap = i === 3;
          return (
            <g key={c.capability}>
              <Pop p={pop(frame, t.b.s5b3 + 6 + i * 5)} cx={x} cy={560}>
                <rect x={x - 140} y={330 + 10} width={280} height={420} rx={30} fill={C.ink} />
                <rect x={x - 140} y={330} width={280} height={420} rx={30} fill={C.night2} />
                <Txt x={x} y={390} size={T.small + 2} weight={900} fill={c.fill}>
                  {c.capability}
                </Txt>
                <rect x={x - 110} y={460} width={220} height={220} rx={26} fill="none" stroke={C.dim} strokeWidth={6} strokeDasharray="14 12" />
              </Pop>
              <Pop p={pop(frame, filled + i * 4)} cx={x} cy={570}>
                <g transform={`translate(0 ${isSwap ? -360 * ease(frame, swapped + 4, swapped + 20) + 360 * ease(frame, swapped + 22, swapped + 40) : 0})`}>
                  <rect x={x - 100} y={480} width={200} height={180} rx={24} fill={c.fill} />
                  <Txt x={x} y={570} size={30} weight={900} fill={C.ink}>
                    {c.tool}
                  </Txt>
                </g>
              </Pop>
            </g>
          );
        })}
        <Txt x={960} y={880} size={T.label} weight={800} fill={C.muted} opacity={fade(frame, swapped, 10)}>
          Swap the tool, keep the capability
        </Txt>
      </g>

      {/* s5b4: weekly bots, a sweep for rot, and the release pace */}
      <g opacity={vis(t.b.s5b4, t.b.s5b5)}>
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, i) => (
          <Pop key={day} p={pop(frame, bots + i * 2)} cx={300 + i * 96} cy={400}>
            <rect x={258 + i * 96} y={350} width={84} height={100} rx={16} fill={i === 0 ? C.violet : C.night2} />
            <Txt x={300 + i * 96} y={378} size={24} weight={900}>
              {day}
            </Txt>
            {i === 0 && <Tick x={300} y={420} r={18} />}
          </Pop>
        ))}
        <Pop p={pop(frame, bots + 10)} cx={540} cy={560}>
          <Agent x={540} y={580} scale={0.6} frame={frame} />
          <Txt x={540} y={680} size={T.small} weight={900}>
            Weekly dependency bots
          </Txt>
        </Pop>
        {/* The sweep */}
        <g opacity={fade(frame, sweep - 6, 8)}>
          <rect x={1080} y={330} width={420} height={300} rx={26} fill={C.teal} />
          <Txt x={1290} y={480} size={T.h2} weight={900} fill={C.ink}>
            repo
          </Txt>
          {Array.from({ length: 10 }, (_, i) => (
            <circle
              key={i}
              cx={mix(1120 + (i % 5) * 80, 1700 + (i % 5) * 40, ease(frame, sweep + 6 + i, sweep + 26 + i))}
              cy={600 - (i % 3) * 30}
              r={10 + (i % 3) * 4}
              fill={C.muted}
              opacity={fadeOut(frame, sweep + 22 + i, 8)}
            />
          ))}
          <g transform={`translate(${mix(0, 520, ease(frame, sweep + 4, sweep + 30))} 0) rotate(-20 1100 560)`}>
            <rect x={1090} y={400} width={16} height={190} rx={8} fill={C.sunShade} />
            <path d="M 1060 586 H 1136 L 1150 650 H 1046 Z" fill={C.sun} />
          </g>
          <Txt x={1290} y={700} size={T.small} weight={900}>
            Swept for anything stale
          </Txt>
        </g>
        <g opacity={fade(frame, releases, 8)}>
          <Txt x={960} y={840} size={T.big - 40} weight={900} fill={C.coral}>
            {`${count(frame, releases, 13, 30)} releases`}
          </Txt>
          <Txt x={960} y={940} size={T.small} weight={800} fill={C.muted}>
            in just over three weeks
          </Txt>
        </g>
      </g>

      {/* s5b5: the successor is named before it's needed */}
      <g opacity={vis(t.b.s5b5, t.b.s5b6)}>
        <Txt x={960} y={230} size={T.h2} weight={900} opacity={fade(frame, t.b.s5b5, 10)}>
          The successor, named before it’s needed
        </Txt>
        <rect x={160} y={600} width={1600} height={60} rx={30} fill={C.dim} />
        <Pop p={pop(frame, today)} cx={600} cy={460}>
          <Card x={600} y={460} w={460} h={220} fill={C.night2}>
            <Txt x={600} y={410} size={T.small} weight={900} fill={C.muted}>
              Today
            </Txt>
            <Txt x={600} y={480} size={T.h2} weight={900}>
              Docker
            </Txt>
          </Card>
          <circle cx={600} cy={630} r={22} fill={C.white} />
        </Pop>
        <Pop p={pop(frame, later)} cx={1320} cy={460}>
          <rect x={1080} y={340} width={480} height={240} rx={30} fill="none" stroke={C.coral} strokeWidth={8} strokeDasharray="20 16" />
          <Txt x={1320} y={410} size={T.small} weight={900} fill={C.coral}>
            Next · design ready
          </Txt>
          <Txt x={1320} y={480} size={T.h2} weight={900}>
            Kubernetes
          </Txt>
          <circle cx={1320} cy={630} r={22} fill={C.coral} />
        </Pop>
      </g>

      {/* s5b6: old and new contract side by side; we set the exit date */}
      <g opacity={vis(t.b.s5b6, t.length)}>
        <Pop p={pop(frame, t.b.s5b6)} cx={600} cy={520}>
          <Card x={480} y={520} w={300} h={260} fill={C.night2}>
            <Txt x={480} y={470} size={T.small} weight={900} fill={C.muted}>
              Contract
            </Txt>
            <Txt x={480} y={540} size={T.h1} weight={900}>
              v1
            </Txt>
          </Card>
        </Pop>
        <Pop p={pop(frame, policy)} cx={880} cy={520}>
          <Card x={880} y={520} w={300} h={260} fill={C.coral} shade={C.coralShade}>
            <Txt x={880} y={470} size={T.small} weight={900} fill={C.ink}>
              Contract
            </Txt>
            <Txt x={880} y={540} size={T.h1} weight={900} fill={C.ink}>
              v2
            </Txt>
          </Card>
          <Txt x={680} y={720} size={T.small} weight={900} fill={C.muted}>
            side by side for at least a release
          </Txt>
        </Pop>
        <Pop p={pop(frame, exitDate - 6)} cx={1440} cy={520}>
          <ExitCalendar x={1440} y={520} pin={ease(frame, exitDate + 2, exitDate + 16)} label="v1 exit" />
          <Txt x={1440} y={700} size={T.small} weight={900}>
            We set the date
          </Txt>
        </Pop>
      </g>
    </Stage>
  );
};
