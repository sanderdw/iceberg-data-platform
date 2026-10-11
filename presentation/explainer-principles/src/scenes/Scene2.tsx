import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent, Person } from "../components/Characters";
import { CodeScreen } from "../components/Desk";
import { ItemCard, PrincipleTitle } from "../components/Principle";
import { Chip, Doc, Pop, Stage, Tick, Txt } from "../components/ui";

const t = sceneTimes("s2");

/** A two-option question card; `chosen` (0..1) highlights option `pick`. */
const Question: React.FC<{ frame: number; at: number; x: number; y: number; q: string; options: string[]; pick: number; chosen: number }> = ({
  frame, at, x, y, q, options, pick, chosen,
}) => (
  <Pop p={pop(frame, at)} cx={x} cy={y}>
    <rect x={x - 330} y={y - 150 + 10} width={660} height={300} rx={34} fill={C.ink} />
    <rect x={x - 330} y={y - 150} width={660} height={300} rx={34} fill={C.night2} />
    <Txt x={x} y={y - 85} size={T.label} weight={900}>
      {q}
    </Txt>
    {options.map((option, i) => {
      const on = i === pick ? chosen : 0;
      const ox = x - 155 + i * 310;
      return (
        <g key={option}>
          <rect x={ox - 140} y={y - 10} width={280} height={96} rx={26} fill={on > 0.5 ? C.sun : C.dim} opacity={i === pick ? 1 : mix(1, 0.45, chosen)} />
          <Txt x={ox} y={y + 38} size={T.small} weight={900} fill={on > 0.5 ? C.ink : C.white}>
            {option}
          </Txt>
          {on > 0.5 && <Tick x={ox + 130} y={y - 6} r={26} />}
        </g>
      );
    })}
  </Pop>
);

export const Scene2: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s2b2: what AI does, what you do
  const agentItems = ["Code", "Tests", "Analysis", "First drafts"].map((text, i) => ({
    text,
    at: t.at("s2b2", ["code", "tests", "analysis", "first drafts"][i]),
  }));
  const youItems = ["Framing", "Trade-offs", "What goes live"].map((text, i) => ({
    text,
    at: t.at("s2b2", ["framing", "trade-offs", "everything that goes live"][i]),
  }));
  // s2b3
  const writes = t.at("s2b3", "an AI agent writes");
  // s2b4
  const k8s = t.at("s2b4", "Kubernetes now");
  const token = t.at("s2b4", "A two-hour token");
  const call = t.at("s2b4", "Each answer");
  // s2b5
  const tags = t.at("s2b5", "when a person tags it");
  const passes = t.at("s2b5", "the full test run passes");
  const steps = ["lint", "unit", "contract", "build", "integration", "smoke"];

  return (
    <Stage>
      <PrincipleTitle frame={frame} index={0} at={t.b.s2b1} dock={t.b.s2b2} />

      {/* s2b2 */}
      <g opacity={vis(t.b.s2b2, t.b.s2b3)}>
        <Txt x={520} y={250} size={T.h2} weight={900} fill={C.sun}>
          AI operates
        </Txt>
        <Txt x={1400} y={250} size={T.h2} weight={900} fill={C.sun}>
          You architect
        </Txt>
        <Pop p={pop(frame, t.b.s2b2)} cx={250} cy={600}>
          <Agent x={250} y={620} scale={1.2} frame={frame} />
        </Pop>
        {agentItems.map((item, i) => (
          <ItemCard key={item.text} frame={frame} at={item.at} x={600} y={380 + i * 120} text={item.text} w={340} />
        ))}
        <line x1={960} y1={300} x2={960} y2={900} stroke={C.dim} strokeWidth={6} strokeDasharray="10 18" />
        <Pop p={pop(frame, youItems[0].at - 6)} cx={1690} cy={700}>
          <Person x={1690} y={860} scale={1.0} frame={frame} />
        </Pop>
        {youItems.map((item, i) => (
          <ItemCard key={item.text} frame={frame} at={item.at} x={1330} y={440 + i * 120} text={item.text} w={380} fill={C.sun} shade={C.sunShade} color={C.ink} />
        ))}
      </g>

      {/* s2b3: the agent at work */}
      <g opacity={vis(t.b.s2b3, t.b.s2b4)}>
        <Pop p={pop(frame, t.b.s2b3)} cx={380} cy={600}>
          <Agent x={380} y={620} scale={1.5} frame={frame} />
        </Pop>
        <g transform="translate(860 700)">
          <rect x={-210} y={-290} width={420} height={270} rx={22} fill={C.dim} />
          <rect x={-194} y={-274} width={388} height={238} rx={12} fill={C.ink} />
          <CodeScreen frame={frame} from={writes} />
        </g>
        <Txt x={860} y={760} size={T.small} weight={900}>
          Code
        </Txt>
        {[0, 1, 2, 3, 4].map((i) => (
          <Pop key={i} p={pop(frame, writes + 14 + i * 5)} cx={1220} cy={400 + i * 64}>
            <Tick x={1220} y={400 + i * 64} r={24} />
            <rect x={1256} y={390 + i * 64} width={90} height={18} rx={9} fill={C.muted} />
          </Pop>
        ))}
        <Txt x={1270} y={760} size={T.small} weight={900}>
          Tests
        </Txt>
        <Pop p={pop(frame, writes + 30)} cx={1600} cy={520}>
          <Doc x={1600} y={530} w={240} h={300} title="Draft" accent={C.sun} lines={4} titleSize={32} />
        </Pop>
        <Txt x={1600} y={760} size={T.small} weight={900}>
          First drafts
        </Txt>
        <Txt x={960} y={880} size={T.label} weight={800} fill={C.muted} opacity={fade(frame, writes, 10)}>
          The agent writes most of it
        </Txt>
      </g>

      {/* s2b4: the real questions come back to a person */}
      <g opacity={vis(t.b.s2b4, t.b.s2b5)}>
        <Question frame={frame} at={k8s - 4} x={560} y={500} q="Kubernetes now?" options={["Build it", "Only a design"]} pick={1} chosen={ease(frame, token - 10, token - 2)} />
        <Question frame={frame} at={token - 4} x={1360} y={500} q="Runs over an hour?" options={["Two-hour token", "A setting"]} pick={1} chosen={ease(frame, call - 10, call - 2)} />
        <Pop p={pop(frame, call)} cx={960} cy={830}>
          <Chip x={960} y={830} text="A human call" fill={C.sun} shade={C.sunShade} size={T.label} />
        </Pop>
      </g>

      {/* s2b5: a person tags the release; it ships after the full test run */}
      <g opacity={vis(t.b.s2b5, t.length)}>
        <Pop p={pop(frame, t.b.s2b5)} cx={300} cy={700}>
          <Person x={300} y={900} scale={1.0} frame={frame} />
        </Pop>
        <g transform={`translate(${mix(-60, 0, ease(frame, tags, tags + 14))} 0)`} opacity={fade(frame, tags, 8)}>
          <rect x={430} y={430} width={270} height={90} rx={20} fill={C.sunShade} transform="translate(0 8)" />
          <path d="M 430 475 L 470 430 H 700 V 520 H 470 Z" fill={C.sun} />
          <circle cx={475} cy={475} r={10} fill={C.ink} />
          <Txt x={590} y={475} size={T.label} weight={900} fill={C.ink}>
            v0.8.0
          </Txt>
        </g>
        <line x1={800} y1={475} x2={1500} y2={475} stroke={C.dim} strokeWidth={10} strokeLinecap="round" />
        {steps.map((step, i) => {
          const x = 800 + i * 140;
          const on = fade(frame, mix(passes - 10, passes + 20, i / (steps.length - 1)), 6) > 0.5;
          return (
            <g key={step}>
              <circle cx={x} cy={475} r={36} fill={on ? C.green : C.night2} />
              {on && <Tick x={x} y={475} r={26} />}
              <Txt x={x} y={545} size={26} weight={800} fill={on ? C.white : C.muted}>
                {step}
              </Txt>
            </g>
          );
        })}
        <Pop p={pop(frame, passes + 30)} cx={1650} cy={475}>
          <rect x={1560} y={400} width={180} height={150} rx={22} fill={C.greenShade} />
          <rect x={1560} y={390} width={180} height={150} rx={22} fill={C.green} />
          <Txt x={1650} y={465} size={T.small} weight={900} fill={C.ink}>
            Released
          </Txt>
        </Pop>
        <Txt x={1100} y={760} size={T.label} weight={800} fill={C.muted} opacity={fade(frame, tags, 10)}>
          Nothing goes live on the agent’s word
        </Txt>
      </g>
    </Stage>
  );
};
