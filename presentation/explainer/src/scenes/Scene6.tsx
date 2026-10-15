import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent, Person } from "../components/Characters";
import { ChatBubble, Magnifier, Padlock, Stamp } from "../components/Icons";
import { Card, Chip, Doc, Pop, Stage, Tick, Txt } from "../components/ui";
import { PrincipleTiles } from "./Scene4";

const t = sceneTimes("s6");

const TREE = ["extensions/", "  dbt/", "    runner/", "    launcher/", "    README.md", "docs/", "  extensions.md"];
const LIBRARIES = [
  { licence: "Apache-2.0", fill: C.green },
  { licence: "MIT", fill: C.green },
  { licence: "LGPL-2.1", fill: C.amber },
  { licence: "BSD", fill: C.green },
];
const STEPS = ["lint", "unit", "contract", "build", "integration", "smoke"];

export const Scene6: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s6b1
  const reads = t.at("s6b1", "the agent reads");
  const scan = ease(frame, reads, t.e.s6b1);
  // s6b2
  const noInternet = t.at("s6b2", "no internet");
  const proposes = t.at("s6b2", "So it proposes");
  const contract = t.at("s6b2", "behind the same contract");
  const slide = ease(frame, proposes + 4, proposes + 26);
  // s6b3
  const finds = t.at("s6b3", "finds a library");
  const swaps = t.at("s6b3", "swaps it");
  const belt = ease(frame, t.b.s6b3, t.e.s6b3);
  const swap = ease(frame, swaps, swaps + 14);
  // s6b4
  const decide = t.at("s6b4", "Then I decide");
  const spike = t.at("s6b4", "test it in a spike");
  const pick = ease(frame, decide + 6, decide + 30);
  // s6b5
  const tiles = t.at("s6b5", "five principles");
  const suite = t.at("s6b5", "runs the full test suite");
  const done = t.at("s6b5", "before it says done");
  // s6b6
  const people = t.at("s6b6", "The people decide");

  return (
    <Stage>
      {/* s6b1: the agent reads the existing code first */}
      <g opacity={vis(t.b.s6b1, t.b.s6b2)}>
        <Pop p={pop(frame, t.b.s6b1)} cx={560} cy={170}>
          <ChatBubble x={620} y={160} w={900} lines={["Can we load this table from Statistics", "Netherlands, every day?"]} size={T.small} />
        </Pop>
        <Pop p={pop(frame, t.b.s6b1 + 8)} cx={860} cy={640}>
          <Card x={860} y={650} w={760} h={480} />
          {TREE.map((line, i) => {
            const depth = line.length - line.trimStart().length;
            const name = line.trim();
            const y = 470 + i * 60;
            const read = scan * TREE.length > i;
            return (
              <g key={line} opacity={read ? 1 : 0.45}>
                <rect x={540 + depth * 20} y={y - 16} width={34} height={28} rx={6} fill={name.endsWith("/") ? C.sun : C.paper} />
                <Txt x={590 + depth * 20} y={y} size={T.small} anchor="start" weight={800} fill={read ? C.white : C.muted}>
                  {name}
                </Txt>
              </g>
            );
          })}
        </Pop>
        <Pop p={pop(frame, reads)} cx={1100} cy={500}>
          <Magnifier x={1160} y={470 + scan * (TREE.length - 1) * 60} scale={0.7} />
        </Pop>
        <Pop p={pop(frame, t.b.s6b1 + 14)} cx={1560} cy={700}>
          <Agent x={1560} y={700} scale={1.3} frame={frame} mood="focus" />
        </Pop>
      </g>

      {/* s6b2: runs have no internet, so loading becomes its own extension behind the same contract */}
      <g opacity={vis(t.b.s6b2, t.b.s6b3)}>
        <Pop p={pop(frame, t.b.s6b2)} cx={640} cy={520}>
          <Card x={640} y={520} w={460} h={300} fill={C.night2}>
            <Txt x={640} y={420} size={T.label} weight={900}>
              Pipeline runs
            </Txt>
            <rect x={470} y={470} width={340} height={22} rx={11} fill={C.teal} opacity={0.8} />
            <rect x={470} y={510} width={260} height={22} rx={11} fill={C.teal} opacity={0.5} />
          </Card>
        </Pop>
        <Pop p={pop(frame, noInternet)} cx={830} cy={380}>
          <Padlock x={905} y={350} scale={0.9} />
          <Chip x={640} y={720} text="no internet, on purpose" fill={C.amber} shade={C.sunShade} size={T.small} />
        </Pop>
        <g transform={`translate(${mix(900, 0, slide)} 0)`} opacity={fade(frame, proposes, 8)}>
          <Card x={1300} y={520} w={460} h={300} fill={C.night2}>
            <Txt x={1300} y={420} size={T.label} weight={900} fill={C.sun}>
              Loading data
            </Txt>
            <rect x={1130} y={470} width={340} height={22} rx={11} fill={C.sun} opacity={0.8} />
            <rect x={1130} y={510} width={220} height={22} rx={11} fill={C.sun} opacity={0.5} />
          </Card>
          <Chip x={1300} y={720} text="new extension" fill={C.sun} shade={C.sunShade} size={T.small} />
        </g>
        {/* The contract both extensions plug into */}
        <Pop p={pop(frame, contract - 4)} cx={960} cy={900}>
          <rect x={360} y={870} width={1200} height={70} rx={35} fill={C.violetShade} />
          <rect x={360} y={860} width={1200} height={70} rx={35} fill={C.violet} />
          <Txt x={960} y={895} size={T.small} weight={900}>
            One contract
          </Txt>
          <rect x={622} y={800} width={36} height={60} rx={10} fill={C.violet} />
          <rect x={1282} y={800} width={36} height={60} rx={10} fill={C.violet} opacity={fade(frame, contract + 4, 8)} />
        </Pop>
      </g>

      {/* s6b3: licence check */}
      <g opacity={vis(t.b.s6b3, t.b.s6b4)}>
        <Txt x={960} y={170} size={T.h1} weight={900}>
          Licence check
        </Txt>
        <rect x={150} y={720} width={1620} height={26} rx={13} fill={C.dim} />
        {/* Scanner arch */}
        <path d="M 860 720 V 380 A 100 100 0 0 1 1060 380 V 720" stroke={C.muted} strokeWidth={22} fill="none" />
        {LIBRARIES.map((lib, i) => {
          const x = mix(-300, 1400, belt) + i * 340 - 340;
          const flagged = lib.licence.startsWith("LGPL");
          const replaced = flagged && swap > 0.5;
          const fill = flagged ? (frame > finds ? (replaced ? C.green : C.amber) : C.paper) : frame > finds - 20 ? lib.fill : C.paper;
          const lift = flagged ? Math.sin(swap * Math.PI) * -120 : 0;
          return (
            <g key={lib.licence} transform={`translate(0 ${lift})`}>
              <Card x={x} y={600} w={240} h={220} fill={C.paper} shade={C.paperShade}>
                <rect x={x - 120} y={490} width={240} height={50} rx={24} fill={fill} />
                <Txt x={x} y={515} size={28} weight={900} fill={C.ink}>
                  {replaced ? "MIT" : lib.licence}
                </Txt>
                <rect x={x - 80} y={580} width={160} height={16} rx={8} fill={C.paperShade} />
                <rect x={x - 80} y={614} width={110} height={16} rx={8} fill={C.paperShade} />
                <Txt x={x} y={665} size={26} weight={800} fill={C.dim}>
                  library
                </Txt>
              </Card>
            </g>
          );
        })}
        <Pop p={pop(frame, finds)} cx={960} cy={860}>
          <Chip x={960} y={860} text={swap > 0.5 ? "swapped for an open licence" : "copyleft terms"} fill={swap > 0.5 ? C.green : C.amber} shade={swap > 0.5 ? C.greenShade : C.sunShade} size={T.small} />
        </Pop>
      </g>

      {/* s6b4: compare tools, then a person decides */}
      <g opacity={vis(t.b.s6b4, t.b.s6b5)}>
        <Txt x={960} y={150} size={T.h1} weight={900}>
          Compare, then decide
        </Txt>
        {[0, 1, 2].map((i) => {
          const x = 560 + i * 400;
          const chosen = i === 1;
          const p = chosen ? pick : 0;
          const cx = mix(x, 960, p);
          const cy = mix(470, 790, p);
          return (
            <Pop key={i} p={pop(frame, t.b.s6b4 + i * 5)} cx={x} cy={470}>
              <g opacity={chosen ? 1 : mix(1, 0.4, fade(frame, decide, 10))}>
                <Card x={cx} y={cy} w={300} h={240} fill={C.night2}>
                  <Txt x={cx} y={cy - 70} size={T.small} weight={900}>
                    {`Tool ${"ABC"[i]}`}
                  </Txt>
                  {[0, 1, 2].map((r) => (
                    <g key={r}>
                      <rect x={cx - 110} y={cy - 26 + r * 44} width={220} height={20} rx={10} fill={C.ink} />
                      <rect x={cx - 110} y={cy - 26 + r * 44} width={220 * [[0.5, 0.8, 0.4], [0.8, 0.6, 0.7], [0.6, 0.4, 0.9]][i][r]} height={20} rx={10} fill={[C.teal, C.sun, C.coral][r]} />
                    </g>
                  ))}
                </Card>
              </g>
            </Pop>
          );
        })}
        {/* The person's hand */}
        <g opacity={fade(frame, decide, 6) * fadeOut(frame, decide + 40, 10)} transform={`translate(${mix(960, 960, pick)} ${mix(300, 620, pick)})`}>
          {/* Sleeve and a mitten hand reaching down */}
          <rect x={-46} y={-420} width={92} height={330} rx={40} fill={C.coralShade} />
          <rect x={-46} y={-420} width={74} height={330} rx={36} fill={C.coral} />
          <rect x={-40} y={-110} width={80} height={96} rx={34} fill={C.skinShade} />
          <rect x={-40} y={-110} width={66} height={96} rx={32} fill={C.skin} />
          <rect x={-58} y={-96} width={30} height={56} rx={15} fill={C.skin} transform="rotate(-24 -43 -68)" />
        </g>
        <Pop p={pop(frame, spike - 6)} cx={960} cy={790}>
          <rect x={720} y={650} width={480} height={300} rx={36} fill="none" stroke={C.sun} strokeWidth={8} strokeDasharray="20 16" />
          <Chip x={960} y={970} text="Candidate · spike" fill={C.sun} shade={C.sunShade} size={T.small} />
        </Pop>
      </g>

      {/* s6b5: principles check, then the full test suite before "done" */}
      <g opacity={vis(t.b.s6b5, t.b.s6b6)}>
        <PrincipleTiles frame={frame} at={t.b.s6b5} y={330} size={190} spacing={240} ticks={[0, 1, 2, 3, 4].map((i) => pop(frame, tiles + 6 + i * 4))} />
        <line x1={460} y1={720} x2={1460} y2={720} stroke={C.dim} strokeWidth={12} strokeLinecap="round" />
        {STEPS.map((step, i) => {
          const x = 460 + i * 200;
          const at = mix(suite, done, i / (STEPS.length - 1));
          const on = fade(frame, at, 6);
          return (
            <g key={step}>
              <circle cx={x} cy={720} r={44} fill={on > 0.5 ? C.green : C.night2} />
              {on > 0.5 && <Tick x={x} y={720} r={30} fill={C.green} />}
              <Txt x={x} y={800} size={30} weight={800} fill={on > 0.5 ? C.white : C.muted}>
                {step}
              </Txt>
            </g>
          );
        })}
        <Pop p={pop(frame, done + 10)} cx={960} cy={920}>
          <Chip x={960} y={920} text="done ✓" fill={C.green} shade={C.greenShade} size={T.label} />
        </Pop>
      </g>

      {/* s6b6: the agent proposes, the people decide */}
      <g opacity={vis(t.b.s6b6, t.length)}>
        <line x1={960} y1={180} x2={960} y2={900} stroke={C.dim} strokeWidth={6} strokeDasharray="10 18" />
        <Pop p={pop(frame, t.b.s6b6)} cx={480} cy={600}>
          <Agent x={420} y={640} scale={1.4} frame={frame} />
          {[0, 1, 2].map((i) => (
            <Doc key={i} x={640 + i * 18} y={640 - i * 18} w={180} h={220} title="" accent={[C.teal, C.sun, C.coral][i]} lines={3} />
          ))}
          <Txt x={480} y={900} size={T.label} weight={900}>
            Researches, proposes
          </Txt>
        </Pop>
        <Pop p={pop(frame, people)} cx={1440} cy={600}>
          <Person x={1360} y={820} scale={1.2} frame={frame} />
          <Stamp x={1580} y={760} text="DECIDE" />
          <Txt x={1440} y={900} size={T.label} weight={900} fill={C.coral}>
            Decides
          </Txt>
        </Pop>
      </g>
    </Stage>
  );
};
