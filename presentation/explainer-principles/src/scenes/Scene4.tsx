import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Padlock } from "../components/Icons";
import { PrincipleTitle } from "../components/Principle";
import { Card, Chip, Cross, Doc, Pop, Stage, Tick, Txt } from "../components/ui";

const t = sceneTimes("s4");

const SERVICES = [
  { name: "polaris", extension: false },
  { name: "rustfs", extension: false },
  { name: "keycloak", extension: false },
  { name: "user-portal", extension: false },
  { name: "dbt-portal", extension: true },
  { name: "dbt-launcher", extension: true },
];
const STANDARDS = ["Iceberg", "S3", "OpenID Connect", "MCP"];

export const Scene4: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s4b2: three habits, then the cage opens
  const why = t.at("s4b2", "Write down why");
  const run = t.at("s4b2", "Build it");
  const compose = t.at("s4b2", "design for composability");
  const cage = t.at("s4b2", "tomorrow's cage");
  // s4b3
  const chosen = t.at("s4b3", "what we chose");
  const notChosen = t.at("s4b3", "what we didn't choose");
  const andWhy = t.at("s4b3", "and why");
  // s4b4
  const noInternet = t.at("s4b4", "no internet");
  const says = t.at("s4b4", "The record says why");
  // s4b5
  const included = t.at("s4b5", "extensions included");
  // s4b6
  const standards = STANDARDS.map((s) => t.at("s4b6", s));
  const replaced = t.at("s4b6", "So one part");
  const swap = ease(frame, replaced + 6, replaced + 36);

  return (
    <Stage>
      <PrincipleTitle frame={frame} index={2} at={t.b.s4b1} dock={t.b.s4b2} />

      {/* s4b2 */}
      <g opacity={vis(t.b.s4b2, t.b.s4b3)}>
        <Pop p={pop(frame, why)} cx={420} cy={520}>
          <Doc x={420} y={520} w={280} h={340} title="Decision" accent={C.violet} lines={4} titleSize={34} />
          <Txt x={420} y={760} size={T.small} weight={900}>
            Write down why
          </Txt>
        </Pop>
        <Pop p={pop(frame, run)} cx={960} cy={520}>
          {/* A gauge: you run what you build */}
          <path d="M 820 600 A 140 140 0 0 1 1100 600" stroke={C.dim} strokeWidth={36} fill="none" strokeLinecap="round" />
          <path d="M 820 600 A 140 140 0 0 1 1060 500" stroke={C.green} strokeWidth={36} fill="none" strokeLinecap="round" />
          <line x1={960} y1={600} x2={mix(880, 1040, ease(frame, run + 6, run + 30))} y2={500} stroke={C.white} strokeWidth={12} strokeLinecap="round" />
          <circle cx={960} cy={600} r={20} fill={C.white} />
          <Txt x={960} y={760} size={T.small} weight={900}>
            Build it, run it
          </Txt>
        </Pop>
        <Pop p={pop(frame, compose)} cx={1500} cy={520}>
          {[0, 1, 2].map((i) => (
            <g key={i}>
              <rect x={1370 + i * 90} y={470} width={80} height={100} rx={18} fill={[C.violet, C.teal, C.sun][i]} />
              {i < 2 && <rect x={1440 + i * 90} y={510} width={30} height={20} rx={10} fill={C.white} />}
            </g>
          ))}
          {/* The cage opens and fades */}
          <g opacity={fadeOut(frame, cage + 8, 14)} transform={`translate(0 ${-140 * ease(frame, cage, cage + 20)})`}>
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <rect key={i} x={1350 + i * 64} y={420} width={10} height={200} rx={5} fill={C.muted} />
            ))}
            <rect x={1340} y={410} width={340} height={14} rx={7} fill={C.muted} />
          </g>
          <Txt x={1500} y={760} size={T.small} weight={900}>
            No cage tomorrow
          </Txt>
        </Pop>
      </g>

      {/* s4b3: a decision record */}
      <g opacity={vis(t.b.s4b3, t.b.s4b4)}>
        <Pop p={pop(frame, t.b.s4b3)} cx={960} cy={570}>
          <rect x={560} y={270} width={800} height={620} rx={26} fill={C.paperShade} />
          <rect x={560} y={260} width={800} height={620} rx={26} fill={C.paper} />
          <rect x={560} y={260} width={800} height={24} rx={12} fill={C.violet} />
          <Txt x={620} y={340} size={T.h2} weight={900} fill={C.ink} anchor="start">
            Decision record
          </Txt>
          {[
            { label: "Chosen", at: chosen, mark: "tick" },
            { label: "Not chosen", at: notChosen, mark: "cross" },
            { label: "Why", at: andWhy, mark: "why" },
          ].map((row, i) => {
            const y = 470 + i * 140;
            return (
              <Pop key={row.label} p={pop(frame, row.at)} cx={700} cy={y}>
                {row.mark === "tick" && <Tick x={650} y={y} r={30} />}
                {row.mark === "cross" && <Cross x={650} y={y} r={30} fill={C.muted} />}
                {row.mark === "why" && (
                  <g>
                    <circle cx={650} cy={y} r={30} fill={C.violet} />
                    <Txt x={650} y={y} size={34} weight={900}>
                      ?
                    </Txt>
                  </g>
                )}
                <Txt x={706} y={y - 18} size={T.label} weight={900} fill={C.ink} anchor="start">
                  {row.label}
                </Txt>
                <rect x={706} y={y + 20} width={420 - i * 60} height={16} rx={8} fill={C.paperShade} />
              </Pop>
            );
          })}
        </Pop>
      </g>

      {/* s4b4: runs have no internet, and the record says why */}
      <g opacity={vis(t.b.s4b4, t.b.s4b5)}>
        <Pop p={pop(frame, t.b.s4b4)} cx={600} cy={560}>
          <Card x={600} y={560} w={520} h={320} fill={C.night2}>
            <Txt x={600} y={450} size={T.label} weight={900}>
              Pipeline runs
            </Txt>
            <rect x={410} y={500} width={380} height={22} rx={11} fill={C.teal} opacity={0.8} />
            <rect x={410} y={540} width={280} height={22} rx={11} fill={C.teal} opacity={0.5} />
          </Card>
        </Pop>
        <Pop p={pop(frame, noInternet)} cx={860} cy={400}>
          <Padlock x={870} y={390} scale={0.9} />
          <Chip x={600} y={790} text="no internet, on purpose" fill={C.amber} shade={C.sunShade} size={T.small} />
        </Pop>
        <Pop p={pop(frame, says - 6)} cx={1340} cy={560}>
          <Doc x={1340} y={560} w={340} h={420} title="Decision record" accent={C.violet} lines={6} titleSize={34} />
        </Pop>
        <Pop p={pop(frame, says + 6, 220, 10)} cx={1400} cy={680}>
          <g transform="rotate(-12 1400 680)">
            <rect x={1290} y={630} width={220} height={100} rx={20} fill="none" stroke={C.violet} strokeWidth={10} />
            <Txt x={1400} y={680} size={T.h2} weight={900} fill={C.violet}>
              WHY
            </Txt>
          </g>
        </Pop>
      </g>

      {/* s4b5: we run what we build */}
      <g opacity={vis(t.b.s4b5, t.b.s4b6)}>
        <Pop p={pop(frame, t.b.s4b5)} cx={960} cy={570}>
          <rect x={460} y={250} width={1000} height={660} rx={30} fill={C.ink} opacity={0.7} />
          <rect x={460} y={240} width={1000} height={660} rx={30} fill={C.night2} />
          <Txt x={520} y={300} size={T.label} weight={900} anchor="start">
            Infrastructure
          </Txt>
          {SERVICES.map((s, i) => {
            const y = 390 + i * 82;
            const highlight = s.extension ? fade(frame, included, 8) : 0;
            return (
              <g key={s.name} opacity={fade(frame, t.b.s4b5 + 6 + i * 4, 6)}>
                <rect x={500} y={y - 32} width={920} height={64} rx={16} fill={C.violet} opacity={0.35 * highlight} />
                <circle cx={540} cy={y} r={14} fill={C.green} />
                <Txt x={576} y={y} size={T.small} weight={800} anchor="start">
                  {s.name}
                </Txt>
                <rect x={900} y={y - 10} width={200} height={20} rx={10} fill={C.dim} />
                <rect x={900} y={y - 10} width={60 + ((i * 47) % 120)} height={20} rx={10} fill={C.teal} />
                <rect x={1130} y={y - 10} width={160} height={20} rx={10} fill={C.dim} />
                <rect x={1130} y={y - 10} width={40 + ((i * 31) % 100)} height={20} rx={10} fill={C.sun} />
                {s.extension && (
                  <g opacity={highlight}>
                    <rect x={1310} y={y - 20} width={96} height={40} rx={20} fill={C.violet} />
                    <Txt x={1358} y={y} size={22} weight={900}>
                      ext.
                    </Txt>
                  </g>
                )}
              </g>
            );
          })}
        </Pop>
      </g>

      {/* s4b6: open standards between the parts; one part is swapped */}
      <g opacity={vis(t.b.s4b6, t.length)}>
        <Pop p={pop(frame, t.b.s4b6)} cx={960} cy={560}>
          <circle cx={960} cy={560} r={110} fill={C.violetShade} />
          <circle cx={960} cy={550} r={110} fill={C.violet} />
          <Txt x={960} y={550} size={T.small} weight={900}>
            platform
          </Txt>
        </Pop>
        {STANDARDS.map((name, i) => {
          const angle = (-135 + i * 90) * (Math.PI / 180);
          const x = 960 + Math.cos(angle) * 520;
          const y = 560 + Math.sin(angle) * 300;
          const isSwap = i === 1;
          const out = isSwap ? swap : 0;
          return (
            <g key={name}>
              <line x1={960} y1={560} x2={mix(960, x, ease(frame, standards[i], standards[i] + 12))} y2={mix(560, y, ease(frame, standards[i], standards[i] + 12))} stroke={C.muted} strokeWidth={8} strokeDasharray="4 16" strokeLinecap="round" />
              <Pop p={pop(frame, standards[i])} cx={x} cy={y}>
                <Chip x={x * 0.64 + 960 * 0.36} y={y * 0.64 + 560 * 0.36} text={name} fill={C.white} shade={C.paperShade} size={T.small} />
                <g transform={`translate(${isSwap ? out * 500 : 0} 0)`} opacity={1 - out}>
                  <rect x={x - 80} y={y - 60} width={160} height={120} rx={24} fill={[C.sun, C.teal, C.coral, C.green][i]} />
                </g>
                {isSwap && (
                  <g transform={`translate(0 ${(1 - ease(frame, replaced + 24, replaced + 48)) * -420})`} opacity={fade(frame, replaced + 24, 8)}>
                    <rect x={x - 80} y={y - 60} width={160} height={120} rx={24} fill={C.sun} />
                    <Txt x={x} y={y} size={26} weight={900} fill={C.ink}>
                      new
                    </Txt>
                  </g>
                )}
              </Pop>
            </g>
          );
        })}
        <Txt x={960} y={970} size={T.label} weight={800} fill={C.muted} opacity={fade(frame, replaced, 10)}>
          Replace one part without touching the others
        </Txt>
      </g>
    </Stage>
  );
};
