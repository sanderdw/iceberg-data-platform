import React from "react";
import { useCurrentFrame } from "remotion";
import { bob, count, ease, fade, fadeOut, formatNumber, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent, Person } from "../components/Characters";
import { Chip, Doc, Pop, Source, Stage, Txt } from "../components/ui";

const t = sceneTimes("s5");

/** Thought bubble; `children` is drawn inside it. Origin is the bubble's centre. */
const Thought: React.FC<{ x: number; y: number; r?: number; children?: React.ReactNode; tail?: [number, number] }> = ({
  x, y, r = 90, children, tail = [-60, 110],
}) => (
  <g>
    <circle cx={x + tail[0] * 0.5} cy={y + tail[1] * 0.75} r={14} fill={C.white} />
    <circle cx={x + tail[0]} cy={y + tail[1]} r={9} fill={C.white} />
    <ellipse cx={x} cy={y} rx={r * 1.25} ry={r} fill={C.white} />
    {children}
  </g>
);

/** Tiny document glyph. */
const MiniDoc: React.FC<{ x: number; y: number; fill: string; s?: number }> = ({ x, y, fill, s = 1 }) => (
  <g transform={`translate(${x} ${y}) scale(${s})`}>
    <rect x={-22} y={-28} width={44} height={56} rx={8} fill={C.paperShade} />
    <rect x={-22} y={-28} width={44} height={10} rx={5} fill={fill} />
    <rect x={-14} y={-6} width={28} height={6} rx={3} fill={C.dim} />
    <rect x={-14} y={6} width={20} height={6} rx={3} fill={C.dim} />
  </g>
);

const SHELF = [
  { title: "AGENTS.md", fill: C.teal },
  { title: "SKILL.md", fill: C.sun },
  { title: "Design record", fill: C.coral },
];

export const Scene5: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s5b1: three mornings, each time an empty head.
  const day = t.d.s5b1 / 3;
  const k = Math.max(0, Math.min(2, Math.floor((frame - t.b.s5b1) / day)));
  const dayStart = t.b.s5b1 + k * day;
  const walk = ease(frame, dayStart, dayStart + 18);
  const sunArc = Math.max(0, Math.min(1, (frame - dayStart) / day));
  // s5b2
  const files = t.at("s5b2", "It has to live");
  // s5b3
  const sixty = t.at("s5b3", "More than sixty");
  // s5b4
  const why = t.at("s5b4", "And design records");
  const noCode = t.at("s5b4", "before any code");
  // s5b5
  const plugIn = t.at("s5b5", "connect AI");

  return (
    <Stage>
      {/* s5b1: a brilliant colleague arrives every morning with no memory */}
      <g opacity={vis(t.b.s5b1, t.b.s5b2)}>
        {/* Sun crossing the sky, once per morning */}
        <circle cx={mix(200, 1720, sunArc)} cy={330 - Math.sin(sunArc * Math.PI) * 180} r={56} fill={C.sun} opacity={0.9} />
        {/* Door */}
        <rect x={360} y={480} width={260} height={430} rx={20} fill={C.ink} />
        <rect x={380} y={500} width={220} height={410} rx={14} fill={C.violet} opacity={0.4} />
        <rect x={0} y={910} width={1920} height={170} fill="#0D1B45" />
        <Pop p={pop(frame, dayStart)} cx={960} cy={995}>
          <Chip x={960} y={995} text={["Monday", "Tuesday", "Wednesday"][k]} fill={C.violet} shade={C.violetShade} color={C.white} size={T.label} />
        </Pop>
        <Agent x={mix(490, 1000, walk)} y={840 + Math.abs(bob(frame, 8, 16)) * (walk < 1 ? 1 : 0)} scale={1.2} frame={frame} mood={walk >= 1 ? "blank" : "happy"} />
        <Pop p={pop(frame, dayStart + 20)} cx={1160} cy={560}>
          <Thought x={1160} y={560} r={84} tail={[-80, 120]}>
            <Txt x={1160} y={560} size={T.h1} weight={900} fill={C.dim}>
              ?
            </Txt>
          </Thought>
        </Pop>
      </g>

      {/* s5b2: what matters goes from heads into files */}
      <g opacity={vis(t.b.s5b2, t.b.s5b3)}>
        <Pop p={pop(frame, t.b.s5b2)} cx={460} cy={800}>
          <Person x={460} y={980} scale={1.3} frame={frame} />
        </Pop>
        {[0, 1, 2].map((i) => {
          const leave = ease(frame, files + i * 6, files + i * 6 + 24);
          const x = mix(560 + i * 70, 1110 + i * 260, leave);
          const y = mix(440 - i * 90, 470, leave) - Math.sin(leave * Math.PI) * 120;
          return (
            <Pop key={i} p={pop(frame, t.b.s5b2 + 8 + i * 6)} cx={x} cy={y}>
              {leave < 0.6 ? (
                <ellipse cx={x} cy={y} rx={56} ry={44} fill={C.white} opacity={1 - leave} />
              ) : (
                <MiniDoc x={x} y={y} fill={SHELF[i].fill} s={2.2} />
              )}
            </Pop>
          );
        })}
        <Pop p={pop(frame, files - 6)} cx={1370} cy={560}>
          <rect x={1030} y={548} width={680} height={24} rx={12} fill={C.muted} />
          <rect x={1060} y={572} width={24} height={60} fill={C.dim} />
          <rect x={1660} y={572} width={24} height={60} fill={C.dim} />
        </Pop>
      </g>

      {/* s5b3: AGENTS.md */}
      <g opacity={vis(t.b.s5b3, t.b.s5b4)}>
        <Pop p={pop(frame, t.b.s5b3)} cx={640} cy={560}>
          <Doc x={640} y={560} w={420} h={520} title="AGENTS.md" accent={C.teal} lines={8} titleSize={44} />
        </Pop>
        <g opacity={fade(frame, sixty, 8)}>
          <Txt x={1340} y={500} size={T.big} weight={900} fill={C.teal}>
            {`${formatNumber(count(frame, sixty, 60000, 40))}+`}
          </Txt>
          <Txt x={1340} y={620} size={T.label} weight={800}>
            open-source projects
          </Txt>
        </g>
        <Source text="Source: agents.md" opacity={fade(frame, sixty, 10)} />
      </g>

      {/* s5b4: a skill and a design record */}
      <g opacity={vis(t.b.s5b4, t.b.s5b5)}>
        <Pop p={pop(frame, t.b.s5b4)} cx={620} cy={560}>
          <Doc x={620} y={560} w={420} h={520} title="SKILL.md" accent={C.sun} lines={0} titleSize={44} />
          <Txt x={440} y={420} size={28} weight={800} fill={C.dim} anchor="start">
            Build a semantic model
          </Txt>
          {["Interview the owner", "Draft the model", "Run the checks", "Publish"].map((step, i) => (
            <g key={step} opacity={fade(frame, t.b.s5b4 + 10 + i * 6, 8)}>
              <circle cx={462} cy={490 + i * 66} r={20} fill={C.sun} />
              <Txt x={462} y={490 + i * 66} size={26} weight={900} fill={C.ink}>
                {i + 1}
              </Txt>
              <Txt x={500} y={490 + i * 66} size={32} weight={800} fill={C.ink} anchor="start">
                {step}
              </Txt>
            </g>
          ))}
        </Pop>
        <Pop p={pop(frame, why)} cx={1300} cy={560}>
          <Doc x={1300} y={560} w={420} h={520} title="Design record" accent={C.coral} lines={7} titleSize={44} />
        </Pop>
        <Pop p={pop(frame, why + 14, 220, 10)} cx={1380} cy={700}>
          <g transform="rotate(-12 1380 700)">
            <rect x={1270} y={650} width={220} height={100} rx={20} fill="none" stroke={C.coral} strokeWidth={10} />
            <Txt x={1380} y={700} size={T.h2} weight={900} fill={C.coral}>
              WHY
            </Txt>
          </g>
        </Pop>
        <Pop p={pop(frame, noCode)} cx={1300} cy={900}>
          <Chip x={1300} y={900} text="no code yet" fill={C.dim} shade={C.ink} color={C.white} size={T.small} />
        </Pop>
      </g>

      {/* s5b5: connect the agent to the shelf */}
      <g opacity={vis(t.b.s5b5, t.length)}>
        <Txt x={960} y={160} size={T.h2} weight={900} fill={C.sun} opacity={fade(frame, plugIn, 8)}>
          “Connect AI to your internal context”
        </Txt>
        <Pop p={pop(frame, t.b.s5b5)} cx={520} cy={720}>
          <Agent x={520} y={720} scale={1.4} frame={frame} mood={frame > plugIn + 20 ? "happy" : "blank"} />
        </Pop>
        <Pop p={pop(frame, t.b.s5b5 + 6)} cx={1380} cy={620}>
          <rect x={1060} y={760} width={640} height={24} rx={12} fill={C.muted} />
          {SHELF.map((d, i) => (
            <Doc key={d.title} x={1170 + i * 210} y={640} w={180} h={220} title="" accent={d.fill} lines={3} />
          ))}
          {SHELF.map((d, i) => (
            <Txt key={d.title} x={1170 + i * 210} y={830} size={28} weight={800} fill={C.white}>
              {d.title}
            </Txt>
          ))}
        </Pop>
        <path
          d="M 600 760 C 760 900 900 900 1060 772"
          stroke={C.teal}
          strokeWidth={10}
          fill="none"
          strokeLinecap="round"
          pathLength={1}
          strokeDasharray="1 1"
          strokeDashoffset={1 - ease(frame, plugIn, plugIn + 18)}
        />
        <Pop p={pop(frame, plugIn + 18)} cx={640} cy={430}>
          <Thought x={660} y={430} r={84} tail={[-60, 120]}>
            {SHELF.map((d, i) => (
              <MiniDoc key={d.title} x={600 + i * 60} y={430} fill={d.fill} s={0.9} />
            ))}
          </Thought>
        </Pop>
        <Source text="Source: Google DORA AI Capabilities Model, 2025" opacity={fade(frame, plugIn, 10)} />
      </g>
    </Stage>
  );
};
